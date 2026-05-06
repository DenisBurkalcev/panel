"""Application configuration loaded from environment / .env file."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = PROJECT_ROOT / "app" / "static"
MASTER_KEY_FILE = DATA_DIR / ".master.key"
PLUGINS_INSTALLED_DIR = PROJECT_ROOT / "plugins-installed"
PLUGINS_INSTALLED_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_master_key() -> str:
    """Return a hex-encoded 32-byte master key, generating one on first run."""
    if MASTER_KEY_FILE.exists():
        key = MASTER_KEY_FILE.read_text(encoding="ascii").strip()
        if len(key) >= 32:
            return key
    key = secrets.token_hex(32)
    MASTER_KEY_FILE.write_text(key, encoding="ascii")
    try:
        MASTER_KEY_FILE.chmod(0o600)
    except (OSError, NotImplementedError):
        # Windows: chmod is best-effort; rely on user-level home directory perms.
        pass
    return key


class Settings(BaseSettings):
    """Runtime configuration. All knobs are loaded from env / .env."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="FPK_",
        extra="ignore",
    )

    secret_key: str = Field(default="")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000, ge=1, le=65535)
    allowed_origin: str = Field(default="http://127.0.0.1:8000")
    env: Literal["development", "production"] = Field(default="development")

    db_path: Path = Field(default=DATA_DIR / "funpay.sqlite3")
    session_cookie_name: str = Field(default="fpk_session")
    csrf_cookie_name: str = Field(default="fpk_csrf")
    session_max_age_seconds: int = Field(default=8 * 60 * 60)

    funpay_base_url: str = Field(default="https://funpay.com")
    funpay_default_user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    )
    funpay_request_timeout_seconds: float = Field(default=20.0)

    @field_validator("secret_key", mode="before")
    @classmethod
    def _resolve_secret_key(cls, value: str | None) -> str:
        if value:
            v = str(value).strip()
            if len(v) < 32:
                raise ValueError("FPK_SECRET_KEY must be at least 32 characters (hex of 32 bytes).")
            return v
        return _ensure_master_key()

    @field_validator("host")
    @classmethod
    def _warn_on_public_bind(cls, v: str) -> str:
        # We don't fail-close here so users can intentionally expose, but log a clear warning
        # the first time the app boots.
        if v not in {"127.0.0.1", "::1", "localhost"}:
            os.environ["FPK_PUBLIC_BIND_WARNING"] = "1"
        return v

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
