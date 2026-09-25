"""Lightweight 30-minute publication watcher. It starts the full pipeline only when a newer bulletin appears available."""

#!/usr/bin/env python3
from __future__ import annotations
import os,re,subprocess
from datetime import date,timedelta
from pathlib import Path
from urllib.parse import urljoin
import requests

BASE='https://www.bvm-ac.org'
PAGE=BASE+'/bulletin-officiel-de-la-cote-boc/'
UPLOAD=BASE+'/wp-content/uploads'
OUT=Path(os.environ.get('BVMAC_BULLETIN_DIR','/var/lib/bvmac/bulletins'))
DB=os.environ.get('BVMAC_DB_NAME','bvmac')
UA={'User-Agent':'Mozilla/5.0 (BVMAC Market data freshness monitor)'}
DATE_RE=[
    re.compile(r'BOC-(\d{4})(\d{2})(\d{2})(?:-\d+)?\.pdf',re.I),
    re.compile(r'BOC(?:-BVMAC)?-?(\d{2})-(\d{2})-(\d{2,4})(?:-\d+)?\.pdf',re.I),
]

def parse_date(name):
    m=DATE_RE[0].search(name)
    if m:
        try:return date(int(m.group(1)),int(m.group(2)),int(m.group(3)))
        except ValueError:return None
    m=DATE_RE[1].search(name)
    if m:
        y=int(m.group(3));y=y+2000 if y<100 else y
        try:return date(y,int(m.group(2)),int(m.group(1)))
        except ValueError:return None
    return None

def local_dates():
    out=set()
    if OUT.is_dir():
        for p in OUT.rglob('*.pdf'):
            d=parse_date(p.name)
            if d: out.add(d)
    return out

def imported_latest_date():
    """Date réellement intégrée en PostgreSQL, référence de fraîcheur.

    Le watcher s'exécute en root sur le VPS de production. L'appel local psql
    évite d'ajouter un secret et permet de relancer un pipeline qui aurait
    téléchargé un PDF mais échoué avant l'import.
    """
    sql="""SELECT max((data->>'bulletin_date_id')::int)
           FROM market.current_excel_row
           WHERE sheet_name='fact_prices' AND data ? 'bulletin_date_id'"""
    try:
        cp=subprocess.run(
            ['runuser','-u','postgres','--','psql','-d',DB,'-qAt','-c',sql],
            check=False,capture_output=True,text=True,timeout=10,
        )
        raw=(cp.stdout or '').strip()
        if cp.returncode==0 and re.fullmatch(r'\d{8}',raw):
            return date(int(raw[:4]),int(raw[4:6]),int(raw[6:8]))
    except Exception:
        pass
    return None

def candidates(d):
    names=[f'BOC-{d:%Y%m%d}.pdf',f'BOC-{d:%Y%m%d}-1.pdf']
    folders=[];y=d.year;m=d.month
    for _ in range(2):
        folders.append(f'{y}/{m:02d}');m+=1
        if m==13:m=1;y+=1
    return [f'{UPLOAD}/{folder}/{name}' for folder in folders for name in names]

def remote_pdf(s,url):
    try:
        r=s.head(url,allow_redirects=True,timeout=(5,10))
        return r.status_code==200 and ('pdf' in r.headers.get('Content-Type','').lower() or r.url.lower().endswith('.pdf'))
    except requests.RequestException:return False

def detect():
    known=local_dates();latest=imported_latest_date();today=date.today();cut=today-timedelta(days=21)
    s=requests.Session();s.headers.update(UA)
    # La page officielle détecte les variantes de nommage non prévues par les URL directes.
    try:
        r=s.get(PAGE,timeout=(5,15));r.raise_for_status()
        found=[]
        for href in re.findall(r'href=["\']([^"\']+)["\']',r.text,re.I):
            u=urljoin(PAGE,href).split('?',1)[0];d=parse_date(u)
            if d and cut<=d<=today+timedelta(days=1) and '.pdf' in u.lower():
                found.append((d,u))
        for d,u in sorted(found,reverse=True):
            if latest is not None:
                if d>latest:return d,u
            elif d not in known:
                return d,u
    except requests.RequestException:
        pass
    # Si PostgreSQL connaît sa dernière séance, seules les dates plus récentes sont sondées.
    for offset in range(0,22):
        d=today-timedelta(days=offset)
        if d.weekday()>=5: continue
        if latest is not None and d<=latest: break
        if latest is None and d in known: continue
        for u in candidates(d):
            if remote_pdf(s,u):return d,u
    return None,None

def main():
    d,u=detect()
    if not d:
        print('Aucun nouveau bulletin récent détecté.');return 0
    print(f'Nouveau bulletin non encore intégré détecté: {d} {u}')
    # systemd sérialise le service : start sur une unité déjà active ne crée pas
    # de second pipeline concurrent.
    cp=subprocess.run(['systemctl','start','--no-block','bvmac-pipeline.service'],check=False)
    return cp.returncode
if __name__=='__main__':raise SystemExit(main())
