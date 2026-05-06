"""Chat list / messages / send-message routes (per FunPay account).

Reads go through the per-account `AccountSession` for caching; writes go
through the same session and bust the cache so the UI sees the new message.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.account import Account
from app.schemas.chat import ChatPreview, ChatThread, SendMessageRequest
from app.security import get_current_user, require_csrf
from app.services.account_service import credentials_for
from app.services.account_session import get_session_manager
from app.services.funpay_client import FunPayAuthError, FunPayError

router = APIRouter(
    prefix="/api/accounts/{account_id}",
    tags=["chats"],
    dependencies=[Depends(get_current_user)],
)


def _account_or_404(account_id: int, db: Session) -> Account:
    a = db.get(Account, account_id)
    if a is None or not a.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return a


@router.get("/chats", response_model=list[ChatPreview])
async def list_chats(
    account_id: int,
    fresh: bool = Query(default=False, description="Bypass cache"),
    db: Session = Depends(get_db),
) -> list[ChatPreview]:
    account = _account_or_404(account_id, db)
    try:
        session = await get_session_manager().get(account.id, credentials_for(account))
        return await session.list_chats(fresh=fresh)
    except FunPayAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except FunPayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/chats/{chat_id}", response_model=ChatThread)
async def get_chat(
    account_id: int,
    chat_id: str,
    fresh: bool = Query(default=False, description="Bypass cache"),
    db: Session = Depends(get_db),
) -> ChatThread:
    account = _account_or_404(account_id, db)
    try:
        session = await get_session_manager().get(account.id, credentials_for(account))
        return await session.get_chat(chat_id, fresh=fresh)
    except FunPayAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except FunPayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/chats/{chat_id}/messages",
    response_model=ChatThread,
    dependencies=[Depends(require_csrf)],
)
async def send_message(
    account_id: int,
    chat_id: str,
    payload: SendMessageRequest,
    db: Session = Depends(get_db),
) -> ChatThread:
    account = _account_or_404(account_id, db)
    try:
        session = await get_session_manager().get(account.id, credentials_for(account))
        return await session.send_message(chat_id, payload)
    except FunPayAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except FunPayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
