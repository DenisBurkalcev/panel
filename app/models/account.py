"""FunPay account managed by the operator (golden_key + proxy + UA)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    """A FunPay account.

    Sensitive fields (`golden_key_enc`, `proxy_url_enc`) are encrypted at rest using
    the application master key and decrypted only when needed for an outbound HTTP call.
    """

    __tablename__ = "funpay_account"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)

    # Encrypted ciphertext blobs.
    golden_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    proxy_url_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    user_agent: Mapped[str] = mapped_column(String(512), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Cached, non-sensitive metadata (refreshed on profile probes).
    funpay_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    funpay_username: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_check_ok: Mapped[bool] = mapped_column(Boolean, default=False)
    last_check_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
