"""Typed brand profile and channel account contracts backed by PostgreSQL (Phase 4)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class BrandProfileSummary(BaseModel):
    """Persisted `BrandProfile` summary (with explicit data_origin tracking)."""

    model_config = ConfigDict(extra="forbid")

    brand_id: str
    slug: str
    name: str
    niche: str
    description: str
    target_audience: str = ""
    language: str
    tone_of_voice: str
    content_pillars: list[str]
    visual_identity: dict[str, Any] = Field(default_factory=dict)
    timezone: str = "Asia/Kolkata"
    approval_policy: Literal["human_required"] = "human_required"
    placeholder_unconfigured: bool
    active: bool
    data_origin: Literal["postgresql", "phase3_prototype_fixture"] = "postgresql"
    created_at_utc: str | None = None
    updated_at_utc: str | None = None


class BrandUpdateRequest(BaseModel):
    """Single-owner brand update payload."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    niche: str | None = Field(default=None, min_length=1, max_length=1000)
    target_audience: str | None = Field(default=None, min_length=1, max_length=1000)
    language: str | None = Field(default=None, min_length=2, max_length=32)
    tone_of_voice: str | None = Field(default=None, min_length=1, max_length=1000)
    content_pillars: list[str] | None = None
    timezone: str | None = Field(default=None, min_length=1, max_length=64)


class BrandActivationRequest(BaseModel):
    """Single-owner brand activation toggle payload."""

    model_config = ConfigDict(extra="forbid")

    is_active: bool


class ChannelAccountSummary(BaseModel):
    """Persisted `ChannelAccount` binding metadata (Postiz owns OAuth credentials)."""

    model_config = ConfigDict(extra="forbid")

    channel_id: str
    brand_id: str
    platform: Literal["instagram", "youtube"]
    display_label: str
    external_username: str | None = None
    connection_status: str = "DISCONNECTED"
    publishing_enabled: Literal[False] = False
    postiz_integration_id: str | None = None
    data_origin: Literal["postgresql", "phase3_prototype_fixture"] = "postgresql"


class BrandListResponse(BaseModel):
    """List wrapper for brand profiles."""

    model_config = ConfigDict(extra="forbid")

    is_prototype_fixture: bool = False
    persistent: bool = True
    items: list[BrandProfileSummary]


class ChannelListResponse(BaseModel):
    """List wrapper for channel accounts."""

    model_config = ConfigDict(extra="forbid")

    is_prototype_fixture: bool = False
    persistent: bool = True
    items: list[ChannelAccountSummary]
