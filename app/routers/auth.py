"""Auth endpoints: setup-on-first-run, login, logout, me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.user import AdminUser
from app.schemas.auth import (
    LoginRequest,
    MeResponse,
    SetupRequest,
    SetupStatusResponse,
)
from app.security import (
    SESSION_COOKIE,
    check_same_origin,
    clear_csrf_cookie,
    clear_session_cookie,
    get_current_user,
    hash_password,
    issue_csrf_token,
    require_csrf,
    session_store,
    set_csrf_cookie,
    set_session_cookie,
    touch_login_throttle,
    utcnow,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _setup_complete(db: Session) -> bool:
    return db.execute(select(AdminUser.id).limit(1)).first() is not None


@router.get("/status", response_model=SetupStatusResponse)
def status_(db: Session = Depends(get_db)) -> SetupStatusResponse:
    return SetupStatusResponse(setup_complete=_setup_complete(db))


@router.post(
    "/setup",
    status_code=status.HTTP_201_CREATED,
    response_model=MeResponse,
    dependencies=[Depends(check_same_origin)],
)
def setup(
    payload: SetupRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> MeResponse:
    """First-run admin creation. Disabled once a user exists.

    CSRF is intentionally NOT enforced here — the cookie isn't set yet on the
    very first call. We do enforce a same-origin check via `check_same_origin`
    so a random tab can't bootstrap an admin user against the local panel.
    """
    if _setup_complete(db):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Setup already complete")
    user = AdminUser(username=payload.username, password_hash=hash_password(payload.password))
    db.add(user)
    db.commit()
    db.refresh(user)

    sid = session_store.create(user.id)
    set_session_cookie(response, sid)
    set_csrf_cookie(response, issue_csrf_token())
    user.last_login_at = utcnow()
    db.add(user)
    db.commit()
    return MeResponse(username=user.username)


@router.post("/login", response_model=MeResponse)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> MeResponse:
    touch_login_throttle(request)
    user = db.execute(
        select(AdminUser).where(AdminUser.username == payload.username)
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        # Avoid leaking whether the username exists.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    sid = session_store.create(user.id)
    set_session_cookie(response, sid)
    set_csrf_cookie(response, issue_csrf_token())
    user.last_login_at = utcnow()
    db.add(user)
    db.commit()
    return MeResponse(username=user.username)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(
    request: Request,
    response: Response,
    _: AdminUser = Depends(get_current_user),
) -> dict[str, bool]:
    sid = request.cookies.get(SESSION_COOKIE)
    if sid:
        session_store.revoke(sid)
    clear_session_cookie(response)
    clear_csrf_cookie(response)
    return {"ok": True}


@router.get("/me", response_model=MeResponse)
def me(user: AdminUser = Depends(get_current_user)) -> MeResponse:
    return MeResponse(username=user.username)
