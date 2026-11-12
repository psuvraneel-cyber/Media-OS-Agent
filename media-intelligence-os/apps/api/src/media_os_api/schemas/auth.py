"""Authentication request and response contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Credentials payload submitted to `POST /api/v1/auth/login`."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)


class SessionUserResponse(BaseModel):
    """Authenticated owner session metadata returned by login and `/me`."""

    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[True] = True
    username: str
    role: Literal["owner"] = "owner"
    csrf_token: str
    expires_at_utc: str
    phase: str


class LogoutResponse(BaseModel):
    """Confirmation payload returned after revoking the active session."""

    model_config = ConfigDict(extra="forbid")

    authenticated: Literal[False] = False
    message: str
