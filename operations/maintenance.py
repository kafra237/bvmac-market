#!/usr/bin/env python3
"""Sauvegarde locale, contrôle de restauration isolé et rétention."""
import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import uuid

def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--database', required=True)
    p.add_argument('--destination', required=True)
    p.add_argument('--config', required=True)
    p.add_argument('--app', required=True)
    p.add_argument('--verify-restore', action='store_true')
    a=p.parse_args()
    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]{0,62}',a.database):
        p.error('Nom de base invalide')
    root=Path(a.destination).resolve()
    if root.parent != Path('/var/backups') or not root.name.endswith('-daily'):
        p.error('Destination attendue : /var/backups/<service>-daily')
    root.mkdir(mode=0o700,parents=True,exist_ok=True);root.chmod(0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    pending=root/(stamp+'.pending');pending.mkdir(mode=0o700)
    dump=pending/'database.dump'
    with dump.open('wb') as out:
        run(['runuser','-u','postgres','--','pg_dump','--format=custom','--dbname='+a.database],stdout=out)
    with dump.open('rb') as data:
        run(['runuser','-u','postgres','--','pg_restore','--list'],stdin=data,stdout=subprocess.DEVNULL)
    # Includes encryption keys: archive stays root-only and is never web-served.
    with tarfile.open(pending/'configuration.tar.gz','w:gz') as tar:
        tar.add(a.config,arcname='configuration')
        tar.add(str(Path(a.app).resolve()),arcname='application',filter=lambda info:None if '/.venv/' in info.name or info.name.endswith('/.venv') else info)
    verify=a.verify_restore or datetime.now(timezone.utc).weekday()==6
    if verify:
        temporary='bvmac_restore_'+uuid.uuid4().hex[:16]
        created=False
        try:
            run(['runuser','-u','postgres','--','createdb',temporary]);created=True
            with dump.open('rb') as data:
                run(['runuser','-u','postgres','--','pg_restore','--exit-on-error','--no-owner','--no-privileges','--dbname='+temporary],stdin=data)
            run(['runuser','-u','postgres','--','psql','-v','ON_ERROR_STOP=1','-d',temporary,'-c','SELECT count(*) FROM auth.user_account; SELECT count(*) FROM market.current_excel_row;'],stdout=subprocess.DEVNULL)
        finally:
            if created:
                run(['runuser','-u','postgres','--','dropdb',temporary])
    (pending/'manifest.json').write_text(json.dumps({'database':a.database,'created_at':stamp,'restore_verified':verify}),encoding='utf-8')
    pending.rename(root/stamp)
    # Purge only timestamp-named directories owned by this backup job.
    cutoff=datetime.now(timezone.utc)-timedelta(days=14)
    for folder in root.iterdir():
        if folder.is_symlink() or not folder.is_dir() or not re.fullmatch(r'\d{8}T\d{6}Z',folder.name):continue
        if datetime.strptime(folder.name,'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)<cutoff:
            if folder.resolve().parent != root:raise RuntimeError('Chemin de sauvegarde inattendu')
            shutil.rmtree(folder)
    retention="""BEGIN;
    DELETE FROM webstats.browser_event WHERE occurred_at < now()-interval '90 days';
    DELETE FROM webstats.browser_session WHERE last_seen_at < now()-interval '90 days';
    DELETE FROM webstats.browser_visitor WHERE last_seen_at < now()-interval '90 days';
    DELETE FROM webstats.request_event WHERE occurred_at < now()-interval '90 days';
    DELETE FROM webstats.visitor_day WHERE day < current_date-interval '13 months';
    DELETE FROM auth.user_account WHERE registration_pending=true AND email_verified_at IS NULL AND created_at<now()-interval '1 day';
    DELETE FROM auth.human_challenge WHERE expires_at < now();
    DELETE FROM auth.email_challenge WHERE expires_at < now()-interval '7 days';
    DELETE FROM auth.mail_quota WHERE day < current_date-2;
    DELETE FROM admin.console_challenge WHERE expires_at < now();
    DELETE FROM admin.session WHERE expires_at < now();
    DELETE FROM auth.user_session WHERE expires_at < now()-interval '7 days';
    COMMIT;"""
    run(['runuser','-u','postgres','--','psql','-v','ON_ERROR_STOP=1','-d',a.database],input=retention,text=True,stdout=subprocess.DEVNULL)
    print('Sauvegarde terminée, restauration vérifiée :',verify)

if __name__=='__main__':main()
