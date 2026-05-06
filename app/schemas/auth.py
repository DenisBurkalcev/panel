"""Auth-related Pydantic schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SetupRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)


class MeResponse(BaseModel):
    username: str
    setup_complete: bool = True


class SetupStatusResponse(BaseModel):
    setup_complete: bool
