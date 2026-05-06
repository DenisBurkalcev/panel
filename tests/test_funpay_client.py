"""Unit tests for FunPay client error handling (no real network)."""

from __future__ import annotations

import asyncio

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
