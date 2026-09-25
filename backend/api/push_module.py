from __future__ import annotations

import hashlib
import os

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from auth_module import require_user
from common import db_connect

router = APIRouter(prefix='/api/v3/push', tags=['push'])
VAPID_PUBLIC_KEY = os.environ.get('BVMAC_VAPID_PUBLIC_KEY', '').strip()


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=40, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=4096)
    keys: PushKeys


class PushUnsubscribeIn(BaseModel):
    endpoint: str = Field(min_length=20, max_length=4096)


@router.get('/status')
def status(request: Request):
    user = require_user(request)
    with db_connect() as conn:
        count = int(conn.execute(
            'SELECT count(*) FROM auth.push_subscription WHERE user_id=%s AND disabled_at IS NULL',
            (user['user_id'],),
        ).fetchone()[0])
    return {
        'available': bool(VAPID_PUBLIC_KEY),
        'public_key': VAPID_PUBLIC_KEY,
        'subscription_count': count,
    }


@router.post('/subscription')
def subscribe(data: PushSubscriptionIn, request: Request):
    user = require_user(request, csrf=True)
    if not VAPID_PUBLIC_KEY:
        raise HTTPException(status_code=503, detail='Notifications push non configurées sur le serveur')
    endpoint = data.endpoint.strip()
    if not endpoint.startswith('https://'):
        raise HTTPException(status_code=400, detail='Abonnement push invalide')
    ua_hash = hashlib.sha256((request.headers.get('user-agent') or '').encode()).hexdigest()
    with db_connect() as conn:
        conn.execute('UPDATE auth.push_subscription SET disabled_at=now(),updated_at=now() WHERE endpoint=%s AND user_id<>%s',
                     (endpoint,user['user_id']))
        conn.execute(
            '''INSERT INTO auth.push_subscription(user_id,endpoint,p256dh,auth_secret,user_agent_hash,disabled_at,updated_at)
               VALUES(%s,%s,%s,%s,%s,NULL,now())
               ON CONFLICT(user_id,endpoint) DO UPDATE SET p256dh=EXCLUDED.p256dh,auth_secret=EXCLUDED.auth_secret,
                 user_agent_hash=EXCLUDED.user_agent_hash,disabled_at=NULL,failure_count=0,updated_at=now()''',
            (user['user_id'], endpoint, data.keys.p256dh, data.keys.auth, ua_hash),
        )
        conn.commit()
    return {'ok': True}


class InboxRead(BaseModel):
    through_id: int = Field(ge=1)


@router.get('/inbox')
def inbox(request: Request, before: int | None = Query(default=None, ge=1),
          limit: int = Query(default=30, ge=1, le=100)):
    user=require_user(request)
    with db_connect() as conn:
        rows=conn.execute('''SELECT notification_id,title,body,target_url,category,created_at,read_at
                            FROM auth.notification_inbox
                            WHERE user_id=%s AND (%s::bigint IS NULL OR notification_id<%s)
                            ORDER BY notification_id DESC LIMIT %s''',
                          (user['user_id'],before,before,limit+1)).fetchall()
        unread=int(conn.execute('SELECT count(*) FROM auth.notification_inbox WHERE user_id=%s AND read_at IS NULL',
                               (user['user_id'],)).fetchone()[0])
    return {'unread':unread,'items':[dict(notification_id=r[0],title=r[1],body=r[2],url=r[3],category=r[4],created_at=r[5],read=bool(r[6])) for r in rows[:limit]],
            'next_before':rows[limit-1][0] if len(rows)>limit else None}


@router.post('/inbox/read')
def read_inbox(data:InboxRead,request:Request):
    user=require_user(request,csrf=True)
    with db_connect() as conn:
        conn.execute('UPDATE auth.notification_inbox SET read_at=now() WHERE user_id=%s AND notification_id<=%s AND read_at IS NULL',
                     (user['user_id'],data.through_id))
        conn.commit()
    return {'ok':True}


@router.post('/unsubscribe')
def unsubscribe(data: PushUnsubscribeIn, request: Request):
    user = require_user(request, csrf=True)
    with db_connect() as conn:
        conn.execute(
            'UPDATE auth.push_subscription SET disabled_at=now(),updated_at=now() WHERE user_id=%s AND endpoint=%s',
            (user['user_id'], data.endpoint.strip()),
        )
        conn.commit()
    return {'ok': True}
