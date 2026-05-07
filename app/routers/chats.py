"""Chat list / messages / send-message routes (per FunPay account).

Reads go through the per-account `AccountSession` for caching; writes go
through the same session and bust the cache so the UI sees the new message.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.account import Account
from app.schemas.chat import (
    ChatPreview,
    ChatThread,
    OrderInfo,
    ProductInfo,
    SendMessageRequest,
    UploadAttachmentResult,
)
from app.security import get_current_user, require_csrf
from app.services.account_service import credentials_for
from app.services.account_session import get_session_manager
from app.services.funpay_client import (
    FunPayAuthError,
    FunPayError,
    FunPayMessageRejected,
)

# FunPay's own chat form caps uploads at 7 MB (data-size-max="7340032").
# We mirror that limit so a buyer's panel can't be DoSed by a multi-megabyte
# upload that FunPay would refuse anyway.
_MAX_IMAGE_BYTES = 7 * 1024 * 1024
_ALLOWED_IMAGE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
})

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
    if not payload.text.strip() and not payload.image_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Message must have either text or an attachment",
        )
    try:
        session = await get_session_manager().get(account.id, credentials_for(account))
        return await session.send_message(chat_id, payload)
    except FunPayMessageRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except FunPayAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except FunPayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/chats/{chat_id}/attachments",
    response_model=UploadAttachmentResult,
    dependencies=[Depends(require_csrf)],
)
async def upload_attachment(
    account_id: int,
    chat_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadAttachmentResult:
    """Upload an image to FunPay so it can be referenced from a follow-up
    `POST /messages` call (`image_id`).

    FunPay accepts images only (PNG/JPG/GIF/WebP, ≤ 7 MB, between 10×10 and
    4100×4100). Other file types are rejected with HTTP 415 / 413 and the
    user is asked to either resize or paste a URL into the message body.
    """
    account = _account_or_404(account_id, db)
    # Validate before reading: stream the file into memory chunk by chunk so we
    # can bail early on oversized uploads instead of buffering the whole thing.
    if file.content_type and file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "FunPay accepts only PNG/JPG/GIF/WebP images. For other "
                "files paste a download URL into the message text."
            ),
        )
    blob = bytearray()
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        blob.extend(chunk)
        if len(blob) > _MAX_IMAGE_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Image exceeds FunPay's 7 MB upload limit",
            )
    if not blob:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Empty file")

    try:
        session = await get_session_manager().get(account.id, credentials_for(account))
        image_id = await session.upload_chat_image(
            filename=file.filename or "image.png",
            content_type=file.content_type or "application/octet-stream",
            content=bytes(blob),
        )
    except FunPayMessageRejected as exc:
        # Bad image dimensions / unsupported format → tell the user, not a 502.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except FunPayAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except FunPayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return UploadAttachmentResult(image_id=image_id)


@router.get("/chats/{chat_id}/product", response_model=ProductInfo)
async def get_current_product(
    account_id: int,
    chat_id: str,
    db: Session = Depends(get_db),
) -> ProductInfo:
    account = _account_or_404(account_id, db)
    try:
        session = await get_session_manager().get(account.id, credentials_for(account))
        return await session.fetch_current_product(chat_id)
    except FunPayAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except FunPayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/orders/{order_id}", response_model=OrderInfo)
async def get_order(
    account_id: int,
    order_id: str,
    db: Session = Depends(get_db),
) -> OrderInfo:
    account = _account_or_404(account_id, db)
    try:
        session = await get_session_manager().get(account.id, credentials_for(account))
        return await session.fetch_order(order_id)
    except FunPayAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except FunPayError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
