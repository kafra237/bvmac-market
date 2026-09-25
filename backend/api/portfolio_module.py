"""Portfolio simulation and analysis endpoints. No brokerage order is ever submitted."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Literal

import numpy as np
import pandas as pd
import psycopg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from portfolio_valuation import value_portfolio
from auth_module import require_user
from market import companies, last_price_before, price_rows, _series_df
from common import db_connect, parse_ymd, range_start, safe_json

router = APIRouter(prefix="/api/v3/portfolio", tags=["portfolio"])
DEFAULT_AMOUNT = float(__import__('os').environ.get('BVMAC_DEFAULT_PORTFOLIO_AMOUNT','1000000'))

class PositionIn(BaseModel):
    company_id: int
    mode: Literal['percent','amount','quantity']
    value: float = Field(gt=0,le=1e15,allow_inf_nan=False)

class PortfolioCreate(BaseModel):
    name: str = Field(min_length=1,max_length=80)
    start_date: date
    total_amount: float = Field(default=DEFAULT_AMOUNT,gt=0,le=1e15)
    positions: list[PositionIn] = Field(min_length=1,max_length=30)

class PortfolioName(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @model_validator(mode='after')
    def normalize_name(self):
        self.name = ' '.join(self.name.split())
        if not self.name:
            raise ValueError('Donne un nom au portefeuille.')
        return self


class PortfolioArchive(BaseModel):
    archived: bool


class OptimizeIn(BaseModel):
    start_date: date
    end_date: date
    amount: float = Field(default=DEFAULT_AMOUNT,gt=0,le=1e15)
    objective: Literal['min_volatility','max_sharpe','return_risk','min_drawdown','risk_parity','max_diversification']='max_sharpe'
    company_ids: list[int] | None = Field(default=None,max_length=30)
    max_assets: int = Field(default=12,ge=2,le=30)

    @model_validator(mode='after')
    def bounded_period(self):
        if self.end_date < self.start_date or (self.end_date-self.start_date).days > 3660:
            raise ValueError('Choisis une période positive ne dépassant pas 10 ans')
        return self

class BacktestIn(OptimizeIn):
    lookback_days: int = Field(default=365,ge=90,le=3650)
    rebalance: Literal['monthly','quarterly','yearly']='quarterly'


def _project_simplex(v: np.ndarray) -> np.ndarray:
    if np.all(v>=0) and abs(v.sum()-1)<1e-10: return v
    u=np.sort(v)[::-1]; cssv=np.cumsum(u)-1
    ind=np.arange(1,len(v)+1); cond=u-cssv/ind>0
    rho=ind[cond][-1] if np.any(cond) else 1; theta=cssv[rho-1]/rho
    return np.maximum(v-theta,0)


def _returns_matrix(cids:list[int], start:date, end:date, conn) -> tuple[pd.DataFrame,dict[int,dict]]:
    frames=[]; meta={int(c['company_id']):c for c in companies(conn)}
    for cid in cids:
        df=_series_df(price_rows(cid,start=start,end=end,conn=conn))
        if len(df)<3: continue
        s=df.set_index('date').close.rename(cid); frames.append(s)
    if not frames: return pd.DataFrame(),meta
    prices=pd.concat(frames,axis=1).sort_index().ffill().dropna(how='all')
    # Un titre absent au début n'est pas considéré avant sa première observation.
    returns=prices.pct_change(fill_method=None).replace([np.inf,-np.inf],np.nan).fillna(0)
    return returns,meta


def _individual_drawdowns(returns:pd.DataFrame)->np.ndarray:
    vals=[]
    for c in returns.columns:
        curve=(1+returns[c]).cumprod(); dd=curve/curve.cummax()-1; vals.append(abs(float(dd.min())) or 1e-6)
    return np.array(vals)


def optimize_weights(returns:pd.DataFrame, objective:str)->np.ndarray:
    if returns.empty or returns.shape[1]<1: raise ValueError('Données insuffisantes')
    n=returns.shape[1]; mu=returns.mean().to_numpy()*252; cov=returns.cov().to_numpy()*252
    cov=np.nan_to_num(cov,nan=0.0,posinf=0.0,neginf=0.0)+np.eye(n)*1e-8
    vol=np.sqrt(np.maximum(np.diag(cov),1e-10))
    if objective in {'risk_parity','max_diversification'}:
        w=1/vol; return w/w.sum()
    if objective=='min_drawdown':
        dd=_individual_drawdowns(returns); w=1/dd; return w/w.sum()
    w=np.repeat(1/n,n)
    if objective=='min_volatility':
        # Gradient projeté long-only.
        scale=max(float(np.linalg.norm(cov,2)),1e-8); step=.15/scale
        for _ in range(900): w=_project_simplex(w-step*(2*cov@w))
        return w
    if objective=='return_risk':
        # Rendement espéré pénalisé par la variance, normalisé pour stabilité numérique.
        mus=mu/(np.std(mu)+1e-8); cs=cov/(np.mean(np.diag(cov))+1e-8)
        for _ in range(800): w=_project_simplex(w+.02*(mus-0.7*(2*cs@w)))
        return w
    # max_sharpe : ascension projetée de mu'w / sqrt(w'Cw).
    for _ in range(1200):
        cw=cov@w; var=max(float(w@cw),1e-12); sd=math.sqrt(var); m=float(mu@w)
        grad=mu/sd-(m*cw)/(sd**3)
        norm=max(float(np.linalg.norm(grad)),1.0)
        w=_project_simplex(w+.025*grad/norm)
    return w


def _metrics_from_returns(r:pd.Series)->dict:
    r=r.dropna();
    if len(r)==0: return {}
    curve=(1+r).cumprod(); total=float(curve.iloc[-1]-1); years=max(len(r)/252,1/252)
    ann=(1+total)**(1/years)-1 if total>-1 else -1; vol=float(r.std(ddof=1)*math.sqrt(252)) if len(r)>1 else 0
    dd=curve/curve.cummax()-1; downside=r[r<0].std(ddof=1)*math.sqrt(252) if (r<0).sum()>1 else np.nan
    return {'performance_pct':round(total*100,4),'annualized_pct':round(ann*100,4),'volatility_pct':round(vol*100,4),
            'max_drawdown_pct':round(float(dd.min())*100,4),'sharpe':round(float(r.mean()*252/vol),4) if vol>0 else None,
            'sortino':round(float(r.mean()*252/downside),4) if downside and np.isfinite(downside) and downside>0 else None}


def _lot_size(conn,cid:int)->int:
    row=conn.execute('SELECT lot_size FROM portfolio.instrument_rule WHERE company_id=%s',(cid,)).fetchone(); return int(row[0]) if row else 1




def _prepare_manual_allocation(data: PortfolioCreate, conn):
    seen=set(); prepared=[]; planned=0.0; invested=0.0
    meta={int(c['company_id']):c for c in companies(conn)}
    for p in data.positions:
        if p.company_id in seen:
            raise HTTPException(status_code=400, detail='Une même valeur ne peut apparaître qu’une fois dans le portefeuille.')
        seen.add(p.company_id)
        px=last_price_before(p.company_id,data.start_date,conn)
        if not px:
            raise HTTPException(status_code=400,detail=f'Aucun cours disponible à cette date pour company_id={p.company_id}.')
        pdte,price=px; lot=_lot_size(conn,p.company_id)
        if p.mode=='percent':
            target=data.total_amount*p.value/100.0
            requested_qty=target/price
        elif p.mode=='amount':
            target=p.value
            requested_qty=target/price
        else:
            requested_qty=p.value
            target=requested_qty*price
        qty=math.floor(requested_qty/lot)*lot
        if qty <= 0:
            label=meta.get(p.company_id,{}).get('ticker') or f'company_id={p.company_id}'
            raise HTTPException(status_code=400,detail=f'Allocation insuffisante pour acheter au moins un lot de {label} à cette date.')
        amount=qty*price
        planned+=target; invested+=amount
        prepared.append({
            'position':p,'lot':lot,'quantity':qty,'requested_quantity':requested_qty,
            'price':price,'amount':amount,'target':target,'price_date':pdte,
            'ticker':meta.get(p.company_id,{}).get('ticker') or meta.get(p.company_id,{}).get('short_name')
        })
    tolerance=max(1.0, data.total_amount*1e-8)
    gap=data.total_amount-planned
    balanced=abs(gap)<=tolerance
    return prepared,planned,invested,gap,balanced


@router.post('/preview')
def preview_portfolio(data:PortfolioCreate,request:Request):
    require_user(request,csrf=True)
    with db_connect() as conn:
        prepared,planned,invested,gap,balanced=_prepare_manual_allocation(data,conn)
    rows=[]
    for x in prepared:
        p=x['position']
        rows.append({
            'company_id':p.company_id,'ticker':x['ticker'],'mode':p.mode,'input_value':p.value,
            'target_amount':round(x['target'],2),'price_date':x['price_date'].isoformat(),
            'price':round(float(x['price']),4),'lot_size':int(x['lot']),
            'requested_quantity':round(float(x['requested_quantity']),6),'quantity':int(x['quantity']),
            'invested_amount':round(float(x['amount']),2),'rounding_cash':round(float(x['target']-x['amount']),2)
        })
    return {
        'balanced':balanced,'amount':round(data.total_amount,2),'planned_total':round(planned,2),
        'planning_gap':round(gap,2),'invested':round(invested,2),
        'rounding_cash':round(data.total_amount-invested,2),'rows':rows
    }

def _allocation_from_weights(conn,cids:list[int],weights:np.ndarray,amount:float,on_date:date,meta:dict)->dict:
    items=[]; spent=0.0
    for cid,w in zip(cids,weights):
        p=last_price_before(cid,on_date,conn)
        if not p: continue
        d,price=p; lot=_lot_size(conn,cid); target=amount*float(w); qty=math.floor(target/(price*lot))*lot; alloc=qty*price; spent+=alloc
        items.append({'company_id':cid,'ticker':meta.get(cid,{}).get('ticker'),'weight_pct':round(float(w)*100,4),'target_amount':round(target,2),
                      'price_date':d.isoformat(),'price':round(price,4),'lot_size':lot,'quantity':qty,'allocated_amount':round(alloc,2)})
    return {'items':items,'invested':round(spent,2),'cash':round(amount-spent,2),'amount':round(amount,2)}


@router.post('/create')
def create_portfolio(data:PortfolioCreate,request:Request):
    u=require_user(request,csrf=True)
    name=' '.join(data.name.split())
    if not name or data.start_date > date.today():
        raise HTTPException(status_code=400, detail='Indique un nom et une date de départ qui ne soit pas dans le futur.')
    with db_connect() as conn:
        if conn.execute('SELECT 1 FROM portfolio.portfolio WHERE user_id=%s AND name=%s',(u['user_id'],name)).fetchone():
            raise HTTPException(status_code=409,detail='Un portefeuille portant déjà ce nom existe. Choisis un autre nom.')
        prepared,planned,allocated,gap,balanced=_prepare_manual_allocation(data,conn)
        if not balanced:
            direction='à affecter' if gap>0 else 'en dépassement'
            raise HTTPException(status_code=400,detail=f'Le budget doit être affecté à 100 % avant arrondi aux lots/nominaux : {abs(gap):,.0f} XAF {direction}.')
        try:
            row=conn.execute('''INSERT INTO portfolio.portfolio(user_id,name,start_date,initial_amount,cash_initial)
                                VALUES(%s,%s,%s,%s,%s) RETURNING portfolio_id''',(u['user_id'],name,data.start_date,data.total_amount,data.total_amount-allocated)).fetchone()
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409,detail='Un portefeuille portant déjà ce nom existe. Choisis un autre nom.')
        pid=int(row[0])
        for x in prepared:
            p=x['position']; lot=x['lot']; qty=x['quantity']; price=x['price']; amount=x['amount']
            conn.execute('''INSERT INTO portfolio.position(portfolio_id,company_id,input_mode,input_value,lot_size,quantity,start_price,allocated_amount)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',(pid,p.company_id,p.mode,p.value,lot,qty,price,amount))
        conn.execute("INSERT INTO auth.audit_log(actor_user_id,action,target_type,target_id,metadata) VALUES(%s,'portfolio_created','portfolio',%s,'{}'::jsonb)",(u['user_id'],str(pid)))
        conn.commit()
    return {'ok':True,'portfolio_id':pid,'cash':round(data.total_amount-allocated,2)}


@router.get('/list')
def list_portfolios(request:Request, archived:bool=False):
    u=require_user(request)
    with db_connect() as conn:
        rows=conn.execute('SELECT portfolio_id,name,start_date,initial_amount,cash_initial,created_at,archived_at FROM portfolio.portfolio WHERE user_id=%s AND (archived_at IS NOT NULL)=%s ORDER BY created_at DESC',(u['user_id'],archived)).fetchall()
    return {'portfolios':[{'portfolio_id':r[0],'name':r[1],'start_date':r[2],'initial_amount':float(r[3]),'cash_initial':float(r[4]),'created_at':r[5],'archived':r[6] is not None} for r in rows]}


@router.get('/{portfolio_id}')
def portfolio_detail(portfolio_id:int,request:Request,range:str='1y',start:str|None=None,end:str|None=None):
    u=require_user(request)
    sd, ed = parse_ymd(start), parse_ymd(end)
    if (start and not sd) or (end and not ed) or (sd and ed and sd > ed):
        raise HTTPException(status_code=400, detail='La période choisie est invalide.')
    with db_connect() as conn:
        p=conn.execute('SELECT name,start_date,initial_amount,cash_initial,archived_at FROM portfolio.portfolio WHERE portfolio_id=%s AND user_id=%s',(portfolio_id,u['user_id'])).fetchone()
        if not p: raise HTTPException(status_code=404,detail='Portefeuille introuvable')
        rows=conn.execute('SELECT company_id,quantity,start_price,allocated_amount,lot_size FROM portfolio.position WHERE portfolio_id=%s ORDER BY company_id',(portfolio_id,)).fetchall()
        meta={int(c['company_id']):c for c in companies(conn)}
        positions, histories = [], {}
        for r in rows:
            cid=int(r[0])
            seed=last_price_before(cid,p[1],conn)
            positions.append({'company_id':cid,'ticker':meta.get(cid,{}).get('ticker'),
                              'name':meta.get(cid,{}).get('short_name'),
                              'quantity':float(r[1]),'start_price':float(r[2]),
                              'start_price_date':seed[0].isoformat() if seed else None,
                              'allocated_amount':float(r[3]),'lot_size':int(r[4])})
            df=_series_df(price_rows(cid,start=p[1],end=date.today(),conn=conn))
            histories[cid]=[{'date':x['date'].date().isoformat(),'close':float(x['close'])}
                            for x in df.to_dict('records')]
    detail=value_portfolio(p[1],p[2],p[3],positions,histories,
                           period_start=sd or range_start(ed or date.today(),range),
                           period_end=ed,last_only=(range in {'last','1d','day'} and not start))
    return {'portfolio_id':portfolio_id,'name':p[0],'start_date':p[1],
            'initial_amount':float(p[2]),'cash':float(p[3]),'archived':p[4] is not None,**detail}


@router.post('/{portfolio_id}/rename')
def rename_portfolio(portfolio_id:int,data:PortfolioName,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as conn:
        try:
            row=conn.execute('UPDATE portfolio.portfolio SET name=%s,updated_at=now() WHERE portfolio_id=%s AND user_id=%s RETURNING portfolio_id',
                             (data.name,portfolio_id,u['user_id'])).fetchone()
            if not row: raise HTTPException(status_code=404,detail='Portefeuille introuvable')
            conn.commit()
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409,detail='Un portefeuille portant déjà ce nom existe.')
    return {'ok':True,'portfolio_id':portfolio_id}


@router.post('/{portfolio_id}/archive')
def archive_portfolio(portfolio_id:int,data:PortfolioArchive,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as conn:
        row=conn.execute('UPDATE portfolio.portfolio SET archived_at=CASE WHEN %s THEN now() ELSE NULL END,updated_at=now() WHERE portfolio_id=%s AND user_id=%s RETURNING portfolio_id',
                         (data.archived,portfolio_id,u['user_id'])).fetchone()
        if not row: raise HTTPException(status_code=404,detail='Portefeuille introuvable')
        conn.commit()
    return {'ok':True,'archived':data.archived}


@router.post('/{portfolio_id}/duplicate')
def duplicate_portfolio(portfolio_id:int,data:PortfolioName,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as conn:
        try:
            row=conn.execute('''INSERT INTO portfolio.portfolio(user_id,name,start_date,initial_amount,cash_initial)
                SELECT user_id,%s,start_date,initial_amount,cash_initial FROM portfolio.portfolio
                WHERE portfolio_id=%s AND user_id=%s RETURNING portfolio_id''',
                (data.name,portfolio_id,u['user_id'])).fetchone()
            if not row: raise HTTPException(status_code=404,detail='Portefeuille introuvable')
            pid=int(row[0])
            conn.execute('''INSERT INTO portfolio.position(portfolio_id,company_id,input_mode,input_value,lot_size,quantity,start_price,allocated_amount)
                SELECT %s,company_id,input_mode,input_value,lot_size,quantity,start_price,allocated_amount
                FROM portfolio.position WHERE portfolio_id=%s''',(pid,portfolio_id))
            conn.commit()
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409,detail='Un portefeuille portant déjà ce nom existe.')
    return {'ok':True,'portfolio_id':pid}


@router.delete('/{portfolio_id}')
def delete_portfolio(portfolio_id:int,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as conn:
        r=conn.execute('DELETE FROM portfolio.portfolio WHERE portfolio_id=%s AND user_id=%s RETURNING portfolio_id',(portfolio_id,u['user_id'])).fetchone(); conn.commit()
    if not r: raise HTTPException(status_code=404,detail='Portefeuille introuvable')
    return {'ok':True}


@router.post('/optimize')
def optimize(data:OptimizeIn,request:Request):
    require_user(request,csrf=True)
    if data.end_date<=data.start_date: raise HTTPException(status_code=400,detail='Période invalide')
    with db_connect() as conn:
        all_ids=[int(c['company_id']) for c in companies(conn)]; cids=(data.company_ids or all_ids)[:30]
        returns,meta=_returns_matrix(cids,data.start_date,data.end_date,conn)
        # Écarte séries trop pauvres/plates.
        keep=[c for c in returns.columns if int((returns[c]!=0).sum())>=3]
        returns=returns[keep]
        if returns.shape[1]<2: raise HTTPException(status_code=400,detail='Historique insuffisant pour optimiser')
        # Limite les actifs par disponibilité/activité avant optimisation.
        activity=(returns!=0).sum().sort_values(ascending=False); keep=list(activity.head(data.max_assets).index); returns=returns[keep]
        w=optimize_weights(returns,data.objective); port=returns.mul(w,axis=1).sum(axis=1); metrics=_metrics_from_returns(port)
        alloc=_allocation_from_weights(conn,[int(x) for x in returns.columns],w,data.amount,data.start_date,meta)
    return {'mode':'retrospective','warning':'Cette allocation utilise les données de toute la période choisie : elle décrit le passé et ne constitue pas une recommandation.',
            'objective':data.objective,'start_date':data.start_date,'end_date':data.end_date,'metrics':metrics,'allocation':alloc}


@router.post('/backtest')
def backtest(data:BacktestIn,request:Request):
    require_user(request,csrf=True)
    if data.end_date<=data.start_date: raise HTTPException(status_code=400,detail='Période invalide')
    with db_connect() as conn:
        all_ids=[int(c['company_id']) for c in companies(conn)]; cids=(data.company_ids or all_ids)[:30]
        full_start=data.start_date-timedelta(days=data.lookback_days+60); returns,meta=_returns_matrix(cids,full_start,data.end_date,conn)
    if returns.shape[1]<2 or len(returns)<10: raise HTTPException(status_code=400,detail='Historique insuffisant')
    dates=returns.index; test_dates=dates[dates.date>=data.start_date]
    if len(test_dates)<2: raise HTTPException(status_code=400,detail='Aucune séance de test')
    step={'monthly':1,'quarterly':3,'yearly':12}[data.rebalance]
    rebalance=[]; last_key=None
    for d in test_dates:
        key=(d.year,(d.month-1)//step)
        if key!=last_key: rebalance.append(d); last_key=key
    value=data.amount; curve=[]; current_w=None; rebalances=[]
    for i,d in enumerate(test_dates):
        if d in rebalance:
            hist=returns[(returns.index<d)&(returns.index>=d-pd.Timedelta(days=data.lookback_days))]
            active=[c for c in hist.columns if int((hist[c]!=0).sum())>=3]
            if len(active)>=2:
                activity=(hist[active]!=0).sum().sort_values(ascending=False); active=list(activity.head(data.max_assets).index)
                w=optimize_weights(hist[active],data.objective); current_w=pd.Series(w,index=active); rebalances.append({'date':d.date().isoformat(),'weights':{str(k):round(float(v),6) for k,v in current_w.items()}})
        r=returns.loc[d]
        daily=float((r[current_w.index]*current_w).sum()) if current_w is not None else 0.0
        value*=1+daily; curve.append({'date':d.date().isoformat(),'value':round(value,2),'performance_pct':round((value/data.amount-1)*100,4)})
    s=pd.Series([0]+[(curve[i]['value']/curve[i-1]['value']-1) for i in range(1,len(curve))])
    return {'mode':'walk_forward','objective':data.objective,'rebalance':data.rebalance,'lookback_days':data.lookback_days,
            'metrics':_metrics_from_returns(s),'curve':curve,'rebalances':rebalances,
            'note':'Chaque réallocation n’utilise que les données antérieures à sa date de calcul.'}
