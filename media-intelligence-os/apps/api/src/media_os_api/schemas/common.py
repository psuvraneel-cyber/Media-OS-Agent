"""Common error envelope and health/readiness response contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    """Structured non-leaking error payload embedded in `ErrorEnvelope`."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str
    details: Any | None = None


class ErrorEnvelope(BaseModel):
    """Standard API error envelope returned by FastAPI exception handlers."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail


class HealthResponse(BaseModel):
    """Process liveness response (`GET /health/live`)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    service: str
    phase: str
    environment: str
    timestamp_utc: str


class DependencyCheck(BaseModel):
    """Explicit readiness state for a single subsystem boundary."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: Literal["ready", "not_implemented", "reachable", "unreachable", "degraded"]
    phase_target: str
    detail: str


class ReadinessResponse(BaseModel):
    """Honest runtime readiness response (`GET /health/ready`)."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "not_ready"]
    service: str
    phase: str
    environment: str
    persistent_storage: bool = False
    checks: list[DependencyCheck]
    timestamp_utc: str
