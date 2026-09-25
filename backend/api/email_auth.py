"""Validation email, calculs à usage unique et quotas persistants."""
from datetime import timedelta
import html
import hmac
import secrets
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from common import (db_connect, utcnow, sha256_text, hmac_hex, AUTH_PEPPER,
    encrypt_pii, decrypt_pii, lookup_hash, normalize_email, normalize_username,
    COOKIE_SECURE, require_same_origin, PUBLIC_ORIGIN)

router=APIRouter(prefix='/api/v3/auth',tags=['email-auth'])
DEVICE='__Host-bvmac_device' if COOKIE_SECURE else 'bvmac_device'
DEFAULT_TEMPLATE='''<!doctype html><html lang="fr"><head><meta charset="utf-8"></head><body style="margin:0;background:#f2f4f1;color:#17201c;font:16px Arial,sans-serif"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td style="padding:32px 16px"><table role="presentation" width="100%" style="max-width:580px;margin:auto;background:#fff;border:1px solid #d9ded8;border-radius:12px" cellpadding="24"><tr><td style="background:#17251f;color:#fff;font-size:24px">Marché <span style="color:#c9a23f">BVMAC</span></td></tr><tr><td><h1 style="font-size:23px;color:#2e5e4e">{{title}}</h1><p>Bonjour {{pseudo}},</p><p>{{message}}</p><p style="font-size:32px;font-weight:bold;letter-spacing:5px;color:#2e5e4e">{{code}}</p><p><a href="{{action_url}}" style="display:inline-block;padding:12px 18px;background:#2e5e4e;color:#fff;border-radius:6px;text-decoration:none">{{action_label}}</a></p><p>Valable pendant {{minutes}} minutes. Ne partage jamais ce message ou son code.</p><p style="color:#67726c;font-size:13px">Si tu n’es pas à l’origine de cette demande, ignore ce message.</p></td></tr><tr><td style="border-top:1px solid #d9ded8;color:#67726c;font-size:12px">Initiative personnelle indépendante, sans affiliation à la BVMAC. Données sources : Bulletins Officiels de la Cote.</td></tr></table></td></tr></table></body></html>'''

class HumanIn(BaseModel):
    captcha_id:str=Field(min_length=32,max_length=64)
    captcha_answer:str=Field(min_length=1,max_length=12)

class VerifyIn(BaseModel):
    ticket:str=Field(min_length=32,max_length=128)
    code:str=Field(pattern=r'^\d{6}$')

class ResendIn(HumanIn):
    ticket:str=Field(min_length=32,max_length=128)

class ResetIn(HumanIn):
    token:str=Field(min_length=32,max_length=128)
    new_password:str=Field(min_length=12,max_length=128)

def device(request):
    value=request.cookies.get(DEVICE,'')
    if len(value)<32 or len(value)>128:raise HTTPException(400,'Recharge le calcul de vérification.')
    return sha256_text(value)

@router.get('/challenge')
def challenge(request:Request,response:Response,purpose:Literal['login','register','forgot','resend','reset']):
    cookie=request.cookies.get(DEVICE) or secrets.token_urlsafe(32)
    if len(cookie)>128:cookie=secrets.token_urlsafe(32)
    response.set_cookie(DEVICE,cookie,secure=COOKIE_SECURE,httponly=True,samesite='strict',path='/')
    a,b=secrets.randbelow(80)+10,secrets.randbelow(80)+10
    op=secrets.choice(['+','−','×'])
    if op=='−':a,b=max(a,b),min(a,b)
    if op=='×':a,b=secrets.randbelow(9)+1,secrets.randbelow(9)+1
    answer=a+b if op=='+' else a-b if op=='−' else a*b
    ticket=secrets.token_hex(24)
    with db_connect() as conn:
        conn.execute("DELETE FROM auth.human_challenge WHERE expires_at<now()")
        # At most one live calculation per purpose and browser.
        conn.execute('DELETE FROM auth.human_challenge WHERE device_hash=%s AND purpose=%s',(sha256_text(cookie),purpose))
        conn.execute("INSERT INTO auth.human_challenge(ticket_hash,device_hash,purpose,answer_hash,expires_at) VALUES(%s,%s,%s,%s,now()+interval '5 minutes')",(sha256_text(ticket),sha256_text(cookie),purpose,hmac_hex(AUTH_PEPPER,ticket+'|'+str(answer))))
        conn.commit()
    response.headers['Cache-Control']='no-store'
    return {'captcha_id':ticket,'question':f'{a} {op} {b} = ?'}

def check_human(data,request,purpose):
    require_same_origin(request)
    with db_connect() as conn:
        row=conn.execute('DELETE FROM auth.human_challenge WHERE ticket_hash=%s RETURNING device_hash,purpose,answer_hash,expires_at',(sha256_text(data.captcha_id),)).fetchone()
        conn.commit() # Consume even a wrong answer: no replay or brute force.
    valid=row and row[0]==device(request) and row[1]==purpose and row[3]>utcnow()
    if not valid or not hmac.compare_digest(row[2],hmac_hex(AUTH_PEPPER,data.captcha_id+'|'+data.captcha_answer.strip())):
        raise HTTPException(400,'Calcul incorrect ou expiré. Un nouveau calcul est nécessaire.')

def reserve_quota(conn,keys):
    now=utcnow();today=now.date(); deadlines=[]
    # Stable locking order avoids deadlocks when a browser changes email.
    for key in sorted(keys):
        conn.execute('INSERT INTO auth.mail_quota(quota_key,day,cycles,sends,started_at) VALUES(%s,%s,0,0,%s) ON CONFLICT DO NOTHING',(key,today,now))
        row=conn.execute('SELECT day,cycles,sends,started_at FROM auth.mail_quota WHERE quota_key=%s FOR UPDATE',(key,)).fetchone()
        day,cycles,sends,started=row
        if day!=today:cycles,sends,started=0,0,now
        new_cycle=sends==0 or now>=started+timedelta(minutes=20)
        if new_cycle:
            if cycles>=5:raise HTTPException(429,'Limite quotidienne atteinte. Réessaie demain (UTC).',headers={'Retry-After':str(int(((now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)-now).total_seconds()))})
            cycles+=1;sends=0;started=now
        if now>=started+timedelta(minutes=10) or sends>=3:
            seconds=max(1,int((started+timedelta(minutes=20)-now).total_seconds()))
            raise HTTPException(429,'Cycle épuisé. Réessaie après le délai indiqué.',headers={'Retry-After':str(seconds)})
        conn.execute('UPDATE auth.mail_quota SET day=%s,cycles=%s,sends=%s,started_at=%s WHERE quota_key=%s',(today,cycles,sends+1,started,key))
        deadlines.append(started+timedelta(minutes=10))
    return min(deadlines)

def settings(conn):
    # api_key_cipher is retained as the encrypted credential column for zero-downtime
    # compatibility with existing production databases and rollback releases.
    r=conn.execute('SELECT api_key_cipher,sender,template_html FROM admin.mail_settings WHERE singleton=true').fetchone()
    credential=decrypt_pii(r[0]) if r and r[0] else None
    legacy=bool(credential and credential.startswith('re_'))
    if legacy:credential=None
    sender=r[1] if r else ''
    if legacy and sender=='onboarding@resend.dev':sender=''
    return {'password':credential,'sender':sender,'template':r[2] if r else DEFAULT_TEMPLATE,'legacy':legacy}

def render(template,values):
    # Only escaped literal substitution; never execute template code.
    import re
    return re.sub(r'\{\{\s*([a-z_]+)\s*\}\}',lambda m:html.escape(str(values.get(m[1],'')),quote=True),template)

def deliver_internal_notice(config, subject: str, body_html: str) -> bool:
    """Send an editor notification to the configured Gmail address itself.

    Feedback is persisted before this best-effort notification is attempted, so a
    temporary SMTP outage never loses a user's report.
    """
    if not config.get('password') or not config.get('sender'):
        return False
    login_address=parseaddr(config['sender'])[1]
    if not login_address:
        return False
    message=MIMEMultipart('alternative')
    message['From']=config['sender']
    message['To']=config['sender']
    message['Subject']=subject[:160]
    message.attach(MIMEText('Nouvelle remontée BVMAC Market. Consulte le contenu HTML ou l’espace admin.', 'plain', 'utf-8'))
    message.attach(MIMEText(body_html, 'html', 'utf-8'))
    try:
        with smtplib.SMTP('smtp.gmail.com',587,timeout=15) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(login_address,config['password'])
            server.send_message(message)
        return True
    except (smtplib.SMTPException,TimeoutError,OSError):
        return False

def deliver(config,email,pseudo,kind,secret,expires,identifier):
    if not config['password'] or not config['sender']:raise HTTPException(503,'Envoi email non configuré. Réessaie plus tard.')
    reset=kind=='reset'
    values={'title':'Réinitialiser ton mot de passe' if reset else 'Valider ton adresse email','pseudo':pseudo,
      'message':'Utilise le lien ci-dessous pour choisir un nouveau mot de passe.' if reset else 'Saisis ce code dans l’onglet où tu as commencé ta connexion ou ton inscription.',
      'code':'' if reset else secret,'action_url':PUBLIC_ORIGIN+'/auth.html'+('?mode=forgot#reset='+secret if reset else '?mode=login'),
      'action_label':'Choisir mon mot de passe' if reset else 'Ouvrir la plateforme','minutes':max(1,int((expires-utcnow()).total_seconds()/60))}
    login_address=parseaddr(config['sender'])[1]
    if not login_address:raise HTTPException(503,'Envoi email non configuré. Réessaie plus tard.')
    message=MIMEMultipart('alternative')
    message['From']=config['sender'];message['To']=email;message['Subject']=values['title']
    message.attach(MIMEText(render(config['template'],values),'html','utf-8'))
    try:
        with smtplib.SMTP('smtp.gmail.com',587,timeout=15) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(login_address,config['password'])
            server.send_message(message)
    except (smtplib.SMTPException,TimeoutError,OSError):
        # Never expose SMTP responses or the application password to the client.
        raise HTTPException(503,'L’envoi n’a pas pu être confirmé. Réessaie plus tard ; les limites de sécurité restent actives.') from None

def begin_email(uid,email,pseudo,kind,request):
    token=secrets.token_urlsafe(32);secret=secrets.token_urlsafe(32) if kind=='reset' else f'{secrets.randbelow(1000000):06d}'
    dh=device(request);eh=lookup_hash('email',normalize_email(email))
    with db_connect() as conn:
        config=settings(conn)
        if uid and (not config['password'] or not config['sender']):raise HTTPException(503,'Envoi email non configuré. Réessaie plus tard.')
        expires=reserve_quota(conn,['email:'+eh,'device:'+dh])
        conn.execute('UPDATE auth.email_challenge SET consumed_at=now() WHERE email_hash=%s AND purpose=%s AND consumed_at IS NULL',(eh,kind))
        conn.execute('''INSERT INTO auth.email_challenge(ticket_hash,user_id,email_hash,device_hash,purpose,secret_hash,expires_at)
          VALUES(%s,%s,%s,%s,%s,%s,%s)''',(sha256_text(token),uid,eh,dh,kind,hmac_hex(AUTH_PEPPER,secret),expires))
        conn.commit()
    if uid:deliver(config,email,pseudo,kind,secret,expires,sha256_text(token))
    return {'verification_required':kind=='verify','ticket':token,'expires_at':expires.isoformat(),
      'message':'Si un compte validé correspond, un lien a été envoyé.' if kind=='reset' else 'Saisis le code envoyé à ton email. Il expire au plus tard dans 10 minutes.'}

@router.post('/verify-email')
def verify(data:VerifyIn,request:Request,response:Response):
    require_same_origin(request)
    from auth_module import _new_session,_set_session_cookie
    with db_connect() as conn:
        row=conn.execute("SELECT user_id,device_hash,secret_hash,expires_at,attempts FROM auth.email_challenge WHERE ticket_hash=%s AND purpose='verify' AND consumed_at IS NULL FOR UPDATE",(sha256_text(data.ticket),)).fetchone()
        if not row or row[1]!=device(request) or row[3]<=utcnow() or row[4]>=5:raise HTTPException(400,'Code expiré ou demande invalide.')
        conn.execute('UPDATE auth.email_challenge SET attempts=attempts+1 WHERE ticket_hash=%s',(sha256_text(data.ticket),))
        if not hmac.compare_digest(row[2],hmac_hex(AUTH_PEPPER,data.code)):
            conn.commit();raise HTTPException(400,'Code incorrect. Cinq essais maximum.')
        user=conn.execute("SELECT status FROM auth.user_account WHERE user_id=%s FOR UPDATE",(row[0],)).fetchone()
        if not user or user[0]!='active':raise HTTPException(400,'Compte indisponible.')
        conn.execute('UPDATE auth.email_challenge SET consumed_at=now() WHERE ticket_hash=%s',(sha256_text(data.ticket),))
        conn.execute('UPDATE auth.user_account SET email_verified_at=now(),registration_pending=false,updated_at=now() WHERE user_id=%s',(row[0],))
        token,csrf,expires=_new_session(conn,row[0],request);conn.commit()
    _set_session_cookie(response,token)
    return {'ok':True,'csrf_token':csrf}

@router.post('/resend-email')
def resend_email(data:ResendIn,request:Request):
    check_human(data,request,'resend')
    with db_connect() as conn:
        row=conn.execute('''SELECT c.user_id,c.device_hash,c.purpose,u.email_cipher,u.username,u.email_verified_at
          FROM auth.email_challenge c LEFT JOIN auth.user_account u ON u.user_id=c.user_id
          WHERE c.ticket_hash=%s AND c.consumed_at IS NULL''',(sha256_text(data.ticket),)).fetchone()
    if not row or row[1]!=device(request):raise HTTPException(400,'Demande invalide. Recommence la procédure.')
    # Generic reset requests for nonexistent accounts cannot send email.
    if not row[0]:return {'message':'Si un compte validé correspond, un lien a été envoyé.'}
    if row[2]=='verify' and row[5]:raise HTTPException(400,'Adresse déjà validée. Connecte-toi.')
    return begin_email(row[0],decrypt_pii(row[3]),row[4],row[2],request)

@router.post('/reset-password')
def reset_password(data:ResetIn,request:Request):
    check_human(data,request,'reset')
    from auth_module import _hash_password
    hashed=hmac_hex(AUTH_PEPPER,data.token)
    with db_connect() as conn:
        row=conn.execute("SELECT user_id,ticket_hash FROM auth.email_challenge WHERE secret_hash=%s AND purpose='reset' AND consumed_at IS NULL AND expires_at>now() FOR UPDATE",(hashed,)).fetchone()
        if not row or not row[0]:raise HTTPException(400,'Lien invalide, déjà utilisé ou expiré.')
        user=conn.execute("SELECT status,email_verified_at FROM auth.user_account WHERE user_id=%s FOR UPDATE",(row[0],)).fetchone()
        if not user or user[0]!='active' or not user[1]:raise HTTPException(400,'Lien invalide, déjà utilisé ou expiré.')
        conn.execute('UPDATE auth.user_account SET password_hash=%s,must_change_password=false,updated_at=now() WHERE user_id=%s',(_hash_password(data.new_password),row[0]))
        conn.execute('UPDATE auth.email_challenge SET consumed_at=now() WHERE user_id=%s AND consumed_at IS NULL',(row[0],))
        conn.execute('DELETE FROM auth.recovery_code WHERE user_id=%s',(row[0],))
        conn.execute('UPDATE auth.user_session SET revoked_at=now() WHERE user_id=%s',(row[0],))
        conn.execute('UPDATE auth.login_guard SET failures_in_cycle=0,lock_strikes=0,locked_until=NULL WHERE user_id=%s',(row[0],))
        conn.commit()
    return {'ok':True,'message':'Mot de passe modifié. Connecte-toi avec ton nouveau mot de passe.'}
