"""Persist every AI attempt to the `model_usages` ledger (metadata only; zero cost)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from media_os_api.ai.router import AttemptCallback
from media_os_api.ai.types import AttemptRecord, GenerationRequest
from media_os_api.db.models import ModelUsage
from media_os_api.db.session import session_scope

_QUOTA_OUTCOMES = {"QUOTA", "RATE_LIMITED", "SKIPPED_QUOTA"}


def usage_recorder(
    session_factory: sessionmaker[Session], *, brand_profile_id: uuid.UUID | None = None
) -> AttemptCallback:
    """Callback for `AIRouter(on_attempt=...)`; each attempt is committed independently."""

    def record(request: GenerationRequest, attempt: AttemptRecord) -> None:
        with session_scope(session_factory) as db:
            db.add(
                ModelUsage(
                    brand_profile_id=brand_profile_id,
                    provider=attempt.provider,
                    project_alias="free-tier",
                    model_id=attempt.model[:128],
                    operation=f"{request.task.value}:{request.operation}"[:64],
                    input_tokens=attempt.input_tokens,
                    output_tokens=attempt.output_tokens,
                    estimated_cost=Decimal("0"),
                    quota_status="LIMITED" if attempt.outcome in _QUOTA_OUTCOMES else "OK",
                    request_status=attempt.outcome[:32],
                )
            )

    return record
