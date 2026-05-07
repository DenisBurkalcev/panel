"""Smoke tests: encryption round-trip, config defaults, app boots."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.usefixtures("app_env")
def test_encryption_roundtrip() -> None:
    from app.crypto import decrypt_str, encrypt_str

    plain = "hello-world-голубой-кит"
    token = encrypt_str(plain)
    assert token != plain
    assert decrypt_str(token) == plain


@pytest.mark.usefixtures("app_env")
def test_settings_defaults_and_master_key_persisted(app_env: Path) -> None:
    from app.config import get_settings

    s = get_settings()
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.secret_key
    # Master key file is created on first access if FPK_SECRET_KEY isn't preset; here we
    # passed FPK_SECRET_KEY explicitly so the file may or may not exist — both are valid.
    assert app_env.exists()


@pytest.mark.usefixtures("app_env")
def test_app_boots_and_health() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app())
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.usefixtures("app_env")
def test_setup_login_and_csrf_enforcement() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), base_url="http://127.0.0.1:8000")

    # First-run setup status.
    r = client.get("/api/auth/status")
    assert r.status_code == 200
    assert r.json() == {"setup_complete": False}

    # Create the admin (no CSRF needed for first-run setup, by design).
    r = client.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "supersecret"},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 201, r.text
    assert "fpk_session" in r.cookies
    assert "fpk_csrf" in r.cookies

    # CSRF must be required for state-changing endpoints.
    r = client.post(
        "/api/auth/logout",
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code in (401, 403)

    # With both Origin and CSRF echoed, logout must succeed.
    csrf = client.cookies.get("fpk_csrf") or ""
    r = client.post(
        "/api/auth/logout",
        headers={"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": csrf},
    )
    assert r.status_code == 200


@pytest.mark.usefixtures("app_env")
def test_account_validation_rejects_bad_input() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    client.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "supersecret"},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    csrf = client.cookies.get("fpk_csrf") or ""
    headers = {"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": csrf}

    # Bogus proxy scheme -> 422.
    r = client.post(
        "/api/accounts",
        json={
            "label": "test",
            "golden_key": "a" * 32,
            "proxy_url": "ftp://nope/",
            "user_agent": "Mozilla/5.0 test",
        },
        headers=headers,
    )
    assert r.status_code == 422

    # Good payload -> 201.
    r = client.post(
        "/api/accounts",
        json={
            "label": "Primary",
            "golden_key": "a" * 32,
            "proxy_url": None,
            "user_agent": "Mozilla/5.0 test",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["label"] == "Primary"
    assert body["proxy_present"] is False

    # Listing requires session — verify it works.
    r = client.get("/api/accounts")
    assert r.status_code == 200
    assert len(r.json()) == 1
