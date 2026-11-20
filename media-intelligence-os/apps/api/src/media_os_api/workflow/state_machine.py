"""Validated ContentPackage workflow state machine with transition history and idempotency."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.db.models import AuditEvent, ContentPackage, ContentStateTransition


class ContentLifecycleStatus(StrEnum):
    """Roadmap lifecycle statuses for ContentPackage entities."""

    DISCOVERED = "DISCOVERED"
    NORMALIZED = "NORMALIZED"
    DEDUPED = "DEDUPED"
    RANKED = "RANKED"
    RESEARCHED = "RESEARCHED"
    DRAFTED = "DRAFTED"
    RENDERED = "RENDERED"
    QA_PASSED = "QA_PASSED"
    POSTIZ_DRAFT = "POSTIZ_DRAFT"
    USER_APPROVED = "USER_APPROVED"
    SCHEDULED = "SCHEDULED"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    METRICS_CAPTURED = "METRICS_CAPTURED"
    ARCHIVED = "ARCHIVED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    REJECTED = "REJECTED"
    PAUSED = "PAUSED"


PRIMARY_PIPELINE_ORDER: tuple[ContentLifecycleStatus, ...] = (
    ContentLifecycleStatus.DISCOVERED,
    ContentLifecycleStatus.NORMALIZED,
    ContentLifecycleStatus.DEDUPED,
    ContentLifecycleStatus.RANKED,
    ContentLifecycleStatus.RESEARCHED,
    ContentLifecycleStatus.DRAFTED,
    ContentLifecycleStatus.RENDERED,
    ContentLifecycleStatus.QA_PASSED,
    ContentLifecycleStatus.POSTIZ_DRAFT,
    ContentLifecycleStatus.USER_APPROVED,
    ContentLifecycleStatus.SCHEDULED,
    ContentLifecycleStatus.PUBLISHING,
    ContentLifecycleStatus.PUBLISHED,
    ContentLifecycleStatus.METRICS_CAPTURED,
    ContentLifecycleStatus.ARCHIVED,
)

STATIC_ALLOWED_TRANSITIONS: dict[ContentLifecycleStatus, frozenset[ContentLifecycleStatus]] = {
    ContentLifecycleStatus.DISCOVERED: frozenset(
        {
            ContentLifecycleStatus.NORMALIZED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.NORMALIZED: frozenset(
        {
            ContentLifecycleStatus.DEDUPED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.DEDUPED: frozenset(
        {
            ContentLifecycleStatus.RANKED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.RANKED: frozenset(
        {
            ContentLifecycleStatus.RESEARCHED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.RESEARCHED: frozenset(
        {
            ContentLifecycleStatus.DRAFTED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.DRAFTED: frozenset(
        {
            ContentLifecycleStatus.RENDERED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.RENDERED: frozenset(
        {
            ContentLifecycleStatus.QA_PASSED,
            ContentLifecycleStatus.DRAFTED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.QA_PASSED: frozenset(
        {
            ContentLifecycleStatus.DRAFTED,  # the owner edited the text before approving
            ContentLifecycleStatus.POSTIZ_DRAFT,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.POSTIZ_DRAFT: frozenset(
        {
            ContentLifecycleStatus.USER_APPROVED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.USER_APPROVED: frozenset(
        {
            ContentLifecycleStatus.SCHEDULED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.SCHEDULED: frozenset(
        {
            ContentLifecycleStatus.PUBLISHING,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.PAUSED,
        }
    ),
    ContentLifecycleStatus.PUBLISHING: frozenset(
        {
            ContentLifecycleStatus.PUBLISHED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
        }
    ),
    ContentLifecycleStatus.PUBLISHED: frozenset(
        {
            ContentLifecycleStatus.METRICS_CAPTURED,
            ContentLifecycleStatus.ARCHIVED,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            ContentLifecycleStatus.FAILED_FINAL,
        }
    ),
    ContentLifecycleStatus.METRICS_CAPTURED: frozenset({ContentLifecycleStatus.ARCHIVED}),
    ContentLifecycleStatus.ARCHIVED: frozenset(),
    ContentLifecycleStatus.REJECTED: frozenset({ContentLifecycleStatus.ARCHIVED}),
    ContentLifecycleStatus.FAILED_FINAL: frozenset({ContentLifecycleStatus.ARCHIVED}),
    # Dynamic resume targets added based on `previous_status`
    ContentLifecycleStatus.FAILED_RETRYABLE: frozenset(
        {
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.ARCHIVED,
        }
    ),
    ContentLifecycleStatus.PAUSED: frozenset(
        {
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.FAILED_FINAL,
            ContentLifecycleStatus.ARCHIVED,
        }
    ),
}

HOLD_STATES = frozenset(
    {
        ContentLifecycleStatus.PAUSED,
        ContentLifecycleStatus.FAILED_RETRYABLE,
    }
)


class WorkflowError(ValueError):
    """Base error for workflow state validation."""


class ContentPackageNotFoundError(WorkflowError):
    """Raised when the target ContentPackage does not exist (or fails brand scoping)."""


class InvalidStateTransitionError(WorkflowError):
    """Raised when an illegal ContentPackage workflow transition is attempted."""


class TransitionIdempotencyConflictError(WorkflowError):
    """Raised when an idempotency key is reused for a different target status."""


@dataclass(frozen=True)
class TransitionResult:
    """Outcome of a validated state transition attempt."""

    content_package_id: uuid.UUID
    from_status: ContentLifecycleStatus
    to_status: ContentLifecycleStatus
    content_version: int
    idempotent_replay: bool
    transition_id: uuid.UUID


def allowed_targets_for(
    current_status: ContentLifecycleStatus,
    *,
    previous_status: ContentLifecycleStatus | None = None,
) -> frozenset[ContentLifecycleStatus]:
    """Compute exact allowed target states from `current_status` and optional resume state."""
    base = set(STATIC_ALLOWED_TRANSITIONS[current_status])
    if (
        current_status in HOLD_STATES
        and previous_status is not None
        and previous_status not in HOLD_STATES
        and previous_status
        not in {
            ContentLifecycleStatus.ARCHIVED,
            ContentLifecycleStatus.REJECTED,
            ContentLifecycleStatus.FAILED_FINAL,
        }
    ):
        base.add(previous_status)
    return frozenset(base)


def is_transition_allowed(
    current_status: ContentLifecycleStatus,
    to_status: ContentLifecycleStatus,
    *,
    previous_status: ContentLifecycleStatus | None = None,
) -> bool:
    """Pure validation helper checking whether a state transition is permitted."""
    return to_status in allowed_targets_for(current_status, previous_status=previous_status)


def transition_content_status(
    session: Session,
    *,
    content_package_id: uuid.UUID,
    to_status: ContentLifecycleStatus,
    actor_id: str,
    reason: str | None = None,
    idempotency_key: str | None = None,
    expected_brand_id: uuid.UUID | None = None,
    request_id: str = "-",
    audit_retention_days: int = 365,
) -> TransitionResult:
    """Validate and atomically apply a ContentPackage state transition with full history."""
    if not actor_id.strip():
        raise WorkflowError("actor_id is required for every workflow transition.")

    stmt = select(ContentPackage).where(ContentPackage.id == content_package_id)
    if expected_brand_id is not None:
        stmt = stmt.where(ContentPackage.brand_profile_id == expected_brand_id)
    pkg = session.execute(stmt.with_for_update()).scalar_one_or_none()
    if pkg is None:
        raise ContentPackageNotFoundError(
            f"ContentPackage {content_package_id} not found within active brand scope."
        )

    normalized_idem = idempotency_key.strip() if idempotency_key is not None else None
    if normalized_idem == "":
        normalized_idem = None

    if normalized_idem is not None:
        existing_transition = session.execute(
            select(ContentStateTransition).where(
                ContentStateTransition.content_package_id == pkg.id,
                ContentStateTransition.idempotency_key == normalized_idem,
            )
        ).scalar_one_or_none()
        if existing_transition is not None:
            if existing_transition.to_status == to_status.value:
                return TransitionResult(
                    content_package_id=pkg.id,
                    from_status=ContentLifecycleStatus(
                        existing_transition.from_status or pkg.status
                    ),
                    to_status=to_status,
                    content_version=existing_transition.content_version,
                    idempotent_replay=True,
                    transition_id=existing_transition.id,
                )
            raise TransitionIdempotencyConflictError(
                f"Idempotency key '{normalized_idem}' was already used for "
                f"transition to {existing_transition.to_status}, not {to_status.value}."
            )

    current = ContentLifecycleStatus(pkg.status)
    previous = ContentLifecycleStatus(pkg.previous_status) if pkg.previous_status else None
    if not is_transition_allowed(current, to_status, previous_status=previous):
        raise InvalidStateTransitionError(
            f"Illegal content workflow transition: {current.value} -> {to_status.value}"
        )

    # Record pre-hold state when entering PAUSED / FAILED_RETRYABLE; clear when leaving
    if to_status in HOLD_STATES:
        if current not in HOLD_STATES:
            pkg.previous_status = current.value
    else:
        pkg.previous_status = None

    pkg.status = to_status.value
    if to_status == ContentLifecycleStatus.USER_APPROVED:
        pkg.approval_status = "APPROVED"
    elif to_status == ContentLifecycleStatus.REJECTED:
        pkg.approval_status = "REJECTED"

    transition = ContentStateTransition(
        content_package_id=pkg.id,
        brand_profile_id=pkg.brand_profile_id,
        from_status=current.value,
        to_status=to_status.value,
        actor_id=actor_id.strip(),
        reason=reason,
        content_version=pkg.version,
        idempotency_key=normalized_idem,
    )
    session.add(transition)
    session.flush()

    now = datetime.now(UTC)
    session.add(
        AuditEvent(
            actor_id=actor_id.strip(),
            action="CONTENT_STATUS_TRANSITION",
            target_type="ContentPackage",
            target_id=str(pkg.id),
            request_id=request_id,
            approval_reference=f"v{pkg.version}"
            if to_status == ContentLifecycleStatus.USER_APPROVED
            else None,
            outcome="SUCCESS",
            event_metadata={
                "brand_profile_id": str(pkg.brand_profile_id),
                "from_status": current.value,
                "to_status": to_status.value,
                "content_version": pkg.version,
                "reason": reason or "",
            },
            retention_expires_at=now + timedelta(days=audit_retention_days),
        )
    )

    return TransitionResult(
        content_package_id=pkg.id,
        from_status=current,
        to_status=to_status,
        content_version=pkg.version,
        idempotent_replay=False,
        transition_id=transition.id,
    )
