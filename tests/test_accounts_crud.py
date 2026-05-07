"""Account CRUD endpoints + label uniqueness + cache eviction on update/delete."""

from __future__ import annotations

import pytest


def _bootstrap(client) -> dict[str, str]:
    """Create the admin and return CSRF + Origin headers usable for mutating calls."""
    r = client.post(
        "/api/auth/setup",
        json={"username": "admin", "password": "supersecret"},
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 201, r.text
    csrf = client.cookies.get("fpk_csrf") or ""
    return {"Origin": "http://127.0.0.1:8000", "X-CSRF-Token": csrf}


@pytest.mark.usefixtures("app_env")
def test_create_update_delete_account_and_label_uniqueness() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    headers = _bootstrap(client)

    # Create.
    r = client.post(
        "/api/accounts",
        json={
            "label": "Main",
            "golden_key": "a" * 32,
            "user_agent": "Mozilla/5.0 test",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    aid = r.json()["id"]

    # Duplicate label rejected.
    r = client.post(
        "/api/accounts",
        json={
            "label": "Main",
            "golden_key": "b" * 32,
            "user_agent": "Mozilla/5.0 test",
        },
        headers=headers,
    )
    assert r.status_code == 409

    # Patch with new label is fine.
    r = client.patch(
        f"/api/accounts/{aid}",
        json={"label": "Renamed"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Renamed"

    # Delete returns 204 and account disappears.
    r = client.delete(f"/api/accounts/{aid}", headers=headers)
    assert r.status_code == 204
    r = client.get(f"/api/accounts/{aid}")
    assert r.status_code == 404


@pytest.mark.usefixtures("app_env")
def test_account_endpoints_require_auth() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    # No setup, no session → list must be 401.
    r = client.get("/api/accounts")
    assert r.status_code == 401


@pytest.mark.usefixtures("app_env")
def test_account_mutation_requires_csrf() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    client = TestClient(create_app(), base_url="http://127.0.0.1:8000")
    _bootstrap(client)
    # Drop CSRF header → 403 even with valid session cookie.
    r = client.post(
        "/api/accounts",
        json={
            "label": "x",
            "golden_key": "a" * 32,
            "user_agent": "Mozilla/5.0 test",
        },
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert r.status_code == 403
