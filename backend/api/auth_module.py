"""User authentication, sessions, registration and account access controls."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import timedelta
from typing import Any, Literal
import json
from email_auth import HumanIn, check_human, begin_email

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from common import (
    AUTH_PEPPER, SESSION_COOKIE, SESSION_HOURS, COOKIE_SECURE,
    audit, client_fingerprint, db_connect, decrypt_pii, encrypt_pii,
    lookup_hash, mask_email, mask_phone, normalize_email, normalize_phone,
    normalize_username, require_same_origin, sha256_text, utcnow,
)

router = APIRouter(prefix="/api/v3/auth", tags=["auth"])

PH = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1,
                    hash_len=32, salt_len=16, type=Type.ID)
DUMMY_HASH = PH.hash("bvmac-dummy-password-never-used-2026")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,190}\.[^\s@]{2,63}$")


class RegisterIn(HumanIn):
    occupation: Literal['employee','entrepreneur','student','finance','retired','other','undisclosed']='undisclosed'
    experience: Literal['beginner','under_1_year','1_3_years','over_3_years','undisclosed']='undisclosed'
    objective: Literal['discover','learn','monitor','simulate','undisclosed']='undisclosed'

    pseudo: str = Field(min_length=3, max_length=32)
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=12, max_length=128)
    country: str = Field(min_length=2, max_length=80)
    phone: str | None = Field(default=None, max_length=40)


class LoginIn(HumanIn):
    remember_me: bool = False
    pseudo: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ForgotIn(HumanIn):
    pseudo: str = Field(min_length=1, max_length=64)
    email: str = Field(min_length=3, max_length=320)
    phone: str | None = Field(default=None, max_length=40)


def _peppered(password: str) -> str:
    if not AUTH_PEPPER:
        raise RuntimeError("BVMAC_AUTH_PEPPER absent")
    return password + "\x00" + AUTH_PEPPER


def _hash_password(password: str) -> str:
    return PH.hash(_peppered(password))


def _verify_password(stored: str, password: str) -> bool:
    try:
        return bool(PH.verify(stored, _peppered(password)))
    except (VerifyMismatchError, VerificationError, Exception):
        return False


def _verify_dummy(password: str) -> None:
    try:
        PH.verify(DUMMY_HASH, password)
    except Exception:
        pass


def _client_guard(conn, client_hash: str) -> None:
    now = utcnow()
    row = conn.execute(
        "SELECT failure_count,window_started_at,locked_until FROM auth.client_throttle WHERE client_hash=%s FOR UPDATE",
        (client_hash,),
    ).fetchone()
    if not row:
        return
    if row[2] and row[2] > now:
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessaie plus tard.")
    if row[1] and now - row[1] > timedelta(minutes=15):
        conn.execute("DELETE FROM auth.client_throttle WHERE client_hash=%s", (client_hash,))


def _client_failure(conn, client_hash: str) -> None:
    now = utcnow()
    row = conn.execute(
        "SELECT failure_count,window_started_at FROM auth.client_throttle WHERE client_hash=%s FOR UPDATE",
        (client_hash,),
    ).fetchone()
    if not row or now - row[1] > timedelta(minutes=15):
        conn.execute(
            """INSERT INTO auth.client_throttle(client_hash,failure_count,window_started_at,updated_at)
               VALUES(%s,1,%s,%s)
               ON CONFLICT(client_hash) DO UPDATE SET failure_count=1,window_started_at=EXCLUDED.window_started_at,locked_until=NULL,updated_at=EXCLUDED.updated_at""",
            (client_hash, now, now),
        )
        return
    failures = int(row[0]) + 1
    locked = now + timedelta(minutes=15) if failures >= 12 else None
    conn.execute(
        "UPDATE auth.client_throttle SET failure_count=%s,locked_until=%s,updated_at=%s WHERE client_hash=%s",
        (failures, locked, now, client_hash),
    )


def _account_guard(conn, user_id: int) -> tuple[int, int, Any, Any]:
    now = utcnow()
    row = conn.execute(
        "SELECT failures_in_cycle,lock_strikes,strike_window_started_at,locked_until FROM auth.login_guard WHERE user_id=%s FOR UPDATE",
        (user_id,),
    ).fetchone()
    if not row:
        conn.execute("INSERT INTO auth.login_guard(user_id) VALUES(%s)", (user_id,))
        return 0, 0, None, None
    failures, strikes, started, locked = int(row[0]), int(row[1]), row[2], row[3]
    if started and now - started > timedelta(days=30):
        failures = strikes = 0
        started = None
        locked = None
        conn.execute(
            "UPDATE auth.login_guard SET failures_in_cycle=0,lock_strikes=0,strike_window_started_at=NULL,locked_until=NULL,updated_at=now() WHERE user_id=%s",
            (user_id,),
        )
    return failures, strikes, started, locked


def _account_failure(conn, user_id: int, client_hash: str) -> int:
    now = utcnow()
    failures, strikes, started, locked = _account_guard(conn, user_id)
    if locked and locked > now:
        return int((locked - now).total_seconds())
    failures += 1
    new_locked = None
    if failures >= 3:
        strikes += 1
        failures = 0
        started = started or now
        new_locked = now + (timedelta(days=2) if strikes >= 3 else timedelta(minutes=10))
    conn.execute(
        """UPDATE auth.login_guard SET failures_in_cycle=%s,lock_strikes=%s,
           strike_window_started_at=%s,locked_until=%s,last_failure_at=%s,updated_at=%s WHERE user_id=%s""",
        (failures, strikes, started, new_locked, now, now, user_id),
    )
    audit(conn, "login_failed", user_id=user_id, client_hash=client_hash,
          metadata={"locked_seconds": int((new_locked-now).total_seconds()) if new_locked else 0,
                    "lock_strikes": strikes})
    return int((new_locked - now).total_seconds()) if new_locked else 0


def _account_success(conn, user_id: int, client_hash: str) -> None:
    conn.execute(
        """UPDATE auth.login_guard SET failures_in_cycle=0,lock_strikes=0,
           strike_window_started_at=NULL,locked_until=NULL,updated_at=now() WHERE user_id=%s""",
        (user_id,),
    )
    conn.execute("DELETE FROM auth.client_throttle WHERE client_hash=%s", (client_hash,))
    conn.execute("UPDATE auth.user_account SET last_login_at=now(),updated_at=now() WHERE user_id=%s", (user_id,))
    audit(conn, "login_success", user_id=user_id, client_hash=client_hash)


def _csrf_for(token: str) -> str:
    return hmac.new(AUTH_PEPPER.encode(), ("csrf:"+token).encode(), hashlib.sha256).hexdigest()


def _new_session(conn, user_id: int, request: Request, remember_me: bool = False) -> tuple[str, str, Any]:
    token = secrets.token_urlsafe(48)
    csrf = _csrf_for(token)
    expires = utcnow() + timedelta(hours=720 if remember_me else SESSION_HOURS)
    ua_hash = sha256_text(request.headers.get("user-agent") or "")
    ch = client_fingerprint(request)
    conn.execute(
        """INSERT INTO auth.user_session(token_hash,user_id,csrf_hash,expires_at,user_agent_hash,client_hash)
           VALUES(%s,%s,%s,%s,%s,%s)""",
        (sha256_text(token), user_id, sha256_text(csrf), expires, ua_hash, ch),
    )
    # Limite à 8 sessions actives par compte.
    conn.execute(
        """UPDATE auth.user_session SET revoked_at=now() WHERE token_hash IN (
             SELECT token_hash FROM auth.user_session WHERE user_id=%s AND revoked_at IS NULL AND expires_at>now()
             ORDER BY created_at DESC OFFSET 8
           )""", (user_id,)
    )
    return token, csrf, expires


def _set_session_cookie(response: Response, token: str, remember_me: bool = False) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=(720 if remember_me else SESSION_HOURS)*3600, expires=(720 if remember_me else SESSION_HOURS)*3600,
        secure=COOKIE_SECURE, httponly=True, samesite="lax", path="/",
    )


def _session_row(request: Request, *, csrf: bool = False):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Connexion requise")
    th = sha256_text(token)
    ua_hash = sha256_text(request.headers.get("user-agent") or "")
    with db_connect() as conn:
        row = conn.execute(
            """SELECT s.user_id,s.csrf_hash,s.expires_at,s.user_agent_hash,u.username,u.country,u.role,u.status,
                      u.email_cipher,u.phone_cipher,u.must_change_password,u.email_verified_at
               FROM auth.user_session s JOIN auth.user_account u ON u.user_id=s.user_id
               WHERE s.token_hash=%s AND s.revoked_at IS NULL""", (th,)
        ).fetchone()
        if not row or row[2] <= utcnow() or row[7] != "active" or not row[11]:
            conn.execute("UPDATE auth.user_session SET revoked_at=now() WHERE token_hash=%s", (th,))
            conn.commit()
            raise HTTPException(status_code=401, detail="Session expirée")
        if row[3] and not hmac.compare_digest(row[3], ua_hash):
            conn.execute("UPDATE auth.user_session SET revoked_at=now() WHERE token_hash=%s", (th,))
            conn.commit()
            raise HTTPException(status_code=401, detail="Session invalide")
        if csrf:
            require_same_origin(request)
            supplied = request.headers.get("x-csrf-token") or ""
            if not supplied or not hmac.compare_digest(row[1], sha256_text(supplied)):
                raise HTTPException(status_code=403, detail="Jeton CSRF invalide")
        conn.execute("UPDATE auth.user_session SET last_seen_at=now() WHERE token_hash=%s", (th,))
        conn.commit()
    return {
        "user_id": int(row[0]), "username": row[4], "country": row[5], "role": row[6],
        "email": decrypt_pii(row[8]), "phone": decrypt_pii(row[9]), "must_change_password": bool(row[10]), "token_hash": th,
    }


def require_user(request: Request, *, csrf: bool = False, allow_password_change: bool = False) -> dict[str, Any]:
    user = _session_row(request, csrf=csrf)
    if user.get("must_change_password") and not allow_password_change:
        raise HTTPException(status_code=403, detail="Changement de mot de passe obligatoire")
    return user


def optional_user(request: Request) -> dict[str, Any] | None:
    try:
        return _session_row(request, csrf=False)
    except HTTPException:
        return None


@router.post("/register")
def register(data: RegisterIn, request: Request, response: Response):
    require_same_origin(request)
    check_human(data,request,"register")
    from email_auth import settings
    with db_connect() as conn:
        mail_settings=settings(conn)
        if not mail_settings['password'] or not mail_settings['sender']:raise HTTPException(503,'Les inscriptions sont momentanément indisponibles : envoi email non configuré.')
    pseudo = data.pseudo.strip()
    if not USERNAME_RE.fullmatch(pseudo):
        raise HTTPException(status_code=400, detail="Le pseudo doit contenir 3 à 32 lettres, chiffres, ., _ ou -")
    email = normalize_email(data.email)
    if not EMAIL_RE.fullmatch(email):
        raise HTTPException(status_code=400, detail="Adresse email invalide")
    phone = normalize_phone(data.phone)
    country = re.sub(r"\s+", " ", data.country.strip())
    if len(country) < 2:
        raise HTTPException(status_code=400, detail="Pays invalide")
    ch = client_fingerprint(request)
    password_hash = _hash_password(data.password)
    with db_connect() as conn:
        _client_guard(conn, ch)
        exists = conn.execute(
            "SELECT 1 FROM auth.user_account WHERE username_norm=%s OR email_lookup_hash=%s",
            (normalize_username(pseudo), lookup_hash("email", email)),
        ).fetchone()
        if exists:
            # Message générique pour ne pas indiquer précisément quel champ existe.
            raise HTTPException(status_code=409, detail="Pseudo ou adresse email indisponible")
        row = conn.execute(
            """INSERT INTO auth.user_account
               (username,username_norm,email_cipher,email_lookup_hash,phone_cipher,phone_lookup_hash,country,password_hash)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING user_id""",
            (pseudo, normalize_username(pseudo), encrypt_pii(email), lookup_hash("email", email),
             encrypt_pii(phone) if phone else None, lookup_hash("phone", phone) if phone else None,
             country, password_hash),
        ).fetchone()
        uid = int(row[0])
        conn.execute("INSERT INTO auth.login_guard(user_id) VALUES(%s)", (uid,))
        conn.execute("INSERT INTO auth.email_preference(user_id,weekly_market_summary) VALUES(%s,true) ON CONFLICT(user_id) DO NOTHING", (uid,))
        audit(conn, "account_registered", user_id=uid, client_hash=ch)
        conn.execute('UPDATE auth.user_account SET registration_pending=true,profile=%s::jsonb WHERE user_id=%s',(json.dumps({'occupation':data.occupation,'experience':data.experience,'objective':data.objective}),uid))
        conn.commit()
    try:
        return begin_email(uid,email,pseudo,'verify',request)
    except HTTPException as exc:
        if exc.status_code==503:raise HTTPException(503,'Compte créé, mais envoi email non confirmé. Utilise Connexion pour reprendre la validation plus tard.') from None
        raise


@router.post("/login")
def login(data: LoginIn, request: Request, response: Response):
    require_same_origin(request)
    check_human(data,request,"login")
    ch = client_fingerprint(request)
    with db_connect() as conn:
        _client_guard(conn, ch)
        row = conn.execute(
            "SELECT user_id,password_hash,status,email_verified_at,email_cipher,username FROM auth.user_account WHERE username_norm=%s",
            (normalize_username(data.pseudo),),
        ).fetchone()
        if not row:
            _verify_dummy(data.password)
            _client_failure(conn, ch)
            conn.commit()
            raise HTTPException(status_code=401, detail="Pseudo ou mot de passe incorrect")
        uid, stored, status = int(row[0]), row[1], row[2]
        failures, strikes, started, locked = _account_guard(conn, uid)
        if locked and locked > utcnow():
            _client_failure(conn, ch)
            conn.commit()
            raise HTTPException(status_code=429, detail="Connexion temporairement bloquée. Réessaie plus tard.")
        valid = status == "active" and _verify_password(stored, data.password)
        if not valid:
            lock_seconds = _account_failure(conn, uid, ch)
            _client_failure(conn, ch)
            conn.commit()
            if lock_seconds:
                mins = 2880 if lock_seconds >= 86400 else max(1, lock_seconds//60)
                raise HTTPException(status_code=429, detail=f"Trop d'erreurs. Connexion bloquée environ {mins} minute(s).")
            raise HTTPException(status_code=401, detail="Pseudo ou mot de passe incorrect")
        if not row[3]:
            conn.commit()
            return begin_email(uid,decrypt_pii(row[4]),row[5],'verify',request)
        if PH.check_needs_rehash(stored):
            conn.execute("UPDATE auth.user_account SET password_hash=%s,updated_at=now() WHERE user_id=%s",
                         (_hash_password(data.password), uid))
        _account_success(conn, uid, ch)
        token, csrf, expires = _new_session(conn, uid, request, remember_me=data.remember_me)
        conn.commit()
    _set_session_cookie(response, token, remember_me=data.remember_me)
    return {"ok": True, "csrf_token": csrf, "expires_at": expires.isoformat()}


@router.get("/me")
def me(request: Request):
    u = require_user(request, allow_password_change=True)
    return {"user_id": u["user_id"], "pseudo": u["username"], "country": u["country"],
            "email": u["email"], "phone": u["phone"], "role": u["role"], "must_change_password": u["must_change_password"]}


@router.get("/csrf")
def csrf_token(request: Request):
    u = require_user(request, allow_password_change=True)
    token = _csrf_for(request.cookies.get(SESSION_COOKIE) or "")
    with db_connect() as conn:
        conn.execute("UPDATE auth.user_session SET csrf_hash=%s,last_seen_at=now() WHERE token_hash=%s",
                     (sha256_text(token), u["token_hash"]))
        conn.commit()
    return {"csrf_token": token}


@router.post("/logout")
def logout(request: Request, response: Response):
    u = require_user(request, csrf=True, allow_password_change=True)
    with db_connect() as conn:
        conn.execute("UPDATE auth.user_session SET revoked_at=now() WHERE token_hash=%s", (u["token_hash"],))
        audit(conn, "logout", user_id=u["user_id"], client_hash=client_fingerprint(request))
        conn.commit()
    response.delete_cookie(SESSION_COOKIE, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
    return {"ok": True}


@router.post('/forgot-password')
def forgot_password(data:ForgotIn,request:Request):
    check_human(data,request,'forgot')
    email=normalize_email(data.email)
    with db_connect() as conn:
        row=conn.execute("SELECT user_id,username FROM auth.user_account WHERE email_lookup_hash=%s AND username_norm=%s AND email_verified_at IS NOT NULL AND status='active'",(lookup_hash('email',email),normalize_username(data.pseudo))).fetchone()
    return begin_email(row[0] if row else None,email,row[1] if row else '', 'reset',request)

class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1,max_length=256)
    new_password: str = Field(min_length=12,max_length=128)

@router.post('/change-password')
def change_password(data:ChangePasswordIn,request:Request):
    u=require_user(request,csrf=True,allow_password_change=True)
    with db_connect() as conn:
        row=conn.execute('SELECT password_hash FROM auth.user_account WHERE user_id=%s FOR UPDATE',(u['user_id'],)).fetchone()
        if not row or not _verify_password(row[0],data.current_password):
            raise HTTPException(status_code=400,detail='Mot de passe actuel incorrect')
        conn.execute('UPDATE auth.user_account SET password_hash=%s,must_change_password=false,updated_at=now() WHERE user_id=%s',(_hash_password(data.new_password),u['user_id']))
        conn.execute('UPDATE auth.user_session SET revoked_at=now() WHERE user_id=%s AND token_hash<>%s',(u['user_id'],u['token_hash']))
        audit(conn,'password_changed',user_id=u['user_id'],client_hash=client_fingerprint(request)); conn.commit()
    return {'ok':True}
