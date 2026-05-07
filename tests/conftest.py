"""Pytest fixtures: isolated SQLite DB and TestClient per test."""

from __future__ import annotations

import os
import secrets
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Set up a clean data directory + master key + DB for each test."""
    tmp = Path(tempfile.mkdtemp(prefix="fpk-test-"))
    monkeypatch.setenv("FPK_SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("FPK_HOST", "127.0.0.1")
    monkeypatch.setenv("FPK_ALLOWED_ORIGIN", "http://127.0.0.1:8000")
    monkeypatch.setenv("FPK_ENV", "development")

    # Force the app modules to re-evaluate settings against this env.
    import app.config as config

    config._settings = None
    config.DATA_DIR = tmp
    config.MASTER_KEY_FILE = tmp / ".master.key"

    # Override DB path before db engine is reused.
    import importlib

    import app.db as db_module

    new_engine_url = f"sqlite:///{(tmp / 'funpay.sqlite3').as_posix()}"
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        new_engine_url, future=True, connect_args={"check_same_thread": False}
    )
    db_module.engine = engine
    db_module.SessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    importlib.reload(importlib.import_module("app.models"))
    db_module.init_db()

    # Reset crypto module's lru-cached fernet so the new secret takes effect.
    import app.crypto as crypto

    crypto._fernet.cache_clear()  # type: ignore[attr-defined]

    yield tmp

    # Reset settings cache for the next test.
    config._settings = None
    if "FPK_PUBLIC_BIND_WARNING" in os.environ:
        del os.environ["FPK_PUBLIC_BIND_WARNING"]
