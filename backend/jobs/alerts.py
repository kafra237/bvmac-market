"""Scheduled evaluation of user alerts and portfolio rules, followed by web-push delivery when a rule is met."""

#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

import psycopg

DSN = os.environ.get('BVMAC_DB_DSN', 'dbname=bvmac user=bvmacapi host=/var/run/postgresql')
APP_DIR = Path(__file__).resolve().parent / 'app'
if APP_DIR.is_dir() and str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from push_service import push_user, subscribed_users


def num(v):
    try:
        return float(v)
    except Exception:
        return None


def fr_date_id(value) -> str:
    try:
        return datetime.strptime(str(int(value)), '%Y%m%d').strftime('%d/%m/%Y')
    except Exception:
        return str(value or '')


def _latest_date_id(conn, sheet: str, field: str) -> int | None:
    row = conn.execute(
        f'''SELECT max((data->>%s)::int) FROM market.current_excel_row
            WHERE sheet_name=%s AND nullif(data->>%s,'') IS NOT NULL''',
        (field, sheet, field),
    ).fetchone()
    return int(row[0]) if row and row[0] else None


def _market_digest(conn, session_id: int) -> str:
    rows = [dict(r[0]) for r in conn.execute(
        "SELECT data FROM market.current_excel_row WHERE sheet_name='fact_prices' AND (data->>'bulletin_date_id')::int=%s",
        (session_id,),
    ).fetchall()]
    vals = [(r, num(r.get('variation_pct'))) for r in rows]
    changes = [(r, v) for r, v in vals if v is not None]
    up = sum(1 for _, v in changes if v > 0)
    down = sum(1 for _, v in changes if v < 0)
    flat = sum(1 for _, v in changes if v == 0)
    value = sum(num(r.get('value_traded')) or 0 for r in rows)
    top = max(changes, key=lambda x: x[1], default=None)
    low = min(changes, key=lambda x: x[1], default=None)
    cmap = {str(r[0]): (r[1] or r[2] or f'#{r[0]}') for r in conn.execute(
        "SELECT (data->>'company_id')::int,data->>'ticker',data->>'short_name' FROM market.current_excel_row WHERE sheet_name='dim_company'"
    ).fetchall()}
    parts = [f'{up} hausse(s), {down} baisse(s), {flat} stable(s)', f'{value:,.0f} XAF échangés']
    if top:
        parts.append(f"meilleure variation {cmap.get(str(top[0].get('company_id')), '#')} {top[1]:+.2f} %")
    if low and (not top or low[0].get('company_id') != top[0].get('company_id')):
        parts.append(f"plus forte baisse {cmap.get(str(low[0].get('company_id')), '#')} {low[1]:+.2f} %")
    return ' · '.join(parts)


def _broadcast_market(conn, import_id: int):
    session_id = _latest_date_id(conn, 'fact_prices', 'bulletin_date_id')
    if not session_id:
        return
    body = f"Cotation du {fr_date_id(session_id)} : {_market_digest(conn, session_id)}."
    for user_id in subscribed_users(conn):
        push_user(
            conn, user_id=user_id, category='market',
            event_key=f'market-session:{session_id}',
            title='BVMAC · Synthèse de la dernière cotation',
            body=body, target_url='/app?view=overview',
        )

def _broadcast_opcvm(conn, import_id: int):
    nav_id = _latest_date_id(conn, 'fact_opcvm_nav', 'nav_date_id') or _latest_date_id(conn, 'fact_opcvm_nav', 'bulletin_date_id')
    if not nav_id:
        return
    for user_id in subscribed_users(conn):
        push_user(
            conn, user_id=user_id, category='opcvm',
            event_key=f'opcvm-nav:{nav_id}',
            title='BVMAC · OPCVM',
            body=f'De nouvelles valeurs liquidatives OPCVM sont disponibles ({fr_date_id(nav_id)}).',
            target_url='/app?view=funds',
        )


def _evaluate_alert_rules(conn, import_id: int):
    rules = conn.execute(
        'SELECT rule_id,user_id,company_id,kind,threshold FROM auth.alert_rule WHERE active=true'
    ).fetchall()
    price_cache = {}
    ticker_cache = {}
    quality_errors = None
    for rule_id, user_id, company_id, kind, threshold in rules:
        fire = False
        key = ''
        message = ''
        if kind == 'new_import':
            fire = True
            key = f'import:{import_id}'
            message = f'Nouvel import de données disponible : #{import_id}.'
        elif kind == 'quality_error':
            if quality_errors is None:
                quality_errors = int(conn.execute(
                    "SELECT count(*) FROM market.current_excel_row WHERE sheet_name='quality_report' AND upper(coalesce(data->>'severity',''))='ERROR'"
                ).fetchone()[0])
            if quality_errors > 0:
                fire = True
                key = f'quality-errors:{quality_errors}'
                message = f'{quality_errors} erreur(s) de qualité signalée(s) dans l’import #{import_id}.'
        elif company_id is not None:
            cid = int(company_id)
            if cid not in price_cache:
                row = conn.execute(
                    '''SELECT data FROM market.current_excel_row WHERE sheet_name='fact_prices' AND data->>'company_id'=%s
                       ORDER BY (data->>'bulletin_date_id')::int DESC LIMIT 1''',
                    (str(cid),),
                ).fetchone()
                price_cache[cid] = dict(row[0]) if row else None
            if cid not in ticker_cache:
                row = conn.execute(
                    "SELECT data->>'ticker' FROM market.current_excel_row WHERE sheet_name='dim_company' AND data->>'company_id'=%s LIMIT 1",
                    (str(cid),),
                ).fetchone()
                ticker_cache[cid] = row[0] if row and row[0] else f'#{cid}'
            row = price_cache[cid]
            if row:
                price = num(row.get('close_price'))
                volume = num(row.get('vol_traded'))
                thr = num(threshold)
                ticker = ticker_cache[cid]
                if kind == 'price_above' and price is not None and thr is not None and price >= thr:
                    fire = True; key = f'price-above:{thr:g}'; message = f'{ticker} : cours {price:g} ≥ seuil {thr:g}.'
                elif kind == 'price_below' and price is not None and thr is not None and price <= thr:
                    fire = True; key = f'price-below:{thr:g}'; message = f'{ticker} : cours {price:g} ≤ seuil {thr:g}.'
                elif kind == 'volume_above' and volume is not None and thr is not None and volume >= thr:
                    fire = True; key = f'volume-above:{thr:g}'; message = f'{ticker} : volume {volume:g} ≥ seuil {thr:g}.'
        if not fire:
            continue
        inserted = conn.execute(
            '''INSERT INTO auth.alert_event(rule_id,user_id,import_id,condition_key,message)
               VALUES(%s,%s,%s,%s,%s)
               ON CONFLICT(rule_id,import_id,condition_key) DO NOTHING
               RETURNING event_id''',
            (rule_id, user_id, import_id, key, message),
        ).fetchone()
        conn.commit()
        if inserted:
            push_user(
                conn, user_id=int(user_id), category='alert',
                event_key=f'alert:{int(inserted[0])}',
                title='BVMAC · Alerte atteinte', body=message,
                target_url='/app?view=portfolios#tracking',
            )


def _metric_matches(metric: dict, filters: dict) -> bool:
    score = metric.get('score')
    if filters.get('min_score') not in (None, ''):
        if score is None or score < float(filters['min_score']):
            return False
    if filters.get('max_volatility') not in (None, ''):
        v = metric.get('volatility')
        if v is None or v > float(filters['max_volatility']):
            return False
    if filters.get('min_momentum_6m') not in (None, ''):
        v = metric.get('r6m')
        if v is None or v < float(filters['min_momentum_6m']):
            return False
    company = metric.get('company') or {}
    if filters.get('sector') and company.get('sector') != filters.get('sector'):
        return False
    if filters.get('country') and company.get('country') != filters.get('country'):
        return False
    return True


def _evaluate_screeners(conn, import_id: int):
    screens = conn.execute(
        'SELECT screen_id,user_id,name,filters FROM portfolio.saved_screen WHERE notify=true'
    ).fetchall()
    if not screens:
        return
    try:
        from market import _universe_metrics
        metrics = _universe_metrics(conn)
    except Exception as exc:
        print(f'[push] screener metrics error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return
    metric_ids = {int(m['company_id']) for m in metrics}
    for screen_id, user_id, name, filters in screens:
        filt = dict(filters or {})
        matches = {int(m['company_id']): m for m in metrics if _metric_matches(m, filt)}
        old = {int(r[0]) for r in conn.execute(
            'SELECT company_id FROM auth.screener_state WHERE screen_id=%s AND is_matching=true',
            (screen_id,),
        ).fetchall()}
        entrants = sorted(set(matches) - old)
        # État complet : permet de notifier à nouveau seulement si une valeur sort puis rentre plus tard.
        for cid in metric_ids:
            conn.execute(
                '''INSERT INTO auth.screener_state(screen_id,company_id,is_matching,last_import_id,updated_at)
                   VALUES(%s,%s,%s,%s,now())
                   ON CONFLICT(screen_id,company_id) DO UPDATE SET is_matching=EXCLUDED.is_matching,
                     last_import_id=EXCLUDED.last_import_id,updated_at=now()''',
                (screen_id, cid, cid in matches, import_id),
            )
        conn.commit()
        if not entrants:
            continue
        labels = []
        for cid in entrants[:5]:
            company = matches[cid].get('company') or {}
            labels.append(company.get('ticker') or company.get('short_name') or f'#{cid}')
        suffix = f' +{len(entrants)-5}' if len(entrants) > 5 else ''
        joined = ', '.join(labels) + suffix
        digest = hashlib.sha256(','.join(map(str, entrants)).encode()).hexdigest()[:16]
        push_user(
            conn, user_id=int(user_id), category='screener',
            event_key=f'screener:{int(screen_id)}:import:{import_id}:{digest}',
            title=f'BVMAC · Screener « {name} »',
            body=f'{len(entrants)} nouvelle(s) valeur(s) correspondent : {joined}.',
            target_url='/app?view=portfolios#tracking',
        )


def _portfolio_updates(conn, session_id: int | None):
    if not session_id:
        return
    portfolios = conn.execute(
        'SELECT portfolio_id,user_id,name,initial_amount,cash_initial FROM portfolio.portfolio'
    ).fetchall()
    price_cache: dict[int, float | None] = {}
    for portfolio_id, user_id, name, initial_amount, cash_initial in portfolios:
        positions = conn.execute(
            'SELECT company_id,quantity FROM portfolio.position WHERE portfolio_id=%s',
            (portfolio_id,),
        ).fetchall()
        if not positions:
            continue
        total = float(cash_initial or 0)
        complete = True
        for company_id, quantity in positions:
            cid = int(company_id)
            if cid not in price_cache:
                row = conn.execute(
                    '''SELECT data->>'close_price' FROM market.current_excel_row
                       WHERE sheet_name='fact_prices' AND data->>'company_id'=%s
                       ORDER BY (data->>'bulletin_date_id')::int DESC LIMIT 1''',
                    (str(cid),),
                ).fetchone()
                price_cache[cid] = num(row[0]) if row else None
            price = price_cache[cid]
            if price is None:
                complete = False
                break
            total += float(quantity) * price
        if not complete:
            continue
        initial = float(initial_amount)
        perf = ((total / initial) - 1) * 100 if initial else 0
        push_user(
            conn, user_id=int(user_id), category='portfolio',
            event_key=f'portfolio:{int(portfolio_id)}:session:{session_id}',
            title=f'BVMAC · {name}',
            body=f'Valeur actualisée : {total:,.0f} XAF · {perf:+.2f} %.',
            target_url='/app?view=portfolios#portfolios',
        )


def main():
    with psycopg.connect(DSN) as conn:
        imp = conn.execute('SELECT import_id FROM market.current_import').fetchone()
        if not imp:
            return
        import_id = int(imp[0])
        session_id = _latest_date_id(conn, 'fact_prices', 'bulletin_date_id')
        _broadcast_market(conn, import_id)
        _broadcast_opcvm(conn, import_id)
        _evaluate_alert_rules(conn, import_id)
        _evaluate_screeners(conn, import_id)
        _portfolio_updates(conn, session_id)


if __name__ == '__main__':
    main()
