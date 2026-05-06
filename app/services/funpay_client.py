"""Async HTTP client for FunPay.

FunPay has no official public API; this client talks to the same endpoints the
website uses (`/`, `/runner/`, `/chat/history`) and authenticates with a single
`golden_key` cookie. Each `FunPayClient` is bound to one account and uses that
account's cookie, proxy URL (HTTP/HTTPS/SOCKS) and user-agent — credentials
are *never* shared between accounts.

Reference: https://github.com/LIMBODS/FunPayAPI (GPL-3.0) — used for
endpoint reverse-engineering only; no source is copied.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from app.config import get_settings
from app.schemas.chat import ChatMessage, ChatPreview, ChatThread

log = logging.getLogger("funpay.client")
_settings = get_settings()


class FunPayError(RuntimeError):
    """Raised on any unrecoverable FunPay client error (auth, network, parse)."""


class FunPayAuthError(FunPayError):
    """Raised when the golden_key is invalid or the session is unauthenticated."""


class FunPayMessageRejected(FunPayError):
    """Raised when FunPay accepts the request but rejects the message itself."""


@dataclass(frozen=True)
class FunPayCredentials:
    golden_key: str
    user_agent: str
    proxy_url: str | None = None


@dataclass
class FunPayProfile:
    user_id: int
    username: str
    csrf_token: str


def _build_httpx_client(creds: FunPayCredentials) -> httpx.AsyncClient:
    headers = {
        "User-Agent": creds.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    }
    cookies = {"golden_key": creds.golden_key, "locale": "en"}
    return httpx.AsyncClient(
        headers=headers,
        cookies=cookies,
        proxy=creds.proxy_url or None,
        timeout=_settings.funpay_request_timeout_seconds,
        follow_redirects=True,
        http2=False,
        trust_env=False,  # never inherit ambient HTTP_PROXY etc.
    )


def _extract_app_data(html: str, soup: BeautifulSoup | None = None) -> dict[str, Any] | None:
    """Pull the `data-app-data` JSON blob from the FunPay <body> tag (if present)."""
    if soup is None:
        soup = BeautifulSoup(html, "lxml")
    body = soup.find("body")
    if not isinstance(body, Tag):
        return None
    raw = body.get("data-app-data")
    if not raw:
        return None
    try:
        return json.loads(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


_CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_USER_LINK_RE = re.compile(r"/users/(\d+)/?")
# Pulls the URL out of `style="background-image: url(...)"`. FunPay quotes the
# URL inconsistently (single, double, or no quotes), so we accept all three.
_BG_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")\s]+)['\"]?\s*\)", re.IGNORECASE)
# Default placeholder avatar served by FunPay when a user has no photo. Treated
# as "no avatar" so the frontend renders the initials fallback instead.
_DEFAULT_AVATAR_PATH = "/img/layout/avatar.png"


def _resolve_avatar(style: str | None) -> str | None:
    """Extract a fully-qualified avatar URL from a `style` attribute, or None.

    FunPay sets the avatar via `background-image: url(...)`. We resolve relative
    URLs against the FunPay base so the frontend can render them directly, and
    we drop the placeholder so the UI can fall back to initials.
    """
    if not style:
        return None
    match = _BG_URL_RE.search(style)
    if not match:
        return None
    raw = match.group(1).strip()
    if not raw or raw.endswith(_DEFAULT_AVATAR_PATH):
        return None
    return urljoin(_settings.funpay_base_url, raw)


# Block-level tags inside `.chat-msg-text` whose boundaries should produce a
# newline in the rendered text. Inline tags are left as-is and separated by a
# single space (see `_normalize_message_text`).
_BLOCK_TAGS = frozenset(
    {"p", "div", "li", "ul", "ol", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}
)


def _normalize_message_text(node: Tag) -> str:
    """Extract message text from a BeautifulSoup node with line breaks preserved.

    FunPay uses `<br>` for explicit line breaks (and occasionally wraps blocks
    in `<p>`/`<div>`). The previous extraction used `get_text(separator=" ")`
    which collapsed every break into a space, gluing whole paragraphs onto one
    line. We replace `<br>` with `\\n` and append `\\n` after each block-level
    descendant, then call `get_text(separator=" ")` so adjacent inline tags
    still get a word boundary. Finally we normalize horizontal whitespace per
    line and clamp consecutive blank lines so a single Enter is kept as one
    `\\n` and runs of empty `<p>`s collapse to at most one blank line.
    """
    for br in node.find_all("br"):
        br.replace_with("\n")
    # Append a paragraph-break to each block so consecutive `<p>`s render with
    # a blank line between them; the per-line collapse below clamps runs of
    # blanks to at most one, so this stays bounded.
    for block in node.find_all(True):
        if isinstance(block, Tag) and block.name in _BLOCK_TAGS:
            block.append("\n\n")
    text = node.get_text(separator=" ")
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        if not line:
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(line)
    return "\n".join(out).strip()


def _coerce_chat_id(chat_id: str) -> int | str:
    """FunPay treats numeric chat ids as ints; private chats use `users-<a>-<b>`."""
    if chat_id.isdigit():
        return int(chat_id)
    return chat_id


def _random_tag() -> str:
    return secrets.token_hex(4)


class FunPayClient:
    """Per-account FunPay HTTP client. Use as an async context manager."""

    def __init__(self, creds: FunPayCredentials) -> None:
        self._creds = creds
        self._client: httpx.AsyncClient | None = None
        self._profile: FunPayProfile | None = None

    async def __aenter__(self) -> FunPayClient:
        self._client = _build_httpx_client(self._creds)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("FunPayClient must be used as an async context manager")
        return self._client

    # ------------------------------------------------------------------
    # Profile / CSRF
    # ------------------------------------------------------------------

    async def fetch_profile(self, *, force: bool = False) -> FunPayProfile:
        """Fetch the main page, resolve the authenticated user + CSRF token.

        Memoised on the client instance — only the first call hits the network
        unless `force=True`. Raises FunPayAuthError if golden_key is invalid.
        """
        if self._profile is not None and not force:
            return self._profile

        url = urljoin(_settings.funpay_base_url, "/")
        try:
            resp = await self.http.get(url)
        except httpx.HTTPError as exc:
            raise FunPayError(f"Network error talking to FunPay: {exc}") from exc

        if resp.status_code in (401, 403):
            raise FunPayAuthError(
                f"FunPay refused the home page ({resp.status_code}) — golden_key likely invalid."
            )
        if resp.status_code >= 500:
            raise FunPayError(f"FunPay returned HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise FunPayError(f"FunPay returned HTTP {resp.status_code} for /")
        html = resp.text
        if "data-app-data" not in html:
            raise FunPayAuthError("FunPay didn't recognise the golden_key (not logged in).")

        soup = BeautifulSoup(html, "lxml")
        data = _extract_app_data(html, soup) or {}
        user_id_raw = data.get("userId") or (data.get("user") or {}).get("id")
        csrf = data.get("csrf-token") or data.get("csrfToken")
        username: str | None = None
        username_node = soup.select_one(".user-link-name, .header-user-name")
        if username_node:
            username = username_node.get_text(strip=True) or None
        if not username:
            link = soup.find("a", href=_USER_LINK_RE)
            if isinstance(link, Tag):
                username = (link.get_text() or "").strip() or None
        if not user_id_raw or not username or not csrf:
            raise FunPayAuthError(
                "Could not extract authenticated user / CSRF token from FunPay home page."
            )
        self._profile = FunPayProfile(
            user_id=int(user_id_raw), username=str(username), csrf_token=str(csrf)
        )
        return self._profile

    # ------------------------------------------------------------------
    # Chat list (via /runner/ chat_bookmarks — much cheaper than scraping /chat/)
    # ------------------------------------------------------------------

    async def list_chats(self) -> list[ChatPreview]:
        profile = await self.fetch_profile()
        objects = [
            {
                "type": "chat_bookmarks",
                "id": profile.user_id,
                "tag": _random_tag(),
                "data": False,
            }
        ]
        body = await self._runner_post(objects=objects, request=False, csrf=profile.csrf_token)

        # Find the bookmarks object regardless of order.
        html_blob = ""
        for obj in body.get("objects") or []:
            if obj.get("type") == "chat_bookmarks":
                data = obj.get("data") or {}
                html_blob = data.get("html") or ""
                break
        if not html_blob:
            return []

        soup = BeautifulSoup(html_blob, "lxml")
        previews: list[ChatPreview] = []
        for node in soup.select("a.contact-item"):
            chat_id = node.get("data-id")
            if not chat_id:
                continue
            title_node = node.select_one(".media-user-name")
            preview_node = node.select_one(".contact-item-message")
            avatar_node = node.select_one(".avatar-photo, .contact-item-photo")
            classes = node.get("class") or []
            title = (title_node.get_text(strip=True) if title_node else "") or f"chat {chat_id}"
            preview = preview_node.get_text(strip=True) if preview_node else None
            unread = "unread" in classes
            avatar_style = avatar_node.get("style") if isinstance(avatar_node, Tag) else None
            previews.append(
                ChatPreview(
                    id=str(chat_id),
                    title=title,
                    last_message=preview or None,
                    unread=unread,
                    avatar_url=_resolve_avatar(
                        avatar_style if isinstance(avatar_style, str) else None
                    ),
                )
            )
        return previews

    # ------------------------------------------------------------------
    # Chat history (JSON endpoint — far faster than the HTML chat page)
    # ------------------------------------------------------------------

    async def get_chat(self, chat_id: str, *, last_message_id: int | None = None) -> ChatThread:
        if not _CHAT_ID_RE.match(chat_id):
            raise FunPayError("Invalid chat id")
        profile = await self.fetch_profile()
        coerced = _coerce_chat_id(chat_id)
        last = last_message_id if last_message_id is not None else 99999999999
        url = urljoin(_settings.funpay_base_url, "/chat/history")
        try:
            resp = await self.http.get(
                url,
                params={"node": coerced, "last_message": last},
                headers={
                    "Accept": "*/*",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": urljoin(_settings.funpay_base_url, f"/chat/?node={coerced}"),
                },
            )
        except httpx.HTTPError as exc:
            raise FunPayError(f"Network error: {exc}") from exc
        if resp.status_code != 200:
            raise FunPayError(f"FunPay returned HTTP {resp.status_code} for /chat/history")
        try:
            body = resp.json()
        except ValueError as exc:
            raise FunPayError("FunPay returned non-JSON for /chat/history") from exc

        chat = body.get("chat") or {}
        node_info = chat.get("node") or {}
        title = node_info.get("name") or f"chat {chat_id}"
        # Try to derive a human title from the messages' author block.
        messages_json = chat.get("messages") or []

        msgs: list[ChatMessage] = []
        interlocutor_name: str | None = None
        for raw in messages_json:
            html_blob = raw.get("html") or ""
            author_id = raw.get("author")
            soup = BeautifulSoup(html_blob, "lxml")

            # Pull the author label *before* trimming so we can both record it
            # and remove it from the message body (FunPay puts the author
            # name *inside* the same `.message` block as the text, so the
            # naive `.get_text()` would print it twice).
            author_node = soup.select_one(".media-user-name a, .chat-msg-author")
            author = (author_node.get_text(strip=True) if author_node else None) or None
            if author and not interlocutor_name and author_id != profile.user_id:
                interlocutor_name = author

            # Strip non-message scaffolding (avatar, header link with the
            # username, day-divider date, per-message timestamp tooltip,
            # role-labels like "автоответ" / "оповещение", and any image
            # attachment chrome) so they don't bleed into the body text.
            for sel in (
                ".chat-message-list-date",
                ".chat-msg-date",
                ".chat-msg-author-label",
                ".media-user-name",
                ".message-author",
                ".chat-message-author",
                ".chat-img-link",
                ".message-time",
                ".avatar",
                "img",
            ):
                for node in soup.select(sel):
                    node.decompose()

            text_node = soup.select_one(
                ".chat-msg-text, .message-text, .alert.alert-with-icon.alert-info"
            )
            # Preserve line breaks (`<br>`, block boundaries) so multi-line
            # buyer messages and system alerts render with paragraphs rather
            # than as one run-on sentence.
            text = _normalize_message_text(text_node) if text_node else ""
            if not text:
                text = _normalize_message_text(soup)
            sent_at = _parse_message_timestamp(raw)
            msgs.append(
                ChatMessage(
                    id=str(raw.get("id")) if raw.get("id") is not None else None,
                    author=author,
                    is_me=(author_id == profile.user_id),
                    text=text,
                    sent_at=sent_at,
                )
            )
        if interlocutor_name:
            title = interlocutor_name
        return ChatThread(id=chat_id, title=title, messages=msgs)

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------

    async def send_message(self, chat_id: str, text: str) -> None:
        if not _CHAT_ID_RE.match(chat_id):
            raise FunPayError("Invalid chat id")
        if not text or not text.strip():
            raise FunPayError("Empty message")
        profile = await self.fetch_profile()
        coerced = _coerce_chat_id(chat_id)

        request = {
            "action": "chat_message",
            "data": {"node": coerced, "last_message": -1, "content": text},
        }
        objects = [
            {
                "type": "chat_node",
                "id": coerced,
                "tag": _random_tag(),
                "data": {"node": coerced, "last_message": -1, "content": ""},
            }
        ]
        body = await self._runner_post(
            objects=objects,
            request=request,
            csrf=profile.csrf_token,
            referer=f"/chat/?node={coerced}",
        )

        # Successful runner response: {"response": {...}, "objects": [...]}
        response_obj = body.get("response")
        if not isinstance(response_obj, dict):
            # If the CSRF expired, FunPay sometimes returns an error/redirect; force-refresh
            # and try once more.
            await self.fetch_profile(force=True)
            raise FunPayMessageRejected(
                "FunPay didn't return a runner response — message likely not delivered."
            )
        error_text = response_obj.get("error")
        if error_text:
            # Common cause: stale CSRF token. Re-fetch and retry once.
            if "csrf" in str(error_text).lower():
                refreshed = await self.fetch_profile(force=True)
                body = await self._runner_post(
                    objects=objects,
                    request=request,
                    csrf=refreshed.csrf_token,
                    referer=f"/chat/?node={coerced}",
                )
                response_obj = body.get("response") or {}
                if response_obj.get("error"):
                    raise FunPayMessageRejected(str(response_obj["error"]))
                return
            raise FunPayMessageRejected(str(error_text))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _runner_post(
        self,
        *,
        objects: list[dict[str, Any]] | bool,
        request: dict[str, Any] | bool,
        csrf: str,
        referer: str | None = None,
    ) -> dict[str, Any]:
        url = urljoin(_settings.funpay_base_url, "/runner/")
        form = {
            "objects": json.dumps(objects, ensure_ascii=False) if objects is not False else "",
            "request": json.dumps(request, ensure_ascii=False) if request is not False else "false",
            "csrf_token": csrf,
        }
        headers: dict[str, str] = {
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": _settings.funpay_base_url,
        }
        if referer:
            headers["Referer"] = urljoin(_settings.funpay_base_url, referer)
        try:
            resp = await self.http.post(url, data=form, headers=headers)
        except httpx.HTTPError as exc:
            raise FunPayError(f"Network error: {exc}") from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise FunPayAuthError(f"FunPay refused /runner/ ({resp.status_code})")
        if resp.status_code != 200:
            raise FunPayError(f"FunPay /runner/ returned HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise FunPayError("FunPay returned a non-JSON response from /runner/") from exc
        if not isinstance(data, dict):
            raise FunPayError("Unexpected /runner/ response shape (not an object)")
        return data


def _parse_message_timestamp(raw: dict[str, Any]) -> datetime | None:
    for key in ("createdAt", "created_at", "time", "date"):
        v = raw.get(key)
        if isinstance(v, (int, float)) and v > 0:
            try:
                return datetime.fromtimestamp(int(v), tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return None
    return None
