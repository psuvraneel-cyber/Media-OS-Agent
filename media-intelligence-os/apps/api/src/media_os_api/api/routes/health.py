"""Liveness and readiness routes for the Phase 4 FastAPI backend."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from media_os_api.auth.rate_limit import check_redis_health
from media_os_api.config import Settings
from media_os_api.db.session import check_database_health
from media_os_api.schemas.common import DependencyCheck, HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


def _get_settings_or_fallback(request: Request) -> Settings | None:
    return getattr(request.app.state, "settings", None)


@router.get("/health/live", response_model=HealthResponse)
def health_live(request: Request) -> HealthResponse:
    """Report basic FastAPI process liveness for container and local probes."""
    settings = _get_settings_or_fallback(request)
    return HealthResponse(
        status="ok",
        service=settings.service_name if settings else "media-os-api",
        phase=settings.app_phase if settings else "phase-4-persistence",
        environment=settings.app_env if settings else "unconfigured",
        timestamp_utc=datetime.now(UTC).isoformat(),
    )


@router.get("/health/ready", response_model=ReadinessResponse)
def health_ready(request: Request) -> ReadinessResponse | JSONResponse:
    """Report real PostgreSQL and Redis readiness alongside configuration status."""
    settings = _get_settings_or_fallback(request)
    auth_state = getattr(request.app.state, "auth_state", None)
    now_iso = datetime.now(UTC).isoformat()

    if settings is None or auth_state is None:
        payload = ReadinessResponse(
            status="not_ready",
            service="media-os-api",
            phase="phase-4-persistence",
            environment="unconfigured",
            persistent_storage=False,
            checks=[
                DependencyCheck(
                    name="configuration",
                    status="degraded",
                    phase_target="phase-4",
                    detail="Runtime configuration or auth state is not initialized.",
                )
            ],
            timestamp_utc=now_iso,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload.model_dump()
        )

    db_engine = getattr(request.app.state, "db_engine", None)
    redis_client = getattr(request.app.state, "redis_client", None)

    checks: list[DependencyCheck] = [
        DependencyCheck(
            name="configuration",
            status="ready",
            phase_target="phase-4",
            detail="Validated fail-closed Phase 4 runtime configuration.",
        ),
        DependencyCheck(
            name="auth_bootstrap",
            status="ready",
            phase_target="phase-4",
            detail="Single-owner Argon2id verification and signed session cookies active.",
        ),
    ]

    all_ready = True
    persistent_storage = False

    if db_engine is None:
        checks.append(
            DependencyCheck(
                name="database",
                status="not_implemented",
                phase_target="phase-4",
                detail="PostgreSQL DSN not configured in non-persistent mode.",
            )
        )
    else:
        db_ok, db_detail, _schema_ver = check_database_health(db_engine)
        if db_ok:
            persistent_storage = True
            checks.append(
                DependencyCheck(
                    name="database",
                    status="ready",
                    phase_target="phase-4",
                    detail=db_detail,
                )
            )
        else:
            all_ready = False
            checks.append(
                DependencyCheck(
                    name="database",
                    status="unreachable",
                    phase_target="phase-4",
                    detail=db_detail,
                )
            )

    if redis_client is None:
        checks.append(
            DependencyCheck(
                name="redis",
                status="not_implemented",
                phase_target="phase-4",
                detail="Redis DSN not configured in non-persistent mode.",
            )
        )
    else:
        redis_ok, redis_detail = check_redis_health(redis_client)
        if redis_ok:
            checks.append(
                DependencyCheck(
                    name="redis",
                    status="ready",
                    phase_target="phase-4",
                    detail=redis_detail,
                )
            )
        else:
            all_ready = False
            checks.append(
                DependencyCheck(
                    name="redis",
                    status="unreachable",
                    phase_target="phase-4",
                    detail=redis_detail,
                )
            )

    payload = ReadinessResponse(
        status="ready" if all_ready else "not_ready",
        service=settings.service_name,
        phase=settings.app_phase,
        environment=settings.app_env,
        persistent_storage=persistent_storage,
        checks=checks,
        timestamp_utc=now_iso,
    )
    if not all_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload.model_dump()
        )
    return payload
