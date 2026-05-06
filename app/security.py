"""Authentication, session, CSRF, and rate-limit primitives.

Design notes (OWASP ASVS-aligned):
- Passwords hashed with Argon2id (`argon2-cffi`), with per-hash random salts.
- Session id is a 256-bit random token, stored in an HttpOnly + SameSite=Strict cookie.
  The mapping `session_id -> user_id` lives in-memory (single-process server). Sessions
  expire after `session_max_age_seconds` of inactivity.
- CSRF: synchronizer-token pattern. A 256-bit token is set in a non-HttpOnly cookie
  named `fpk_csrf` and must be echoed via the `X-CSRF-Token` header on any state-
  changing request (POST/PUT/PATCH/DELETE). The server also enforces a strict
  Origin/Referer check against `FPK_ALLOWED_ORIGIN`.
- Login throttle: simple sliding-window per-IP limiter to mitigate online brute force.
"""

from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models.user import AdminUser

_PH = PasswordHasher()
_SETTINGS = get_settings()

SESSION_COOKIE: Final[str] = _SETTINGS.session_cookie_name
CSRF_COOKIE: Final[str] = _SETTINGS.csrf_cookie_name
CSRF_HEADER: Final[str] = "X-CSRF-Token"


def hash_password(plain: str) -> str:
    if not isinstance(plain, str) or len(plain) < 8:
        raise ValueError("Password must be at least 8 characters long.")
    return _PH.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        _PH.verify(hashed, plain)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


@dataclass
class SessionRecord:
    user_id: int
    created_at: float
    last_seen: float


class SessionStore:
    """Tiny in-process session store. Adequate for a single-user local server."""

    def __init__(self, max_age_seconds: int) -> None:
        self._max_age = max_age_seconds
        self._lock = RLock()
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, user_id: int) -> str:
        sid = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self._sessions[sid] = SessionRecord(user_id=user_id, created_at=now, last_seen=now)
        return sid

    def get(self, sid: str) -> SessionRecord | None:
        if not sid:
            return None
        now = time.time()
        with self._lock:
            rec = self._sessions.get(sid)
            if rec is None:
                return None
            if now - rec.last_seen > self._max_age:
                self._sessions.pop(sid, None)
                return None
            rec.last_seen = now
            return rec

    def revoke(self, sid: str) -> None:
        with self._lock:
            self._sessions.pop(sid, None)

    def revoke_all(self, user_id: int | None = None) -> None:
        with self._lock:
            if user_id is None:
                self._sessions.clear()
                return
            for sid, rec in list(self._sessions.items()):
                if rec.user_id == user_id:
                    self._sessions.pop(sid, None)


session_store = SessionStore(_SETTINGS.session_max_age_seconds)


@dataclass
class _Bucket:
    timestamps: deque[float] = field(default_factory=deque)


class RateLimiter:
    """Sliding-window limiter, per key (e.g. client IP)."""

    def __init__(self, *, max_events: int, window_seconds: int) -> None:
        self._max = max_events
        self._window = window_seconds
        self._lock = RLock()
        self._buckets: dict[str, _Bucket] = defaultdict(_Bucket)

    def hit(self, key: str) -> bool:
        """Return True if the request is allowed; False if rate-limited."""
        now = time.time()
        with self._lock:
            bucket = self._buckets[key]
            cutoff = now - self._window
            while bucket.timestamps and bucket.timestamps[0] < cutoff:
                bucket.timestamps.popleft()
            if len(bucket.timestamps) >= self._max:
                return False
            bucket.timestamps.append(now)
            return True


login_limiter = RateLimiter(max_events=10, window_seconds=300)


def issue_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _cookie_kwargs(*, http_only: bool, max_age: int | None = None) -> dict[str, object]:
    return {
        "secure": _SETTINGS.is_production,
        "httponly": http_only,
        "samesite": "strict",
        "path": "/",
        "max_age": max_age,
    }


def set_session_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        **_cookie_kwargs(http_only=True, max_age=_SETTINGS.session_max_age_seconds),  # type: ignore[arg-type]
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def set_csrf_cookie(response: Response, token: str) -> None:
    # CSRF cookie is intentionally readable by JS so the SPA can echo it via header.
    response.set_cookie(
        CSRF_COOKIE,
        token,
        **_cookie_kwargs(http_only=False, max_age=_SETTINGS.session_max_age_seconds),  # type: ignore[arg-type]
    )


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(CSRF_COOKIE, path="/")


def _client_key(request: Request) -> str:
    # We bind to localhost by default; client.host is generally 127.0.0.1, but key on it
    # anyway in case the user changes the bind address.
    return f"ip:{request.client.host if request.client else 'unknown'}"


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> AdminUser:
    sid = request.cookies.get(SESSION_COOKIE)
    rec = session_store.get(sid) if sid else None
    if rec is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    user = db.get(AdminUser, rec.user_id)
    if user is None:
        session_store.revoke(sid or "")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session user no longer exists")
    return user


def require_csrf(request: Request) -> None:
    """Enforce same-origin and CSRF token on state-changing requests."""
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return

    expected_origin = _SETTINGS.allowed_origin.rstrip("/")
    origin = request.headers.get("origin", "").rstrip("/")
    referer = request.headers.get("referer", "")

    # At least one must match the allowed origin (modern browsers always send one).
    if origin and origin != expected_origin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Bad Origin")
    if not origin and referer and not referer.startswith(expected_origin):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Bad Referer")
    if not origin and not referer:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Missing Origin/Referer")

    cookie_token = request.cookies.get(CSRF_COOKIE) or ""
    header_token = request.headers.get(CSRF_HEADER) or ""
    if not cookie_token or not header_token or not secrets.compare_digest(
        cookie_token, header_token
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="CSRF check failed")


def touch_login_throttle(request: Request) -> None:
    if not login_limiter.hit(_client_key(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
