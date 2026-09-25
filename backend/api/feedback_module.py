from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from auth_module import optional_user, require_user
from common import client_fingerprint, db_connect, require_same_origin
from email_auth import deliver_internal_notice, settings as mail_settings

router = APIRouter(prefix='/api/v3/feedback', tags=['feedback'])

class FeedbackIn(BaseModel):
    # La classification est facultative côté utilisateur : aucune nomenclature imposée.
    category: str | None = Field(default=None, pattern=r'^(suggestion|anomaly|data_quality|security|other)$')
    subject: str | None = Field(default=None,max_length=160)
    message: str = Field(min_length=3,max_length=5000)
    page: str | None = Field(default=None,max_length=500)

@router.post('')
def submit_feedback(data:FeedbackIn, request:Request):
    require_same_origin(request)
    user=optional_user(request)
    ch=client_fingerprint(request)
    with db_connect() as conn:
        # Anti-spam côté serveur, indépendant de l’analytics navigateur.
        recent=conn.execute("""SELECT count(*) FROM auth.audit_log WHERE action='feedback_submitted'
                              AND client_hash=%s AND occurred_at>now()-interval '1 hour'""",(ch,)).fetchone()[0]
        if recent>=10:
            raise HTTPException(status_code=429,detail='Trop de remontées. Réessaie plus tard.')
        category=(data.category or 'other').strip() or 'other'
        message=data.message.strip()
        subject=(data.subject or '').strip() or (message[:80] + ('…' if len(message)>80 else ''))
        page=(data.page or '').strip() or None
        row=conn.execute('''INSERT INTO feedback.report(user_id,category,subject,message,page,priority)
                            VALUES(%s,%s,%s,%s,%s,%s) RETURNING report_id''',
                         (user['user_id'] if user else None,category,subject,message,page,
                          'critical' if category=='security' else 'normal')).fetchone()
        rid=int(row[0])
        conn.execute("INSERT INTO auth.audit_log(actor_user_id,action,target_type,target_id,client_hash,metadata) VALUES(%s,'feedback_submitted','feedback',%s,%s,'{}'::jsonb)",
                     (user['user_id'] if user else None,str(rid),ch))
        config=mail_settings(conn)
        conn.commit()
    identity=(f"{user['username']} · {user['email']} · utilisateur #{user['user_id']}" if user else 'Visiteur non connecté')
    body='''<!doctype html><html lang="fr"><body style="font:15px Arial,sans-serif;color:#17201c;background:#f2f4f1;padding:24px"><div style="max-width:680px;margin:auto;background:#fff;border:1px solid #d9ded8;border-radius:12px;padding:22px"><h1 style="font-size:21px;color:#2e5e4e">Nouvelle remontée BVMAC Market</h1><p><b>Référence :</b> #{rid}</p><p><b>Utilisateur :</b> {identity}</p><p><b>Type :</b> {category}</p><p><b>Sujet :</b> {subject}</p><p><b>Page / contexte :</b> {page}</p><hr style="border:0;border-top:1px solid #d9ded8"><p style="white-space:pre-wrap">{message}</p><p style="font-size:12px;color:#67726c">La remontée reste enregistrée dans l’espace administrateur, même si cette notification email échoue.</p></div></body></html>'''.format(
        rid=rid,identity=html.escape(identity),category=html.escape(category),subject=html.escape(subject),
        page=html.escape(page or 'Non précisé'),message=html.escape(message))
    email_sent=deliver_internal_notice(config,f'BVMAC Market — remontée #{rid}',body)
    if not email_sent:
        with db_connect() as conn:
            conn.execute("INSERT INTO auth.audit_log(actor_user_id,action,target_type,target_id,client_hash,metadata) VALUES(%s,'feedback_email_failed','feedback',%s,%s,'{}'::jsonb)",
                         (user['user_id'] if user else None,str(rid),ch))
            conn.commit()
    return {'ok':True,'report_id':rid,'email_sent':email_sent,'message':'Merci. La remontée a été enregistrée et transmise à l’administrateur.'}

@router.get('/mine')
def mine(request:Request):
    u=require_user(request)
    with db_connect() as conn:
        rows=conn.execute('SELECT report_id,category,subject,status,priority,created_at,updated_at FROM feedback.report WHERE user_id=%s ORDER BY created_at DESC LIMIT 100',(u['user_id'],)).fetchall()
    return {'reports':[{'report_id':r[0],'category':r[1],'subject':r[2],'status':r[3],'priority':r[4],'created_at':r[5],'updated_at':r[6]} for r in rows]}
