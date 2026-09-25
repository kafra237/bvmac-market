#!/usr/bin/env python3
"""État local consultable dans /stat ; aucun email ni appel à un tiers."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import urlopen

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,required=True);p.add_argument('--backups',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    problems=[]
    try:
        with urlopen(f'http://127.0.0.1:{a.port}/api/health',timeout=10) as response:data=json.load(response)
        if data.get('status')!='ok':problems.append('API ou import indisponible')
    except Exception:problems.append('API injoignable')
    stamps=list(Path(a.backups).glob('????????T??????Z/manifest.json'))
    latest=max((x.stat().st_mtime for x in stamps),default=0)
    if datetime.now(timezone.utc).timestamp()-latest>36*3600:problems.append('Sauvegarde absente ou âgée de plus de 36 heures')
    verified=[]
    for x in stamps:
        try:
            if json.loads(x.read_text()).get('restore_verified'):verified.append(x.stat().st_mtime)
        except (OSError,ValueError):continue
    if datetime.now(timezone.utc).timestamp()-max(verified,default=0)>8*86400:problems.append('Restauration de contrôle non vérifiée depuis plus de 8 jours')
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'ok':not problems,'problems':problems}
    target=Path(a.output);tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(result),encoding='utf-8');tmp.chmod(0o644);tmp.replace(target)
    print(json.dumps(result,ensure_ascii=False))
    return 1 if problems else 0

if __name__=='__main__':raise SystemExit(main())
