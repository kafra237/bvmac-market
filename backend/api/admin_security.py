"""Second facteur sur console serveur, session liée à un onglet, configuration email."""
from datetime import timedelta
from email.utils import parseaddr
from html.parser import HTMLParser
from pathlib import Path
import hmac
import re
import secrets
from fastapi import APIRouter, Request, Response, HTTPException
from pydantic import BaseModel,Field
from common import db_connect,utcnow,sha256_text,encrypt_pii,require_same_origin,COOKIE_SECURE
from email_auth import settings,render,DEFAULT_TEMPLATE

router=APIRouter()

class StartIn(BaseModel):
    password:str=Field(min_length=1,max_length=256)
    admin_name:str=Field(min_length=2,max_length=80)
    tab_token:str=Field(min_length=40,max_length=128)

class FinishIn(BaseModel):
    ticket:str=Field(pattern=r'^[a-f0-9]{48}$')
    code:str=Field(pattern=r'^\d{8}$')
    tab_token:str=Field(min_length=40,max_length=128)

@router.post('/api/stat/login/start')
def start(data:StartIn,request:Request):
    require_same_origin(request)
    from admin_module import _verify_admin_password
    from launch_support import allowed
    from common import client_fingerprint
    if not allowed(('admin-login',client_fingerprint(request)),5):raise HTTPException(429,'Réessaie dans une minute.')
    if not _verify_admin_password(data.password):raise HTTPException(401,'Informations administrateur incorrectes')
    ticket=secrets.token_hex(24)
    with db_connect() as conn:
        conn.execute('DELETE FROM admin.console_challenge WHERE expires_at<now()')
        conn.execute("INSERT INTO admin.console_challenge(ticket,tab_hash,admin_name,expires_at) VALUES(%s,%s,%s,now()+interval '5 minutes')",(ticket,sha256_text(data.tab_token),data.admin_name.strip()))
        conn.commit()
    script=Path(__file__).resolve().parent.parent/'scripts/admin_code.py'
    return {'ticket':ticket,'command':f'sudo python3 {script} --challenge {ticket} --database '+__import__('psycopg').conninfo.conninfo_to_dict(__import__('common').DB_DSN).get('dbname','bvmac'),
        'message':'Connecte-toi au serveur et exécute cette commande. Saisis ici le code affiché (5 minutes, usage unique).'}

@router.post('/api/stat/login/finish')
def finish(data:FinishIn,request:Request,response:Response):
    require_same_origin(request)
    from admin_module import STAT_COOKIE
    with db_connect() as conn:
        r=conn.execute('SELECT tab_hash,code_hash,expires_at,attempts,admin_name FROM admin.console_challenge WHERE ticket=%s FOR UPDATE',(data.ticket,)).fetchone()
        if not r or r[0]!=sha256_text(data.tab_token) or not r[1] or r[2]<=utcnow() or r[3]>=5:raise HTTPException(401,'Code invalide ou expiré. Recommence la connexion.')
        conn.execute('UPDATE admin.console_challenge SET attempts=attempts+1 WHERE ticket=%s',(data.ticket,))
        if not hmac.compare_digest(r[1],sha256_text(data.ticket+'|'+data.code)):
            conn.commit();raise HTTPException(401,'Code incorrect. Cinq essais maximum.')
        conn.execute('DELETE FROM admin.console_challenge WHERE ticket=%s',(data.ticket,))
        token=secrets.token_urlsafe(48);expires=utcnow()+timedelta(hours=12)
        conn.execute('INSERT INTO admin.session(token_hash,expires_at,user_agent_hash,tab_hash,admin_name) VALUES(%s,%s,%s,%s,%s)',(sha256_text(token),expires,sha256_text(request.headers.get('user-agent','')),r[0],r[4]))
        conn.commit()
    # Session cookie only. In-memory tab secret additionally required on every API call.
    response.set_cookie(STAT_COOKIE,token,secure=COOKIE_SECURE,httponly=True,samesite='strict',path='/')
    response.headers['Cache-Control']='no-store'
    return {'ok':True}

@router.post('/api/stat/heartbeat')
def heartbeat(request:Request):
    from admin_module import _admin_required
    require_same_origin(request);_admin_required(request);return {'ok':True}

class MailIn(BaseModel):
    app_password:str|None=Field(default=None,max_length=64)
    sender:str=Field(min_length=3,max_length=320)
    template_html:str=Field(min_length=100,max_length=30000)

class TemplateCheck(HTMLParser):
    allowed={'html','head','meta','title','body','style','table','tbody','thead','tr','td','th','div','span','p','a','h1','h2','h3','br','hr','strong','b','em','small','ul','li','ol'}
    def handle_starttag(self,tag,attrs):
        if tag not in self.allowed:raise ValueError('Balise non autorisée : '+tag)
        for name,value in attrs:
            if name.startswith('on') or name in {'src','srcdoc','action','formaction','http-equiv'}:raise ValueError('Attribut actif interdit')
            if name=='href' and not ((value or '').startswith('https://') or value=='{{action_url}}'):raise ValueError('Les liens doivent utiliser HTTPS ou {{action_url}}')

def validate_template(value):
    if any(x in value.lower() for x in ('@import','url(','expression(','javascript:','data:','<script','<iframe')):raise HTTPException(400,'Le modèle doit contenir uniquement du HTML et CSS statique, sans contenu externe.')
    try:TemplateCheck().feed(value)
    except ValueError as exc:raise HTTPException(400,str(exc))
    if '{{code}}' not in value or '{{action_url}}' not in value:raise HTTPException(400,'Conserve {{code}} et {{action_url}} dans le modèle.')

@router.get('/api/stat/email-settings')
def get_settings(request:Request):
    from admin_module import _admin_required
    _admin_required(request)
    with db_connect() as conn:s=settings(conn)
    return {'configured':bool(s['password']),'sender':s['sender'],'template_html':s['template'],'default_template':DEFAULT_TEMPLATE,'provider':'gmail_smtp','host':'smtp.gmail.com','port':587,'legacy_config_ignored':s['legacy']}

@router.post('/api/stat/email-settings')
def save_settings(data:MailIn,request:Request):
    from admin_module import _admin_required
    _admin_required(request);require_same_origin(request)
    from auth_module import EMAIL_RE
    address=parseaddr(data.sender)[1]
    if '\n' in data.sender or '\r' in data.sender or not EMAIL_RE.fullmatch(address):raise HTTPException(400,'Adresse expéditeur invalide')
    validate_template(data.template_html)
    app_password=''.join((data.app_password or '').split()) or None
    if app_password and (len(app_password)!=16 or not app_password.isascii() or not app_password.isalnum()):
        raise HTTPException(400,'Le mot de passe d’application Google doit contenir 16 caractères alphanumériques.')
    with db_connect() as conn:
        conn.execute('''INSERT INTO admin.mail_settings(singleton,api_key_cipher,sender,template_html) VALUES(true,%s,%s,%s)
          ON CONFLICT(singleton) DO UPDATE SET api_key_cipher=coalesce(EXCLUDED.api_key_cipher,admin.mail_settings.api_key_cipher),sender=EXCLUDED.sender,template_html=EXCLUDED.template_html,updated_at=now()''',(encrypt_pii(app_password) if app_password else None,data.sender,data.template_html))
        conn.commit()
    return {'ok':True}

@router.post('/api/stat/email-preview')
def preview(data:MailIn,request:Request):
    from admin_module import _admin_required
    _admin_required(request);require_same_origin(request);validate_template(data.template_html)
    return {'html':render(data.template_html,{'title':'Valider ton adresse email','pseudo':'Exemple','message':'Saisis ce code dans ton onglet.','code':'123456','action_url':'https://example.invalid/auth.html','action_label':'Ouvrir la plateforme','minutes':10})}

@router.get('/api/stat/operations')
def operations(request:Request):
    from admin_module import _admin_required,STAT_ENV_PATH
    import json
    _admin_required(request)
    try:
        p=STAT_ENV_PATH.parent/'operations.json';data=json.loads(p.read_text());return data
    except (OSError,ValueError,AttributeError):return {'ok':False,'problems':['Contrôle opérationnel non encore disponible']}
