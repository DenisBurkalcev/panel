"""CRUD for FunPay accounts + profile probe."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crypto import encrypt_str
from app.db import get_db
from app.models.account import Account
from app.schemas.account import (
    AccountCheckResult,
    AccountCreate,
    AccountOut,
    AccountUpdate,
)
from app.security import get_current_user, require_csrf
from app.services.account_service import probe_account
from app.services.account_session import get_session_manager

router = APIRouter(
    prefix="/api/accounts",
    tags=["accounts"],
    dependencies=[Depends(get_current_user)],
)


def _to_out(a: Account) -> AccountOut:
    return AccountOut(
        id=a.id,
        label=a.label,
        user_agent=a.user_agent,
        note=a.note,
        proxy_present=bool(a.proxy_url_enc),
        funpay_user_id=a.funpay_user_id,
        funpay_username=a.funpay_username,
        last_checked_at=a.last_checked_at,
        last_check_ok=a.last_check_ok,
        last_check_error=a.last_check_error,
        enabled=a.enabled,
        created_at=a.created_at,
    )


@router.get("", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[AccountOut]:
    rows = db.execute(select(Account).order_by(Account.id.asc())).scalars().all()
    return [_to_out(a) for a in rows]


@router.post(
    "",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)) -> AccountOut:
    existing = db.execute(
        select(Account.id).where(Account.label == payload.label)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Label already in use")

    a = Account(
        label=payload.label,
        golden_key_enc=encrypt_str(payload.golden_key),
        proxy_url_enc=encrypt_str(payload.proxy_url) if payload.proxy_url else None,
        user_agent=payload.user_agent,
        note=payload.note,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _to_out(a)


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int, db: Session = Depends(get_db)) -> AccountOut:
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _to_out(a)


@router.patch("/{account_id}", response_model=AccountOut, dependencies=[Depends(require_csrf)])
async def update_account(
    account_id: int, payload: AccountUpdate, db: Session = Depends(get_db)
) -> AccountOut:
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    if payload.label is not None and payload.label != a.label:
        clash = db.execute(
            select(Account.id).where(Account.label == payload.label, Account.id != account_id)
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Label already in use")
        a.label = payload.label

    if payload.golden_key is not None:
        a.golden_key_enc = encrypt_str(payload.golden_key)
    if payload.proxy_url is not None:
        a.proxy_url_enc = encrypt_str(payload.proxy_url) if payload.proxy_url else None
    if payload.user_agent is not None:
        a.user_agent = payload.user_agent
    if payload.note is not None:
        a.note = payload.note or None
    if payload.enabled is not None:
        a.enabled = payload.enabled

    db.add(a)
    db.commit()
    db.refresh(a)
    # Invalidate cached session so credential changes take effect immediately.
    await get_session_manager().evict(a.id)
    return _to_out(a)


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
async def delete_account(account_id: int, db: Session = Depends(get_db)) -> None:
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    db.delete(a)
    db.commit()
    await get_session_manager().evict(account_id)


@router.post(
    "/{account_id}/check",
    response_model=AccountCheckResult,
    dependencies=[Depends(require_csrf)],
)
async def check_account(
    account_id: int, db: Session = Depends(get_db)
) -> AccountCheckResult:
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    ok, err = await probe_account(a, db)
    # Force the session pool to refetch the profile next time.
    await get_session_manager().evict(a.id)
    return AccountCheckResult(
        ok=ok,
        funpay_user_id=a.funpay_user_id,
        funpay_username=a.funpay_username,
        error=None if ok else err,
    )
