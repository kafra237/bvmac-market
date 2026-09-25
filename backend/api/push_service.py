from __future__ import annotations

import json
import os
from pathlib import Path

try:
    from pywebpush import WebPushException, webpush
except Exception:  # pywebpush est installé par deploy/update.
    WebPushException = Exception
    webpush = None

VAPID_PRIVATE_KEY = os.environ.get('BVMAC_VAPID_PRIVATE_KEY', '/etc/bvmac/vapid_private.pem')
VAPID_SUBJECT = os.environ.get('BVMAC_VAPID_SUBJECT', 'mailto:admin@example.com')

SETTING_COLUMNS = {
    'market': 'market_digest',
    'alert': 'alerts',
    'screener': 'screeners',
    'opcvm': 'opcvm',
    'portfolio': 'portfolio',
    'feed_avis': 'feeds_avis',
    'feed_communique': 'feeds_communique',
    'platform': 'platform_updates',
    'communication': 'admin_communications',
    'weekly': 'weekly_digest',
}


def global_enabled(conn, category: str) -> bool:
    col = SETTING_COLUMNS.get(category)
    if not col:
        return True
    row = conn.execute(f'SELECT {col} FROM admin.notification_settings WHERE singleton=true').fetchone()
    return True if not row else bool(row[0])


def subscribed_users(conn) -> list[int]:
    return [int(r[0]) for r in conn.execute(
        "SELECT user_id FROM auth.user_account WHERE status='active' AND email_verified_at IS NOT NULL"
    ).fetchall()]


def _reserve_delivery(conn, *, user_id: int, subscription_id: int, event_key: str,
                      category: str, title: str, body: str, target_url: str) -> int | None:
    row = conn.execute(
        '''INSERT INTO auth.notification_delivery
           (user_id,subscription_id,event_key,category,title,body,target_url,status)
           VALUES(%s,%s,%s,%s,%s,%s,%s,'pending')
           ON CONFLICT(user_id,subscription_id,event_key) DO NOTHING
           RETURNING delivery_id''',
        (user_id, subscription_id, event_key, category, title, body, target_url),
    ).fetchone()
    conn.commit()
    return int(row[0]) if row else None


def push_user(conn, *, user_id: int, category: str, event_key: str,
              title: str, body: str, target_url: str = '/app') -> int:
    if not global_enabled(conn, category):
        return 0
    # Inbox delivery is independent of browser subscriptions and push-provider TTL.
    conn.execute('''INSERT INTO auth.notification_inbox(user_id,event_key,category,title,body,target_url)
                    VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,event_key) DO NOTHING''',
                 (user_id,event_key,category,title,body,target_url))
    conn.commit()
    subscriptions = conn.execute(
        '''SELECT subscription_id,endpoint,p256dh,auth_secret
           FROM auth.push_subscription WHERE user_id=%s AND disabled_at IS NULL''',
        (user_id,),
    ).fetchall()
    sent = 0
    for sub_id, endpoint, p256dh, auth_secret in subscriptions:
        delivery_id = _reserve_delivery(
            conn, user_id=user_id, subscription_id=int(sub_id), event_key=event_key,
            category=category, title=title, body=body, target_url=target_url,
        )
        if not delivery_id:
            continue
        if webpush is None or not Path(VAPID_PRIVATE_KEY).is_file():
            conn.execute(
                "UPDATE auth.notification_delivery SET status='failed',error=%s,attempted_at=now() WHERE delivery_id=%s",
                ('Web Push non configuré sur le serveur', delivery_id),
            )
            conn.commit()
            continue
        payload = json.dumps({
            'title': title, 'body': body, 'url': target_url,
            'tag': event_key, 'category': category,
        }, ensure_ascii=False)
        try:
            webpush(
                subscription_info={'endpoint': endpoint, 'keys': {'p256dh': p256dh, 'auth': auth_secret}},
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={'sub': VAPID_SUBJECT},
                ttl=2419200,
            )
            conn.execute(
                "UPDATE auth.notification_delivery SET status='sent',error=NULL,attempted_at=now() WHERE delivery_id=%s",
                (delivery_id,),
            )
            conn.execute(
                '''UPDATE auth.push_subscription SET last_success_at=now(),failure_count=0,last_failure_at=NULL,updated_at=now()
                   WHERE subscription_id=%s''', (sub_id,),
            )
            conn.commit()
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            err = f'{type(exc).__name__}: {str(exc)[:500]}'
            conn.execute(
                "UPDATE auth.notification_delivery SET status='failed',error=%s,attempted_at=now() WHERE delivery_id=%s",
                (err, delivery_id),
            )
            if status in (404, 410):
                conn.execute(
                    '''UPDATE auth.push_subscription SET disabled_at=now(),last_failure_at=now(),failure_count=failure_count+1,updated_at=now()
                       WHERE subscription_id=%s''', (sub_id,),
                )
            else:
                conn.execute(
                    '''UPDATE auth.push_subscription SET last_failure_at=now(),failure_count=failure_count+1,updated_at=now()
                       WHERE subscription_id=%s''', (sub_id,),
                )
            conn.commit()
        except Exception as exc:
            err = f'{type(exc).__name__}: {str(exc)[:500]}'
            conn.execute(
                "UPDATE auth.notification_delivery SET status='failed',error=%s,attempted_at=now() WHERE delivery_id=%s",
                (err, delivery_id),
            )
            conn.execute(
                '''UPDATE auth.push_subscription SET last_failure_at=now(),failure_count=failure_count+1,updated_at=now()
                   WHERE subscription_id=%s''', (sub_id,),
            )
            conn.commit()
    return sent


def broadcast_all(conn, *, category: str, event_key: str, title: str, body: str,
                  target_url: str = '/app') -> int:
    if not global_enabled(conn, category):
        return 0
    return sum(push_user(
        conn, user_id=user_id, category=category, event_key=event_key,
        title=title, body=body, target_url=target_url,
    ) for user_id in subscribed_users(conn))
