"""Schemas for FunPay account CRUD."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

GOLDEN_KEY_RE = re.compile(r"^[A-Za-z0-9]{16,128}$")
PROXY_RE = re.compile(r"^(?:http|https|socks5|socks5h|socks4)://[^\s]+$")
LABEL_RE = re.compile(r"^[A-Za-z0-9 _\-.@()]{1,120}$")
USER_AGENT_RE = re.compile(r"^[\x20-\x7E]{8,512}$")


class AccountCreate(BaseModel):
    label: Annotated[str, Field(min_length=1, max_length=120)]
    golden_key: Annotated[str, Field(min_length=16, max_length=128)]
    proxy_url: Annotated[str | None, Field(default=None, max_length=500)] = None
    user_agent: Annotated[str, Field(min_length=8, max_length=512)]
    note: Annotated[str | None, Field(default=None, max_length=500)] = None

    @field_validator("label")
    @classmethod
    def _v_label(cls, v: str) -> str:
        v = v.strip()
        if not LABEL_RE.match(v):
            raise ValueError("Label may only contain letters, digits, spaces, and _-.@()")
        return v

    @field_validator("golden_key")
    @classmethod
    def _v_golden(cls, v: str) -> str:
        v = v.strip()
        if not GOLDEN_KEY_RE.match(v):
            raise ValueError("golden_key looks invalid (expected 16-128 alphanumeric chars)")
        return v

    @field_validator("proxy_url")
    @classmethod
    def _v_proxy(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.strip()
        if not PROXY_RE.match(v):
            raise ValueError(
                "proxy_url must be like http://user:pass@host:port or socks5://host:port"
            )
        return v

    @field_validator("user_agent")
    @classmethod
    def _v_ua(cls, v: str) -> str:
        v = v.strip()
        if not USER_AGENT_RE.match(v):
            raise ValueError("user_agent must be printable ASCII (8..512 chars)")
        return v


class AccountUpdate(BaseModel):
    label: Annotated[str | None, Field(default=None, min_length=1, max_length=120)] = None
    golden_key: Annotated[str | None, Field(default=None, min_length=16, max_length=128)] = None
    proxy_url: Annotated[str | None, Field(default=None, max_length=500)] = None
    user_agent: Annotated[str | None, Field(default=None, min_length=8, max_length=512)] = None
    note: Annotated[str | None, Field(default=None, max_length=500)] = None
    enabled: bool | None = None

    @field_validator("label")
    @classmethod
    def _v_label(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not LABEL_RE.match(v):
            raise ValueError("Label may only contain letters, digits, spaces, and _-.@()")
        return v

    @field_validator("golden_key")
    @classmethod
    def _v_golden(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not GOLDEN_KEY_RE.match(v):
            raise ValueError("golden_key looks invalid")
        return v

    @field_validator("proxy_url")
    @classmethod
    def _v_proxy(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if not PROXY_RE.match(v):
            raise ValueError("proxy_url must look like scheme://[user:pass@]host:port")
        return v

    @field_validator("user_agent")
    @classmethod
    def _v_ua(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not USER_AGENT_RE.match(v):
            raise ValueError("user_agent must be printable ASCII (8..512 chars)")
        return v


class AccountOut(BaseModel):
    id: int
    label: str
    user_agent: str
    note: str | None
    proxy_present: bool
    funpay_user_id: int | None
    funpay_username: str | None
    last_checked_at: datetime | None
    last_check_ok: bool
    last_check_error: str | None
    enabled: bool
    created_at: datetime


class AccountCheckResult(BaseModel):
    ok: bool
    funpay_user_id: int | None = None
    funpay_username: str | None = None
    error: str | None = None
