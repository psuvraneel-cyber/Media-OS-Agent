"""Sanitized persistent AuditEvent writer and query helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from media_os_api.db.models import AuditEvent
from media_os_api.observability.redaction import redact_mapping, redact_text


def record_audit_event(
    session: Session,
    *,
    actor_id: str,
    action: str,
    target_type: str,
    target_id: str,
    outcome: str,
    request_id: str = "-",
    approval_reference: str | None = None,
    before_hash: str | None = None,
    after_hash: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    retention_days: int = 365,
    now: datetime | None = None,
) -> AuditEvent:
    """Persist an AuditEvent after recursively scrubbing all sensitive fields."""
    current = (now or datetime.now(UTC)).astimezone(UTC)
    safe_metadata = redact_mapping(dict(metadata or {}))
    event = AuditEvent(
        actor_id=redact_text(actor_id),
        action=action,
        target_type=target_type,
        target_id=target_id,
        timestamp=current,
        request_id=request_id,
        approval_reference=approval_reference,
        before_hash=before_hash,
        after_hash=after_hash,
        outcome=outcome,
        event_metadata=safe_metadata,
        retention_expires_at=current + timedelta(days=retention_days),
    )
    session.add(event)
    session.flush()
    return event


def list_recent_audit_events(session: Session, *, limit: int = 50) -> list[AuditEvent]:
    """Return most recent persisted audit events bounded safely."""
    bounded = max(1, min(limit, 100))
    return list(
        session.execute(select(AuditEvent).order_by(desc(AuditEvent.timestamp)).limit(bounded))
        .scalars()
        .all()
    )
