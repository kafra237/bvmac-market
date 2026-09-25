"""Production inference for the validated Predictive Radar targets. Training is deliberately kept outside the live ETL path."""

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

MODEL_VERSION = '1.0.0'
BASE = Path(__file__).resolve().parent
MODELS = BASE / 'models'
VALIDATION = BASE / 'validation'
DSN = os.environ.get('BVMAC_IMPORT_DSN') or os.environ.get('BVMAC_DB_DSN') or 'dbname=bvmac user=bvmacimport host=/var/run/postgresql'

ACTION_FACTOR_LABELS = {
    'log_transactions_mean60': 'activité des transactions sur 60 observations',
    'vol_ask': 'volume actuellement offert',
    'log_book_total_mean60': 'profondeur moyenne du carnet sur 60 observations',
    'book_imbalance_mean20': 'pression achat/vente moyenne sur 20 observations',
    'log_vol_traded_mean60': 'volume échangé moyen sur 60 observations',
    'log_book_total_mean10': 'profondeur récente du carnet',
    'log_vol_traded_mean20': 'volume échangé moyen sur 20 observations',
    'ret1_std60': 'volatilité récente',
    'book_imbalance': 'déséquilibre actuel demande/offre',
    'book_imbalance_mean60': 'pression achat/vente moyenne sur 60 observations',
    'yoy_variation_pct': 'variation annuelle du cours',
    'price_vs_ytd_high': 'position par rapport au plus haut annuel',
    'price_vs_ytd_low': 'position par rapport au plus bas annuel',
    'book_imbalance_mean5': 'pression achat/vente très récente',
}
TRADE5_EXPLAIN = ['log_transactions_mean60','vol_ask','log_book_total_mean60','book_imbalance_mean20','log_vol_traded_mean60','log_book_total_mean10','log_vol_traded_mean20','ret1_std60','book_imbalance']
UP20_EXPLAIN = ['ret1_std60','book_imbalance_mean60','yoy_variation_pct','book_imbalance_mean20','price_vs_ytd_high','price_vs_ytd_low','book_imbalance_mean5']


def _sheet(conn, name: str) -> pd.DataFrame:
    rows = conn.execute('SELECT data FROM market.current_excel_row WHERE sheet_name=%s ORDER BY row_number', (name,)).fetchall()
    return pd.DataFrame([dict(r[0]) for r in rows])


def _dateid(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series.astype(str), format='%Y%m%d', errors='coerce')


def _numeric(df: pd.DataFrame, cols: list[str]) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors='coerce')


def prepare_action_features(conn):
    comp = _sheet(conn, 'dim_company')
    prices = _sheet(conn, 'fact_prices')
    idx = _sheet(conn, 'fact_index')
    cap = _sheet(conn, 'fact_market_cap')
    fin = _sheet(conn, 'fact_financials')
    if prices.empty or comp.empty:
        raise RuntimeError('Données actions insuffisantes pour Radar')

    prices['date'] = _dateid(prices['bulletin_date_id'])
    idx['date'] = _dateid(idx['date_id']) if not idx.empty else pd.NaT
    cap['date'] = _dateid(cap['date_id']) if not cap.empty else pd.NaT
    fin['source_date'] = _dateid(fin['source_bulletin_date_id']) if not fin.empty else pd.NaT
    _numeric(prices, ['prev_price','open_price','close_price','upper_limit','lower_limit','vol_bid','vol_ask','vol_traded','value_traded','num_transactions','variation_pct','daily_return_pct','last_div_amount','yoy_variation_pct','ytd_high','ytd_low','next_ref_price'])
    _numeric(idx, ['index_value','variation_day_pct'])
    _numeric(cap, ['close_price','total_market_cap','float_market_cap','float_shares','total_shares','liquidity_pct','eps','per','last_div_amount'])
    _numeric(fin, ['capitaux_propres','chiffre_affaires','dividende_total','dividende_unitaire','resultat_net','roe_publie_pct','taux_rendement_brut_pct','total_bilan','valeur_ajoutee','fiscal_year'])

    comp['company_id'] = comp['company_id'].astype(str)
    prices['company_id'] = prices['company_id'].astype(str)
    if not cap.empty: cap['company_id'] = cap['company_id'].astype(str)
    if not fin.empty: fin['company_id'] = fin['company_id'].astype(str)
    prices = prices.merge(comp[['company_id','ticker','sector','country','listing_date']], on='company_id', how='left')

    if not idx.empty:
        idx2 = idx[['date','index_value','variation_day_pct']].rename(columns={'variation_day_pct':'index_ret'}).dropna(subset=['date']).sort_values('date')
        all_dates = pd.DataFrame({'date': sorted(prices['date'].dropna().unique())})
        idx_asof = pd.merge_asof(all_dates.sort_values('date'), idx2, on='date', direction='backward')
        prices = prices.merge(idx_asof, on='date', how='left')
    else:
        prices['index_value'] = np.nan; prices['index_ret'] = np.nan

    capcols = ['company_id','date','total_market_cap','float_market_cap','float_shares','total_shares','liquidity_pct','eps','per']
    parts=[]
    for cid,g in prices.groupby('company_id', sort=False):
        gg=g.sort_values('date')
        if not cap.empty:
            cc=cap.loc[cap.company_id==cid, [c for c in capcols if c in cap.columns]].drop(columns='company_id').dropna(subset=['date']).sort_values('date')
            z=pd.merge_asof(gg,cc,on='date',direction='backward') if len(cc) else gg
        else: z=gg
        parts.append(z)
    prices=pd.concat(parts,ignore_index=True)

    fincols=['company_id','source_date','fiscal_year','capitaux_propres','chiffre_affaires','dividende_unitaire','resultat_net','total_bilan','valeur_ajoutee']
    parts=[]
    for cid,g in prices.groupby('company_id',sort=False):
        gg=g.sort_values('date')
        if not fin.empty:
            ff=fin.loc[fin.company_id==cid,[c for c in fincols if c in fin.columns]].drop(columns='company_id').dropna(subset=['source_date']).sort_values('source_date').rename(columns={'source_date':'date'})
            z=pd.merge_asof(gg,ff,on='date',direction='backward',suffixes=('','_fin')) if len(ff) else gg
        else:z=gg
        parts.append(z)
    prices=pd.concat(parts,ignore_index=True).sort_values(['ticker','date']).reset_index(drop=True)

    G=prices.groupby('ticker',group_keys=False)
    prices['ret1']=G['close_price'].pct_change()*100
    prices['trade_now']=(prices['vol_traded'].fillna(0)>0).astype(int)
    prices['move_now']=(prices['close_price']!=G['close_price'].shift(1)).astype(int)
    prices['bid_present']=(prices['vol_bid'].fillna(0)>0).astype(int)
    prices['ask_present']=(prices['vol_ask'].fillna(0)>0).astype(int)
    prices['book_imbalance']=(prices['vol_bid'].fillna(0)-prices['vol_ask'].fillna(0))/(prices['vol_bid'].fillna(0)+prices['vol_ask'].fillna(0)+1.0)
    prices['book_total']=prices['vol_bid'].fillna(0)+prices['vol_ask'].fillna(0)
    prices['log_book_total']=np.log1p(prices['book_total'])
    prices['log_vol_traded']=np.log1p(prices['vol_traded'].clip(lower=0).fillna(0))
    prices['log_value_traded']=np.log1p(prices['value_traded'].clip(lower=0).fillna(0))
    prices['log_transactions']=np.log1p(prices['num_transactions'].clip(lower=0).fillna(0))
    prices['price_vs_ytd_high']=(prices['close_price']/prices['ytd_high']-1)*100
    prices['price_vs_ytd_low']=(prices['close_price']/prices['ytd_low']-1)*100
    prices['index_ret1']=prices.groupby('ticker')['index_value'].pct_change()*100
    prices['rel_ret1']=prices['ret1']-prices['index_ret1']
    for lag in [1,2,3,5,10,20]:
        for c in ['ret1','book_imbalance','log_book_total','log_vol_traded','log_transactions','trade_now','move_now','index_ret1']:
            prices[f'{c}_lag{lag}']=G[c].shift(lag)
    for w in [5,10,20,60]:
        for c in ['ret1','book_imbalance','log_book_total','log_vol_traded','log_transactions','trade_now','move_now']:
            prices[f'{c}_mean{w}']=G[c].transform(lambda s:s.shift(1).rolling(w,min_periods=max(2,w//4)).mean())
        prices[f'ret1_std{w}']=G['ret1'].transform(lambda s:s.shift(1).rolling(w,min_periods=max(2,w//4)).std())
        prices[f'close_min{w}']=G['close_price'].transform(lambda s:s.shift(1).rolling(w,min_periods=max(2,w//4)).min())
        prices[f'close_max{w}']=G['close_price'].transform(lambda s:s.shift(1).rolling(w,min_periods=max(2,w//4)).max())
        prices[f'close_pos{w}']=(prices['close_price']-prices[f'close_min{w}'])/(prices[f'close_max{w}']-prices[f'close_min{w}']+1e-9)
    for col in ['trade_now','move_now']:
        vals=[]
        for _,g in prices.groupby('ticker'):
            n=999
            for x in g[col].values:
                n=0 if x==1 else min(n+1,999); vals.append(n)
        prices[f'obs_since_{col}']=vals

    # Statistical future-liquidity history only; never exposed as a learned model.
    for h in [1,5,20]:
        arr=[]
        for _,gg in prices.groupby('ticker'):
            tr=gg['trade_now'].values; n=len(gg)
            for i in range(n):
                sl=tr[i+1:min(n,i+h+1)]
                arr.append(float(np.any(sl>0)) if len(sl)==h else np.nan)
        prices[f'trade_{h}']=arr

    excluded={'date','ticker','company_id','sector','country','status','listing_date','bulletin_date_id','session_date_id','price_id','last_div_date_id','next_ref_price'}
    target_prefix=('trade_','future_','target_end_date_','return_','up_','move_','volume_sum_','tx_sum_')
    nums=[]
    for c in prices.columns:
        if c in excluded or any(c.startswith(p) for p in target_prefix): continue
        if pd.api.types.is_numeric_dtype(prices[c]): nums.append(c)
    X=pd.get_dummies(prices[nums+['ticker','sector','country']], columns=['ticker','sector','country'], dummy_na=True, dtype=float)
    return prices, X


def prepare_opcvm_features(conn, global_max: pd.Timestamp):
    funds=_sheet(conn,'dim_opcvm'); nav=_sheet(conn,'fact_opcvm_nav')
    if funds.empty or nav.empty: raise RuntimeError('Données OPCVM insuffisantes pour Radar')
    funds['fund_id']=funds.fund_id.astype(str); nav['fund_id']=nav.fund_id.astype(str)
    nav['date']=_dateid(nav['nav_date_id']); nav['bulletin_date']=_dateid(nav['bulletin_date_id'])
    _numeric(nav,['nav','prev_nav','var_inception_pct','var_prev_pct'])
    nav=nav[(nav.date>='2023-01-01')&(nav.date<=global_max)&(nav.bulletin_date<=global_max)].sort_values(['fund_id','date','bulletin_date']).drop_duplicates(['fund_id','date'],keep='first')
    nav=nav.merge(funds[['fund_id','fund_name','category','manager','valuation_frequency']],on='fund_id',how='left').sort_values(['fund_id','date']).reset_index(drop=True)
    G=nav.groupby('fund_id',group_keys=False);nav['ret1']=G.nav.pct_change()*100
    for lag in [1,2,3,4,8,12]:nav[f'ret_lag{lag}']=G.ret1.shift(lag)
    for w in [4,8,12,26]:
        nav[f'ret_mean{w}']=G.ret1.transform(lambda s:s.shift(1).rolling(w,min_periods=max(2,w//3)).mean())
        nav[f'ret_std{w}']=G.ret1.transform(lambda s:s.shift(1).rolling(w,min_periods=max(2,w//3)).std())
        nav[f'nav_min{w}']=G.nav.transform(lambda s:s.shift(1).rolling(w,min_periods=max(2,w//3)).min())
        nav[f'nav_max{w}']=G.nav.transform(lambda s:s.shift(1).rolling(w,min_periods=max(2,w//3)).max())
        nav[f'nav_pos{w}']=(nav.nav-nav[f'nav_min{w}'])/(nav[f'nav_max{w}']-nav[f'nav_min{w}']+1e-9)
    nums=[c for c in nav if pd.api.types.is_numeric_dtype(nav[c]) and c not in ['nav_id','bulletin_date_id','nav_date_id','prev_nav_date_id']]
    NX=pd.get_dummies(nav[nums+['fund_id','category','valuation_frequency']],columns=['fund_id','category','valuation_frequency'],dummy_na=True,dtype=float)
    return nav,NX


def _predict(bundle, X):
    B=X.reindex(columns=bundle['features'],fill_value=0).replace([np.inf,-np.inf],np.nan)
    med=bundle['medians'].reindex(bundle['features']).fillna(0)
    B=B.fillna(med).fillna(0)
    raw=bundle['model'].predict_proba(B)[:,1]
    z=np.log(np.clip(raw,1e-6,1-1e-6)/(1-np.clip(raw,1e-6,1-1e-6))).reshape(-1,1)
    return bundle['calibrator'].predict_proba(z)[:,1],B


def _local_factors(bundle, row: pd.DataFrame, base_prob: float, candidates: list[str], limit=4):
    out=[]
    for feature in candidates:
        if feature not in row.columns or feature not in bundle['features']: continue
        val=row.iloc[0][feature]
        if pd.isna(val): continue
        alt=row.copy(); alt.loc[:,feature]=bundle['medians'].get(feature,0)
        prob,_=_predict(bundle,alt)
        delta=float(base_prob-prob[0])
        if abs(delta)<0.005: continue
        out.append({'feature':feature,'label':ACTION_FACTOR_LABELS.get(feature,feature),'impact':round(delta,4),'direction':'supporte' if delta>0 else 'freine','value':round(float(val),4) if math.isfinite(float(val)) else None})
    return sorted(out,key=lambda x:abs(x['impact']),reverse=True)[:limit]


def run(force=False):
    with psycopg.connect(DSN) as conn:
        cur=conn.execute('SELECT import_id FROM market.current_import'); row=cur.fetchone()
        if not row: raise RuntimeError('Aucun import PostgreSQL courant')
        import_id=int(row[0])
        existing=conn.execute('SELECT run_id,status FROM ml.prediction_run WHERE import_id=%s AND model_version=%s',(import_id,MODEL_VERSION)).fetchone()
        if existing and existing[1]=='completed' and not force:
            print(f'Radar déjà calculé pour import_id={import_id}'); return int(existing[0])
        if existing:
            run_id=int(existing[0]); conn.execute('DELETE FROM ml.action_prediction WHERE run_id=%s',(run_id,));conn.execute('DELETE FROM ml.opcvm_prediction WHERE run_id=%s',(run_id,));conn.execute("UPDATE ml.prediction_run SET status='running',generated_at=now(),error=NULL WHERE run_id=%s",(run_id,))
        else:
            run_id=int(conn.execute('INSERT INTO ml.prediction_run(import_id,model_version) VALUES(%s,%s) RETURNING run_id',(import_id,MODEL_VERSION)).fetchone()[0])
        conn.commit()
        try:
            prices,X=prepare_action_features(conn);global_max=prices.date.max();current=prices.sort_values('date').groupby('ticker',as_index=False).tail(1).copy();current=current[(global_max-current.date).dt.days<=30].copy()
            rel=json.loads((VALIDATION/'action_ticker_reliability.json').read_text(encoding='utf-8'))
            bundles={k:joblib.load(MODELS/f'{k}.joblib') for k in ('trade_1','trade_5','up_20')}
            pred={};BX={}
            for key,bundle in bundles.items():
                probs,b=_predict(bundle,X.loc[current.index]);pred[key]=probs;BX[key]=b
            nav,NX=prepare_opcvm_features(conn,global_max);nb=joblib.load(MODELS/'opcvm_down_next.joblib');latest=nav.sort_values('date').groupby('fund_id',as_index=False).tail(1).copy();nprob,NB=_predict(nb,NX.loc[latest.index]);frel=json.loads((VALIDATION/'opcvm_fund_reliability.json').read_text(encoding='utf-8'))
            for pos,(idxrow,r) in enumerate(current.iterrows()):
                t=str(r.ticker); hist=prices[(prices.ticker==t)&prices.trade_20.notna()].sort_values('date').tail(120);trade20=float(hist.trade_20.mean()) if len(hist) else None;recent=float(prices[prices.ticker==t].sort_values('date').tail(20).trade_now.mean())
                p1=float(pred['trade_1'][pos]);p5=float(pred['trade_5'][pos]);pu=float(pred['up_20'][pos])
                f5=_local_factors(bundles['trade_5'],BX['trade_5'].iloc[[pos]],p5,TRADE5_EXPLAIN);fu=_local_factors(bundles['up_20'],BX['up_20'].iloc[[pos]],pu,UP20_EXPLAIN)
                conn.execute('''INSERT INTO ml.action_prediction(run_id,company_id,ticker,prediction_date,close_price,vol_bid,vol_ask,vol_traded,book_imbalance,trade_recent20_rate,trade_1_prob,trade1_confidence,trade_5_prob,trade5_confidence,trade_20_stat_prob,up_20_prob,up20_confidence,trade5_factors,up20_factors)
                                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                             (run_id,str(r.get('company_id') or ''),t,r.date.date(),float(r.close_price) if pd.notna(r.close_price) else None,float(r.vol_bid) if pd.notna(r.vol_bid) else None,float(r.vol_ask) if pd.notna(r.vol_ask) else None,float(r.vol_traded) if pd.notna(r.vol_traded) else None,float(r.book_imbalance) if pd.notna(r.book_imbalance) else None,recent,p1,rel.get('trade_1',{}).get(t,{}).get('confidence','low'),p5,rel.get('trade_5',{}).get(t,{}).get('confidence','low'),trade20,pu,rel.get('up_20',{}).get(t,{}).get('confidence','low'),Jsonb(f5),Jsonb(fu)))
            for pos,(_,r) in enumerate(latest.iterrows()):
                fid=str(r.fund_id); rr=frel.get(fid,{})
                conn.execute('''INSERT INTO ml.opcvm_prediction(run_id,fund_id,fund_name,category,valuation_frequency,nav_date,nav,risk_down_next_nav,confidence,history_points)
                                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(run_id,fid,r.get('fund_name'),r.get('category'),r.get('valuation_frequency'),r.date.date(),float(r.nav) if pd.notna(r.nav) else None,float(nprob[pos]),rr.get('confidence','low'),int((nav.fund_id==fid).sum())))
            conn.execute("UPDATE ml.prediction_run SET status='completed',data_through=%s,action_count=%s,opcvm_count=%s,error=NULL WHERE run_id=%s",(global_max.date(),len(current),len(latest),run_id));conn.commit()
            print(f'Radar ML OK run={run_id} import={import_id} actions={len(current)} opcvm={len(latest)} data={global_max.date()}');return run_id
        except Exception as exc:
            conn.rollback();conn.execute("UPDATE ml.prediction_run SET status='failed',error=%s WHERE run_id=%s",(str(exc)[:2000],run_id));conn.commit();raise


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--force',action='store_true');args=ap.parse_args();run(args.force)

if __name__=='__main__': main()
