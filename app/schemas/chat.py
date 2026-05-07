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


class Attachment(BaseModel):
    """A file/image attachment carried by a chat message.

    Currently only `image` is supported — that's the only attachment kind FunPay
    itself exposes via `<a class="chat-img-link"><img class="chat-img" />`. The
    `kind` field exists so the schema can grow to other types (audio/video)
    without breaking the wire format.
    """

    kind: Literal["image"] = "image"
    src: str  # thumbnail URL (smaller, used for preview)
    href: str  # full-resolution URL (used when the user clicks the thumb)
    name: str | None = None
    width: int | None = None
    height: int | None = None


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
    attachments: list[Attachment] = Field(default_factory=list)
    # Grouping flags computed by the session layer when the message list is
    # finalised: a "group" is a run of consecutive messages from the same
    # author of the same kind within a 5-minute window. We render the avatar
    # only on the last message of a group and the username only on the first.
    is_group_first: bool = True
    is_group_last: bool = True


class ChatThread(BaseModel):
    id: str
    title: str
    messages: list[ChatMessage]
    peer_avatar_url: str | None = None
    peer_online: bool | None = None


class SendMessageRequest(BaseModel):
    # Allow empty `text` when `image_id` is set so the frontend can send an
    # image-only message (FunPay's chat form does the same — when you attach
    # an image without typing the form clears `content` and just sends
    # `image_id`).
    text: Annotated[str, Field(min_length=0, max_length=4000)] = ""
    image_id: Annotated[str | None, Field(default=None, max_length=64)] = None


class UploadAttachmentResult(BaseModel):
    image_id: str
    url: str | None = None


class ProductInfo(BaseModel):
    """Brief description of the product a buyer is currently viewing.

    `available=False` means FunPay reports the buyer isn't viewing any of our
    seller's offers right now (the runner endpoint returns empty HTML). We
    still return a valid object so the frontend can show a placeholder.
    """

    available: bool
    title: str | None = None
    description: str | None = None
    price: str | None = None
    url: str | None = None


class OrderItem(BaseModel):
    label: str
    value: str


class OrderInfo(BaseModel):
    id: str
    title: str | None = None
    status: str | None = None
    buyer: str | None = None
    items: list[OrderItem] = Field(default_factory=list)
    total: str | None = None
    url: str
