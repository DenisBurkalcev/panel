"""Unit tests for FunPay client error handling (no real network)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest


def _make_client(handler) -> object:
    """Build a FunPayClient pre-wired to a `httpx.MockTransport`."""
    from app.services.funpay_client import (
        FunPayClient,
        FunPayCredentials,
    )

    transport = httpx.MockTransport(handler)
    creds = FunPayCredentials(golden_key="x" * 32, user_agent="ua-test", proxy_url=None)
    client = FunPayClient(creds)
    # Inject our own AsyncClient with the mock transport instead of going to network.
    client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=transport,
        cookies={"golden_key": creds.golden_key, "locale": "en"},
        follow_redirects=True,
    )
    client._opened = True  # type: ignore[attr-defined]
    return client


def test_fetch_profile_401_raises_auth_error() -> None:
    from app.services.funpay_client import FunPayAuthError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    client = _make_client(handler)
    with pytest.raises(FunPayAuthError):
        asyncio.run(client.fetch_profile())  # type: ignore[attr-defined]


def test_fetch_profile_404_raises_funpay_error() -> None:
    from app.services.funpay_client import FunPayAuthError, FunPayError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    client = _make_client(handler)
    with pytest.raises(FunPayError) as exc:
        asyncio.run(client.fetch_profile())  # type: ignore[attr-defined]
    # 404 is not auth-specific; should NOT be FunPayAuthError.
    assert not isinstance(exc.value, FunPayAuthError)


def test_fetch_profile_500_raises_funpay_error() -> None:
    from app.services.funpay_client import FunPayError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = _make_client(handler)
    with pytest.raises(FunPayError):
        asyncio.run(client.fetch_profile())  # type: ignore[attr-defined]


def test_fetch_profile_logged_out_raises_auth_error() -> None:
    from app.services.funpay_client import FunPayAuthError

    def handler(request: httpx.Request) -> httpx.Response:
        # 200 OK but no `data-app-data` blob → not logged in.
        return httpx.Response(200, text="<html><body>guest page</body></html>")

    client = _make_client(handler)
    with pytest.raises(FunPayAuthError):
        asyncio.run(client.fetch_profile())  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Chat parser tests — exercise message classification, title resolution and
# chat-header (avatar / online status) extraction against the exact HTML
# shape FunPay serves on production.
# ---------------------------------------------------------------------------


def _profile_html(user_id: int, csrf: str) -> str:
    app_data = {"userId": user_id, "csrf-token": csrf}
    return (
        f'<html><body data-app-data=\'{json.dumps(app_data)}\'>'
        f'<a href="/users/{user_id}/" class="user-link-name">me</a>'
        f"</body></html>"
    )


def _msg_html(*, author_name: str | None, author_link: bool, label_class: str | None,
              label_text: str | None, body: str) -> str:
    name_block = ""
    if author_name and author_link:
        name_block = (
            '<div class="media-user-name">'
            f'<a href="https://funpay.com/users/123/" class="chat-msg-author-link">'
            f'{author_name}</a></div>'
        )
    elif author_name:
        name_block = f'<div class="media-user-name">{author_name}</div>'
    label_block = ""
    if label_class and label_text:
        label_block = (
            f'<span class="chat-msg-author-label label {label_class}">{label_text}</span>'
        )
    return (
        '<div class="chat-msg-item"><div class="chat-message">'
        f"{name_block}{label_block}"
        f'<div class="chat-msg-body"><div class="chat-msg-text">{body}</div></div>'
        "</div></div>"
    )


def _make_get_chat_handler(*, my_id: int, messages: list[dict]):
    csrf = "tok-test"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=_profile_html(my_id, csrf))
        if request.url.path == "/chat/history":
            return httpx.Response(200, json={"chat": {
                "node": {"id": 1, "name": "users-1-2", "silent": False},
                "messages": messages,
            }})
        if request.url.path == "/chat/":
            return httpx.Response(200, text="<html><body></body></html>")
        return httpx.Response(404, text="not found")

    return handler


def test_get_chat_classifies_system_support_and_autoreply() -> None:
    my_id = 12998200
    messages = [
        {"id": 1, "author": 0, "html": _msg_html(
            author_name="FunPay", author_link=False,
            label_class="label-primary", label_text="оповещение",
            body="Покупатель ni6 оплатил заказ #X.")},
        {"id": 2, "author": my_id, "html": _msg_html(
            author_name="ni6", author_link=True,
            label_class="label-default", label_text="автоответ",
            body="жду аккаунто")},
        {"id": 3, "author": 6637130, "html": _msg_html(
            author_name="gorent", author_link=True,
            label_class=None, label_text=None,
            body="Хорошей игры")},
        {"id": 4, "author": 500, "html": _msg_html(
            author_name="FunPay", author_link=True,
            label_class="label-success", label_text="поддержка",
            body="Реклама.")},
    ]
    client = _make_client(_make_get_chat_handler(my_id=my_id, messages=messages))
    thread = asyncio.run(client.get_chat("212091918"))  # type: ignore[attr-defined]
    kinds = [m.kind for m in thread.messages]
    assert kinds == ["system", "autoreply", "regular", "support"]
    # The buyer is "gorent" — not "FunPay" (support broadcast) and not the
    # autoreply (which is sent by us). This guards the bug the user reported.
    assert thread.title == "gorent"


def test_get_chat_title_falls_back_when_only_system_messages() -> None:
    my_id = 12998200
    messages = [
        {"id": 1, "author": 0, "html": _msg_html(
            author_name="FunPay", author_link=False,
            label_class="label-primary", label_text="оповещение",
            body="Покупатель ni6 оплатил заказ #Q.")},
        {"id": 2, "author": my_id, "html": _msg_html(
            author_name="ni6", author_link=True,
            label_class="label-default", label_text="автоответ",
            body="жду аккаунто")},
    ]
    client = _make_client(_make_get_chat_handler(my_id=my_id, messages=messages))
    thread = asyncio.run(client.get_chat("99"))  # type: ignore[attr-defined]
    # No real interlocutor message exists yet, and node.name is "users-A-B" —
    # so we keep the placeholder rather than render "FunPay" or "users-1-2".
    assert thread.title == "chat 99"


def test_fetch_chat_header_extracts_username_avatar_and_online() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/chat/":
            return httpx.Response(
                200,
                text=(
                    '<div class="chat-header">'
                    '<div class="media media-user online">'
                    '<div class="media-left">'
                    '<a href="https://funpay.com/users/15975403/">'
                    '<img src="https://sfunpay.com/s/avatar/a1/t0/x.jpg" /></a>'
                    "</div>"
                    '<div class="media-body">'
                    '<div class="media-user-name">'
                    '<a href="https://funpay.com/users/15975403/">HonpShop</a></div>'
                    '<div class="media-user-status">Онлайн</div>'
                    "</div></div></div>"
                ),
            )
        return httpx.Response(404)

    client = _make_client(handler)
    header = asyncio.run(client.fetch_chat_header("212091918"))  # type: ignore[attr-defined]
    assert header.username == "HonpShop"
    assert header.user_id == 15975403
    assert header.online is True
    assert header.avatar_url == "https://sfunpay.com/s/avatar/a1/t0/x.jpg"


def test_fetch_chat_header_offline_class() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                '<div class="chat-header">'
                '<div class="media media-user offline">'
                '<div class="media-body"><div class="media-user-name">'
                '<a href="/users/1/">x</a></div></div></div></div>'
            ),
        )

    client = _make_client(handler)
    header = asyncio.run(client.fetch_chat_header("1"))  # type: ignore[attr-defined]
    assert header.online is False


def test_list_chats_extracts_avatar_url_from_inner_div() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, text=_profile_html(12998200, "tok"))
        if request.url.path == "/runner/":
            html = (
                '<a class="contact-item" data-id="42" href="#">'
                '<div class="contact-item-photo">'
                '<div class="avatar-photo" '
                "style=\"background-image: url('/s/avatar/a/b.jpg');\"></div>"
                "</div>"
                '<div class="media-user-name">Buyer</div>'
                '<div class="contact-item-message">hi</div></a>'
            )
            return httpx.Response(
                200,
                json={"objects": [
                    {"type": "chat_bookmarks", "data": {"html": html}}
                ]},
            )
        return httpx.Response(404)

    client = _make_client(handler)
    chats = asyncio.run(client.list_chats())  # type: ignore[attr-defined]
    assert len(chats) == 1
    assert chats[0].title == "Buyer"
    # The fix for the silent-avatar bug: the *inner* `.avatar-photo` div is the
    # one that carries `style`, the outer wrapper is empty.
    assert chats[0].avatar_url == "https://funpay.com/s/avatar/a/b.jpg"
