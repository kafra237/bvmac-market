from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from argon2 import PasswordHasher, Type
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import BaseModel, Field

from push_service import broadcast_all, global_enabled
from weekly_email import create_run as create_weekly_email_run, process_run_by_id as process_weekly_email_run
from admin_communications import create_campaign as create_targeted_campaign, process_campaign_by_id as process_targeted_campaign

from common import (
    COOKIE_SECURE, DB_DSN, PUBLIC_ORIGIN, client_fingerprint, db_connect, decrypt_pii,
    encrypt_pii, mask_email, mask_phone, require_same_origin, safe_json, sha256_text, utcnow,
)

router=APIRouter(tags=['admin'])
STAT_PASSWORD_HASH=os.environ.get('BVMAC_STAT_PASSWORD_HASH','')
STAT_ENV_PATH=Path(os.environ.get('BVMAC_STAT_ENV','')) if os.environ.get('BVMAC_STAT_ENV') else None
STAT_HOURS=max(1,min(int(os.environ.get('BVMAC_STAT_SESSION_HOURS','12')),72))
STAT_COOKIE='__Host-bvmac_stat' if COOKIE_SECURE else 'bvmac_stat_session'
REPORT_FILE=Path(os.environ.get('BVMAC_DOWNLOAD_REPORT','/var/lib/bvmac/output/bvmac_download_report.json'))
MASTER_FILE=Path(os.environ.get('BVMAC_MASTER_FILE','/var/lib/bvmac/output/bvmac_master.xlsx'))
HOST=os.environ.get('BVMAC_ANALYTICS_HOST','localhost')
PIPELINE_MODE=os.environ.get('BVMAC_PIPELINE_MODE','external-master')
SERVICE_PREFIX=os.environ.get('BVMAC_SERVICE_PREFIX','bvmac')
BVMAC_ENV=os.environ.get('BVMAC_ENV','unknown')
APP_DIR=Path(os.environ.get('BVMAC_APP_DIR','/opt/bvmac'))
WEB_DIR=Path(os.environ.get('BVMAC_WEB_DIR','/var/www/bvmac'))
DATA_DIR=Path(os.environ.get('BVMAC_DATA_DIR','/var/lib/bvmac'))
API_PORT=int(os.environ.get('BVMAC_API_PORT','8765'))
PH=PasswordHasher(time_cost=2,memory_cost=19456,parallelism=1,hash_len=32,salt_len=16,type=Type.ID)

class AdminLogin(BaseModel): password:str=Field(min_length=1,max_length=256)
class StatusIn(BaseModel): status:str=Field(pattern=r'^(open|reviewing|resolved|rejected)$'); note:str|None=Field(default=None,max_length=2000)
class UserStatusIn(BaseModel): status:str=Field(pattern=r'^(active|disabled)$')

class NotificationSettingsIn(BaseModel):
    market_digest: bool = True
    alerts: bool = True
    screeners: bool = True
    opcvm: bool = True
    portfolio: bool = True
    feeds_avis: bool = True
    feeds_communique: bool = True
    platform_updates: bool = True
    admin_communications: bool = True
    weekly_digest: bool = True

class BroadcastIn(BaseModel):
    title: str = Field(min_length=1,max_length=120)
    body: str = Field(min_length=1,max_length=500)
    target_url: str = Field(default='/app',min_length=1,max_length=500)

class TargetedCommunicationIn(BaseModel):
    kind: str = Field(pattern=r'^(email|weekly_email|push)$')
    user_ids: list[int] = Field(min_length=1,max_length=500)
    subject: str | None = Field(default=None,max_length=180)
    body: str | None = Field(default=None,max_length=5000)
    target_url: str | None = Field(default='/app',max_length=500)

class TargetedWeeklyIn(BaseModel):
    user_ids: list[int] = Field(min_length=1,max_length=500)


def _current_stat_password_hash()->str:
    # Le hash est relu à chaque connexion afin qu'une réinitialisation via
    # set_stat_password.py soit prise en compte sans redémarrage obligatoire.
    if STAT_ENV_PATH and STAT_ENV_PATH.is_file():
        try:
            for line in STAT_ENV_PATH.read_text(encoding='utf-8').splitlines():
                if line.startswith('BVMAC_STAT_PASSWORD_HASH='):
                    return line.split('=',1)[1].strip()
        except OSError:
            pass
    return STAT_PASSWORD_HASH


def _verify_admin_password(password:str)->bool:
    current_hash=_current_stat_password_hash()
    if not current_hash: return False
    if current_hash.startswith('$argon2'):
        try: return bool(PH.verify(current_hash,password))
        except Exception: return False
    try:
        scheme,it,salt_hex,expected=current_hash.split('$',3)
        if scheme!='pbkdf2_sha256': return False
        actual=hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt_hex),int(it)).hex()
        return hmac.compare_digest(actual,expected)
    except Exception: return False


def _admin_required(request:Request)->str:
    token=request.cookies.get(STAT_COOKIE)
    tab=request.headers.get('x-admin-tab','')
    if len(tab)<40:raise HTTPException(status_code=401,detail='Onglet administrateur non validé')
    if not token:
        raise HTTPException(status_code=401,detail='Authentification administrateur requise')
    th=sha256_text(token)
    # Le User-Agent est conservé à des fins d'audit, mais n'est plus une condition
    # d'authentification. Certains navigateurs/proxy de confidentialité peuvent le
    # faire varier entre deux requêtes, ce qui invalidait des sessions légitimes.
    with db_connect() as conn:
        row=conn.execute('SELECT expires_at,tab_hash,last_seen_at FROM admin.session WHERE token_hash=%s',(th,)).fetchone()
        if row and row[1] and not hmac.compare_digest(row[1],sha256_text(tab)):
            raise HTTPException(status_code=401,detail='Cet onglet doit être validé depuis le serveur')
        if not row or row[0]<=utcnow() or not row[1] or row[2]<utcnow()-timedelta(seconds=90):
            conn.execute('DELETE FROM admin.session WHERE token_hash=%s',(th,)); conn.commit()
            raise HTTPException(status_code=401,detail='Session administrateur expirée')
        conn.execute('UPDATE admin.session SET last_seen_at=now() WHERE token_hash=%s',(th,)); conn.commit()
    return th


def _systemd_unit(unit:str)->dict[str,Any]:
    """Lecture seule de l'état systemd. Le nom vient uniquement de la configuration serveur."""
    try:
        cp=subprocess.run(
            ['systemctl','show',unit,'--no-pager','--property=LoadState,ActiveState,SubState,Result,MainPID,MemoryCurrent,CPUUsageNSec,ActiveEnterTimestamp'],
            capture_output=True,text=True,timeout=2,check=False,
        )
        values={}
        for line in cp.stdout.splitlines():
            if '=' in line:
                k,v=line.split('=',1); values[k]=v
        return {'unit':unit,'load':values.get('LoadState','unknown'),'active':values.get('ActiveState','unknown'),
                'sub':values.get('SubState','unknown'),'result':values.get('Result') or None,
                'pid':int(values.get('MainPID') or 0),'memory_bytes':int(values.get('MemoryCurrent') or 0) if (values.get('MemoryCurrent') or '').isdigit() else None,
                'cpu_ns':int(values.get('CPUUsageNSec') or 0) if (values.get('CPUUsageNSec') or '').isdigit() else None,
                'since':values.get('ActiveEnterTimestamp') or None}
    except Exception as exc:
        return {'unit':unit,'load':'unknown','active':'unknown','sub':'unknown','error':type(exc).__name__}


def _memory_snapshot()->dict[str,Any]:
    vals={}
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if ':' in line:
                k,v=line.split(':',1); vals[k]=int(v.strip().split()[0])*1024
    except Exception:
        pass
    total=vals.get('MemTotal',0); avail=vals.get('MemAvailable',0)
    return {'total_bytes':total or None,'available_bytes':avail or None,'used_bytes':(total-avail) if total and avail else None,
            'used_pct':round((total-avail)/total*100,1) if total and avail else None,
            'swap_total_bytes':vals.get('SwapTotal') or None,'swap_free_bytes':vals.get('SwapFree') or None}


def _proc_rss()->int|None:
    try:
        for line in Path('/proc/self/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1])*1024
    except Exception:
        return None
    return None

@router.post('/api/stat/login')
def stat_login(data:AdminLogin,request:Request,response:Response):
    raise HTTPException(status_code=403,detail='La validation par code serveur est obligatoire.')

@router.post('/api/stat/logout')
def stat_logout(request:Request,response:Response):
    th=_admin_required(request)
    with db_connect() as conn: conn.execute('DELETE FROM admin.session WHERE token_hash=%s',(th,)); conn.commit()
    response.delete_cookie(STAT_COOKIE,path='/',secure=COOKIE_SECURE,httponly=True,samesite='strict')
    return {'ok':True}

@router.get('/api/stat/me')
def stat_me(request:Request): _admin_required(request); return {'ok':True}


def _cutoff(days:int):
    return None if days<=0 else utcnow()-timedelta(days=min(days,3650))

@router.get('/api/stat/dashboard')
def stat_dashboard(request:Request,days:int=30):
    _admin_required(request); cutoff=_cutoff(days)
    with db_connect() as conn:
        params=[HOST]; where="host=%s AND is_bot=false"
        if cutoff: where+=' AND occurred_at>=%s'; params.append(cutoff)
        overview=conn.execute(f'''SELECT count(*),count(DISTINCT visitor_hash),
            count(*) FILTER (WHERE left(path,5) <> '/api/'),
            avg(request_time_ms) FROM webstats.request_event WHERE {where}''',params).fetchone()
        # visiteurs récurrents = au moins deux jours actifs dans la fenêtre
        p2=[HOST]; w2='host=%s'
        if cutoff: w2+=' AND day>=%s'; p2.append(cutoff.date())
        recurrent=conn.execute(f'''SELECT count(*) FROM (SELECT visitor_hash FROM webstats.visitor_day WHERE {w2} GROUP BY visitor_hash HAVING count(*)>=2) q''',p2).fetchone()[0]
        timeline=conn.execute(f'''SELECT day,count(DISTINCT visitor_hash),sum(request_count) FROM webstats.visitor_day WHERE {w2} GROUP BY day ORDER BY day''',p2).fetchall()
        pages=conn.execute(f'''SELECT path,count(*) c,count(DISTINCT visitor_hash) v FROM webstats.request_event WHERE {where} AND left(path,5) <> '/api/' GROUP BY path ORDER BY c DESC LIMIT 30''',params).fetchall()
        agents=conn.execute(f'''SELECT coalesce(user_agent_family,'Autre'),count(*) FROM webstats.request_event WHERE {where} GROUP BY 1 ORDER BY 2 DESC LIMIT 12''',params).fetchall()
        freq=conn.execute(f'''SELECT active_days,count(*) FROM (SELECT visitor_hash,count(*) active_days FROM webstats.visitor_day WHERE {w2} GROUP BY visitor_hash) x GROUP BY active_days ORDER BY active_days''',p2).fetchall()
        recent=conn.execute(f'''SELECT visitor_hash,min(occurred_at),max(occurred_at),count(*),max(user_agent_family) FROM webstats.request_event WHERE {where} GROUP BY visitor_hash ORDER BY max(occurred_at) DESC LIMIT 100''',params).fetchall()
        # Rétention simple : cohorte de première journée et retour J+7/J+30 dans les données disponibles.
        retention=conn.execute('''WITH firsts AS (SELECT visitor_hash,min(day) first_day FROM webstats.visitor_day WHERE host=%s GROUP BY visitor_hash),
            x AS (SELECT f.first_day, f.visitor_hash,
              bool_or(v.day BETWEEN f.first_day+6 AND f.first_day+8) r7,
              bool_or(v.day BETWEEN f.first_day+29 AND f.first_day+31) r30
              FROM firsts f LEFT JOIN webstats.visitor_day v ON v.host=%s AND v.visitor_hash=f.visitor_hash GROUP BY f.first_day,f.visitor_hash)
            SELECT first_day,count(*),round(100.0*sum(r7::int)/count(*),1),round(100.0*sum(r30::int)/count(*),1)
            FROM x GROUP BY first_day ORDER BY first_day DESC LIMIT 12''',(HOST,HOST)).fetchall()
        bparams=[]; bwhere='true'
        if cutoff: bwhere='occurred_at>=%s'; bparams=[cutoff]
        browser=conn.execute(f'''SELECT count(*),count(DISTINCT visitor_id),count(DISTINCT session_id),
            coalesce(sum(CASE WHEN event_name='engagement' AND properties->>'seconds' ~ '^[0-9]{1,6}$'
                THEN least((properties->>'seconds')::int,86400) ELSE 0 END),0),
            count(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL)
            FROM webstats.browser_event WHERE {bwhere}''',bparams).fetchone()
        browser_events=conn.execute(f'''SELECT event_name,count(*) FROM webstats.browser_event WHERE {bwhere}
            GROUP BY event_name ORDER BY count(*) DESC LIMIT 20''',bparams).fetchall()
        browser_users=conn.execute(f'''SELECT u.user_id,u.username,min(e.occurred_at),max(e.occurred_at),count(*),count(DISTINCT e.session_id)
            FROM webstats.browser_event e JOIN auth.user_account u ON u.user_id=e.user_id
            WHERE {bwhere.replace('occurred_at','e.occurred_at')} GROUP BY u.user_id,u.username ORDER BY max(e.occurred_at) DESC LIMIT 100''',bparams).fetchall()
        browser_returning=conn.execute(f'''SELECT count(*) FROM (
            SELECT visitor_id FROM webstats.browser_event WHERE {bwhere}
            GROUP BY visitor_id HAVING count(DISTINCT occurred_at::date)>=2) q''',bparams).fetchone()[0]
        browser_entities=conn.execute(f'''SELECT entity_type,entity_id,count(*) FROM webstats.browser_event
            WHERE {bwhere} AND entity_id IS NOT NULL GROUP BY entity_type,entity_id ORDER BY count(*) DESC LIMIT 20''',bparams).fetchall()
        browser_searches=conn.execute(f'''SELECT properties->>'query',count(*) FROM webstats.browser_event
            WHERE {bwhere} AND event_name='search' AND coalesce(properties->>'query','')<>''
            GROUP BY 1 ORDER BY 2 DESC LIMIT 20''',bparams).fetchall()
    visitors=int(overview[1] or 0); returning=int(recurrent or 0)
    return {'mode':'hybrid_first_party','host':HOST,'overview':{'requests':int(overview[0] or 0),'visitors':visitors,'page_views':int(overview[2] or 0),
            'returning_visitors':returning,'returning_rate':round(returning/visitors*100,1) if visitors else 0,'avg_request_ms':round(float(overview[3] or 0),1)},
            'timeline':[{'day':r[0],'visitors':r[1],'requests':r[2]} for r in timeline],
            'pages':[{'key':r[0],'count':r[1],'visitors':r[2]} for r in pages],
            'agents':[{'key':r[0],'count':r[1]} for r in agents],
            'frequency':[{'active_days':r[0],'visitors':r[1]} for r in freq],
            'visitors_recent':[{'visitor_hash':r[0][:12],'first':r[1],'last':r[2],'requests':r[3],'agent':r[4]} for r in recent],
            'retention':[{'cohort':r[0],'users':r[1],'d7_pct':float(r[2] or 0),'d30_pct':float(r[3] or 0)} for r in retention],
            'browser':{'events':int(browser[0] or 0),'visitors':int(browser[1] or 0),'sessions':int(browser[2] or 0),
                       'engagement_seconds':int(browser[3] or 0),'identified_users':int(browser[4] or 0),
                       'returning_visitors':int(browser_returning or 0),
                       'returning_rate':round((int(browser_returning or 0)/int(browser[1] or 1))*100,1) if browser[1] else 0,
                       'top_events':[{'key':r[0],'count':r[1]} for r in browser_events],
                       'top_entities':[{'type':r[0],'id':r[1],'count':r[2]} for r in browser_entities],
                       'top_searches':[{'key':r[0],'count':r[1]} for r in browser_searches],
                       'identified_activity':[{'user_id':r[0],'pseudo':r[1],'first':r[2],'last':r[3],'events':r[4],'sessions':r[5]} for r in browser_users]}}

@router.get('/api/stat/data-quality')
def data_quality(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        cur=conn.execute('SELECT import_id,source_sha256,source_mtime,imported_at,completed_at,row_count,status,error_message FROM market.import_batch ORDER BY import_id DESC LIMIT 1').fetchone()
        imports=conn.execute('SELECT import_id,status,completed_at,source_mtime,row_count,source_sha256,error_message FROM market.import_batch ORDER BY import_id DESC LIMIT 30').fetchall()
        counts=conn.execute("SELECT sheet_name,count(*) FROM market.current_excel_row GROUP BY sheet_name ORDER BY sheet_name").fetchall()
        quality=conn.execute("SELECT data FROM market.current_excel_row WHERE sheet_name='quality_report' ORDER BY row_number LIMIT 1000").fetchall()
    q=[dict(r[0]) for r in quality]; sev={}
    for x in q: sev[str(x.get('severity','INFO')).upper()]=sev.get(str(x.get('severity','INFO')).upper(),0)+1
    report={}
    if REPORT_FILE.is_file():
        try: report=json.loads(REPORT_FILE.read_text(encoding='utf-8'))
        except Exception: report={'error':'rapport illisible'}
    stat=MASTER_FILE.stat() if MASTER_FILE.is_file() else None
    return {'master':{'path':str(MASTER_FILE),'exists':MASTER_FILE.is_file(),'size_bytes':stat.st_size if stat else None,'mtime':datetime.fromtimestamp(stat.st_mtime,timezone.utc) if stat else None},
            'current_import':{'import_id':cur[0],'sha256':cur[1],'source_mtime':cur[2],'imported_at':cur[3],'completed_at':cur[4],'rows':cur[5],'status':cur[6],'error':cur[7]} if cur else None,
            'imports':[{'import_id':r[0],'status':r[1],'completed_at':r[2],'source_mtime':r[3],'rows':r[4],'sha256':r[5],'error':r[6]} for r in imports],
            'sheet_counts':[{'sheet':r[0],'rows':r[1]} for r in counts],'quality':q,'severity_counts':sev,'download_report':report}

@router.get('/api/stat/import-changes')
def import_changes(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        ids=[int(r[0]) for r in conn.execute("SELECT import_id FROM market.import_batch WHERE status='completed' ORDER BY completed_at DESC,import_id DESC LIMIT 2").fetchall()]
        if not ids:
            return {'current':None,'previous':None,'sheets':[]}
        cur=ids[0]; prev=ids[1] if len(ids)>1 else None
        cur_counts={r[0]:int(r[1]) for r in conn.execute("SELECT sheet_name,count(*) FROM market.excel_row WHERE import_id=%s GROUP BY sheet_name",(cur,)).fetchall()}
        prev_counts={r[0]:int(r[1]) for r in conn.execute("SELECT sheet_name,count(*) FROM market.excel_row WHERE import_id=%s GROUP BY sheet_name",(prev,)).fetchall()} if prev else {}
        sheets=sorted(set(cur_counts)|set(prev_counts)); diffs=[]
        for sheet in sheets:
            changed=None
            if prev and sheet in {'fact_prices','fact_market_cap','fact_index','fact_opcvm_nav','fact_financials','quality_report'}:
                a={str(r[0]) for r in conn.execute("SELECT md5(data::text) FROM market.excel_row WHERE import_id=%s AND sheet_name=%s",(cur,sheet)).fetchall()}
                b={str(r[0]) for r in conn.execute("SELECT md5(data::text) FROM market.excel_row WHERE import_id=%s AND sheet_name=%s",(prev,sheet)).fetchall()}
                changed=len(a.symmetric_difference(b))
            diffs.append({'sheet':sheet,'current_rows':cur_counts.get(sheet,0),'previous_rows':prev_counts.get(sheet,0),
                          'row_delta':cur_counts.get(sheet,0)-prev_counts.get(sheet,0),'changed_fingerprints':changed})
        meta=conn.execute("SELECT import_id,source_sha256,completed_at,row_count FROM market.import_batch WHERE import_id=ANY(%s) ORDER BY import_id DESC",(ids,)).fetchall()
    def m(row):
        return {'import_id':row[0],'source_sha256':row[1],'completed_at':row[2],'row_count':row[3]} if row else None
    return {'current':m(meta[0]) if meta else None,'previous':m(meta[1]) if len(meta)>1 else None,'sheets':diffs}

@router.get('/api/stat/users')
def users(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        rows=conn.execute('''SELECT u.user_id,u.username,u.email_cipher,u.phone_cipher,u.country,u.role,u.status,u.created_at,u.last_login_at,
          g.failures_in_cycle,g.lock_strikes,g.locked_until,
          coalesce(a.active_days_30,0),coalesce(a.sessions_30,0),a.last_activity,u.profile,u.email_verified_at
          FROM auth.user_account u
          LEFT JOIN auth.login_guard g USING(user_id)
          LEFT JOIN LATERAL (
            SELECT count(DISTINCT e.occurred_at::date) active_days_30,
                   count(DISTINCT e.session_id) sessions_30,
                   max(e.occurred_at) last_activity
            FROM webstats.browser_event e WHERE e.user_id=u.user_id AND e.occurred_at>=now()-interval '30 days'
          ) a ON true
          ORDER BY u.created_at DESC LIMIT 500''').fetchall()
    return {'users':[{'user_id':r[0],'pseudo':r[1],'email':mask_email(decrypt_pii(r[2])),'phone':mask_phone(decrypt_pii(r[3])),'country':r[4],'role':r[5],'status':r[6],
                      'created_at':r[7],'last_login_at':r[8],'failures':r[9] or 0,'lock_strikes':r[10] or 0,'locked_until':r[11],
                      'active_days_30':r[12] or 0,'sessions_30':r[13] or 0,'last_activity':r[14],'profile':r[15],'email_verified_at':r[16]} for r in rows]}

@router.post('/api/stat/users/{user_id}/unlock')
def unlock_user(user_id:int,request:Request):
    _admin_required(request); require_same_origin(request)
    with db_connect() as conn:
        conn.execute('UPDATE auth.login_guard SET failures_in_cycle=0,lock_strikes=0,strike_window_started_at=NULL,locked_until=NULL,updated_at=now() WHERE user_id=%s',(user_id,)); conn.commit()
    return {'ok':True}

@router.post('/api/stat/users/{user_id}/status')
def user_status(user_id:int,data:UserStatusIn,request:Request):
    _admin_required(request); require_same_origin(request)
    with db_connect() as conn:
        r=conn.execute('UPDATE auth.user_account SET status=%s,updated_at=now() WHERE user_id=%s RETURNING user_id',(data.status,user_id)).fetchone()
        if data.status=='disabled': conn.execute('UPDATE auth.user_session SET revoked_at=now() WHERE user_id=%s',(user_id,))
        conn.commit()
    if not r: raise HTTPException(status_code=404,detail='Utilisateur introuvable')
    return {'ok':True}

@router.get('/api/stat/password-help')
def help_requests(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        rows=conn.execute('''SELECT request_id,username_supplied,email_cipher,phone_cipher,matched_user_id,status,admin_note,created_at,updated_at
                             FROM auth.password_help_request ORDER BY created_at DESC LIMIT 300''').fetchall()
    return {'requests':[{'request_id':r[0],'pseudo':r[1],'email':decrypt_pii(r[2]),'phone':decrypt_pii(r[3]),'matched_user_id':r[4],'status':r[5],'admin_note':r[6],'created_at':r[7],'updated_at':r[8]} for r in rows]}

@router.post('/api/stat/password-help/{request_id}/status')
def help_status(request_id:int,data:StatusIn,request:Request):
    _admin_required(request); require_same_origin(request)
    with db_connect() as conn:
        r=conn.execute('UPDATE auth.password_help_request SET status=%s,admin_note=%s,updated_at=now() WHERE request_id=%s RETURNING request_id',(data.status,data.note,request_id)).fetchone(); conn.commit()
    if not r: raise HTTPException(status_code=404,detail='Demande introuvable')
    return {'ok':True}

class IdentityCheckIn(BaseModel):
    verified: bool
    note: str = Field(min_length=12, max_length=500)

@router.post('/api/stat/password-help/{request_id}/temporary-password')
def temporary_password(request_id:int,data:IdentityCheckIn,request:Request):
    _admin_required(request)
    raise HTTPException(403,"Utilise la récupération par email validé.")
    if not data.verified or len(data.note.strip()) < 12:
        raise HTTPException(status_code=400,detail="Vérification indépendante de l’identité requise")
    _admin_required(request); require_same_origin(request)
    # Import local pour éviter une dépendance circulaire au démarrage.
    from auth_module import _hash_password
    temporary=secrets.token_urlsafe(16)+'!7'
    with db_connect() as conn:
        r=conn.execute('SELECT matched_user_id FROM auth.password_help_request WHERE request_id=%s FOR UPDATE',(request_id,)).fetchone()
        if not r or not r[0]: raise HTTPException(status_code=400,detail='La demande ne correspond pas à un compte vérifié')
        uid=int(r[0]); conn.execute('UPDATE auth.user_account SET password_hash=%s,must_change_password=true,updated_at=now() WHERE user_id=%s',(_hash_password(temporary),uid))
        conn.execute('UPDATE auth.user_session SET revoked_at=now() WHERE user_id=%s',(uid,))
        conn.execute('UPDATE auth.login_guard SET failures_in_cycle=0,lock_strikes=0,strike_window_started_at=NULL,locked_until=NULL WHERE user_id=%s',(uid,))
        conn.execute("UPDATE auth.password_help_request SET status='resolved',admin_note=%s,updated_at=now() WHERE request_id=%s",(data.note.strip(),request_id)); conn.commit()
    return {'ok':True,'temporary_password':temporary,'warning':'Affiché une seule fois. Transmets-le manuellement à l’utilisateur et demande-lui de le changer.'}

@router.get('/api/stat/feedback')
def admin_feedback(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        rows=conn.execute('''SELECT f.report_id,f.user_id,u.username,f.category,f.subject,f.message,f.page,f.status,f.priority,f.admin_note,f.created_at,f.updated_at
                             FROM feedback.report f LEFT JOIN auth.user_account u ON u.user_id=f.user_id ORDER BY f.created_at DESC LIMIT 500''').fetchall()
    return {'reports':[{'report_id':r[0],'user_id':r[1],'pseudo':r[2],'category':r[3],'subject':r[4],'message':r[5],'page':r[6],'status':r[7],'priority':r[8],'admin_note':r[9],'created_at':r[10],'updated_at':r[11]} for r in rows]}

@router.post('/api/stat/feedback/{report_id}/status')
def admin_feedback_status(report_id:int,data:StatusIn,request:Request):
    _admin_required(request); require_same_origin(request)
    with db_connect() as conn:
        r=conn.execute('UPDATE feedback.report SET status=%s,admin_note=%s,updated_at=now() WHERE report_id=%s RETURNING report_id',(data.status,data.note,report_id)).fetchone(); conn.commit()
    if not r: raise HTTPException(status_code=404,detail='Remontée introuvable')
    return {'ok':True}


@router.get('/api/stat/push')
def admin_push(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        subs=conn.execute("""SELECT count(*),count(*) FILTER(WHERE disabled_at IS NULL),count(DISTINCT user_id) FILTER(WHERE disabled_at IS NULL) FROM auth.push_subscription""").fetchone()
        summary=conn.execute("""SELECT category,status,count(*) FROM auth.notification_delivery WHERE attempted_at>=now()-interval '24 hours' GROUP BY category,status ORDER BY category,status""").fetchall()
        rows=conn.execute("""SELECT d.delivery_id,d.user_id,u.username,d.category,d.title,d.body,d.event_key,d.status,d.attempted_at,d.error
          FROM auth.notification_delivery d JOIN auth.user_account u ON u.user_id=d.user_id
          ORDER BY d.attempted_at DESC LIMIT 300""").fetchall()
        settings=conn.execute("""SELECT market_digest,alerts,screeners,opcvm,portfolio,feeds_avis,feeds_communique,
          platform_updates,admin_communications,weekly_digest,updated_at FROM admin.notification_settings WHERE singleton=true""").fetchone()
        broadcasts=conn.execute("""SELECT broadcast_id,kind,title,body,target_url,created_by,created_at,sent_at,sent_count
                                   FROM admin.notification_broadcast ORDER BY broadcast_id DESC LIMIT 100""").fetchall()
        feeds=conn.execute("""SELECT feed,last_check,last_success,items_received,new_items_total,errors_total,last_error
                              FROM admin.feed_monitor ORDER BY feed""").fetchall()
    keys=['market_digest','alerts','screeners','opcvm','portfolio','feeds_avis','feeds_communique','platform_updates','admin_communications','weekly_digest']
    return {'subscriptions':{'total':subs[0],'active':subs[1],'users':subs[2]},
            'settings':({**dict(zip(keys,settings[:10])),'updated_at':settings[10]} if settings else {}),
            'last24h':[{'category':r[0],'status':r[1],'count':r[2]} for r in summary],
            'deliveries':[{'id':r[0],'user_id':r[1],'pseudo':r[2],'category':r[3],'title':r[4],'body':r[5],'event_key':r[6],'status':r[7],'at':r[8],'error':r[9]} for r in rows],
            'broadcasts':[{'id':r[0],'kind':r[1],'title':r[2],'body':r[3],'target_url':r[4],'created_by':r[5],'created_at':r[6],'sent_at':r[7],'sent_count':r[8]} for r in broadcasts],
            'feeds':[{'feed':r[0],'last_check':r[1],'last_success':r[2],'items_received':r[3],'new_items_total':r[4],'errors_total':r[5],'last_error':r[6]} for r in feeds]}


@router.post('/api/stat/push/settings')
def admin_push_settings(data:NotificationSettingsIn,request:Request):
    _admin_required(request); require_same_origin(request)
    d=data.model_dump()
    with db_connect() as conn:
        conn.execute("""INSERT INTO admin.notification_settings(singleton,market_digest,alerts,screeners,opcvm,portfolio,feeds_avis,feeds_communique,platform_updates,admin_communications,weekly_digest,updated_at)
          VALUES(true,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
          ON CONFLICT(singleton) DO UPDATE SET market_digest=EXCLUDED.market_digest,alerts=EXCLUDED.alerts,screeners=EXCLUDED.screeners,
          opcvm=EXCLUDED.opcvm,portfolio=EXCLUDED.portfolio,feeds_avis=EXCLUDED.feeds_avis,feeds_communique=EXCLUDED.feeds_communique,
          platform_updates=EXCLUDED.platform_updates,admin_communications=EXCLUDED.admin_communications,weekly_digest=EXCLUDED.weekly_digest,updated_at=now()""",
          tuple(d[k] for k in ('market_digest','alerts','screeners','opcvm','portfolio','feeds_avis','feeds_communique','platform_updates','admin_communications','weekly_digest')))
        conn.commit()
    return {'ok':True,'settings':d}


@router.post('/api/stat/push/broadcast')
def admin_push_broadcast(data:BroadcastIn,request:Request):
    _admin_required(request); require_same_origin(request)
    target=data.target_url.strip()
    if not target.startswith('/') or target.startswith('//'):
        raise HTTPException(status_code=400,detail='La destination doit être une URL interne commençant par /.')
    with db_connect() as conn:
        if not global_enabled(conn,'communication'):
            raise HTTPException(status_code=409,detail='Les communications administrateur sont désactivées dans les réglages Push.')
        row=conn.execute("""INSERT INTO admin.notification_broadcast(kind,title,body,target_url,created_by)
                            VALUES('admin',%s,%s,%s,'stat') RETURNING broadcast_id""",(data.title.strip(),data.body.strip(),target)).fetchone()
        bid=int(row[0]); conn.commit()
        sent=broadcast_all(conn,category='communication',event_key=f'broadcast:{bid}',title=data.title.strip(),body=data.body.strip(),target_url=target)
        conn.execute('UPDATE admin.notification_broadcast SET sent_at=now(),sent_count=%s WHERE broadcast_id=%s',(sent,bid)); conn.commit()
    return {'ok':True,'broadcast_id':bid,'sent_count':sent}


@router.get('/api/stat/weekly-email')
def admin_weekly_email(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        prefs=conn.execute("""SELECT count(*) FILTER(WHERE u.status='active' AND u.email_verified_at IS NOT NULL),
          count(*) FILTER(WHERE u.status='active' AND u.email_verified_at IS NOT NULL AND coalesce(p.weekly_market_summary,true)=true),
          count(*) FILTER(WHERE u.status='active' AND u.email_verified_at IS NOT NULL AND coalesce(p.weekly_market_summary,true)=false)
          FROM auth.user_account u LEFT JOIN auth.email_preference p ON p.user_id=u.user_id""").fetchone()
        runs=conn.execute("""SELECT run_id,period_start,period_end,trigger,requested_by,subject,status,recipient_count,sent_count,failed_count,created_at,started_at,completed_at,error
          FROM admin.weekly_email_run ORDER BY run_id DESC LIMIT 50""").fetchall()
    return {'preferences':{'eligible':prefs[0],'enabled':prefs[1],'disabled':prefs[2]},
            'schedule':{'weekday':'monday','time':'09:00','timezone':'Africa/Douala'},
            'runs':[{'run_id':r[0],'period_start':r[1],'period_end':r[2],'trigger':r[3],'requested_by':r[4],
                     'subject':r[5],'status':r[6],'recipients':r[7],'sent':r[8],'failed':r[9],
                     'created_at':r[10],'started_at':r[11],'completed_at':r[12],'error':r[13]} for r in runs]}


@router.post('/api/stat/weekly-email/send')
def admin_weekly_email_send(request:Request,background_tasks:BackgroundTasks):
    token_hash=_admin_required(request); require_same_origin(request)
    with db_connect() as conn:
        name=conn.execute('SELECT admin_name FROM admin.session WHERE token_hash=%s',(token_hash,)).fetchone()
        run_id=create_weekly_email_run(conn,trigger='manual',requested_by=(name[0] if name and name[0] else 'stat'))
    background_tasks.add_task(process_weekly_email_run,run_id)
    return {'ok':True,'run_id':run_id,'status':'queued'}


@router.get('/api/stat/communications/recipients')
def targeted_recipients(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        rows=conn.execute('''SELECT u.user_id,u.username,u.status,u.email_verified_at,
          coalesce(p.weekly_market_summary,true),
          count(s.subscription_id) FILTER(WHERE s.disabled_at IS NULL) AS push_devices
          FROM auth.user_account u
          LEFT JOIN auth.email_preference p ON p.user_id=u.user_id
          LEFT JOIN auth.push_subscription s ON s.user_id=u.user_id
          GROUP BY u.user_id,u.username,u.status,u.email_verified_at,p.weekly_market_summary
          ORDER BY lower(u.username),u.user_id''').fetchall()
    return {'users':[{'user_id':r[0],'pseudo':r[1],'status':r[2],'email_verified':bool(r[3]),
                      'weekly_enabled':bool(r[4]),'push_devices':int(r[5] or 0)} for r in rows]}


@router.get('/api/stat/communications/campaigns')
def targeted_campaigns(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        rows=conn.execute('''SELECT campaign_id,kind,subject,body,target_url,requested_by,status,
          recipient_count,sent_count,failed_count,skipped_count,error,created_at,started_at,completed_at
          FROM admin.communication_campaign ORDER BY campaign_id DESC LIMIT 100''').fetchall()
        deliveries=conn.execute('''SELECT r.campaign_id,r.user_id,u.username,r.status,r.sent_count,r.error,r.attempted_at,r.sent_at
          FROM admin.communication_recipient r JOIN auth.user_account u USING(user_id)
          WHERE r.campaign_id IN (SELECT campaign_id FROM admin.communication_campaign ORDER BY campaign_id DESC LIMIT 20)
          ORDER BY r.campaign_id DESC,lower(u.username),r.user_id LIMIT 500''').fetchall()
    return {'campaigns':[{'campaign_id':r[0],'kind':r[1],'subject':r[2],'body':r[3],'target_url':r[4],
      'requested_by':r[5],'status':r[6],'recipients':r[7],'sent':r[8],'failed':r[9],'skipped':r[10],
      'error':r[11],'created_at':r[12],'started_at':r[13],'completed_at':r[14]} for r in rows],
      'deliveries':[{'campaign_id':r[0],'user_id':r[1],'pseudo':r[2],'status':r[3],'sent_count':r[4],
        'error':r[5],'attempted_at':r[6],'sent_at':r[7]} for r in deliveries]}


def _validated_target_user_ids(conn, values: list[int]) -> list[int]:
    ids=sorted({int(x) for x in values if int(x)>0})
    if not ids:
        raise HTTPException(400,'Aucun compte sélectionné.')
    # Cast explicite bigint[] : évite toute ambiguïté d'adaptation PostgreSQL
    # selon la taille des identifiants Python/psycopg.
    existing={int(r[0]) for r in conn.execute(
        'SELECT user_id FROM auth.user_account WHERE user_id=ANY(%s::bigint[])',
        (ids,),
    ).fetchall()}
    if existing!=set(ids):
        raise HTTPException(400,'Un ou plusieurs comptes sélectionnés n’existent plus.')
    return ids


def _requested_admin_name(conn, token_hash: str) -> str:
    row=conn.execute('SELECT admin_name FROM admin.session WHERE token_hash=%s',(token_hash,)).fetchone()
    return row[0] if row and row[0] else 'stat'


@router.post('/api/stat/communications/weekly/send')
def targeted_weekly_send(data:TargetedWeeklyIn,request:Request,background_tasks:BackgroundTasks):
    token_hash=_admin_required(request); require_same_origin(request)
    with db_connect() as conn:
        ids=_validated_target_user_ids(conn,data.user_ids)
        try:
            cid=create_targeted_campaign(
                conn,kind='weekly_email',user_ids=ids,subject=None,body=None,
                target_url='/app?view=radar',requested_by=_requested_admin_name(conn,token_hash),
            )
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from None
    background_tasks.add_task(process_targeted_campaign,cid)
    return {'ok':True,'campaign_id':cid,'status':'queued','recipient_count':len(ids)}


@router.post('/api/stat/communications/send')
def targeted_send(data:TargetedCommunicationIn,request:Request,background_tasks:BackgroundTasks):
    token_hash=_admin_required(request); require_same_origin(request)
    ids=sorted({int(x) for x in data.user_ids if int(x)>0})
    if not ids: raise HTTPException(400,'Aucun compte sélectionné.')
    subject=(data.subject or '').strip(); body=(data.body or '').strip(); target=(data.target_url or '/app').strip()
    if data.kind in ('email','push') and not subject: raise HTTPException(400,'Un objet/titre est requis.')
    if data.kind in ('email','push') and not body: raise HTTPException(400,'Un message est requis.')
    if data.kind=='push' and (not target.startswith('/') or target.startswith('//')):
        raise HTTPException(400,'La destination Push doit être une URL interne commençant par /.')
    with db_connect() as conn:
        if data.kind=='push' and not global_enabled(conn,'communication'):
            raise HTTPException(409,'Les communications administrateur Push sont désactivées dans les réglages.')
        ids=_validated_target_user_ids(conn,ids)
        try:
            cid=create_targeted_campaign(conn,kind=data.kind,user_ids=ids,subject=subject or None,body=body or None,target_url=target or None,requested_by=_requested_admin_name(conn,token_hash))
        except ValueError as exc: raise HTTPException(400,str(exc)) from None
    background_tasks.add_task(process_targeted_campaign,cid)
    return {'ok':True,'campaign_id':cid,'status':'queued','recipient_count':len(ids)}


@router.get('/api/stat/control-center')
def control_center(request:Request):
    _admin_required(request)
    now=utcnow(); since24=now-timedelta(hours=24)
    units=[
        'nginx.service','postgresql.service',f'{SERVICE_PREFIX}-api.service',f'{SERVICE_PREFIX}-db-import.path',
        f'{SERVICE_PREFIX}-db-import.service',f'{SERVICE_PREFIX}-webstats.timer',f'{SERVICE_PREFIX}-webstats.service',
        f'{SERVICE_PREFIX}-alerts.timer',f'{SERVICE_PREFIX}-alerts.service',
        f'{SERVICE_PREFIX}-feeds.timer',f'{SERVICE_PREFIX}-feeds.service',f'{SERVICE_PREFIX}-notifications.timer',f'{SERVICE_PREFIX}-notifications.service',
        f'{SERVICE_PREFIX}-weekly-digest.timer',f'{SERVICE_PREFIX}-weekly-digest.service',f'{SERVICE_PREFIX}-weekly-email.timer',f'{SERVICE_PREFIX}-weekly-email.service',
        f'{SERVICE_PREFIX}-new-data-push.path',f'{SERVICE_PREFIX}-new-data-push.service',
    ]
    if PIPELINE_MODE=='managed':
        units += [f'{SERVICE_PREFIX}-pipeline.timer',f'{SERVICE_PREFIX}-pipeline.service',f'{SERVICE_PREFIX}-pipeline-watch.timer',f'{SERVICE_PREFIX}-pipeline-watch.service']
    services=[_systemd_unit(x) for x in units]
    try:
        du=shutil.disk_usage('/')
        disk={'total_bytes':du.total,'used_bytes':du.used,'free_bytes':du.free,'used_pct':round(du.used/du.total*100,1)}
    except Exception: disk={}
    try:
        data_du=shutil.disk_usage(DATA_DIR)
        data_disk={'path':str(DATA_DIR),'total_bytes':data_du.total,'used_bytes':data_du.used,'free_bytes':data_du.free,'used_pct':round(data_du.used/data_du.total*100,1)}
    except Exception: data_disk={'path':str(DATA_DIR)}
    try:
        uptime=float(Path('/proc/uptime').read_text().split()[0])
    except Exception: uptime=None
    try: load=list(os.getloadavg())
    except Exception: load=[]
    master={'path':str(MASTER_FILE),'exists':MASTER_FILE.is_file()}
    if MASTER_FILE.is_file():
        st=MASTER_FILE.stat(); master.update(size_bytes=st.st_size,mtime=datetime.fromtimestamp(st.st_mtime,tz=timezone.utc))
    with db_connect() as conn:
        db=conn.execute("SELECT pg_database_size(current_database()), (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()), current_database()").fetchone()
        imp=conn.execute("SELECT import_id,status,imported_at,completed_at,row_count,error_message FROM market.import_batch ORDER BY import_id DESC LIMIT 1").fetchone()
        web=conn.execute("SELECT run_id,started_at,completed_at,raw_lines,inserted_lines,status,error_message FROM webstats.import_run ORDER BY run_id DESC LIMIT 1").fetchone()
        users=conn.execute("SELECT count(*),count(*) FILTER(WHERE status='active'),count(*) FILTER(WHERE status='disabled') FROM auth.user_account").fetchone()
        locks=conn.execute("SELECT count(*) FROM auth.login_guard WHERE locked_until>now()").fetchone()[0]
        browser=conn.execute("SELECT count(*),count(DISTINCT visitor_id),count(DISTINCT session_id),count(DISTINCT user_id) FILTER(WHERE user_id IS NOT NULL) FROM webstats.browser_event WHERE occurred_at>=%s",(since24,)).fetchone()
        http=conn.execute("SELECT count(*),count(*) FILTER(WHERE status BETWEEN 400 AND 499),count(*) FILTER(WHERE status>=500),coalesce(avg(request_time_ms),0) FROM webstats.request_event WHERE host=%s AND occurred_at>=%s AND is_bot=false",(HOST,since24)).fetchone()
        feedback=conn.execute("SELECT count(*) FILTER(WHERE status='open'),count(*) FILTER(WHERE status='reviewing') FROM feedback.report").fetchone()
        quality=conn.execute("SELECT count(*) FROM market.current_excel_row WHERE sheet_name='quality_report' AND upper(coalesce(data->>'severity',''))='ERROR'").fetchone()[0]
    return {
        'generated_at':now,'environment':BVMAC_ENV,'host':HOST,'pipeline_mode':PIPELINE_MODE,'api_port':API_PORT,
        'server':{'cpu_count':os.cpu_count(),'load':load,'uptime_seconds':uptime,'memory':_memory_snapshot(),'disk':disk,'data_disk':data_disk,'api_rss_bytes':_proc_rss()},
        'services':services,'master':master,
        'database':{'name':db[2],'size_bytes':db[0],'connections':db[1]},
        'latest_import':({'id':imp[0],'status':imp[1],'started':imp[2],'completed':imp[3],'rows':imp[4],'error':imp[5]} if imp else None),
        'latest_webstats':({'id':web[0],'started':web[1],'completed':web[2],'raw_lines':web[3],'inserted':web[4],'status':web[5],'error':web[6]} if web else None),
        'users':{'total':users[0],'active':users[1],'disabled':users[2],'locked':locks},
        'last24h':{'browser_events':browser[0],'browser_visitors':browser[1],'browser_sessions':browser[2],'identified_users':browser[3],
                   'requests':http[0],'http_4xx':http[1],'http_5xx':http[2],'avg_request_ms':round(float(http[3] or 0),1)},
        'feedback':{'open':feedback[0],'reviewing':feedback[1]},'quality_errors':quality,
    }


@router.get('/api/stat/identifications')
def identifications(request:Request,limit:int=250):
    _admin_required(request); limit=max(20,min(limit,500))
    with db_connect() as conn:
        rows=conn.execute("""SELECT v.visitor_id,v.first_seen_at,v.last_seen_at,v.sessions_count,v.last_user_id,u.username,
          max(s.last_seen_at) session_last,max(s.device_type),max(s.os_family),max(s.browser_family),max(s.referrer_domain),
          count(DISTINCT s.session_id),count(DISTINCT e.event_id)
          FROM webstats.browser_visitor v
          LEFT JOIN auth.user_account u ON u.user_id=v.last_user_id
          LEFT JOIN webstats.browser_session s ON s.visitor_id=v.visitor_id
          LEFT JOIN webstats.browser_event e ON e.visitor_id=v.visitor_id AND e.occurred_at>=now()-interval '30 days'
          GROUP BY v.visitor_id,u.username ORDER BY v.last_seen_at DESC LIMIT %s""",(limit,)).fetchall()
        totals=conn.execute("""SELECT count(*),count(*) FILTER(WHERE last_user_id IS NOT NULL),
          count(*) FILTER(WHERE last_seen_at>=now()-interval '24 hours') FROM webstats.browser_visitor""").fetchone()
        recent=conn.execute("""SELECT e.occurred_at,e.event_name,e.path,e.entity_type,e.entity_id,e.visitor_id,e.user_id,u.username,
          s.device_type,s.browser_family,e.properties
          FROM webstats.browser_event e
          LEFT JOIN auth.user_account u ON u.user_id=e.user_id
          LEFT JOIN webstats.browser_session s ON s.session_id=e.session_id
          ORDER BY e.occurred_at DESC LIMIT 200""").fetchall()
    return {'overview':{'visitors':totals[0],'identified_visitors':totals[1],'active_24h':totals[2],
                         'identification_rate':round((totals[1] or 0)/(totals[0] or 1)*100,1) if totals[0] else 0},
            'visitors':[{'visitor_id':str(r[0]),'first':r[1],'last':r[2],'sessions_total':r[3],'user_id':r[4],'pseudo':r[5],
                         'session_last':r[6],'device':r[7],'os':r[8],'browser':r[9],'referrer':r[10],'sessions_30':r[11],'events_30':r[12]} for r in rows],
            'recent_events':[{'at':r[0],'event':r[1],'path':r[2],'entity_type':r[3],'entity_id':r[4],'visitor_id':str(r[5]),
                              'user_id':r[6],'pseudo':r[7],'device':r[8],'browser':r[9],'properties':r[10]} for r in recent]}


@router.get('/api/stat/pipeline')
def pipeline(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        imports=conn.execute('SELECT import_id,status,imported_at,completed_at,row_count,error_message FROM market.import_batch ORDER BY import_id DESC LIMIT 30').fetchall()
        web=conn.execute('SELECT run_id,started_at,completed_at,raw_lines,inserted_lines,status,error_message FROM webstats.import_run ORDER BY run_id DESC LIMIT 30').fetchall()
    return {'pipeline_mode':PIPELINE_MODE,'master_only':PIPELINE_MODE!='managed','download_managed':PIPELINE_MODE=='managed','extract_managed':PIPELINE_MODE=='managed',
            'imports':[{'id':r[0],'status':r[1],'started':r[2],'completed':r[3],'rows':r[4],'error':r[5]} for r in imports],
            'webstats_imports':[{'id':r[0],'started':r[1],'completed':r[2],'raw_lines':r[3],'inserted':r[4],'status':r[5],'error':r[6]} for r in web]}

@router.get('/stat')
def stat_page(request:Request):
    page=Path(__file__).with_name('stat.html')
    return Response(page.read_text(encoding='utf-8'),media_type='text/html; charset=utf-8',headers={'Cache-Control':'no-store'})

@router.get('/api/stat/audit')
def security_audit(request:Request):
    _admin_required(request)
    with db_connect() as conn:
        rows=conn.execute('''SELECT a.audit_id,a.occurred_at,a.actor_user_id,u.username,a.action,a.target_type,a.target_id,a.client_hash,a.metadata
                             FROM auth.audit_log a LEFT JOIN auth.user_account u ON u.user_id=a.actor_user_id
                             ORDER BY a.occurred_at DESC LIMIT 500''').fetchall()
    return {'events':[{'audit_id':r[0],'occurred_at':r[1],'user_id':r[2],'pseudo':r[3],'action':r[4],
                       'target_type':r[5],'target_id':r[6],'client_hash':(r[7][:12] if r[7] else None),'metadata':r[8]} for r in rows]}
