#!/usr/bin/env python3
"""API BVMAC : lecture PostgreSQL du snapshot Excel courant.

L'ETL historique continue de produire bvmac_master.xlsx. Un module d'import
séparé historise chaque version du classeur dans PostgreSQL. Cette API ne
recalcule ni ne corrige les données métier : elle expose les valeurs du dernier
snapshot importé, y compris les valeurs ajustées par l'ETL existant.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from email_auth import router as email_router
from admin_security import router as admin_security_router
from launch_support import router as launch_router, guard
from auth_module import router as auth_router
from market import router as market_router
from portfolio_module import router as portfolio_router
from feedback_module import router as feedback_router
from user_tools import router as user_tools_router
from admin_module import router as admin_router
from browser_analytics import router as browser_analytics_router
from push_module import router as push_router
from feed_module import router as feed_router
from weekly_email import router as weekly_email_router
from ml_radar import router as ml_radar_router

REPORT_FILE = Path(os.environ.get(
    "BVMAC_DOWNLOAD_REPORT",
    "/var/lib/bvmac/output/bvmac_download_report.json",
))
DB_DSN = os.environ.get(
    "BVMAC_DB_DSN",
    "dbname=bvmac user=bvmacapi host=/var/run/postgresql",
)
RECENT_DAYS = max(30, min(int(os.environ.get("BVMAC_RECENT_DAYS", "120")), 365))

app = FastAPI(title="BVMAC API", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)
app.middleware("http")(guard)
app.include_router(launch_router)
app.include_router(email_router)
app.include_router(admin_security_router)
app.include_router(auth_router)
app.include_router(market_router)
app.include_router(portfolio_router)
app.include_router(feedback_router)
app.include_router(user_tools_router)
app.include_router(admin_router)
app.include_router(browser_analytics_router)
app.include_router(push_router)
app.include_router(feed_router)
app.include_router(weekly_email_router)
app.include_router(ml_radar_router)

logger = logging.getLogger('bvmac.api')


@app.exception_handler(psycopg.errors.UniqueViolation)
async def duplicate_value(request:Request,exc:Exception):
    return JSONResponse(status_code=409,content={'detail':'Cette information est déjà utilisée par un compte ou un enregistrement.'},headers={'Cache-Control':'no-store'})

@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    # Ne jamais renvoyer une traceback/HTML technique au navigateur. Les détails
    # restent dans journalctl et l'utilisateur reçoit un identifiant d'incident.
    incident = secrets.token_hex(4)
    logger.exception('incident=%s method=%s path=%s', incident, request.method, request.url.path, exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={'detail': f'Erreur interne du serveur (incident {incident}). Réessaie dans quelques instants.'},
        headers={'Cache-Control': 'no-store'},
    )

_lock = threading.RLock()
_cache: dict[str, Any] = {
    "signature": None,
    "body": None,
    "etag": None,
    "updated_at": None,
    "last_modified": None,
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def db_connect() -> psycopg.Connection:
    return psycopg.connect(DB_DSN, connect_timeout=4, options="-c statement_timeout=30000")


def _scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return None if not math.isfinite(value) else round(value, 6)
    return value


def _rows_for_sheet(conn: psycopg.Connection, sheet_name: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT data FROM market.current_excel_row WHERE sheet_name=%s ORDER BY row_number",
        (sheet_name,),
    ).fetchall()
    return [dict(row[0]) for row in rows]


def _keep(rows: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    return [
        {column: _scalar(row.get(column)) for column in columns if column in row}
        for row in rows
    ]


def _date_id(value: Any) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    try:
        datetime.strptime(str(number), "%Y%m%d")
    except ValueError:
        return None
    return number if 19000101 <= number <= 29991231 else None


def _public_dates(prices: list[dict[str, Any]]) -> list[int]:
    ids = sorted({
        date_id
        for row in prices
        if (date_id := _date_id(row.get("bulletin_date_id"))) is not None
    })
    if not ids:
        return []
    latest = datetime.strptime(str(ids[-1]), "%Y%m%d").date()
    cutoff = latest - timedelta(days=RECENT_DAYS)
    cutoff_id = int(cutoff.strftime("%Y%m%d"))
    recent = [value for value in ids if value >= cutoff_id]
    monthly: dict[int, int] = {}
    for value in ids:
        if value < cutoff_id:
            monthly[value // 100] = max(value, monthly.get(value // 100, 0))
    return sorted(set(monthly.values()) | set(recent))


def _filter_dates(rows: list[dict[str, Any]], column: str, allowed: set[int]) -> list[dict[str, Any]]:
    if not allowed:
        return []
    return [row for row in rows if _date_id(row.get(column)) in allowed]


def _publication_status() -> dict[str, Any]:
    if not REPORT_FILE.is_file():
        return {
            "publication_grace_days": 10,
            "pending_publication_dates": [],
            "missing_published_dates": [],
            "download_failures": [],
        }
    try:
        raw = json.loads(REPORT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "publication_grace_days": 10,
            "pending_publication_dates": [],
            "missing_published_dates": [],
            "download_failures": [],
        }
    failures = raw.get("download_failures") or []
    return {
        "report_generated_at": raw.get("generated_at"),
        "publication_grace_days": int(raw.get("publication_grace_days") or 10),
        "pending_publication_dates": list(raw.get("pending_publication_dates") or [])[-30:],
        "missing_published_dates": list(raw.get("missing_published_dates") or [])[-30:],
        "download_failures": [item.get("date") for item in failures if isinstance(item, dict)][-30:],
    }


def _current_import(conn: psycopg.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT import_id, source_sha256, source_mtime, imported_at, completed_at, row_count
        FROM market.current_import
        """
    ).fetchone()
    if not row:
        return None
    return {
        "import_id": int(row[0]),
        "source_sha256": row[1],
        "source_mtime": row[2],
        "imported_at": row[3],
        "completed_at": row[4],
        "row_count": int(row[5] or 0),
    }


def _build_payload() -> tuple[bytes, str, str, datetime]:
    with db_connect() as conn:
        current = _current_import(conn)
        if not current:
            raise HTTPException(status_code=503, detail="Données en attente d'import PostgreSQL")

        companies = _rows_for_sheet(conn, "dim_company")
        funds = _rows_for_sheet(conn, "dim_opcvm")
        prices_all = _rows_for_sheet(conn, "fact_prices")
        caps_all = _rows_for_sheet(conn, "fact_market_cap")
        index_all = _rows_for_sheet(conn, "fact_index")
        nav_all = _rows_for_sheet(conn, "fact_opcvm_nav")
        financials = _rows_for_sheet(conn, "fact_financials")

    dates = _public_dates(prices_all)
    allowed = set(dates)

    companies = _keep(companies, [
        "company_id", "isin", "ticker", "short_name", "full_name",
        "country", "sector", "listing_date", "currency",
    ])
    funds = _keep(funds, [
        "fund_id", "fund_name", "manager", "custodian", "category",
        "valuation_frequency", "initial_value", "inception_date_id",
    ])
    prices = _keep(_filter_dates(prices_all, "bulletin_date_id", allowed), [
        "company_id", "bulletin_date_id", "session_date_id", "status",
        "prev_price", "open_price", "close_price", "upper_limit",
        "lower_limit", "vol_bid", "vol_ask", "vol_traded", "value_traded",
        "num_transactions", "variation_pct", "daily_return_pct",
        "next_ref_price", "ytd_high", "ytd_low", "yoy_variation_pct",
        "last_div_amount", "last_div_date_id",
    ])
    caps = _keep(_filter_dates(caps_all, "date_id", allowed), [
        "company_id", "date_id", "close_price", "float_shares",
        "total_shares", "float_market_cap", "total_market_cap",
        "last_div_amount", "last_div_year", "last_div_date_id",
        "liquidity_pct", "eps", "per",
    ])
    index = _keep(_filter_dates(index_all, "date_id", allowed), [
        "date_id", "index_name", "index_value", "variation_day_pct",
    ])
    nav = _keep(_filter_dates(nav_all, "bulletin_date_id", allowed), [
        "fund_id", "bulletin_date_id", "nav", "nav_date_id", "prev_nav",
        "prev_nav_date_id", "var_prev_pct", "var_inception_pct",
    ])
    financials = _keep(financials, [
        "company_id", "fiscal_year", "total_bilan", "capitaux_propres",
        "chiffre_affaires", "valeur_ajoutee", "resultat_net",
        "dividende_unitaire", "taux_rendement_brut_pct", "roe_publie_pct",
    ])

    completed = current["completed_at"] or current["imported_at"] or datetime.now(timezone.utc)
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    updated_at = completed.astimezone().isoformat()
    payload = {
        "meta": {
            "updated_at": updated_at,
            "recent_days": RECENT_DAYS,
            "history_policy": "quotidien_recent_et_mensuel_historique",
            "sessions": len(dates),
            "storage": "postgresql_from_excel",
            "excel_snapshot": {
                "import_id": current["import_id"],
                "sha256": current["source_sha256"],
                "row_count": current["row_count"],
            },
            "publication": _publication_status(),
        },
        "societes": companies,
        "fonds": funds,
        "prix": prices,
        "capi": caps,
        "indice": index,
        "vl": nav,
        "fin": financials,
        "qualite": [],
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    etag = '"' + hashlib.sha256(body).hexdigest()[:24] + '"'
    return body, etag, updated_at, completed.astimezone(timezone.utc)


def _signature() -> tuple[int, int | None]:
    with db_connect() as conn:
        current = _current_import(conn)
    if not current:
        raise HTTPException(status_code=503, detail="Données en attente d'import PostgreSQL")
    report_mtime = REPORT_FILE.stat().st_mtime_ns if REPORT_FILE.is_file() else None
    return current["import_id"], report_mtime


def _cached_fallback():
    payload=json.loads(_cache['body'])
    payload['meta']['served_from_cache']=True
    body=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    return body, '"'+hashlib.sha256(body).hexdigest()[:24]+'"', _cache['updated_at'], _cache['last_modified']

def _current_payload() -> tuple[bytes, str, str, datetime]:
    try:
        signature = _signature()
    except Exception as exc:
        with _lock:
            if _cache["body"] is not None:
                return _cached_fallback()
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=503, detail="PostgreSQL temporairement indisponible") from exc

    with _lock:
        if _cache["body"] is not None and _cache["signature"] == signature:
            return _cache["body"], _cache["etag"], _cache["updated_at"], _cache["last_modified"]
        try:
            body, etag, updated_at, last_modified = _build_payload()
        except Exception as exc:
            if _cache["body"] is not None:
                return _cached_fallback()
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(status_code=503, detail="Données en cours de mise à jour") from exc
        _cache.update(
            signature=signature,
            body=body,
            etag=etag,
            updated_at=updated_at,
            last_modified=last_modified,
        )
        return body, etag, updated_at, last_modified


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        with db_connect() as conn:
            current = _current_import(conn)
            db_ok = conn.execute("SELECT 1").fetchone()[0] == 1
            push_schema = bool(conn.execute("SELECT to_regclass('auth.push_subscription') IS NOT NULL AND to_regclass('admin.notification_settings') IS NOT NULL").fetchone()[0])
            feeds_schema = bool(conn.execute("SELECT to_regclass('market.bvmac_communication') IS NOT NULL AND to_regclass('admin.feed_monitor') IS NOT NULL").fetchone()[0])
            ml_schema = bool(conn.execute("SELECT to_regclass('ml.prediction_run') IS NOT NULL AND to_regclass('ml.action_prediction') IS NOT NULL AND to_regclass('ml.opcvm_prediction') IS NOT NULL").fetchone()[0])
            ml_run = conn.execute("SELECT run_id,data_through FROM ml.latest_run").fetchone() if ml_schema else None
    except Exception:
        current = None
        db_ok = False
        push_schema = False
        feeds_schema = False
        ml_schema = False
        ml_run = None
    vapid_private = Path(os.environ.get('BVMAC_VAPID_PRIVATE_KEY', '/etc/bvmac/vapid_private.pem'))
    push_configured = bool(os.environ.get('BVMAC_VAPID_PUBLIC_KEY','').strip()) and vapid_private.is_file()
    return {
        "status": "ok" if db_ok and current else "waiting_for_data",
        "postgresql": db_ok,
        "current_import_id": current["import_id"] if current else None,
        "current_import_rows": current["row_count"] if current else 0,
        "push_schema": push_schema,
        "feeds_schema": feeds_schema,
        "ml_schema": ml_schema,
        "ml_run_id": int(ml_run[0]) if ml_run else None,
        "ml_data_through": ml_run[1].isoformat() if ml_run and ml_run[1] else None,
        "push_configured": push_configured,
    }


@app.get("/api/landing")
def landing_data() -> dict[str, Any]:
    """Petit aperçu public : dernière séance actions + dernière VL de chaque OPCVM."""
    body, _etag, updated_at, _last_modified = _current_payload()
    payload = json.loads(body)

    companies = {str(x.get("company_id")): x for x in payload.get("societes", [])}
    prices = payload.get("prix", [])
    session_ids = []
    for row in prices:
        try:
            session_ids.append(int(row.get("bulletin_date_id")))
        except (TypeError, ValueError):
            pass
    last_session = max(session_ids) if session_ids else None
    actions = []
    if last_session is not None:
        for row in prices:
            try:
                if int(row.get("bulletin_date_id")) != last_session:
                    continue
            except (TypeError, ValueError):
                continue
            company = companies.get(str(row.get("company_id")), {})
            actions.append({
                "ticker": company.get("ticker"),
                "name": company.get("short_name") or company.get("full_name"),
                "close_price": row.get("close_price"),
                "variation_pct": row.get("variation_pct"),
            })
    actions.sort(key=lambda x: (str(x.get("ticker") or ""), str(x.get("name") or "")))

    funds = {str(x.get("fund_id")): x for x in payload.get("fonds", [])}
    latest_nav: dict[str, dict[str, Any]] = {}
    for row in payload.get("vl", []):
        key = str(row.get("fund_id"))
        current = latest_nav.get(key)
        def nav_key(item):
            try:
                return int(item.get("nav_date_id") or item.get("bulletin_date_id") or 0)
            except (TypeError, ValueError):
                return 0
        if current is None or nav_key(row) > nav_key(current):
            latest_nav[key] = row
    opcvm = []
    for key, row in latest_nav.items():
        fund = funds.get(key, {})
        opcvm.append({
            "name": fund.get("fund_name") or f"OPCVM {key}",
            "nav": row.get("nav"),
            "nav_date_id": row.get("nav_date_id"),
            "bulletin_date_id": row.get("bulletin_date_id"),
            "variation_pct": row.get("var_prev_pct"),
        })
    opcvm.sort(key=lambda x: str(x.get("name") or ""))

    return {
        "meta": {"updated_at": updated_at, "last_session": last_session},
        "actions": actions,
        "opcvm": opcvm,
    }


@app.get("/api/public-data")
def public_data(request: Request) -> Response:
    from auth_module import require_user
    require_user(request)
    body, etag, updated_at, last_modified = _current_payload()
    headers = {
        "Cache-Control": "private, no-cache",
        "ETag": etag,
        "Last-Modified": format_datetime(last_modified, usegmt=True),
        "Vary": "Accept-Encoding, Cookie",
        "X-BVMAC-Data-Updated": updated_at,
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json; charset=utf-8", headers=headers)
