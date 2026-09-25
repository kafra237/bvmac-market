"""Post-import notifier. It sends one global push only when the market date stored in PostgreSQL genuinely advances."""

#!/usr/bin/env python3
from __future__ import annotations
import os, sys
from datetime import datetime
from pathlib import Path
import psycopg

APP = Path(os.environ.get('BVMAC_APP_DIR','/opt/bvmac/current')) / 'app'
if str(APP) not in sys.path: sys.path.insert(0,str(APP))
from push_service import push_user

DSN=os.environ.get('BVMAC_DB_DSN','dbname=bvmac user=bvmacapi host=/var/run/postgresql')

def main():
    with psycopg.connect(DSN) as conn:
        latest=conn.execute("SELECT import_id FROM market.import_batch WHERE status='completed' ORDER BY import_id DESC LIMIT 1").fetchone()
        if not latest: return 0
        import_id=int(latest[0])
        drow=conn.execute("SELECT max((data->>'bulletin_date_id')::int) FROM market.current_excel_row WHERE sheet_name='fact_prices' AND data ? 'bulletin_date_id'").fetchone()
        data_date=int(drow[0]) if drow and drow[0] else None
        state=conn.execute("SELECT last_import_id,last_data_date_id FROM admin.market_data_push_state WHERE singleton=true FOR UPDATE").fetchone()
        if not state:
            conn.execute("INSERT INTO admin.market_data_push_state(singleton,last_import_id,last_data_date_id,updated_at) VALUES(true,%s,%s,now())",(import_id,data_date)); conn.commit(); return 0
        last_import,last_date=state
        if data_date is None or (last_date is not None and data_date <= int(last_date)):
            if last_import != import_id:
                conn.execute("UPDATE admin.market_data_push_state SET last_import_id=%s,updated_at=now() WHERE singleton=true",(import_id,)); conn.commit()
            return 0
        try: label=datetime.strptime(str(data_date),'%Y%m%d').strftime('%d/%m/%Y')
        except Exception: label=str(data_date)
        ml=conn.execute("SELECT data_through FROM ml.latest_run").fetchone()
        ml_ready=bool(ml and ml[0] and ml[0].strftime('%Y%m%d') >= str(data_date))
        body=f'Les données du {label} viennent d’être intégrées. La synthèse du marché et les cotations sont à jour.'
        if ml_ready: body+=' Le Radar prédictif a également été recalculé.'
        users=[int(r[0]) for r in conn.execute("""SELECT DISTINCT u.user_id
            FROM auth.user_account u JOIN auth.push_subscription s ON s.user_id=u.user_id
            WHERE u.status='active' AND s.disabled_at IS NULL""").fetchall()]
        sent=sum(push_user(conn,user_id=uid,category='market',event_key=f'market-data:{data_date}',title='BVMAC · Nouvelles données disponibles',body=body,target_url='/app?view=overview') for uid in users)
        conn.execute("UPDATE admin.market_data_push_state SET last_import_id=%s,last_data_date_id=%s,notified_at=now(),sent_count=%s,updated_at=now() WHERE singleton=true",(import_id,data_date,sent)); conn.commit()
        print(f'push nouvelles données: date={data_date} import={import_id} envois={sent}')
    return 0
if __name__=='__main__': raise SystemExit(main())
