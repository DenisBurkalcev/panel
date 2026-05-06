"""Schemas for FunPay chat endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Message kinds the panel knows how to render distinctly. `regular` covers the
# common buyer/seller exchange; `system` is FunPay's own platform notifications
# (order events, refunds), `support` is the FunPay support staff (label
# `поддержка`), and `autoreply` is the seller's saved canned reply that
# fires automatically (label `автоответ`). Anything FunPay invents in the future
# falls back to `regular` so we don't crash on unknown labels.
MessageKind = Literal["regular", "system", "support", "autoreply"]


class ChatPreview(BaseModel):
    id: str
    title: str
    last_message: str | None = None
    unread: bool = False
    avatar_url: str | None = None


class ChatMessage(BaseModel):
    id: str | None = None
    author: str | None = None
    is_me: bool = False
    text: str
    sent_at: datetime | None = None
    kind: MessageKind = "regular"
    label: str | None = None


class ChatThread(BaseModel):
    id: str
    title: str
    messages: list[ChatMessage]
    peer_avatar_url: str | None = None
    peer_online: bool | None = None


class SendMessageRequest(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=4000)]
