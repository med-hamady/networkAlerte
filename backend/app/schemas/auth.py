"""Pydantic schemas for the authentication endpoints."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Body of POST /auth/login."""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class UserRead(BaseModel):
    """Public view of a User row — never includes the password hash."""

    id: int
    username: str
    full_name: str | None = None
    enabled: bool
    last_login_at: datetime.datetime | None = None

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    """Body returned by POST /auth/login. The session token is in the cookie."""

    user: UserRead


class ChangePasswordRequest(BaseModel):
    """Body of POST /auth/change-password — the logged-in user changes own pwd."""

    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)
