"""Config validation tests: master key + allowed_origin + secret_key."""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest


def _isolate_settings(monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
    """Point the config module at a fresh data dir so we test the real code path."""
    import app.config as config

    config._settings = None
    config.DATA_DIR = tmp
    config.MASTER_KEY_FILE = tmp / ".master.key"


def test_master_key_is_generated_on_first_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FPK_SECRET_KEY", raising=False)
    monkeypatch.setenv("FPK_HOST", "127.0.0.1")
    monkeypatch.setenv("FPK_ALLOWED_ORIGIN", "http://127.0.0.1:8000")
    _isolate_settings(monkeypatch, tmp_path)

    from app.config import MASTER_KEY_FILE, get_settings

    s = get_settings()
    assert MASTER_KEY_FILE.exists()
    # 32 bytes hex-encoded == 64 chars; we accept >= 32.
    text = MASTER_KEY_FILE.read_text().strip()
    assert len(text) >= 32
    assert s.secret_key == text


def test_master_key_invalid_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FPK_SECRET_KEY", raising=False)
    monkeypatch.setenv("FPK_HOST", "127.0.0.1")
    monkeypatch.setenv("FPK_ALLOWED_ORIGIN", "http://127.0.0.1:8000")
    _isolate_settings(monkeypatch, tmp_path)

    from app.config import MASTER_KEY_FILE, get_settings

    MASTER_KEY_FILE.write_text("too-short")
    with pytest.raises(RuntimeError) as exc:
        get_settings()
    assert "Master key file" in str(exc.value)


def test_secret_key_too_short_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FPK_SECRET_KEY", "tiny")
    monkeypatch.setenv("FPK_HOST", "127.0.0.1")
    monkeypatch.setenv("FPK_ALLOWED_ORIGIN", "http://127.0.0.1:8000")
    _isolate_settings(monkeypatch, tmp_path)

    from app.config import get_settings

    with pytest.raises(Exception) as exc:
        get_settings()
    assert "FPK_SECRET_KEY" in str(exc.value)


def test_allowed_origin_must_be_full_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FPK_SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("FPK_HOST", "127.0.0.1")
    monkeypatch.setenv("FPK_ALLOWED_ORIGIN", "127.0.0.1:8000")  # missing scheme
    _isolate_settings(monkeypatch, tmp_path)

    from app.config import get_settings

    with pytest.raises(Exception) as exc:
        get_settings()
    assert "FPK_ALLOWED_ORIGIN" in str(exc.value) or "http" in str(exc.value).lower()


def test_allowed_origin_strips_trailing_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FPK_SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("FPK_HOST", "127.0.0.1")
    monkeypatch.setenv("FPK_ALLOWED_ORIGIN", "http://127.0.0.1:8000/")
    _isolate_settings(monkeypatch, tmp_path)

    from app.config import get_settings

    s = get_settings()
    assert s.allowed_origin == "http://127.0.0.1:8000"


def test_public_bind_emits_warning_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FPK_PUBLIC_BIND_WARNING", raising=False)
    monkeypatch.setenv("FPK_SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("FPK_HOST", "0.0.0.0")
    monkeypatch.setenv("FPK_ALLOWED_ORIGIN", "http://127.0.0.1:8000")
    _isolate_settings(monkeypatch, tmp_path)

    from app.config import get_settings

    get_settings()
    import os

    assert os.environ.get("FPK_PUBLIC_BIND_WARNING") == "1"
