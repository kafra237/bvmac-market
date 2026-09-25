"""Read-only market API built from the latest completed PostgreSQL import."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from common import db_connect, parse_ymd, range_start, safe_json

router = APIRouter(prefix="/api/v3/market", tags=["market-v3"])


def _did(v: Any) -> int | None:
    try:
        n = int(float(v))
        return n if 19000101 <= n <= 29991231 else None
    except Exception:
        return None


def _date_from_did(v: Any) -> date | None:
    n = _did(v)
    if not n:
        return None
    try:
        return datetime.strptime(str(n), "%Y%m%d").date()
    except ValueError:
        return None


def _num(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _current_import_id(conn) -> int:
    row = conn.execute("SELECT import_id FROM market.current_import").fetchone()
    if not row:
        raise HTTPException(status_code=503, detail="Aucun import PostgreSQL disponible")
    return int(row[0])


def companies(conn=None) -> list[dict[str, Any]]:
    own = conn is None
    conn = conn or db_connect()
    try:
        rows = conn.execute(
            "SELECT data FROM market.current_excel_row WHERE sheet_name='dim_company' ORDER BY (data->>'company_id')::int"
        ).fetchall()
        return [dict(r[0]) for r in rows]
    finally:
        if own:
            conn.close()


def price_rows(company_id: int, *, start: date | None = None, end: date | None = None, conn=None) -> list[dict[str, Any]]:
    own = conn is None
    conn = conn or db_connect()
    try:
        rows = conn.execute(
            """SELECT data FROM market.current_excel_row
               WHERE sheet_name='fact_prices' AND data->>'company_id'=%s
               ORDER BY (data->>'bulletin_date_id')::int""", (str(company_id),)
        ).fetchall()
        out=[]
        for rr in rows:
            r=dict(rr[0]); d=_date_from_did(r.get("bulletin_date_id"))
            if not d: continue
            if start and d < start: continue
            if end and d > end: continue
            r["date"] = d.isoformat()
            out.append(r)
        return out
    finally:
        if own:
            conn.close()


def _filter_range(rows: list[dict[str, Any]], code: str, start_s: str | None, end_s: str | None) -> list[dict[str, Any]]:
    if not rows:
        return []
    last = _date_from_did(rows[-1].get("bulletin_date_id"))
    if not last:
        return rows
    end = parse_ymd(end_s) or last
    eligible=[r for r in rows if (d:=_date_from_did(r.get("bulletin_date_id"))) and d <= end]
    if (code or '').lower() in {'last','1d','day'} and not start_s:
        return eligible[-1:] if eligible else []
    start = parse_ymd(start_s) if start_s else range_start(end, code)
    return [r for r in eligible if (d:=_date_from_did(r.get("bulletin_date_id"))) and (start is None or d >= start)]


def _series_df(rows: list[dict[str, Any]]) -> pd.DataFrame:
    data=[]
    for r in rows:
        d=_date_from_did(r.get("bulletin_date_id")); c=_num(r.get("close_price"))
        if d and c and c > 0:
            data.append({"date":pd.Timestamp(d),"close":c,"open":_num(r.get("open_price")),
                         "volume":_num(r.get("vol_traded")) or 0.0,
                         "value":_num(r.get("value_traded")) or 0.0,
                         "bid":_num(r.get("vol_bid")) or 0.0,"ask":_num(r.get("vol_ask")) or 0.0,
                         "transactions":_num(r.get("num_transactions")) or 0.0})
    if not data:
        return pd.DataFrame(columns=["date","close"])
    return pd.DataFrame(data).sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def _last_before(company_id: int, on_date: date, conn=None) -> tuple[date,float] | None:
    own=conn is None; conn=conn or db_connect()
    try:
        did=int(on_date.strftime("%Y%m%d"))
        row=conn.execute(
            """SELECT data FROM market.current_excel_row WHERE sheet_name='fact_prices'
               AND data->>'company_id'=%s AND (data->>'bulletin_date_id')::int<=%s
               AND nullif(data->>'close_price','') IS NOT NULL
               ORDER BY (data->>'bulletin_date_id')::int DESC LIMIT 1""", (str(company_id),did)
        ).fetchone()
        if not row: return None
        r=dict(row[0]); d=_date_from_did(r.get("bulletin_date_id")); p=_num(r.get("close_price"))
        return (d,p) if d and p and p>0 else None
    finally:
        if own: conn.close()


def _pct_rank(vals: list[float | None], x: float | None, inverse=False) -> float | None:
    a=np.array([v for v in vals if v is not None and np.isfinite(v)],dtype=float)
    if x is None or not np.isfinite(x) or len(a)<2:
        return None
    p=float(np.sum(a<=x)-1)/max(1,len(a)-1)*100
    p=max(0,min(100,p)); return 100-p if inverse else p

def _mean_rank(*vals: float | None) -> float | None:
    return sum(vals)/len(vals) if vals and all(v is not None and np.isfinite(v) for v in vals) else None


def _universe_metrics(conn) -> list[dict[str,Any]]:
    comps=companies(conn); metrics=[]
    for c in comps:
        cid=int(c["company_id"]); rows=price_rows(cid,conn=conn); df=_series_df(rows)
        if df.empty: continue
        recent=df.tail(60); close=float(df.close.iloc[-1]);
        returns=df.close.pct_change().dropna(); vol=float(returns.tail(60).std(ddof=1)*math.sqrt(252)*100) if len(returns.tail(60))>=2 else None
        peak=df.close.cummax(); dd=(df.close/peak-1)*100; draw=float(dd.min()) if len(dd) else None
        def perf(days):
            target=df.date.iloc[-1]-pd.Timedelta(days=days); before=df[df.date<=target]
            return float((close/before.close.iloc[-1]-1)*100) if len(before) else None
        cap=conn.execute(
            """SELECT data FROM market.current_excel_row WHERE sheet_name='fact_market_cap' AND data->>'company_id'=%s
               ORDER BY (data->>'date_id')::int DESC LIMIT 1""",(str(cid),)).fetchone()
        cap=dict(cap[0]) if cap else {}
        fin=conn.execute(
            """SELECT data FROM market.current_excel_row WHERE sheet_name='fact_financials' AND data->>'company_id'=%s
               ORDER BY (data->>'fiscal_year')::int DESC LIMIT 1""",(str(cid),)).fetchone()
        fin=dict(fin[0]) if fin else {}
        rn=_num(fin.get("resultat_net")); cp=_num(fin.get("capitaux_propres")); ca=_num(fin.get("chiffre_affaires"))
        roe_pub=_num(fin.get("roe_publie_pct")); roe=roe_pub if roe_pub is not None else ((rn/cp*100) if rn is not None and cp else None)
        margin=(rn/ca*100) if rn is not None and ca else None
        per=_num(cap.get("per")); div=_num(cap.get("last_div_amount")); yld=(div/close*100) if div is not None and close else None
        active=float(((recent.volume>0)|(recent.value>0)).mean()*100) if len(recent) else 0
        metrics.append({"company":c,"company_id":cid,"price":close,"r1m":perf(31),"r6m":perf(183),"r1y":perf(366),
                        "volatility":vol,"drawdown":draw,"frequency":active,"avg_value":float(recent.value.mean()) if len(recent) else 0,
                        "per":per if per and per>0 else None,"yield":yld,"roe":roe,"margin":margin})
    for m in metrics:
        liq=_mean_rank(_pct_rank([x["frequency"] for x in metrics],m["frequency"]),_pct_rank([x["avg_value"] for x in metrics],m["avg_value"]))
        renta=_mean_rank(_pct_rank([x["roe"] for x in metrics],m["roe"]),_pct_rank([x["margin"] for x in metrics],m["margin"]))
        perf=_mean_rank(_pct_rank([x["r6m"] for x in metrics],m["r6m"]),_pct_rank([x["r1y"] for x in metrics],m["r1y"]))
        val=_mean_rank(_pct_rank([x["per"] for x in metrics],m["per"],True),_pct_rank([x["yield"] for x in metrics],m["yield"]))
        risk=_mean_rank(_pct_rank([x["volatility"] for x in metrics],m["volatility"],True),_pct_rank([x["drawdown"] for x in metrics],m["drawdown"]))
        raw={"frequency":m["frequency"],"avg_value":m["avg_value"],"roe":m["roe"],"margin":m["margin"],"r6m":m["r6m"],"r1y":m["r1y"],"per":m["per"],"yield":m["yield"],"volatility":m["volatility"],"drawdown":m["drawdown"]}
        m["score_missing"]=[k for k,v in raw.items() if v is None or not np.isfinite(v)]
        m["components"]={"liquidity":round(liq) if liq is not None else None,"profitability":round(renta) if renta is not None else None,"performance":round(perf) if perf is not None else None,"valuation":round(val) if val is not None else None,"risk":round(risk) if risk is not None else None}
        m["score"]=round(liq*.25+renta*.20+perf*.20+val*.20+risk*.15) if all(v is not None for v in (liq,renta,perf,val,risk)) else None
    return metrics


@router.get("/companies")
def get_companies():
    return {"companies":companies()}


@router.get("/series/{company_id}")
def series(company_id:int, range: str = Query("1y"), start: str|None=None, end: str|None=None):
    rows=_filter_range(price_rows(company_id),range,start,end)
    return {"company_id":company_id,"range":range,"points":[{
        "date":_date_from_did(r.get("bulletin_date_id")).isoformat(),"close":_num(r.get("close_price")),
        "open":_num(r.get("open_price")),"volume":_num(r.get("vol_traded")),"value":_num(r.get("value_traded")),
        "bid":_num(r.get("vol_bid")),"ask":_num(r.get("vol_ask")),"transactions":_num(r.get("num_transactions"))
    } for r in rows]}


@router.get("/technical/{company_id}")
def technical(company_id:int, range: str=Query("1y"), start:str|None=None, end:str|None=None):
    all_rows=price_rows(company_id); selected=_filter_range(all_rows,range,start,end); df=_series_df(all_rows)
    if df.empty: return {"company_id":company_id,"points":[],"summary":{}}
    c=df.close
    for n in (20,50,100,200): df[f"sma{n}"]=c.rolling(n,min_periods=max(2,min(n,5))).mean()
    for n in (20,50,200): df[f"ema{n}"]=c.ewm(span=n,adjust=False).mean()
    delta=c.diff(); gain=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean(); loss=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    rs=gain/loss.replace(0,np.nan); df["rsi14"]=100-(100/(1+rs))
    ema12=c.ewm(span=12,adjust=False).mean(); ema26=c.ewm(span=26,adjust=False).mean(); df["macd"]=ema12-ema26; df["macd_signal"]=df.macd.ewm(span=9,adjust=False).mean()
    mid=c.rolling(20,min_periods=5).mean(); sd=c.rolling(20,min_periods=5).std(); df["boll_mid"]=mid; df["boll_up"]=mid+2*sd; df["boll_low"]=mid-2*sd
    ret=c.pct_change(); df["vol20"]=ret.rolling(20,min_periods=5).std()*math.sqrt(252)*100; peak=c.cummax(); df["drawdown"]=(c/peak-1)*100
    # La fenêtre visuelle filtre les courbes, mais ne doit pas tronquer les momentum 1m/3m/6m/12m.
    # Si une fin de période personnalisée est fournie, elle devient la date d'ancrage ; sinon on utilise la dernière cotation disponible.
    full_df=df.copy()
    start_d=_date_from_did(selected[0]["bulletin_date_id"]) if selected else None; end_d=_date_from_did(selected[-1]["bulletin_date_id"]) if selected else None
    view_df=full_df if selected else full_df.iloc[0:0]
    if start_d: view_df=view_df[view_df.date.dt.date>=start_d]
    if end_d: view_df=view_df[view_df.date.dt.date<=end_d]
    fields=["close","sma20","sma50","sma100","sma200","ema20","ema50","ema200","rsi14","macd","macd_signal","boll_mid","boll_up","boll_low","vol20","drawdown"]
    points=[]
    for _,r in view_df.iterrows():
        p={"date":r.date.date().isoformat()}
        for f in fields:
            v=r.get(f); p[f]=None if pd.isna(v) else round(float(v),6)
        points.append(p)
    last=view_df.iloc[-1] if len(view_df) else None
    anchor_df=full_df
    custom_end=parse_ymd(end) if end else None
    if custom_end:
        anchor_df=anchor_df[anchor_df.date.dt.date<=custom_end]
    momentum_last=anchor_df.iloc[-1] if len(anchor_df) else None
    def momentum(days):
        if momentum_last is None: return None
        target=momentum_last.date-pd.Timedelta(days=days); b=anchor_df[anchor_df.date<=target]
        return round((float(momentum_last.close)/float(b.close.iloc[-1])-1)*100,4) if len(b) else None
    summary={"last_close":round(float(last.close),4) if last is not None else None,"rsi14":None if last is None or pd.isna(last.rsi14) else round(float(last.rsi14),2),
             "volatility20":None if last is None or pd.isna(last.vol20) else round(float(last.vol20),2),"max_drawdown":round(float(view_df.drawdown.min()),2) if len(view_df) else None,
             "momentum_anchor_date":momentum_last.date.date().isoformat() if momentum_last is not None else None,
             "momentum_1m":momentum(31),"momentum_3m":momentum(93),"momentum_6m":momentum(183),"momentum_12m":momentum(366)}
    return {"company_id":company_id,"range":range,"summary":summary,"points":points}


@router.get("/seasonality/{company_id}")
def seasonality(company_id:int, range:str=Query("all"), start:str|None=None, end:str|None=None):
    rows=_filter_range(price_rows(company_id),range,start,end); df=_series_df(rows)
    if len(df)<2: return {"weekday":[],"month":[],"observations":0}
    df["ret"] = df.close.pct_change()*100; df=df.dropna(subset=["ret"])
    df["weekday"] = df.date.dt.day_name(); df["month_num"] = df.date.dt.month
    fr_days={"Monday":"Lundi","Tuesday":"Mardi","Wednesday":"Mercredi","Thursday":"Jeudi","Friday":"Vendredi","Saturday":"Samedi","Sunday":"Dimanche"}
    fr_month={1:"Janvier",2:"Février",3:"Mars",4:"Avril",5:"Mai",6:"Juin",7:"Juillet",8:"Août",9:"Septembre",10:"Octobre",11:"Novembre",12:"Décembre"}
    def agg(g,key,name):
        out=[]
        for k,x in g:
            vals=x.ret.dropna(); out.append({key:name.get(k,k),"mean":round(float(vals.mean()),4),"median":round(float(vals.median()),4),"positive_pct":round(float((vals>0).mean()*100),2),"volatility":round(float(vals.std(ddof=1)),4) if len(vals)>1 else None,"n":int(len(vals))})
        return out
    return {"weekday":agg(df.groupby("weekday",sort=False),"label",fr_days),"month":agg(df.groupby("month_num"),"label",fr_month),"observations":int(len(df))}


@router.get("/orderbook/{company_id}")
def orderbook(company_id:int, range:str=Query("1y"), start:str|None=None, end:str|None=None):
    rows=_filter_range(price_rows(company_id),range,start,end); points=[]
    for r in rows:
        bid=_num(r.get("vol_bid")) or 0; ask=_num(r.get("vol_ask")) or 0; total=bid+ask
        points.append({"date":_date_from_did(r.get("bulletin_date_id")).isoformat(),"bid_volume":bid,"ask_volume":ask,
                       "bid_share_pct":round(bid/total*100,2) if total else None,"imbalance_pct":round((bid-ask)/total*100,2) if total else None,
                       "traded_volume":_num(r.get("vol_traded")),"transactions":_num(r.get("num_transactions"))})
    with db_connect() as conn:
        full=conn.execute("SELECT captured_at,bid_total,ask_total,best_bid,best_ask,levels FROM market.orderbook_snapshot WHERE company_id=%s ORDER BY captured_at DESC LIMIT 100",(company_id,)).fetchall()
    return {"company_id":company_id,"coverage":"aggregate_bid_ask_from_master" if not full else "aggregate_plus_full_snapshots",
            "note":"Le master historique fournit les volumes acheteurs/vendeurs agrégés. Les niveaux complets ne sont disponibles qu'à partir des snapshots explicitement collectés.",
            "points":points,"full_snapshots":[{"captured_at":r[0].isoformat(),"bid_total":float(r[1]) if r[1] is not None else None,"ask_total":float(r[2]) if r[2] is not None else None,"best_bid":float(r[3]) if r[3] is not None else None,"best_ask":float(r[4]) if r[4] is not None else None,"levels":r[5]} for r in full]}


@router.get("/screener")
def screener(min_score:int=0, sector:str|None=None, country:str|None=None, max_volatility:float|None=None, min_momentum_6m:float|None=None):
    with db_connect() as conn: ms=_universe_metrics(conn)
    out=[]
    for m in ms:
        c=m["company"]
        if min_score and (m["score"] is None or m["score"]<min_score): continue
        if sector and str(c.get("sector") or "")!=sector: continue
        if country and str(c.get("country") or "")!=country: continue
        if max_volatility is not None and (m["volatility"] is None or m["volatility"]>max_volatility): continue
        if min_momentum_6m is not None and (m["r6m"] is None or m["r6m"]<min_momentum_6m): continue
        out.append(m)
    return {"count":len(out),"results":safe_json(sorted(out,key=lambda x:(x["score"] is not None,x["score"] if x["score"] is not None else -1),reverse=True))}


@router.get("/compare")
def compare(ids:str, range:str=Query("1y"), start:str|None=None, end:str|None=None):
    try: cids=[int(x) for x in ids.split(",") if x.strip()][:5]
    except ValueError: raise HTTPException(status_code=400,detail="ids invalides")
    if not cids: raise HTTPException(status_code=400,detail="Sélection vide")
    with db_connect() as conn:
        cmap={int(c["company_id"]):c for c in companies(conn)}; series_out=[]
        for cid in cids:
            rows=_filter_range(price_rows(cid,conn=conn),range,start,end); df=_series_df(rows)
            if df.empty: continue
            base=float(df.close.iloc[0]); pts=[{"date":r.date.date().isoformat(),"close":round(float(r.close),6),"base100":round(float(r.close)/base*100,4)} for _,r in df.iterrows()]
            rets=df.close.pct_change().dropna(); peak=df.close.cummax(); dd=(df.close/peak-1)*100
            series_out.append({"company":cmap.get(cid,{"company_id":cid}),"points":pts,"performance_pct":round((float(df.close.iloc[-1])/base-1)*100,4),
                               "volatility_pct":round(float(rets.std(ddof=1)*math.sqrt(252)*100),4) if len(rets)>1 else None,"max_drawdown_pct":round(float(dd.min()),4)})
    return {"range":range,"series":series_out}


@router.get("/today")
def today():
    with db_connect() as conn:
        latest=conn.execute("SELECT max((data->>'bulletin_date_id')::int) FROM market.current_excel_row WHERE sheet_name='fact_prices'").fetchone()[0]
        if not latest: return {"date":None}
        rows=conn.execute("SELECT data FROM market.current_excel_row WHERE sheet_name='fact_prices' AND (data->>'bulletin_date_id')::int=%s",(latest,)).fetchall()
        vals=[dict(r[0]) for r in rows]; vars=[_num(r.get("variation_pct")) for r in vals]; vars=[v for v in vars if v is not None]
        movers=sorted(vals,key=lambda r:abs(_num(r.get("variation_pct")) or 0),reverse=True)[:5]
        cmap={str(c.get('company_id')):c for c in companies(conn)}
    enriched=[]
    for r in movers:
        c=cmap.get(str(r.get('company_id')), {})
        enriched.append({"company_id":r.get("company_id"),"ticker":c.get('ticker'),"short_name":c.get('short_name') or c.get('company_name'),
                         "close":_num(r.get("close_price")),"variation_pct":_num(r.get("variation_pct")),"value_traded":_num(r.get("value_traded"))})
    return {"date":_date_from_did(latest).isoformat(),"listed":len(vals),"up":sum(1 for v in vars if v>0),"down":sum(1 for v in vars if v<0),"flat":sum(1 for v in vars if v==0),
            "value_traded":round(sum(_num(r.get("value_traded")) or 0 for r in vals),2),"volume":round(sum(_num(r.get("vol_traded")) or 0 for r in vals),2),"movers":enriched}


@router.get("/changes")
def changes(request:Request):
    from admin_module import _admin_required
    _admin_required(request)
    with db_connect() as conn:
        ids=[int(r[0]) for r in conn.execute("SELECT import_id FROM market.import_batch WHERE status='completed' ORDER BY completed_at DESC,import_id DESC LIMIT 2").fetchall()]
        if not ids: return {"current":None,"previous":None,"sheets":[]}
        cur=ids[0]; prev=ids[1] if len(ids)>1 else None
        cur_counts={r[0]:int(r[1]) for r in conn.execute("SELECT sheet_name,count(*) FROM market.excel_row WHERE import_id=%s GROUP BY sheet_name",(cur,)).fetchall()}
        prev_counts={r[0]:int(r[1]) for r in conn.execute("SELECT sheet_name,count(*) FROM market.excel_row WHERE import_id=%s GROUP BY sheet_name",(prev,)).fetchall()} if prev else {}
        sheets=sorted(set(cur_counts)|set(prev_counts)); diffs=[]
        for s in sheets:
            changed=None
            if prev and s in {"fact_prices","fact_market_cap","fact_index","fact_opcvm_nav","fact_financials","quality_report"}:
                # Hash par ligne : mesure rapide des différences, sans prétendre identifier une clé métier parfaite.
                a={str(r[0]) for r in conn.execute("SELECT md5(data::text) FROM market.excel_row WHERE import_id=%s AND sheet_name=%s",(cur,s)).fetchall()}
                b={str(r[0]) for r in conn.execute("SELECT md5(data::text) FROM market.excel_row WHERE import_id=%s AND sheet_name=%s",(prev,s)).fetchall()}
                changed=len(a.symmetric_difference(b))
            diffs.append({"sheet":s,"current_rows":cur_counts.get(s,0),"previous_rows":prev_counts.get(s,0),"row_delta":cur_counts.get(s,0)-prev_counts.get(s,0),"changed_fingerprints":changed})
        meta=conn.execute("SELECT import_id,source_sha256,completed_at,row_count FROM market.import_batch WHERE import_id=ANY(%s) ORDER BY import_id DESC",(ids,)).fetchall()
    return {"current":safe_json(meta[0]) if meta else None,"previous":safe_json(meta[1]) if len(meta)>1 else None,"sheets":diffs}


@router.get("/trace/{sheet}/{row_number}")
def trace(sheet:str,row_number:int,request:Request):
    from admin_module import _admin_required
    _admin_required(request)
    allowed={"dim_company","dim_date","fact_prices","fact_market_cap","fact_index","dim_opcvm","fact_opcvm_nav","fact_financials","quality_report"}
    if sheet not in allowed: raise HTTPException(status_code=404,detail="Feuille inconnue")
    with db_connect() as conn:
        row=conn.execute("""SELECT r.data,i.import_id,i.source_sha256,i.source_path,i.source_mtime,i.completed_at
                            FROM market.current_excel_row r JOIN market.current_import i ON i.import_id=r.import_id
                            WHERE r.sheet_name=%s AND r.row_number=%s""",(sheet,row_number)).fetchone()
    if not row: raise HTTPException(status_code=404,detail="Ligne introuvable")
    return {"sheet":sheet,"row_number":row_number,"data":row[0],"import":{"import_id":row[1],"sha256":row[2],"source_path":row[3],"source_mtime":row[4],"completed_at":row[5]}}

# Exposé aux autres modules.
last_price_before = _last_before
universe_metrics = _universe_metrics

@router.get('/quality-score/{company_id}')
def quality_score(company_id:int):
    """Indice de confiance des données, distinct de toute appréciation de l'instrument."""
    with db_connect() as conn:
        cmap={int(c['company_id']):c for c in companies(conn)}
        company=cmap.get(company_id)
        if not company:
            raise HTTPException(status_code=404,detail='Valeur introuvable')
        rows=price_rows(company_id,conn=conn)
        if not rows:
            return {'company_id':company_id,'score':0,'label':'Données insuffisantes','components':{},'issues':[]}
        last=rows[-1]; last_date=_date_from_did(last.get('bulletin_date_id'))
        market_last=conn.execute("SELECT max((data->>'bulletin_date_id')::int) FROM market.current_excel_row WHERE sheet_name='fact_prices'").fetchone()[0]
        market_date=_date_from_did(market_last)
        freshness_days=(market_date-last_date).days if market_date and last_date else None
        freshness=max(0,100-min(100,(freshness_days or 0)*12.5))
        fields=['close_price','open_price','vol_traded','value_traded','num_transactions','vol_bid','vol_ask']
        present=sum(1 for f in fields if last.get(f) not in (None,''))
        completeness=present/len(fields)*100
        history=min(100,len(rows)/250*100)
        qrows=conn.execute("SELECT data FROM market.current_excel_row WHERE sheet_name='quality_report'").fetchall()
        ticker=str(company.get('ticker') or '').lower(); cid=str(company_id)
        issues=[]
        for rr in qrows:
            q=dict(rr[0])
            qcid=str(q.get('company_id') or q.get('entity_id') or '').strip()
            qticker=str(q.get('ticker') or q.get('symbol') or '').strip().lower()
            if qcid==cid or (ticker and qticker==ticker):
                issues.append(q)
        errors=sum(1 for q in issues if str(q.get('severity','')).upper()=='ERROR')
        warnings=sum(1 for q in issues if str(q.get('severity','')).upper() in {'WARNING','WARN'})
        extraction=max(0,100-errors*30-warnings*10)
        score=round(freshness*.35+completeness*.30+history*.20+extraction*.15)
        label='Élevée' if score>=85 else 'Bonne' if score>=70 else 'À surveiller' if score>=50 else 'Faible'
        return safe_json({'company_id':company_id,'ticker':company.get('ticker'),'score':score,'label':label,
          'components':{'freshness':round(freshness),'completeness':round(completeness),'history_depth':round(history),'extraction_quality':round(extraction)},
          'last_quote_date':last_date,'market_last_date':market_date,'freshness_days':freshness_days,
          'issue_counts':{'errors':errors,'warnings':warnings},'issues':issues[:50],
          'note':"Indice de qualité/fraîcheur des données uniquement ; ce n'est pas une recommandation ni une note d'investissement."})
