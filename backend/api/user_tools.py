from __future__ import annotations
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from auth_module import require_user
from common import db_connect

router=APIRouter(prefix='/api/v3/user-tools',tags=['user-tools'])

class WatchIn(BaseModel): company_id:int
class ScreenIn(BaseModel): name:str=Field(min_length=1,max_length=80); filters:dict
class AlertIn(BaseModel):
    kind:str=Field(pattern=r'^(price_above|price_below|volume_above|new_import|quality_error)$')
    company_id:int|None=None
    threshold:float|None=None

@router.get('/watchlist')
def watchlist(request:Request):
    u=require_user(request)
    with db_connect() as c: rows=c.execute('SELECT company_id,created_at FROM auth.watchlist WHERE user_id=%s ORDER BY created_at',(u['user_id'],)).fetchall()
    return {'items':[{'company_id':r[0],'created_at':r[1]} for r in rows]}

@router.post('/watchlist')
def watch_add(data:WatchIn,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as c: c.execute('INSERT INTO auth.watchlist(user_id,company_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',(u['user_id'],data.company_id));c.commit()
    return {'ok':True}

@router.delete('/watchlist/{company_id}')
def watch_del(company_id:int,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as c:c.execute('DELETE FROM auth.watchlist WHERE user_id=%s AND company_id=%s',(u['user_id'],company_id));c.commit()
    return {'ok':True}

@router.get('/screens')
def screens(request:Request):
    u=require_user(request)
    with db_connect() as c: rows=c.execute('SELECT screen_id,name,filters,created_at FROM portfolio.saved_screen WHERE user_id=%s ORDER BY created_at DESC',(u['user_id'],)).fetchall()
    return {'screens':[{'screen_id':r[0],'name':r[1],'filters':r[2],'created_at':r[3]} for r in rows]}

@router.post('/screens')
def screen_save(data:ScreenIn,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as c:
        r=c.execute('''INSERT INTO portfolio.saved_screen(user_id,name,filters,notify) VALUES(%s,%s,%s::jsonb,true)
          ON CONFLICT(user_id,name) DO UPDATE SET filters=EXCLUDED.filters,notify=true RETURNING screen_id''',(u['user_id'],data.name,__import__('json').dumps(data.filters))).fetchone();c.commit()
    return {'ok':True,'screen_id':r[0]}

@router.get('/alerts')
def alerts(request:Request):
    u=require_user(request)
    with db_connect() as c:
        rules=c.execute('SELECT rule_id,company_id,kind,threshold,active,created_at FROM auth.alert_rule WHERE user_id=%s ORDER BY created_at DESC',(u['user_id'],)).fetchall()
        events=c.execute('SELECT event_id,rule_id,message,created_at,read_at FROM auth.alert_event WHERE user_id=%s ORDER BY created_at DESC LIMIT 100',(u['user_id'],)).fetchall()
    return {'rules':[{'rule_id':r[0],'company_id':r[1],'kind':r[2],'threshold':r[3],'active':r[4],'created_at':r[5]} for r in rules],
            'events':[{'event_id':r[0],'rule_id':r[1],'message':r[2],'created_at':r[3],'read_at':r[4]} for r in events]}

@router.post('/alerts')
def alert_add(data:AlertIn,request:Request):
    u=require_user(request,csrf=True)
    if data.kind in {'price_above','price_below','volume_above'} and (data.company_id is None or data.threshold is None):
        raise HTTPException(status_code=400,detail='Valeur et seuil requis')
    with db_connect() as c:
        r=c.execute('INSERT INTO auth.alert_rule(user_id,company_id,kind,threshold) VALUES(%s,%s,%s,%s) RETURNING rule_id',(u['user_id'],data.company_id,data.kind,data.threshold)).fetchone();c.commit()
    return {'ok':True,'rule_id':r[0]}

@router.delete('/alerts/{rule_id}')
def alert_del(rule_id:int,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as c:c.execute('DELETE FROM auth.alert_rule WHERE rule_id=%s AND user_id=%s',(rule_id,u['user_id']));c.commit()
    return {'ok':True}

@router.post('/alerts/events/{event_id}/read')
def alert_read(event_id:int,request:Request):
    u=require_user(request,csrf=True)
    with db_connect() as c:c.execute('UPDATE auth.alert_event SET read_at=now() WHERE event_id=%s AND user_id=%s',(event_id,u['user_id']));c.commit()
    return {'ok':True}
