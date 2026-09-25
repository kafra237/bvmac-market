"""Shared backend primitives: PostgreSQL access, encryption, hashing, origin checks, date parsing and safe JSON helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request

DB_DSN = os.environ.get("BVMAC_DB_DSN", "dbname=bvmac user=bvmacapi host=/var/run/postgresql")
PUBLIC_ORIGIN = os.environ.get("BVMAC_PUBLIC_ORIGIN", "https://localhost").rstrip("/")
AUTH_PEPPER = os.environ.get("BVMAC_AUTH_PEPPER", "")
LOOKUP_PEPPER = os.environ.get("BVMAC_LOOKUP_PEPPER", AUTH_PEPPER)
WEBSTATS_HMAC_SECRET = os.environ.get("BVMAC_WEBSTATS_HMAC_SECRET", AUTH_PEPPER)
PII_KEY_B64 = os.environ.get("BVMAC_PII_KEY", "")
SESSION_HOURS = max(1, min(int(os.environ.get("BVMAC_USER_SESSION_HOURS", "12")), 168))
COOKIE_SECURE = os.environ.get("BVMAC_COOKIE_SECURE", "1") == "1"
SESSION_COOKIE = "__Host-bvmac_session" if COOKIE_SECURE else "bvmac_session"


def db_connect() -> psycopg.Connection:
    return psycopg.connect(DB_DSN, connect_timeout=5, options="-c statement_timeout=30000 -c idle_in_transaction_session_timeout=60000")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hmac_hex(secret: str, value: str) -> str:
    if not secret:
        raise RuntimeError("Secret HMAC absent")
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9+]", "", value.strip())


def normalize_username(value: str) -> str:
    return value.strip().casefold()


def lookup_hash(kind: str, value: str) -> str:
    return hmac_hex(LOOKUP_PEPPER, f"{kind}|{value}")


def _pii_key() -> bytes:
    if not PII_KEY_B64:
        raise RuntimeError("BVMAC_PII_KEY absent")
    raw = base64.urlsafe_b64decode(PII_KEY_B64.encode("ascii"))
    if len(raw) != 32:
        raise RuntimeError("BVMAC_PII_KEY doit faire 32 octets")
    return raw


def encrypt_pii(value: str) -> bytes:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_pii_key()).encrypt(nonce, value.encode("utf-8"), b"bvmac-pii-v1")
    return nonce + ct


def decrypt_pii(blob: bytes | memoryview | None) -> str | None:
    if blob is None:
        return None
    data = bytes(blob)
    if len(data) < 13:
        return None
    try:
        return AESGCM(_pii_key()).decrypt(data[:12], data[12:], b"bvmac-pii-v1").decode("utf-8")
    except Exception:
        return None


def mask_email(value: str | None) -> str | None:
    if not value or "@" not in value:
        return None
    local, domain = value.split("@", 1)
    if not local:
        return "***@" + domain
    return local[:1] + "***@" + domain


def mask_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) <= 4:
        return "****"
    return "*" * max(4, len(digits) - 4) + digits[-4:]


def client_ip(request: Request) -> str:
    # Nginx transmet X-Real-IP depuis le même hôte. On n'accepte pas X-Forwarded-For arbitraire.
    return (request.headers.get("x-real-ip") or (request.client.host if request.client else "0.0.0.0")).strip()


def client_fingerprint(request: Request) -> str:
    ua = (request.headers.get("user-agent") or "")[:500]
    return hmac_hex(AUTH_PEPPER, f"{client_ip(request)}|{ua}")


def host_visitor_hash(host: str, ip: str, user_agent: str) -> str:
    # Domaine inclus : pas de suivi croisé automatique entre sites du VPS.
    ua = re.sub(r"\s+", " ", user_agent.strip())[:500]
    return hmac_hex(WEBSTATS_HMAC_SECRET, f"{host.lower()}|{ip}|{ua}")


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != PUBLIC_ORIGIN:
        raise HTTPException(status_code=403, detail="Origine refusée")
    referer = request.headers.get("referer")
    if not origin and referer:
        p = urlparse(referer)
        ref_origin = f"{p.scheme}://{p.netloc}".rstrip("/")
        if ref_origin != PUBLIC_ORIGIN:
            raise HTTPException(status_code=403, detail="Origine refusée")


def safe_json(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): safe_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_json(v) for v in value]
    if hasattr(value, "__float__") and value.__class__.__name__ == "Decimal":
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def audit(conn: psycopg.Connection, action: str, *, user_id: int | None = None,
          target_type: str | None = None, target_id: str | None = None,
          client_hash: str | None = None, metadata: dict[str, Any] | None = None) -> None:
    conn.execute(
        """
        INSERT INTO auth.audit_log(actor_user_id,action,target_type,target_id,client_hash,metadata)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb)
        """,
        (user_id, action, target_type, target_id, client_hash, json.dumps(metadata or {}, ensure_ascii=False)),
    )


def parse_ymd(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date invalide (YYYY-MM-DD)")


def range_start(end: date, code: str) -> date | None:
    code = (code or "1y").lower()
    if code in {"all", "origin", "origine"}:
        return None
    days = {
        "day": 1, "1d": 1, "last": 1,
        "week": 7, "1w": 7,
        "month": 31, "1m": 31,
        "quarter": 93, "3m": 93,
        "year": 366, "1y": 366,
        "5y": 365*5+2,
        "10y": 365*10+3,
    }.get(code)
    if days is None:
        raise HTTPException(status_code=400, detail="Période inconnue")
    return end - timedelta(days=days)


def json_row_value(row: Any) -> dict[str, Any]:
    return dict(row[0] if isinstance(row, (tuple, list)) else row)
