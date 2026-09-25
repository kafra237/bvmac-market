"""Scheduled dispatcher for market digests, weekly notifications, weekly email runs and pending targeted campaigns."""

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import psycopg

APP_DIR = Path(__file__).resolve().parent / 'app'
if not APP_DIR.is_dir():
    APP_DIR = Path(os.environ.get('BVMAC_APP_DIR', '/opt/bvmac/current')) / 'app'
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
from push_service import broadcast_all
from weekly_email import create_run as create_weekly_email_run, process_run as process_weekly_email_run, process_pending_runs as process_pending_weekly_email_runs
from admin_communications import process_pending_campaigns

DSN = os.environ.get('BVMAC_DB_DSN', 'dbname=bvmac user=bvmacapi host=/var/run/postgresql')


def num(v):
    try: return float(v)
    except Exception: return None


def process_broadcasts(conn):
    rows = conn.execute('''SELECT broadcast_id,kind,title,body,target_url FROM admin.notification_broadcast
                           WHERE sent_at IS NULL ORDER BY broadcast_id LIMIT 50''').fetchall()
    for bid, kind, title, body, target in rows:
        category = 'platform' if kind == 'platform_update' else 'communication'
        sent = broadcast_all(conn, category=category, event_key=f'broadcast:{bid}', title=title, body=body, target_url=target)
        conn.execute('UPDATE admin.notification_broadcast SET sent_at=now(),sent_count=%s WHERE broadcast_id=%s', (sent, bid))
        conn.commit()


def weekly_digest(conn):
    # Fenêtre calendaire calculée dans le fuseau métier de Douala.
    # Le timer systemd dédié déclenche normalement ce traitement le lundi à 08:00.
    # En cas de rattrapage Persistent=true après une indisponibilité, on calcule
    # toujours la dernière semaine civile complète (lundi -> dimanche).
    now = datetime.now(ZoneInfo('Africa/Douala'))
    this_monday = (now - timedelta(days=now.weekday())).date()
    start = this_monday - timedelta(days=7)
    end = this_monday - timedelta(days=1)
    start_id, end_id = int(start.strftime('%Y%m%d')), int(end.strftime('%Y%m%d'))
    rows = conn.execute('''SELECT data FROM market.current_excel_row WHERE sheet_name='fact_prices'
                           AND (data->>'bulletin_date_id')::int BETWEEN %s AND %s''', (start_id, end_id)).fetchall()
    if not rows:
        return
    data = [dict(r[0]) for r in rows]
    dates = sorted({int(r['bulletin_date_id']) for r in data if r.get('bulletin_date_id')})
    if not dates:
        return
    by_company = {}
    for r in data:
        cid = r.get('company_id')
        price = num(r.get('close_price'))
        did = int(r.get('bulletin_date_id') or 0)
        if cid is None or price is None or did <= 0: continue
        by_company.setdefault(str(cid), []).append((did, price))
    changes = []
    for cid, pts in by_company.items():
        pts.sort()
        if len(pts) >= 2 and pts[0][1]:
            changes.append((cid, (pts[-1][1]/pts[0][1]-1)*100))
    cmap = {str(r[0]): (r[1] or r[2] or f'#{r[0]}') for r in conn.execute(
        "SELECT (data->>'company_id')::int,data->>'ticker',data->>'short_name' FROM market.current_excel_row WHERE sheet_name='dim_company'"
    ).fetchall()}
    body = f'Semaine du {start.strftime("%d/%m")} au {end.strftime("%d/%m")} · {len(dates)} séance(s)'
    if changes:
        best=max(changes,key=lambda x:x[1]); worst=min(changes,key=lambda x:x[1])
        body += f' · {cmap.get(best[0],best[0])} {best[1]:+.2f} % · {cmap.get(worst[0],worst[0])} {worst[1]:+.2f} %'
    iso = start.isocalendar()
    broadcast_all(conn, category='weekly', event_key=f'weekly:{iso.year}-W{iso.week:02d}',
                  title='BVMAC · Résumé de la semaine passée', body=body+'.', target_url='/app?view=stories')


def main():
    parser = argparse.ArgumentParser(description='Dispatcher les notifications BVMAC')
    parser.add_argument('--mode', choices=('all', 'broadcasts', 'weekly', 'weekly-email'), default='all')
    args = parser.parse_args()
    with psycopg.connect(DSN) as conn:
        if args.mode in ('all', 'broadcasts'):
            process_broadcasts(conn)
            process_pending_weekly_email_runs(conn)
            process_pending_campaigns(conn)
        if args.mode in ('all', 'weekly'):
            weekly_digest(conn)
        if args.mode in ('all', 'weekly-email'):
            run_id=create_weekly_email_run(conn,trigger='scheduled',requested_by='timer')
            process_weekly_email_run(conn,run_id)


if __name__ == '__main__':
    main()
