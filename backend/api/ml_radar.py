"""Authenticated API exposing the latest stored Predictive Radar probabilities and model metadata."""

from __future__ import annotations

import json
from fastapi import APIRouter, HTTPException, Request

from auth_module import require_user
from common import db_connect

router = APIRouter(prefix='/api/v3/ml', tags=['ml-radar'])

MODEL_INFO = {
    'version': '1.0.0',
    'trade_5': {'label': 'Liquidité à court terme', 'horizon': '5 prochains bulletins observés', 'mean_auc': 0.775, 'min_auc': 0.694},
    'up_20': {'label': 'Tendance à moyen horizon', 'horizon': '~20 prochains bulletins observés', 'mean_auc': 0.717, 'min_auc': 0.628},
    'opcvm_down_next': {'label': 'Risque prochaine VL', 'horizon': 'prochaine nouvelle VL publiée', 'mean_auc': 0.780, 'min_auc': 0.609},
}


def _rowdict(cursor, row):
    return {d.name: v for d, v in zip(cursor.description, row)}


def _latest_payload(conn):
    cur = conn.execute("SELECT run_id,import_id,model_version,data_through,generated_at,action_count,opcvm_count FROM ml.latest_run")
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=503, detail='Radar prédictif en attente du premier calcul.')
    run = _rowdict(cur, row)
    acur = conn.execute("""SELECT company_id,ticker,prediction_date,close_price,vol_bid,vol_ask,vol_traded,book_imbalance,
                                 trade_recent20_rate,trade_1_prob,trade1_confidence,trade_5_prob,trade5_confidence,
                                 trade_20_stat_prob,up_20_prob,up20_confidence,trade5_factors,up20_factors
                          FROM ml.latest_action_prediction ORDER BY ticker""")
    actions = [_rowdict(acur, r) for r in acur.fetchall()]
    fcur = conn.execute("""SELECT fund_id,fund_name,category,valuation_frequency,nav_date,nav,risk_down_next_nav,confidence,history_points
                          FROM ml.latest_opcvm_prediction ORDER BY risk_down_next_nav DESC NULLS LAST,fund_name""")
    funds = [_rowdict(fcur, r) for r in fcur.fetchall()]
    for item in actions:
        for key in ('trade5_factors','up20_factors'):
            if isinstance(item.get(key), str):
                try: item[key] = json.loads(item[key])
                except Exception: item[key] = []
    reliable_actions = [a for a in actions if a.get('trade5_confidence') in ('medium','high')]
    high_liq = [a for a in reliable_actions if (a.get('trade_5_prob') or 0) >= .75]
    trend = [a for a in actions if a.get('up20_confidence') in ('medium','high') and (a.get('up_20_prob') or 0) >= .50]
    low_risk = [f for f in funds if f.get('confidence') in ('medium','high') and (f.get('risk_down_next_nav') or 1) <= .10]
    high_risk = [f for f in funds if f.get('confidence') in ('medium','high') and (f.get('risk_down_next_nav') or 0) >= .25]
    return {
        'run': run,
        'model_info': MODEL_INFO,
        'summary': {
            'actions_high_liquidity': len(high_liq),
            'actions_positive_trend': len(trend),
            'funds_low_downside_risk': len(low_risk),
            'funds_elevated_downside_risk': len(high_risk),
        },
        'actions': actions,
        'opcvm': funds,
        'disclaimer': 'Prévisions probabilistes issues de données historiques. Elles ne constituent pas un conseil en investissement.',
    }


@router.get('/radar')
def radar(request: Request):
    require_user(request)
    with db_connect() as conn:
        return _latest_payload(conn)


@router.get('/history/action/{ticker}')
def action_history(ticker: str, request: Request, limit: int = 120):
    require_user(request)
    limit = max(10, min(limit, 365))
    with db_connect() as conn:
        cur = conn.execute("""SELECT r.data_through AS date,p.trade_5_prob,p.trade5_confidence,p.up_20_prob,p.up20_confidence,p.book_imbalance
                              FROM ml.action_prediction p JOIN ml.prediction_run r USING(run_id)
                              WHERE p.ticker=%s AND r.status='completed'
                              ORDER BY r.generated_at DESC LIMIT %s""", (ticker.upper(), limit))
        rows = [_rowdict(cur, r) for r in cur.fetchall()]
    rows.reverse()
    return {'ticker': ticker.upper(), 'items': rows, 'note': 'Historique enregistré à partir du déploiement du Radar en production.'}


@router.get('/history/opcvm/{fund_id}')
def opcvm_history(fund_id: str, request: Request, limit: int = 120):
    require_user(request)
    limit = max(10, min(limit, 365))
    with db_connect() as conn:
        cur = conn.execute("""SELECT r.data_through AS date,p.nav_date,p.nav,p.risk_down_next_nav,p.confidence
                              FROM ml.opcvm_prediction p JOIN ml.prediction_run r USING(run_id)
                              WHERE p.fund_id=%s AND r.status='completed'
                              ORDER BY r.generated_at DESC LIMIT %s""", (fund_id, limit))
        rows = [_rowdict(cur, r) for r in cur.fetchall()]
    rows.reverse()
    return {'fund_id': fund_id, 'items': rows, 'note': 'Historique enregistré à partir du déploiement du Radar en production.'}
