"""Authenticated system overview, telemetry, and persisted audit event routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import SQLAlchemyError

from media_os_api.auth import SessionPayload, require_session
from media_os_api.auth.rate_limit import check_redis_health
from media_os_api.config import Settings
from media_os_api.db.session import check_database_health, session_scope
from media_os_api.observability.telemetry import RequestTelemetryStore
from media_os_api.schemas.system import (
    AuditEventItem,
    AuditLogResponse,
    ProcessStatusItem,
    ProcessStatusResponse,
    SystemStatusResponse,
    TelemetryEventItem,
    TelemetryLogResponse,
)
from media_os_api.services import (
    get_module_boundaries,
    list_recent_audit_events,
    probe_postiz_reachability,
)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status", response_model=SystemStatusResponse)
async def system_status(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
) -> SystemStatusResponse:
    """Return Phase 4 control-plane status, DB/Redis health, and Postiz reachability."""
    settings: Settings = request.app.state.settings
    postiz = await probe_postiz_reachability(
        settings.postiz_base_url,
        timeout_seconds=settings.postiz_health_timeout_seconds,
    )

    db_engine = getattr(request.app.state, "db_engine", None)
    redis_client = getattr(request.app.state, "redis_client", None)

    db_status: Literal["ready", "unreachable", "not_configured"] = "not_configured"
    redis_status: Literal["ready", "unreachable", "not_configured"] = "not_configured"
    schema_version: str | None = None
    db_ok = False
    redis_ok = False

    if db_engine is not None:
        db_ok, _detail, schema_version = await anyio.to_thread.run_sync(
            check_database_health, db_engine
        )
        db_status = "ready" if db_ok else "unreachable"

    if redis_client is not None:
        redis_ok, _rdetail = await anyio.to_thread.run_sync(check_redis_health, redis_client)
        redis_status = "ready" if redis_ok else "unreachable"

    persistence_active = db_engine is not None and redis_client is not None
    return SystemStatusResponse(
        service=settings.service_name,
        phase=settings.app_phase,
        environment=settings.app_env,
        display_timezone=settings.app_display_timezone,
        auth_mode=(
            "single_owner_postgresql_cookie"
            if db_engine is not None
            else "single_owner_bootstrap_cookie"
        ),
        persistent_storage=db_ok,
        database_status=db_status,
        redis_status=redis_status,
        schema_version=schema_version,
        postiz=postiz,
        modules=get_module_boundaries(
            settings,
            persistence_active=persistence_active,
            db_ok=db_ok,
            redis_ok=redis_ok,
        ),
        timestamp_utc=datetime.now(UTC).isoformat(),
    )


@router.get("/processes", response_model=ProcessStatusResponse)
def system_processes(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
) -> ProcessStatusResponse:
    """Scheduler, worker, renderer and research state, read from the job ledger."""
    from media_os_api.services.process_status import process_states

    now = datetime.now(UTC)
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return ProcessStatusResponse(items=[], checked_at=now.isoformat())
    try:
        with session_scope(factory) as db:
            states = process_states(db, now)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "DATABASE_UNAVAILABLE", "message": "Job ledger is unavailable."},
        ) from exc
    return ProcessStatusResponse(
        items=[
            ProcessStatusItem(
                key=s.key,
                state=s.state,
                detail=s.detail,
                last_activity_at=s.last_activity_at.isoformat() if s.last_activity_at else None,
            )
            for s in states
        ],
        checked_at=now.isoformat(),
    )


@router.get("/logs", response_model=TelemetryLogResponse)
def system_logs(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
) -> TelemetryLogResponse:
    """Return sanitized in-memory HTTP telemetry events recorded during current process uptime."""
    store: RequestTelemetryStore = request.app.state.telemetry_store
    events = store.recent(limit=limit)
    return TelemetryLogResponse(
        note=(
            "Ephemeral in-memory HTTP correlation trace for current API container uptime. "
            "Persistent domain & auth events are recorded in PostgreSQL audit_events."
        ),
        items=[
            TelemetryEventItem(
                timestamp_utc=event.timestamp_utc,
                request_id=event.request_id,
                method=event.method,
                route=event.route,
                status_code=event.status_code,
                duration_ms=event.duration_ms,
                error_category=event.error_category,
            )
            for event in events
        ],
    )


@router.get("/audit-events", response_model=AuditLogResponse)
def system_audit_events(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 40,
) -> AuditLogResponse:
    """Return sanitized persistent AuditEvent rows from PostgreSQL."""
    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return AuditLogResponse(items=[])
    try:
        with session_scope(factory) as db:
            rows = list_recent_audit_events(db, limit=limit)
            return AuditLogResponse(
                items=[
                    AuditEventItem(
                        id=str(row.id),
                        timestamp_utc=row.timestamp.isoformat(),
                        actor_id=row.actor_id,
                        action=row.action,
                        target_type=row.target_type,
                        target_id=row.target_id,
                        request_id=row.request_id,
                        outcome=row.outcome,
                        metadata=dict(row.event_metadata or {}),
                    )
                    for row in rows
                ]
            )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Audit log storage is temporarily unavailable.",
            },
        ) from exc
