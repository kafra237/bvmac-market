"""Targeted administrator campaigns for free-form email, weekly summaries and web push."""

from __future__ import annotations

import html
import smtplib
import ssl
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from email_auth import settings as mail_settings
from push_service import push_user
from common import PUBLIC_ORIGIN, db_connect, decrypt_pii
from weekly_email import period_bounds, build_summary, _chart_pngs, render_email, _message


def create_campaign(conn, *, kind: str, user_ids: list[int], subject: str | None = None,
                    body: str | None = None, target_url: str | None = None,
                    requested_by: str | None = None) -> int:
    unique = sorted({int(x) for x in user_ids if int(x) > 0})
    if not unique:
        raise ValueError('Aucun destinataire sélectionné')
    row = conn.execute('''INSERT INTO admin.communication_campaign
        (kind,subject,body,target_url,requested_by,recipient_count)
        VALUES(%s,%s,%s,%s,%s,%s) RETURNING campaign_id''',
        (kind, subject, body, target_url, requested_by, len(unique))).fetchone()
    cid = int(row[0])
    # Psycopg expose executemany() sur les curseurs, pas comme contrat à dépendre
    # directement sur Connection. Un INSERT set-based est en plus atomique et plus
    # efficace pour une sélection de plusieurs comptes.
    conn.execute('''INSERT INTO admin.communication_recipient(campaign_id,user_id)
                    SELECT %s, unnest(%s::bigint[])
                    ON CONFLICT DO NOTHING''', (cid, unique))
    conn.commit()
    return cid


def _free_email(sender: str, recipient: str, pseudo: str, subject: str, body: str):
    safe_body = '<br>'.join(html.escape(body).splitlines())
    body_html = f'''<!doctype html><html lang="fr"><meta charset="utf-8"><body style="margin:0;background:#f2f4f1;color:#17201c;font:15px Arial,sans-serif"><table role="presentation" width="100%"><tr><td style="padding:24px 12px"><table role="presentation" width="100%" style="max-width:640px;margin:auto;background:#fff;border:1px solid #d9ded8;border-radius:14px;overflow:hidden"><tr><td style="background:#17251f;color:#fff;padding:20px 22px;font-size:25px;font-weight:800">Marché <span style="color:#c9a23f">BVMAC</span></td></tr><tr><td style="padding:22px"><p>Bonjour {html.escape(pseudo)},</p><div style="line-height:1.6">{safe_body}</div><p style="margin-top:24px"><a href="{PUBLIC_ORIGIN}/app" style="display:inline-block;padding:11px 16px;background:#2e5e4e;color:#fff;border-radius:7px;text-decoration:none;font-weight:700">Ouvrir BVMAC Market</a></p><p style="font-size:12px;color:#67726c">Communication envoyée par l’administrateur de BVMAC Market.</p></td></tr></table></td></tr></table></body></html>'''
    msg = MIMEMultipart('alternative')
    msg['From'] = sender; msg['To'] = recipient; msg['Subject'] = subject[:180]
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    msg.attach(MIMEText(body_html, 'html', 'utf-8'))
    return msg


def process_campaign(conn, campaign_id: int) -> dict:
    row = conn.execute('''SELECT kind,subject,body,target_url,status,started_at FROM admin.communication_campaign
                          WHERE campaign_id=%s FOR UPDATE''', (campaign_id,)).fetchone()
    if not row:
        raise RuntimeError('Campagne ciblée introuvable')
    kind, subject, body, target_url, status, started_at = row
    if status == 'completed':
        conn.commit(); return {'campaign_id': campaign_id, 'already_completed': True}
    if status == 'running' and started_at:
        s = started_at if started_at.tzinfo else started_at.replace(tzinfo=ZoneInfo('UTC'))
        if datetime.now(ZoneInfo('UTC')) - s.astimezone(ZoneInfo('UTC')) < timedelta(minutes=30):
            conn.commit(); return {'campaign_id': campaign_id, 'already_running': True}
    conn.execute("UPDATE admin.communication_campaign SET status='running',started_at=now(),completed_at=NULL,error=NULL WHERE campaign_id=%s", (campaign_id,))
    conn.commit()

    recipients = conn.execute('''SELECT r.user_id,u.username,u.email_cipher,u.email_verified_at,u.status,
        coalesce(p.weekly_market_summary,true),
        (SELECT count(*) FROM auth.push_subscription s WHERE s.user_id=u.user_id AND s.disabled_at IS NULL) push_count
        FROM admin.communication_recipient r JOIN auth.user_account u USING(user_id)
        LEFT JOIN auth.email_preference p ON p.user_id=u.user_id
        WHERE r.campaign_id=%s ORDER BY r.user_id''', (campaign_id,)).fetchall()

    sent = failed = skipped = 0
    config = mail_settings(conn) if kind in ('email','weekly_email') else None
    login_address = parseaddr((config or {}).get('sender') or '')[1] if config else ''
    summary = charts = None
    smtp = None
    try:
        if kind in ('email','weekly_email'):
            if not config or not config.get('password') or not login_address:
                raise RuntimeError('Envoi email non configuré')
            smtp = smtplib.SMTP('smtp.gmail.com', 587, timeout=20)
            smtp.ehlo(); smtp.starttls(context=ssl.create_default_context()); smtp.ehlo(); smtp.login(login_address, config['password'])
            if kind == 'weekly_email':
                start,end,prev_start,prev_end = period_bounds()
                summary = build_summary(conn,start,end,prev_start,prev_end)
                charts = _chart_pngs(summary)
        for uid,pseudo,email_cipher,email_verified,status_user,weekly_enabled,push_count in recipients:
            if status_user != 'active':
                skipped += 1; reason='Compte inactif'
                conn.execute("UPDATE admin.communication_recipient SET status='skipped',error=%s,attempted_at=now() WHERE campaign_id=%s AND user_id=%s", (reason,campaign_id,uid)); conn.commit(); continue
            try:
                if kind == 'push':
                    if not push_count:
                        skipped += 1
                        conn.execute("UPDATE admin.communication_recipient SET status='skipped',error='Aucun appareil push actif',attempted_at=now() WHERE campaign_id=%s AND user_id=%s", (campaign_id,uid)); conn.commit(); continue
                    n = push_user(conn,user_id=int(uid),category='communication',event_key=f'campaign:{campaign_id}:user:{uid}',title=(subject or 'BVMAC · Information').strip(),body=(body or '').strip(),target_url=target_url or '/app')
                    if n > 0:
                        sent += 1; conn.execute("UPDATE admin.communication_recipient SET status='sent',sent_count=%s,error=NULL,attempted_at=now(),sent_at=now() WHERE campaign_id=%s AND user_id=%s", (n,campaign_id,uid))
                    else:
                        failed += 1; conn.execute("UPDATE admin.communication_recipient SET status='failed',error='Push non livré',attempted_at=now() WHERE campaign_id=%s AND user_id=%s", (campaign_id,uid))
                else:
                    if email_verified is None:
                        skipped += 1; conn.execute("UPDATE admin.communication_recipient SET status='skipped',error='Email non vérifié',attempted_at=now() WHERE campaign_id=%s AND user_id=%s", (campaign_id,uid)); conn.commit(); continue
                    if kind == 'weekly_email' and not weekly_enabled:
                        skipped += 1; conn.execute("UPDATE admin.communication_recipient SET status='skipped',error='Synthèse hebdomadaire désactivée par l’utilisateur',attempted_at=now() WHERE campaign_id=%s AND user_id=%s", (campaign_id,uid)); conn.commit(); continue
                    email = decrypt_pii(email_cipher)
                    if not email:
                        skipped += 1; conn.execute("UPDATE admin.communication_recipient SET status='skipped',error='Adresse email indisponible',attempted_at=now() WHERE campaign_id=%s AND user_id=%s", (campaign_id,uid)); conn.commit(); continue
                    if kind == 'weekly_email':
                        subj,html_body,plain_body = render_email(summary,pseudo,set(charts or {}))
                        msg = _message(config['sender'],email,subj,html_body,plain_body,charts)
                    else:
                        msg = _free_email(config['sender'],email,pseudo,(subject or 'BVMAC · Information').strip(),(body or '').strip())
                    smtp.send_message(msg)
                    sent += 1; conn.execute("UPDATE admin.communication_recipient SET status='sent',sent_count=1,error=NULL,attempted_at=now(),sent_at=now() WHERE campaign_id=%s AND user_id=%s", (campaign_id,uid))
                conn.commit()
            except Exception as exc:
                failed += 1
                conn.execute("UPDATE admin.communication_recipient SET status='failed',error=%s,attempted_at=now() WHERE campaign_id=%s AND user_id=%s", (f'{type(exc).__name__}: {str(exc)[:450]}',campaign_id,uid)); conn.commit()
    except Exception as exc:
        conn.execute("UPDATE admin.communication_campaign SET status='failed',completed_at=now(),sent_count=%s,failed_count=%s,skipped_count=%s,error=%s WHERE campaign_id=%s", (sent,failed,skipped,str(exc)[:1000],campaign_id)); conn.commit(); raise
    finally:
        if smtp:
            try: smtp.quit()
            except Exception: pass
    conn.execute("UPDATE admin.communication_campaign SET status='completed',completed_at=now(),sent_count=%s,failed_count=%s,skipped_count=%s,error=NULL WHERE campaign_id=%s", (sent,failed,skipped,campaign_id)); conn.commit()
    return {'campaign_id':campaign_id,'sent':sent,'failed':failed,'skipped':skipped}


def process_campaign_by_id(campaign_id: int) -> dict:
    with db_connect() as conn:
        return process_campaign(conn,campaign_id)


def process_pending_campaigns(conn, limit: int = 5):
    ids=[int(r[0]) for r in conn.execute("SELECT campaign_id FROM admin.communication_campaign WHERE status='queued' OR (status='running' AND started_at<now()-interval '30 minutes') ORDER BY campaign_id LIMIT %s",(limit,)).fetchall()]
    out=[]
    for cid in ids:
        try: out.append(process_campaign(conn,cid))
        except Exception as exc: out.append({'campaign_id':cid,'error':str(exc)})
    return out
