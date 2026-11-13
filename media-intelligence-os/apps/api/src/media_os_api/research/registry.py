"""Research source registry: live-fetch gate, per-stream checkpoints, and source health.

The registry (`research_sources`, migration 0005) is the system of record for whether a source may
be fetched. A live external fetch requires all three independent switches:

1. `policy_mode` in {EXPERIMENTAL, ENABLED}  (Reddit is locked to BLOCKED by a DB CHECK),
2. `live_fetch_approved = true` with `approved_by` / `approved_at` recorded (owner action),
3. environment kill-switch `RESEARCH_LIVE_FETCH_ENABLED=true` on the worker.

Gate refusals are permanent and never touch source health (no attempt was made). Health is
updated only after an actual attempt.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from media_os_api.db.models import ResearchSource, ResearchSourceCheckpoint
from media_os_api.jobs.errors import PermanentTaskError, PolicyRefusalError, RetryableTaskError
from media_os_api.observability.redaction import redact_text

LIVE_FETCH_ENV_FLAG = "RESEARCH_LIVE_FETCH_ENABLED"
ACTIVE_POLICY_MODES = frozenset({"EXPERIMENTAL", "ENABLED"})
BLOCKED_SOURCE_CODES = frozenset({"REDDIT"})

FAILING_THRESHOLD = 3
BASE_FAILURE_BACKOFF = timedelta(seconds=60)
MAX_FAILURE_BACKOFF = timedelta(hours=6)
MAX_RETRY_AFTER = timedelta(hours=6)


@dataclass(frozen=True)
class CheckpointState:
    """Persisted per-stream fetch position and conditional-request validators."""

    cursor: dict[str, Any] = field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None


def live_fetch_env_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return the worker-level kill switch (default: disabled)."""
    env = os.environ if environ is None else environ
    return str(env.get(LIVE_FETCH_ENV_FLAG, "")).strip().lower() in {"1", "true", "yes"}


def get_research_source(
    session: Session, source_code: str, *, for_update: bool = False
) -> ResearchSource:
    stmt = select(ResearchSource).where(ResearchSource.source_code == source_code)
    if for_update:
        stmt = stmt.with_for_update()
    source = session.execute(stmt).scalar_one_or_none()
    if source is None:
        raise PermanentTaskError(
            f"Unknown research source '{source_code}'.", code="UNKNOWN_RESEARCH_SOURCE"
        )
    return source


def assert_live_fetch_permitted(
    source: ResearchSource, *, env_enabled: bool, now: datetime
) -> None:
    """Raise unless every live-fetch switch is on. Never issues network traffic itself."""
    if source.source_code in BLOCKED_SOURCE_CODES or source.policy_mode == "BLOCKED":
        raise PolicyRefusalError(
            f"Research source '{source.source_code}' is BLOCKED by policy.",
            code="SOURCE_POLICY_BLOCKED",
        )
    if source.policy_mode not in ACTIVE_POLICY_MODES:
        raise PolicyRefusalError(
            f"Research source '{source.source_code}' is {source.policy_mode}.",
            code="SOURCE_DISABLED",
        )
    if not source.live_fetch_approved:
        raise PolicyRefusalError(
            f"Live fetch for '{source.source_code}' has not been approved by the owner.",
            code="LIVE_FETCH_NOT_APPROVED",
        )
    if not env_enabled:
        raise PolicyRefusalError(
            f"{LIVE_FETCH_ENV_FLAG} is not enabled on this worker.",
            code="LIVE_FETCH_KILL_SWITCH",
        )
    if source.backoff_until is not None and source.backoff_until > now:
        raise RetryableTaskError(
            f"Research source '{source.source_code}' is backing off until "
            f"{source.backoff_until.isoformat()}.",
            code="SOURCE_BACKOFF_ACTIVE",
        )


def load_checkpoint(session: Session, source_code: str, stream_name: str) -> CheckpointState:
    row = session.execute(
        select(ResearchSourceCheckpoint).where(
            ResearchSourceCheckpoint.source_code == source_code,
            ResearchSourceCheckpoint.stream_name == stream_name,
        )
    ).scalar_one_or_none()
    if row is None:
        return CheckpointState()
    return CheckpointState(
        cursor=dict(row.cursor or {}), etag=row.etag, last_modified=row.last_modified
    )


def save_checkpoint(
    session: Session,
    source_code: str,
    stream_name: str,
    state: CheckpointState,
    *,
    now: datetime,
    success: bool = True,
) -> None:
    """Upsert a checkpoint inside the caller's transaction (commit with the records it covers)."""
    values: dict[str, Any] = {
        "source_code": source_code,
        "stream_name": stream_name[:128],
        "cursor": dict(state.cursor),
        "etag": state.etag[:512] if state.etag else None,
        "last_modified": state.last_modified[:128] if state.last_modified else None,
        "last_fetched_at": now,
        "updated_at": now,
    }
    if success:
        values["last_success_at"] = now
    stmt = pg_insert(ResearchSourceCheckpoint).values(**values)
    update_cols = {k: stmt.excluded[k] for k in values if k not in {"source_code", "stream_name"}}
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_research_source_checkpoints_source_stream", set_=update_cols
        )
    )


def record_fetch_success(
    session: Session,
    source_code: str,
    *,
    stats: Mapping[str, Any],
    now: datetime,
    degraded_error: tuple[str, str] | None = None,
) -> ResearchSource:
    """Record a run in which at least one stream succeeded.

    `degraded_error=(code, message)` marks a partial run (some streams failed): health becomes
    DEGRADED and the error is kept for visibility, but no backoff is applied because the source
    itself is reachable.
    """
    source = get_research_source(session, source_code, for_update=True)
    source.consecutive_failures = 0
    source.last_attempt_at = now
    source.last_success_at = now
    source.last_run_stats = dict(stats)
    source.backoff_until = None
    if degraded_error is None:
        source.health_status = "HEALTHY"
    else:
        source.health_status = "DEGRADED"
        source.last_failure_at = now
        source.last_error_code = degraded_error[0][:64]
        source.last_error_message = redact_text(degraded_error[1])[:500]
    session.flush()
    return source


def compute_failure_backoff(
    consecutive_failures: int, retry_after: timedelta | None = None
) -> timedelta:
    """Exponential per-source backoff, overridden (and capped) by a server Retry-After."""
    if retry_after is not None and retry_after > timedelta(0):
        return min(retry_after, MAX_RETRY_AFTER)
    exponent = min(max(consecutive_failures - 1, 0), 20)  # bounded shift; result capped below
    return min(BASE_FAILURE_BACKOFF * (1 << exponent), MAX_FAILURE_BACKOFF)


def record_fetch_failure(
    session: Session,
    source_code: str,
    *,
    error_code: str,
    message: str,
    now: datetime,
    stats: Mapping[str, Any] | None = None,
    retry_after: timedelta | None = None,
) -> ResearchSource:
    """Record a failed attempt: DEGRADED below the threshold, FAILING at/after it."""
    source = get_research_source(session, source_code, for_update=True)
    source.consecutive_failures += 1
    source.health_status = (
        "FAILING" if source.consecutive_failures >= FAILING_THRESHOLD else "DEGRADED"
    )
    source.last_attempt_at = now
    source.last_failure_at = now
    source.last_error_code = error_code[:64]
    source.last_error_message = redact_text(message)[:500]
    if stats is not None:
        source.last_run_stats = dict(stats)
    source.backoff_until = now + compute_failure_backoff(source.consecutive_failures, retry_after)
    session.flush()
    return source
