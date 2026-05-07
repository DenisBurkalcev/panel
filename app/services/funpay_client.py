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
from app.schemas.chat import (
    Attachment,
    ChatMessage,
    ChatPreview,
    ChatThread,
    MessageKind,
    OrderInfo,
    OrderItem,
    ProductInfo,
)

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


@dataclass(frozen=True)
class ChatHeader:
    """Buyer-side info scraped from the `/chat/?node=<id>` HTML header.

    The `/chat/history` JSON endpoint we use for the message thread is fast
    but only knows author *ids* — it carries no human-friendly buyer name,
    avatar URL, or online state. The chat HTML page does, in `.chat-header`.
    We scrape it once per chat-open and cache for ~30s so online status
    stays fresh without us hammering FunPay every poll tick.
    """

    user_id: int | None
    username: str | None
    avatar_url: str | None
    online: bool | None


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

# FunPay tags each message with a small label inside `.chat-msg-author-label`
# (`label-primary` = system notification, `label-default` = saved autoresponse,
# `label-success` = FunPay support staff). Anything unknown falls back to
# `regular` so a future label class doesn't blank the message body.
_LABEL_KIND_BY_CLASS: dict[str, MessageKind] = {
    "label-primary": "system",
    "label-default": "autoreply",
    "label-success": "support",
}


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


def _resolve_image_src(src: str | None) -> str | None:
    """Same as `_resolve_avatar` but for an `<img src>` instead of CSS background.

    The `/chat/?node=<id>` page renders the buyer's photo as `<img src="...">`
    inside `.chat-header .media-left`, while the chat-list bookmarks render it
    via `style="background-image: url(...)"` — same value, two encodings.
    """
    if not src:
        return None
    raw = src.strip()
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
            # The visible photo is the *inner* `.avatar-photo` div whose `style`
            # holds the background-image URL. The outer `.contact-item-photo`
            # has no style and was previously matched first by the comma
            # selector — which always returned `None` for the URL and forced
            # the frontend to render initials for every chat.
            avatar_node = node.select_one(".contact-item-photo .avatar-photo")
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
    # Chat header (HTML page) — buyer name / avatar / online status
    # ------------------------------------------------------------------

    async def fetch_chat_header(self, chat_id: str) -> ChatHeader:
        if not _CHAT_ID_RE.match(chat_id):
            raise FunPayError("Invalid chat id")
        coerced = _coerce_chat_id(chat_id)
        url = urljoin(_settings.funpay_base_url, "/chat/")
        try:
            resp = await self.http.get(url, params={"node": coerced})
        except httpx.HTTPError as exc:
            raise FunPayError(f"Network error: {exc}") from exc
        if resp.status_code in (401, 403):
            raise FunPayAuthError(f"FunPay refused /chat/ ({resp.status_code})")
        if resp.status_code != 200:
            raise FunPayError(f"FunPay returned HTTP {resp.status_code} for /chat/")

        soup = BeautifulSoup(resp.text, "lxml")
        header = soup.select_one(".chat-header")
        if not isinstance(header, Tag):
            return ChatHeader(user_id=None, username=None, avatar_url=None, online=None)

        media_user = header.select_one(".media-user")
        online: bool | None = None
        if isinstance(media_user, Tag):
            mu_classes = media_user.get("class") or []
            if "online" in mu_classes:
                online = True
            elif "offline" in mu_classes:
                online = False

        username: str | None = None
        user_id_int: int | None = None
        name_link = header.select_one(".media-user-name a")
        if isinstance(name_link, Tag):
            username = (name_link.get_text(strip=True) or None) if name_link else None
            href = name_link.get("href")
            if isinstance(href, str):
                m = _USER_LINK_RE.search(href)
                if m:
                    try:
                        user_id_int = int(m.group(1))
                    except ValueError:
                        user_id_int = None

        avatar_url: str | None = None
        avatar_img = header.select_one(".media-left img")
        if isinstance(avatar_img, Tag):
            src = avatar_img.get("src")
            avatar_url = _resolve_image_src(src if isinstance(src, str) else None)

        return ChatHeader(
            user_id=user_id_int,
            username=username,
            avatar_url=avatar_url,
            online=online,
        )

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
        # FunPay's `node.name` is an internal `users-A-B` token — useless as a
        # human title. Default to a generic `chat <id>` placeholder; the real
        # buyer name is recovered from message author blocks (below) or from
        # the `/chat/?node=` header (`fetch_chat_header`, called by the
        # session layer). Falling through to `users-A-B` is what previously
        # caused chats with only system / autoreply messages to render as
        # `users-12998200-...` or `FunPay`.
        title = f"chat {chat_id}"
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

            # Classify by the small `.chat-msg-author-label` pill: system
            # notifications, support staff, or seller-side autoresponses are
            # rendered with their own visual treatment in the panel.
            label_node = soup.select_one(".chat-msg-author-label")
            kind: MessageKind = "regular"
            label_text: str | None = None
            if isinstance(label_node, Tag):
                label_text = label_node.get_text(strip=True) or None
                for cls in label_node.get("class") or []:
                    if cls in _LABEL_KIND_BY_CLASS:
                        kind = _LABEL_KIND_BY_CLASS[cls]
                        break
            # author=0 is FunPay's reserved id for platform-generated messages.
            # If we somehow miss the label class but see this id we still want
            # the system styling.
            if kind == "regular" and author_id == 0:
                kind = "system"

            # Only treat regular human messages from the *other* party as
            # title candidates — system / support broadcasts also link to
            # users named "FunPay" and would poison the title otherwise.
            if (
                author
                and not interlocutor_name
                and author_id != profile.user_id
                and kind == "regular"
            ):
                interlocutor_name = author

            # Pull image attachments *before* we strip the scaffolding —
            # FunPay renders image messages as
            # `<a class="chat-img-link" href="<full>"><img class="chat-img"
            # src="<thumb>"></a>` inside the same `.chat-msg-text` block.
            # Without this we'd silently drop image-only messages along with
            # the avatar/img cleanup below.
            attachments: list[Attachment] = []
            for img_link in soup.select("a.chat-img-link"):
                href = img_link.get("href")
                if not isinstance(href, str) or not href:
                    continue
                inner_img = img_link.find("img")
                src = href
                name: str | None = None
                width: int | None = None
                height: int | None = None
                if isinstance(inner_img, Tag):
                    raw_src = inner_img.get("src")
                    if isinstance(raw_src, str) and raw_src:
                        src = raw_src
                    raw_name = inner_img.get("alt")
                    if isinstance(raw_name, str) and raw_name:
                        name = raw_name
                    raw_w = inner_img.get("width")
                    raw_h = inner_img.get("height")
                    try:
                        width = int(raw_w) if isinstance(raw_w, str) else None
                    except ValueError:
                        width = None
                    try:
                        height = int(raw_h) if isinstance(raw_h, str) else None
                    except ValueError:
                        height = None
                attachments.append(
                    Attachment(
                        kind="image",
                        src=urljoin(_settings.funpay_base_url, src),
                        href=urljoin(_settings.funpay_base_url, href),
                        name=name,
                        width=width,
                        height=height,
                    )
                )

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
                    kind=kind,
                    label=label_text,
                    attachments=attachments,
                )
            )
        # Fall back to FunPay's internal node name only when nothing better
        # is available; the session layer will further override with the
        # `/chat/?node=` header so the title matches the chat list.
        node_name = node_info.get("name")
        if interlocutor_name:
            title = interlocutor_name
        elif isinstance(node_name, str) and node_name and not node_name.startswith("users-"):
            title = node_name
        return ChatThread(id=chat_id, title=title, messages=msgs)

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------

    async def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        image_id: str | None = None,
    ) -> None:
        if not _CHAT_ID_RE.match(chat_id):
            raise FunPayError("Invalid chat id")
        if image_id is None and (not text or not text.strip()):
            raise FunPayError("Empty message")
        if image_id is not None and not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", image_id):
            raise FunPayError("Invalid image_id")
        profile = await self.fetch_profile()
        coerced = _coerce_chat_id(chat_id)

        data: dict[str, Any] = {
            "node": coerced,
            "last_message": -1,
            "content": text or "",
        }
        if image_id is not None:
            # FunPay's frontend clears `content` when it sends an image-only
            # message; we forward what the caller asked for, image-only
            # included.
            data["image_id"] = image_id
        request = {"action": "chat_message", "data": data}
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
    # Image upload (POST /file/addChatImage → JSON `{fileId: ...}`)
    # ------------------------------------------------------------------

    async def upload_chat_image(
        self,
        *,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> str:
        profile = await self.fetch_profile()
        url = urljoin(_settings.funpay_base_url, "/file/addChatImage")
        try:
            resp = await self.http.post(
                url,
                data={"csrf_token": profile.csrf_token},
                files={"file": (filename, content, content_type)},
                headers={
                    "Accept": "*/*",
                    "X-Requested-With": "XMLHttpRequest",
                    "Origin": _settings.funpay_base_url,
                    "Referer": _settings.funpay_base_url + "/chat/",
                },
            )
        except httpx.HTTPError as exc:
            raise FunPayError(f"Network error: {exc}") from exc
        try:
            payload = resp.json()
        except ValueError as exc:
            raise FunPayError("FunPay returned non-JSON for /file/addChatImage") from exc
        if resp.status_code in (401, 403):
            raise FunPayAuthError(
                f"FunPay refused /file/addChatImage ({resp.status_code})"
            )
        if isinstance(payload, dict) and payload.get("error"):
            # FunPay returns `{"msg": "...", "error": 1}` on validation failure.
            raise FunPayMessageRejected(str(payload.get("msg") or payload.get("error")))
        if resp.status_code >= 400:
            raise FunPayError(
                f"FunPay /file/addChatImage returned HTTP {resp.status_code}"
            )
        if not isinstance(payload, dict):
            raise FunPayError("Unexpected /file/addChatImage response shape")
        file_id = payload.get("fileId") or payload.get("file_id") or payload.get("id")
        if file_id is None:
            raise FunPayError("FunPay didn't return fileId for the uploaded image")
        return str(file_id)

    # ------------------------------------------------------------------
    # Currently viewed offer (chat-panel-user runner object)
    # ------------------------------------------------------------------

    async def fetch_current_product(
        self, chat_id: str, *, peer_user_id: int | None
    ) -> ProductInfo:
        """Fetch the offer the buyer is currently viewing, if any.

        FunPay populates a hidden `.chat-panel` div on the chat page via a
        `/runner/` request with `type=c-p-u` (chat-panel-user). When the
        buyer isn't browsing one of our offers the `data.html` field comes
        back as an empty list; otherwise it's an HTML snippet describing
        the offer (title, price, link).
        """
        if not _CHAT_ID_RE.match(chat_id):
            raise FunPayError("Invalid chat id")
        if peer_user_id is None:
            return ProductInfo(available=False)
        profile = await self.fetch_profile()
        coerced = _coerce_chat_id(chat_id)
        objects = [
            {
                "type": "c-p-u",
                "id": peer_user_id,
                "tag": _random_tag(),
                "data": False,
            }
        ]
        body = await self._runner_post(
            objects=objects,
            request=False,
            csrf=profile.csrf_token,
            referer=f"/chat/?node={coerced}",
        )
        html_blob = ""
        for obj in body.get("objects") or []:
            if obj.get("type") == "c-p-u":
                data = obj.get("data") or {}
                raw = data.get("html")
                if isinstance(raw, str) and raw.strip():
                    html_blob = raw
                break
        if not html_blob:
            return ProductInfo(available=False)
        return _parse_product_panel(html_blob)

    # ------------------------------------------------------------------
    # Order details (HTML page /orders/<id>/)
    # ------------------------------------------------------------------

    async def fetch_order(self, order_id: str) -> OrderInfo:
        if not re.fullmatch(r"[A-Za-z0-9]{4,16}", order_id):
            raise FunPayError("Invalid order id")
        url = urljoin(_settings.funpay_base_url, f"/orders/{order_id}/")
        try:
            resp = await self.http.get(url)
        except httpx.HTTPError as exc:
            raise FunPayError(f"Network error: {exc}") from exc
        if resp.status_code == 404:
            raise FunPayError(f"Order {order_id} not found on FunPay")
        if resp.status_code in (401, 403):
            raise FunPayAuthError(f"FunPay refused /orders/{order_id} ({resp.status_code})")
        if resp.status_code != 200:
            raise FunPayError(f"FunPay returned HTTP {resp.status_code} for /orders/{order_id}")
        return _parse_order_page(resp.text, order_id=order_id, url=url)

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


# Order numbers FunPay prints in chat are eight uppercase alnum chars
# (e.g. `#EKW9ZFHL`). Restricting to A–Z + 0–9 keeps the link recogniser
# from latching onto random hashtags or hex hashes.
ORDER_RE = re.compile(r"#([A-Z0-9]{4,16})")


def _parse_product_panel(html_blob: str) -> ProductInfo:
    """Parse the `c-p-u` runner panel HTML into a `ProductInfo`."""
    soup = BeautifulSoup(html_blob, "lxml")
    # The panel is rendered as a small card. We try a few selectors so we
    # don't break on minor markup tweaks; the underlying intent is to pick
    # out a title link, a price chip, and a description fragment.
    title_node = soup.select_one("a, .chat-panel-title, h3, .name")
    title: str | None = None
    href: str | None = None
    if isinstance(title_node, Tag):
        title = (title_node.get_text(strip=True) or None) if title_node else None
        raw_href = title_node.get("href") if title_node else None
        if isinstance(raw_href, str) and raw_href:
            href = urljoin(_settings.funpay_base_url, raw_href)
    price_node = soup.select_one(".chat-panel-price, .price, .text-bold")
    price: str | None = None
    if isinstance(price_node, Tag):
        price = (price_node.get_text(" ", strip=True) or None) if price_node else None
    desc_node = soup.select_one(".chat-panel-desc, .desc, p")
    desc: str | None = None
    if isinstance(desc_node, Tag):
        desc = (desc_node.get_text(" ", strip=True) or None) if desc_node else None
    if title is None and price is None and desc is None:
        # Markup unfamiliar — return the panel text raw rather than dropping
        # the data on the floor.
        title = soup.get_text(" ", strip=True)[:120] or None
    return ProductInfo(
        available=True,
        title=title,
        price=price,
        description=desc,
        url=href,
    )


def _parse_order_page(html: str, *, order_id: str, url: str) -> OrderInfo:
    """Parse `/orders/<id>/` into an `OrderInfo` ready for the side panel."""
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1")
    title: str | None = None
    status: str | None = None
    if isinstance(h1, Tag):
        # H1 looks like `Заказ #EKW9ZFHL Закрыт`. Split off the order id and
        # everything after it as the status badge.
        full = h1.get_text(" ", strip=True)
        m = re.match(r"(Заказ\s+#" + re.escape(order_id) + r")\s*(.*)", full)
        if m:
            title = m.group(1)
            status = (m.group(2) or "").strip() or None
        else:
            title = full or None

    buyer: str | None = None
    media_body = soup.select_one(".media-body")
    if isinstance(media_body, Tag):
        buyer_link = media_body.select_one("a, .media-user-name")
        if isinstance(buyer_link, Tag):
            buyer = buyer_link.get_text(" ", strip=True) or None
        if buyer is None:
            buyer = media_body.get_text(" ", strip=True).split(" ", 1)[0] or None

    items: list[OrderItem] = []
    total: str | None = None
    pl = soup.select_one(".param-list")
    if isinstance(pl, Tag):
        # FunPay's `.param-list` interleaves `<h5>label</h5> <div>value</div>`
        # at the same level. Walk children pairwise so we pick up the order
        # they're rendered in (Игра, Категория, Краткое описание, ..., Сумма).
        children = [c for c in pl.children if isinstance(c, Tag)]
        i = 0
        while i + 1 < len(children):
            if children[i].name == "h5":
                label = children[i].get_text(" ", strip=True)
                value = children[i + 1].get_text(" ", strip=True)
                if label and value:
                    items.append(OrderItem(label=label, value=value))
                    if label.lower().startswith("сумм"):
                        total = value
                i += 2
            else:
                i += 1

    return OrderInfo(
        id=order_id,
        title=title,
        status=status,
        buyer=buyer,
        items=items,
        total=total,
        url=url,
    )


def _parse_message_timestamp(raw: dict[str, Any]) -> datetime | None:
    for key in ("createdAt", "created_at", "time", "date"):
        v = raw.get(key)
        if isinstance(v, (int, float)) and v > 0:
            try:
                return datetime.fromtimestamp(int(v), tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return None
    return None
