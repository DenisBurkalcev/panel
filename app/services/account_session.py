"""Per-account FunPay session manager.

Holds **one long-lived `FunPayClient`** per FunPay account and serves cached
data to the API routers. Caches are short-lived to balance freshness with
load on FunPay (which has no official API and is not designed for high
polling rates from a panel).

Cache layout per account:
    profile         60s TTL (auto-refreshed by FunPayClient on auth errors)
    chat list       5s  TTL
    chat thread     3s  TTL  (per chat_id)

Mutating actions (send_message) invalidate the corresponding entries.

The manager is process-wide and is lazily created on the first request that
needs it. When an Account is updated or deleted in the DB layer, its session
should be evicted via `manager.evict(account_id)` so credential changes take
effect immediately.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from app.schemas.chat import (
    ChatMessage,
    ChatPreview,
    ChatThread,
    OrderInfo,
    ProductInfo,
    SendMessageRequest,
)
from app.services.funpay_client import (
    ChatHeader,
    FunPayClient,
    FunPayCredentials,
    FunPayError,
    FunPayProfile,
)

# Cache TTLs are tuned slightly below the frontend poll cadence so each poll
# tick performs a real fetch from FunPay instead of returning a stale cached
# payload — that's what makes incoming messages appear in the open thread
# without the operator having to click Refresh.
_CHAT_LIST_TTL = 3.0
_THREAD_TTL = 2.0
_PROFILE_TTL = 60.0
# The chat header (peer username, avatar, online status) only changes when the
# buyer logs in/out or updates their profile. Refreshing every 30s gives a
# usable "online/offline" hint without adding a full HTML page hit to every
# 3s thread poll — 9 out of 10 polls are served from this cache.
_HEADER_TTL = 30.0


@dataclass
class _ChatsCacheEntry:
    expires_at: float
    payload: list[ChatPreview]


@dataclass
class _ThreadCacheEntry:
    expires_at: float
    payload: ChatThread


@dataclass
class _HeaderCacheEntry:
    expires_at: float
    payload: ChatHeader


class AccountSession:
    """Long-lived FunPay session for a single account, with caching."""

    def __init__(self, account_id: int, creds: FunPayCredentials) -> None:
        self.account_id = account_id
        self.creds = creds
        self._client = FunPayClient(creds)
        self._opened = False
        self._profile_at: float = 0.0
        self._lock = asyncio.Lock()
        self._chats: _ChatsCacheEntry | None = None
        self._threads: dict[str, _ThreadCacheEntry] = {}
        self._headers: dict[str, _HeaderCacheEntry] = {}

    async def _ensure_open(self) -> None:
        if self._opened:
            return
        await self._client.__aenter__()
        self._opened = True

    async def close(self) -> None:
        if not self._opened:
            return
        try:
            await self._client.__aexit__(None, None, None)
        finally:
            self._opened = False

    async def fetch_profile(self, *, force: bool = False) -> FunPayProfile:
        await self._ensure_open()
        async with self._lock:
            now = time.monotonic()
            stale = (now - self._profile_at) > _PROFILE_TTL
            profile = await self._client.fetch_profile(force=force or stale)
            if force or stale or self._profile_at == 0.0:
                self._profile_at = now
            return profile

    async def list_chats(self, *, fresh: bool = False) -> list[ChatPreview]:
        await self._ensure_open()
        async with self._lock:
            now = time.monotonic()
            if not fresh and self._chats and self._chats.expires_at > now:
                return list(self._chats.payload)
            chats = await self._client.list_chats()
            self._chats = _ChatsCacheEntry(expires_at=now + _CHAT_LIST_TTL, payload=chats)
            return chats

    async def get_chat(self, chat_id: str, *, fresh: bool = False) -> ChatThread:
        await self._ensure_open()
        async with self._lock:
            now = time.monotonic()
            entry = self._threads.get(chat_id)
            if not fresh and entry and entry.expires_at > now:
                return entry.payload
            thread = await self._client.get_chat(chat_id)
            header = await self._fetch_header_locked(chat_id, now=now, fresh=fresh)
            thread = self._enrich_thread(thread, header)
            self._threads[chat_id] = _ThreadCacheEntry(
                expires_at=now + _THREAD_TTL, payload=thread
            )
            return thread

    async def _fetch_header_locked(
        self, chat_id: str, *, now: float, fresh: bool
    ) -> ChatHeader | None:
        """Refresh the cached chat header, swallowing transient failures.

        Header data is best-effort decoration — if the HTML page is briefly
        unavailable we keep the previous value so the title doesn't flicker
        back to the chat id and the buyer's avatar doesn't disappear.
        """
        cached = self._headers.get(chat_id)
        if not fresh and cached and cached.expires_at > now:
            return cached.payload
        try:
            header = await self._client.fetch_chat_header(chat_id)
        except FunPayError as exc:
            logging.getLogger("funpay.account_session").debug(
                "chat header fetch failed for %s: %s", chat_id, exc
            )
            return cached.payload if cached else None
        self._headers[chat_id] = _HeaderCacheEntry(
            expires_at=now + _HEADER_TTL, payload=header
        )
        return header

    def _enrich_thread(
        self, thread: ChatThread, header: ChatHeader | None
    ) -> ChatThread:
        """Layer chat-header + chat-list data onto a fresh thread payload.

        Priority for the displayed title:
          1. Real interlocutor name parsed from message bodies (already on `thread.title`).
          2. Buyer name from `/chat/?node=<id>` header (covers chats with only
             system / autoreply messages, where the message-derived title would
             fall back to a `users-A-B` token).
          3. The original `thread.title` value (chat id placeholder).

        Also computes message-grouping flags (`is_group_first`, `is_group_last`)
        across the message list so the frontend can collapse runs of consecutive
        messages from the same author into a single visual block.
        """
        title = thread.title
        peer_avatar = thread.peer_avatar_url
        peer_online = thread.peer_online
        if header is not None:
            if header.username and (not title or title.startswith("chat ")):
                title = header.username
            if header.avatar_url and not peer_avatar:
                peer_avatar = header.avatar_url
            if header.online is not None:
                peer_online = header.online
        if not peer_avatar and self._chats is not None:
            for preview in self._chats.payload:
                if preview.id == thread.id and preview.avatar_url:
                    peer_avatar = preview.avatar_url
                    break
        grouped = _compute_grouping(thread.messages)
        return ChatThread(
            id=thread.id,
            title=title,
            messages=grouped,
            peer_avatar_url=peer_avatar,
            peer_online=peer_online,
        )

    async def send_message(self, chat_id: str, payload: SendMessageRequest) -> ChatThread:
        await self._ensure_open()
        new_msg = ChatMessage(author=None, is_me=True, text=payload.text)
        async with self._lock:
            # Only invalidate caches *after* a successful send — a failed send
            # shouldn't blow away the chat list and force the next poll to
            # round-trip to FunPay for nothing.
            await self._client.send_message(
                chat_id, payload.text, image_id=payload.image_id
            )
            entry = self._threads.get(chat_id)
            if entry is not None:
                thread = entry.payload
                thread = ChatThread(
                    id=thread.id,
                    title=thread.title,
                    messages=[*thread.messages, new_msg],
                    peer_avatar_url=thread.peer_avatar_url,
                    peer_online=thread.peer_online,
                )
                # Expire the cached thread immediately so the next read forces
                # a refresh; we still keep the optimistic copy as a fallback if
                # the network re-fetch below fails.
                self._threads[chat_id] = _ThreadCacheEntry(
                    expires_at=time.monotonic(), payload=thread
                )
            self._chats = None
        # Force a fresh thread fetch outside the lock so the caller sees the real reply.
        try:
            return await self.get_chat(chat_id, fresh=True)
        except FunPayError:
            return ChatThread(id=chat_id, title=chat_id, messages=[new_msg])

    async def upload_chat_image(
        self, *, filename: str, content_type: str, content: bytes
    ) -> str:
        await self._ensure_open()
        return await self._client.upload_chat_image(
            filename=filename, content_type=content_type, content=content
        )

    async def fetch_current_product(self, chat_id: str) -> ProductInfo:
        await self._ensure_open()
        async with self._lock:
            now = time.monotonic()
            header = await self._fetch_header_locked(chat_id, now=now, fresh=False)
        peer_id = header.user_id if header is not None else None
        return await self._client.fetch_current_product(chat_id, peer_user_id=peer_id)

    async def fetch_order(self, order_id: str) -> OrderInfo:
        await self._ensure_open()
        return await self._client.fetch_order(order_id)


# 5 minutes is the same window most messengers use to decide whether two
# adjacent messages from the same author belong to the same "block". Wider
# than that and you start grouping unrelated conversations across hours.
_GROUP_WINDOW_SECONDS = 300


def _compute_grouping(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Mark consecutive same-author messages with `is_group_first/last` flags.

    Two adjacent messages join the same block iff they have the same `is_me`
    flag, the same author, the same kind (so a system notification isn't
    swallowed by an adjacent regular message), and were sent within the same
    5-minute window. The first message in a block keeps `is_group_first=True`
    and the last gets `is_group_last=True`; middle messages have both False
    so the frontend hides their avatar/username.
    """
    if not messages:
        return messages

    n = len(messages)
    # Same-block predicate between message i and i+1.
    in_same_group: list[bool] = [False] * n
    for i in range(n - 1):
        a, b = messages[i], messages[i + 1]
        if a.kind != b.kind or a.is_me != b.is_me or a.author != b.author:
            in_same_group[i] = False
            continue
        if a.sent_at and b.sent_at:
            delta = (b.sent_at - a.sent_at).total_seconds()
            if delta > _GROUP_WINDOW_SECONDS or delta < -_GROUP_WINDOW_SECONDS:
                in_same_group[i] = False
                continue
        in_same_group[i] = True

    out: list[ChatMessage] = []
    for i, msg in enumerate(messages):
        is_first = i == 0 or not in_same_group[i - 1]
        is_last = i == n - 1 or not in_same_group[i]
        if msg.is_group_first == is_first and msg.is_group_last == is_last:
            out.append(msg)
        else:
            out.append(
                msg.model_copy(
                    update={"is_group_first": is_first, "is_group_last": is_last}
                )
            )
    return out


class AccountSessionManager:
    """Process-wide registry of `AccountSession`s keyed by account id."""

    def __init__(self) -> None:
        self._sessions: dict[int, AccountSession] = {}
        self._lock = asyncio.Lock()

    async def get(self, account_id: int, creds: FunPayCredentials) -> AccountSession:
        async with self._lock:
            existing = self._sessions.get(account_id)
            if existing is not None and existing.creds == creds:
                return existing
            if existing is not None:
                # creds changed — drop and rebuild.
                await existing.close()
            session = AccountSession(account_id, creds)
            self._sessions[account_id] = session
            return session

    async def evict(self, account_id: int) -> None:
        async with self._lock:
            session = self._sessions.pop(account_id, None)
        if session is not None:
            await session.close()

    async def shutdown(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for s in sessions:
            try:
                await s.close()
            except Exception as exc:  # pragma: no cover - best-effort
                # Best-effort cleanup only — log and continue.
                logging.getLogger("funpay.account_session").debug(
                    "Error closing account session %s: %s", s.account_id, exc
                )


_manager: AccountSessionManager | None = None


def get_session_manager() -> AccountSessionManager:
    global _manager
    if _manager is None:
        _manager = AccountSessionManager()
    return _manager
