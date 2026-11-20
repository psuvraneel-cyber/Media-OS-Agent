"""System status, boundary posture, audit, and telemetry response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ModuleBoundaryStatus(BaseModel):
    """Operational boundary status for an architectural module across project phases."""

    model_config = ConfigDict(extra="forbid")

    module_id: str
    title: str
    status: Literal["active_phase_3", "active_phase_4", "not_implemented", "blocked_by_policy"]
    target_phase: str
    summary: str


class PostizReachabilitySummary(BaseModel):
    """Read-only reachability status of standalone `postiz-runtime`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["reachable", "unreachable", "error"]
    checked_at_utc: str
    detail: str
    api_credentials_managed_in_phase: Literal["phase-8"] = "phase-8"


class SystemStatusResponse(BaseModel):
    """Authenticated system overview payload returned by `GET /api/v1/system/status`."""

    model_config = ConfigDict(extra="forbid")

    service: str
    phase: str
    environment: str
    display_timezone: str = "Asia/Kolkata"
    auth_mode: Literal[
        "single_owner_postgresql_cookie",
        "single_owner_bootstrap_cookie",
    ] = "single_owner_postgresql_cookie"
    persistent_storage: bool = False
    database_status: Literal["ready", "unreachable", "not_configured"] = "not_configured"
    redis_status: Literal["ready", "unreachable", "not_configured"] = "not_configured"
    schema_version: str | None = None
    csrf_protection: Literal[True] = True
    postiz: PostizReachabilitySummary
    modules: list[ModuleBoundaryStatus]
    timestamp_utc: str


class ProcessStatusItem(BaseModel):
    """One Media OS process, with its state worked out from the job ledger."""

    model_config = ConfigDict(extra="forbid")

    key: str
    state: Literal["running", "idle", "stalled", "down", "off", "unknown"]
    detail: str
    last_activity_at: str | None = None


class ProcessStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProcessStatusItem]
    checked_at: str


class TelemetryEventItem(BaseModel):
    """Sanitized in-memory request trace item."""

    model_config = ConfigDict(extra="forbid")

    timestamp_utc: str
    request_id: str
    method: str
    route: str
    status_code: int
    duration_ms: float
    error_category: str | None = None


class TelemetryLogResponse(BaseModel):
    """Response payload for `GET /api/v1/system/logs`."""

    model_config = ConfigDict(extra="forbid")

    ephemeral: Literal[True] = True
    persistent: Literal[False] = False
    note: str
    items: list[TelemetryEventItem]


class AuditEventItem(BaseModel):
    """Sanitized persisted PostgreSQL audit event item."""

    model_config = ConfigDict(extra="forbid")

    id: str
    timestamp_utc: str
    actor_id: str
    action: str
    target_type: str
    target_id: str
    request_id: str
    outcome: str
    metadata: dict[str, Any]


class AuditLogResponse(BaseModel):
    """Response payload for `GET /api/v1/system/audit-events`."""

    model_config = ConfigDict(extra="forbid")

    persistent: Literal[True] = True
    items: list[AuditEventItem]
