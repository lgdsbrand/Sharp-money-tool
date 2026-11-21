"""Accounts: email + password sign up / log in / log out.

Users and sessions live in the same `kv` table the rest of the app uses, so they work with SQLite and
Supabase without extra tables.  Passwords are stored as salted PBKDF2-SHA256 hashes (never plain text).
Session tokens are random; only their SHA-256 hash is stored.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from .config import settings

SESSION_DAYS = 30
REMEMBER_DAYS = 180  # "Remember me" ticked
SHORT_DAYS = 1       # not ticked: this browser session only
_ITER = 200_000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_fails: dict[str, list[float]] = {}


class AuthError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _hash(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER).hex()


def _tok_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _ekey(email: str) -> str:
    return "user:" + hashlib.sha256(email.encode()).hexdigest()[:32]


def _clean(email: str) -> str:
    return (email or "").strip().lower()


def public(u: dict) -> dict:
    admin = u["email"] in settings.ADMIN_EMAILS
    return {"id": u["id"], "email": u["email"], "name": u.get("name") or u["email"].split("@")[0], "admin": admin}


def _throttle(key: str) -> None:
    now = time.time()
    recent = [t for t in _fails.get(key, []) if now - t < 600]
    _fails[key] = recent
    if len(recent) >= 8:
        raise AuthError("Too many attempts. Wait 10 minutes and try again.", 429)


async def signup(store, email: str, password: str, name: str = "", remember: bool = True) -> tuple[dict, str]:
    email = _clean(email)
    if not _EMAIL.match(email):
        raise AuthError("Enter a valid email address.")
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    if settings.ALLOWED_EMAILS and email not in settings.ALLOWED_EMAILS and email not in settings.ADMIN_EMAILS:
        raise AuthError("Sign-ups are limited to invited emails. Ask the owner to add yours.", 403)
    if not settings.ALLOW_SIGNUP and email not in settings.ADMIN_EMAILS and email not in settings.ALLOWED_EMAILS:
        raise AuthError("Sign-ups are closed.", 403)
    if await store.kv_get(_ekey(email)):
        raise AuthError("An account with this email already exists. Log in instead.", 409)
    salt = secrets.token_bytes(16)
    user = {"id": secrets.token_hex(8), "email": email, "name": (name or "").strip()[:60], "salt": salt.hex(),
            "pw": _hash(password, salt), "created": datetime.now(timezone.utc).isoformat()}
    await store.kv_set(_ekey(email), user)
    return public(user), await _new_session(store, user, remember)


async def login(store, email: str, password: str, ip: str = "", remember: bool = True) -> tuple[dict, str]:
    email = _clean(email)
    key = f"{email}|{ip}"
    _throttle(key)
    user = await store.kv_get(_ekey(email))
    ok = bool(user) and hmac.compare_digest(_hash(password or "", bytes.fromhex(user["salt"])), user["pw"])
    if not ok:
        _fails.setdefault(key, []).append(time.time())
        raise AuthError("Wrong email or password.", 401)
    _fails.pop(key, None)
    return public(user), await _new_session(store, user, remember)


async def _new_session(store, user: dict, remember: bool = True) -> str:
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(days=REMEMBER_DAYS if remember else SHORT_DAYS)).isoformat()
    await store.kv_set("session:" + _tok_hash(token), {"email": user["email"], "exp": exp})
    return token


async def user_from_token(store, token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    s = await store.kv_get("session:" + _tok_hash(token))
    if not s or s["exp"] < datetime.now(timezone.utc).isoformat():
        return None
    u = await store.kv_get(_ekey(s["email"]))
    return public(u) if u else None


async def logout(store, token: Optional[str]) -> None:
    if token:
        await store.kv_set("session:" + _tok_hash(token), {"email": "", "exp": "1970-01-01T00:00:00+00:00"})
