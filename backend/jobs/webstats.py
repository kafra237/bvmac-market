#!/usr/bin/env python3
"""Importe les requêtes Nginx en statistiques pseudonymisées côté serveur.

Le fichier brut contient l'IP le temps d'un cycle (~5 min). Le script le tourne
d'abord dans /var/log/nginx, demande à Nginx de rouvrir son journal, puis copie le
journal fermé vers le spool (même si /var/log et /var/lib sont sur des filesystems
différents). Il calcule HMAC(host|IP|UA), insère uniquement
le hash et les métadonnées utiles dans PostgreSQL, puis détruit le fichier brut.
Aucune IP brute n'est stockée en base.
"""
from __future__ import annotations

import grp
import json
import os
import pwd
import re
import signal
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import psycopg

APP_DIR=Path(os.environ.get('BVMAC_APP_DIR','/opt/bvmac'))
sys.path.insert(0,str(APP_DIR/'app'))
from common import host_visitor_hash  # noqa:E402

RAW=Path(os.environ.get('BVMAC_WEBSTATS_RAW','/var/log/nginx/bvmac_webstats_raw.log'))
SPOOL=Path(os.environ.get('BVMAC_WEBSTATS_SPOOL','/var/lib/bvmac/webstats-spool'))
DB_DSN=os.environ.get('BVMAC_WEBSTATS_DSN','dbname=bvmac user=bvmacstats host=/var/run/postgresql')
RUN_USER=os.environ.get('BVMAC_WEBSTATS_USER','bvmacstats')
STATIC_RE=re.compile(r'\.(?:js|css|map|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|xlsx?|pdf)$',re.I)
BOT_RE=re.compile(r'bot|crawler|spider|slurp|headless|monitor|uptime|curl/|wget/|python-requests|go-http-client',re.I)


def ua_family(ua:str)->tuple[str,str]:
    u=ua or ''
    fam='Edge' if 'Edg/' in u else 'Firefox' if 'Firefox/' in u else 'Chrome' if ('Chrome/' in u or 'CriOS/' in u) else 'Safari' if 'Safari/' in u else 'Autre'
    dev='mobile' if re.search(r'Mobi|Android|iPhone',u,re.I) else 'tablet' if re.search(r'iPad|Tablet',u,re.I) else 'desktop'
    return fam,dev


def _spool_file(rotated:Path)->Path:
    """Copie un journal fermé vers le spool, même si /var/log et /var/lib sont sur des FS différents."""
    dest=SPOOL/f"nginx-{int(time.time()*1000)}-{os.getpid()}.log"
    shutil.copyfile(rotated,dest)
    # Rend la copie durable avant de supprimer le journal tourné.
    with dest.open('ab') as fh:
        fh.flush(); os.fsync(fh.fileno())
    pw=pwd.getpwnam(RUN_USER); os.chown(dest,pw.pw_uid,pw.pw_gid); os.chmod(dest,0o600)
    rotated.unlink(missing_ok=True)
    return dest


def rotate_raw()->list[Path]:
    SPOOL.mkdir(parents=True,exist_ok=True)
    files=sorted(SPOOL.glob('*.log'))
    if os.geteuid()!=0:
        return files
    if not RAW.exists() or RAW.stat().st_size<=0:
        return files

    # IMPORTANT : on renomme d'abord dans le même dossier que le log Nginx.
    # /var/log/nginx et /var/lib peuvent être sur des filesystems différents ;
    # un rename direct vers SPOOL provoquerait EXDEV (Invalid cross-device link).
    stamp=int(time.time()*1000)
    rotated=RAW.with_name(f"{RAW.name}.{stamp}.{os.getpid()}.rotating")
    RAW.rename(rotated)

    # SIGUSR1 demande à Nginx de fermer l'ancien inode et de rouvrir RAW.
    try:
        pid=int(Path('/run/nginx.pid').read_text().strip())
        os.kill(pid,signal.SIGUSR1)
    except Exception as exc:
        # Si la réouverture échoue, restaure immédiatement le chemin initial :
        # Nginx continue alors à écrire dans le même inode, sans perte.
        try:
            if not RAW.exists() and rotated.exists(): rotated.rename(RAW)
        finally:
            print(f'warning: reopen nginx: {exc}',file=sys.stderr)
        return files

    deadline=time.monotonic()+3.0
    while time.monotonic()<deadline and not RAW.exists():
        time.sleep(.05)
    if not RAW.exists():
        # Nginx n'a pas rouvert le fichier : restaure l'ancien journal et retente au prochain cycle.
        if rotated.exists(): rotated.rename(RAW)
        print('warning: nginx did not reopen webstats log; rotation deferred',file=sys.stderr)
        return files

    # L'ancien fichier n'est désormais plus le log actif : copie inter-filesystem sûre.
    dest=_spool_file(rotated)
    files.append(dest)
    return sorted(set(files))


def drop_privileges():
    if os.geteuid()!=0: return
    pw=pwd.getpwnam(RUN_USER)
    groups=[g.gr_gid for g in grp.getgrall() if RUN_USER in g.gr_mem]
    if pw.pw_gid not in groups: groups.append(pw.pw_gid)
    os.setgroups(groups); os.setgid(pw.pw_gid); os.setuid(pw.pw_uid)


def parse_line(line:str):
    try: x=json.loads(line)
    except Exception: return None
    host=str(x.get('host') or '').lower().split(':')[0]
    ip=str(x.get('ip') or '')
    ua=str(x.get('ua') or '')[:1000]
    if not host or not ip: return None
    uri=str(x.get('uri') or '/')[:2000]
    p=urlparse(uri); path=p.path or '/'
    if STATIC_RE.search(path): return None
    try: when=datetime.fromisoformat(str(x.get('time')).replace('Z','+00:00'))
    except Exception: return None
    try: status=int(x.get('status') or 0)
    except Exception: status=0
    try: ms=int(round(float(x.get('request_time') or 0)*1000))
    except Exception: ms=None
    ref=str(x.get('referer') or '')
    try: refdom=urlparse(ref).netloc.lower()[:255] or None
    except Exception: refdom=None
    fam,dev=ua_family(ua); bot=bool(BOT_RE.search(ua))
    vh=host_visitor_hash(host,ip,ua)
    return (host,vh,when,str(x.get('method') or 'GET')[:12],path[:1000],status,ms,refdom,fam,dev,bot)


def import_file(path:Path):
    raw=path.read_text(encoding='utf-8',errors='replace').splitlines(); parsed=[x for line in raw if (x:=parse_line(line))]
    with psycopg.connect(DB_DSN) as conn:
        run_id=conn.execute("INSERT INTO webstats.import_run(source_file,raw_lines) VALUES(%s,%s) RETURNING run_id",(path.name,len(raw))).fetchone()[0]
        try:
            with conn.cursor() as cur:
                cur.executemany('''INSERT INTO webstats.request_event
                    (host,visitor_hash,occurred_at,method,path,status,request_time_ms,referrer_domain,user_agent_family,device_type,is_bot)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',parsed)
            # Agrégat journalier seulement pour les visiteurs non-bots.
            for host,vh,when,method,pth,status,ms,ref,fam,dev,bot in parsed:
                if bot: continue
                conn.execute('''INSERT INTO webstats.visitor_day(host,visitor_hash,day,first_seen_at,last_seen_at,request_count)
                    VALUES(%s,%s,%s,%s,%s,1)
                    ON CONFLICT(host,visitor_hash,day) DO UPDATE SET
                      first_seen_at=least(webstats.visitor_day.first_seen_at,EXCLUDED.first_seen_at),
                      last_seen_at=greatest(webstats.visitor_day.last_seen_at,EXCLUDED.last_seen_at),
                      request_count=webstats.visitor_day.request_count+1''',(host,vh,when.date(),when,when))
            conn.execute("UPDATE webstats.import_run SET completed_at=now(),inserted_lines=%s,status='completed' WHERE run_id=%s",(len(parsed),run_id)); conn.commit()
        except Exception as exc:
            conn.rollback()
            with psycopg.connect(DB_DSN) as c2:
                c2.execute("UPDATE webstats.import_run SET completed_at=now(),status='failed',error_message=%s WHERE run_id=%s",(str(exc)[:2000],run_id)); c2.commit()
            raise
    path.unlink(missing_ok=True)
    print(json.dumps({'file':path.name,'raw':len(raw),'inserted':len(parsed)}))


def main():
    files=rotate_raw(); drop_privileges()
    for p in files:
        if p.exists(): import_file(p)
    return 0

if __name__=='__main__': raise SystemExit(main())
