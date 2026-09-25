from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response

from auth_module import optional_user
from common import COOKIE_SECURE, db_connect, require_same_origin, utcnow

router = APIRouter(prefix="/api/v3/analytics", tags=["analytics"])
VISITOR_COOKIE = "__Host-bvmac_vid" if COOKIE_SECURE else "bvmac_vid"
VISITOR_MAX_AGE = 400 * 24 * 3600
SAFE_EVENT = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
BOT_RE = re.compile(r"bot|crawler|spider|slurp|headless|lighthouse|preview", re.I)


def _uuid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except Exception:
        return None


def _clean(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_properties(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in list(value.items())[:40]:
        key = _clean(k, 64)
        if not key:
            continue
        if isinstance(v, bool) or v is None:
            out[key] = v
        elif isinstance(v, (int, float)):
            out[key] = v
        elif isinstance(v, str):
            # Ne jamais accepter de secrets évidents dans l'analytics.
            if any(x in key.lower() for x in ("password", "passwd", "secret", "token", "email", "phone", "telephone")):
                continue
            out[key] = _clean(v, 300)
        elif isinstance(v, (list, tuple)):
            out[key] = [_clean(x, 100) for x in list(v)[:20]]
    return out


def _int(value: Any) -> int | None:
    try:
        n = int(value)
        return n if 0 <= n <= 100000 else None
    except Exception:
        return None

def _context(value: Any, request: Request) -> dict[str, Any]:
    c = value if isinstance(value, dict) else {}
    ref = _clean(c.get("referrer"), 500)
    ref_domain = None
    if ref:
        try:
            ref_domain = urlparse(ref).netloc[:200] or None
        except Exception:
            pass
    return {
        "device_type": _clean(c.get("device_type"), 30) or None,
        "os_family": _clean(c.get("os_family"), 50) or None,
        "browser_family": _clean(c.get("browser_family"), 50) or None,
        "language": _clean(c.get("language"), 30) or None,
        "timezone": _clean(c.get("timezone"), 80) or None,
        "screen_width": _int(c.get("screen_width")),
        "screen_height": _int(c.get("screen_height")),
        "viewport_width": _int(c.get("viewport_width")),
        "viewport_height": _int(c.get("viewport_height")),
        "connection_type": _clean(c.get("connection_type"), 30) or None,
        "referrer_domain": ref_domain,
        "landing_page": _clean(c.get("landing_page"), 300) or None,
        "user_agent": _clean(request.headers.get("user-agent"), 500) or None,
    }


def _set_visitor_cookie(response: Response, visitor_id: str) -> None:
    response.set_cookie(
        VISITOR_COOKIE,
        visitor_id,
        max_age=VISITOR_MAX_AGE,
        expires=VISITOR_MAX_AGE,
        secure=COOKIE_SECURE,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.post("/events")
async def events(request: Request, response: Response):
    require_same_origin(request)
    ua = request.headers.get("user-agent") or ""
    if BOT_RE.search(ua):
        return Response(status_code=204)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON invalide")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload invalide")

    raw_events = payload.get("events")
    if not isinstance(raw_events, list) or not raw_events:
        return Response(status_code=204)
    raw_events = raw_events[:50]

    visitor_id = _uuid(request.cookies.get(VISITOR_COOKIE))
    new_visitor = visitor_id is None
    if new_visitor:
        visitor_id = str(UUID(bytes=secrets.token_bytes(16), version=4))
    session_id = _uuid(payload.get("session_id"))
    if not session_id:
        raise HTTPException(status_code=400, detail="Session analytics invalide")

    ctx = _context(payload.get("context"), request)
    user = optional_user(request)
    user_id = int(user["user_id"]) if user else None
    now = utcnow()

    parsed: list[dict[str, Any]] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        name = _clean(raw.get("event_name"), 64)
        if not SAFE_EVENT.fullmatch(name):
            continue
        when = now
        try:
            txt = str(raw.get("occurred_at") or "").replace("Z", "+00:00")
            x = datetime.fromisoformat(txt)
            if x.tzinfo is None:
                x = x.replace(tzinfo=timezone.utc)
            # refuse les événements très anciens/futurs
            if abs((now - x.astimezone(timezone.utc)).total_seconds()) <= 86400:
                when = x.astimezone(timezone.utc)
        except Exception:
            pass
        parsed.append({
            "name": name,
            "when": when,
            "path": _clean(raw.get("page"), 300) or "/",
            "entity_type": _clean(raw.get("entity_type"), 40) or None,
            "entity_id": _clean(raw.get("entity_id"), 100) or None,
            "properties": _safe_properties(raw.get("properties")),
        })
    if not parsed:
        return Response(status_code=204)

    with db_connect() as conn:
        conn.execute(
            """INSERT INTO webstats.browser_visitor(visitor_id,first_seen_at,last_seen_at,last_user_id)
               VALUES(%s,%s,%s,%s)
               ON CONFLICT(visitor_id) DO UPDATE SET last_seen_at=EXCLUDED.last_seen_at,
                 last_user_id=COALESCE(EXCLUDED.last_user_id,webstats.browser_visitor.last_user_id)""",
            (visitor_id, now, now, user_id),
        )
        existed = conn.execute("SELECT 1 FROM webstats.browser_session WHERE session_id=%s", (session_id,)).fetchone()
        conn.execute(
            """INSERT INTO webstats.browser_session(
                 session_id,visitor_id,user_id,started_at,last_seen_at,landing_page,referrer_domain,
                 device_type,os_family,browser_family,language,timezone,screen_width,screen_height,
                 viewport_width,viewport_height,connection_type,user_agent)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(session_id) DO UPDATE SET last_seen_at=GREATEST(webstats.browser_session.last_seen_at,EXCLUDED.last_seen_at),
                 user_id=COALESCE(EXCLUDED.user_id,webstats.browser_session.user_id),
                 viewport_width=COALESCE(EXCLUDED.viewport_width,webstats.browser_session.viewport_width),
                 viewport_height=COALESCE(EXCLUDED.viewport_height,webstats.browser_session.viewport_height),
                 connection_type=COALESCE(EXCLUDED.connection_type,webstats.browser_session.connection_type)""",
            (session_id, visitor_id, user_id, min(e["when"] for e in parsed), now, ctx["landing_page"], ctx["referrer_domain"],
             ctx["device_type"], ctx["os_family"], ctx["browser_family"], ctx["language"], ctx["timezone"],
             ctx["screen_width"], ctx["screen_height"], ctx["viewport_width"], ctx["viewport_height"],
             ctx["connection_type"], ctx["user_agent"]),
        )
        if not existed:
            conn.execute("UPDATE webstats.browser_visitor SET sessions_count=sessions_count+1 WHERE visitor_id=%s", (visitor_id,))
        for e in parsed:
            conn.execute(
                """INSERT INTO webstats.browser_event(visitor_id,session_id,user_id,occurred_at,event_name,path,entity_type,entity_id,properties)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (visitor_id, session_id, user_id, e["when"], e["name"], e["path"], e["entity_type"], e["entity_id"],
                 json.dumps(e["properties"], ensure_ascii=False)),
            )
        conn.commit()

    if new_visitor:
        _set_visitor_cookie(response, visitor_id)
    response.status_code = 204
    return response


@router.post("/reset-visitor")
def reset_visitor(request: Request, response: Response):
    """Outil de support : réinitialise uniquement l'identifiant analytics du navigateur."""
    require_same_origin(request)
    response.delete_cookie(VISITOR_COOKIE, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
    return {"ok": True}
