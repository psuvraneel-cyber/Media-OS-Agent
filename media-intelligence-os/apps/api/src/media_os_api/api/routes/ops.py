"""Owner-requested host actions (migration 0013).

* `POST /api/v1/system/postiz/restart` (CSRF) — ask for the Postiz app container to be restarted.
* `GET  /api/v1/system/postiz/restart` — the latest request and how it went, for polling.

The API never touches Docker: it only records the request. The Windows helper task
(`infra/ops/windows/ops-requests.ps1`, every minute) restarts `postiz-runtime-app` and writes the
outcome back. Repeated clicks reuse a request that's still waiting or running.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.api.routes.content import _db
from media_os_api.auth import SessionPayload, require_csrf, require_session
from media_os_api.db.models import OpsRequest
from media_os_api.services.audit_service import record_audit_event

router = APIRouter(tags=["ops"])

RESTART_POSTIZ = "RESTART_POSTIZ"
OPEN_STATES = ("PENDING", "RUNNING")
# The helper runs every minute; a request still waiting after this means it isn't running.
HELPER_LATE_AFTER = timedelta(minutes=3)
RECENT_RESTART = timedelta(minutes=2)


class RestartState(BaseModel):
    id: str | None
    status: str | None  # PENDING | RUNNING | DONE | FAILED | EXPIRED, or None if never requested
    requested_at: str | None
    finished_at: str | None
    detail: str | None
    helper_late: bool = False


def _state(row: OpsRequest | None, now: datetime) -> RestartState:
    if row is None:
        return RestartState(id=None, status=None, requested_at=None, finished_at=None, detail=None)
    return RestartState(
        id=str(row.id),
        status=row.status,
        requested_at=row.requested_at.isoformat(),
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
        detail=row.result_detail,
        helper_late=row.status == "PENDING" and now - row.requested_at > HELPER_LATE_AFTER,
    )


def _latest(db: Session, *, open_only: bool = False) -> OpsRequest | None:
    stmt = select(OpsRequest).where(OpsRequest.action == RESTART_POSTIZ)
    if open_only:
        stmt = stmt.where(OpsRequest.status.in_(OPEN_STATES))
    return db.execute(stmt.order_by(OpsRequest.requested_at.desc()).limit(1)).scalar()


@router.post("/system/postiz/restart", response_model=RestartState, status_code=202)
def request_postiz_restart(
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
) -> RestartState:
    now = datetime.now(UTC)
    with _db(request) as db:
        open_row = _latest(db, open_only=True)
        if open_row is not None:
            return _state(open_row, now)
        last = _latest(db)
        if (
            last is not None
            and last.status == "DONE"
            and last.finished_at
            and now - last.finished_at < RECENT_RESTART
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "JUST_RESTARTED",
                    "message": "Postiz was restarted less than 2 minutes ago. Give it a minute to "
                    "start up.",
                },
            )
        row = OpsRequest(
            action=RESTART_POSTIZ,
            status="PENDING",
            requested_by=f"owner:{session.subject}",
            requested_at=now,
        )
        db.add(row)
        db.flush()
        record_audit_event(
            db,
            actor_id=f"owner:{session.subject}",
            action="POSTIZ_RESTART_REQUESTED",
            target_type="ops_request",
            target_id=str(row.id),
            outcome="SUCCESS",
        )
        return _state(row, now)


@router.get("/system/postiz/restart", response_model=RestartState)
def postiz_restart_state(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
) -> RestartState:
    with _db(request) as db:
        return _state(_latest(db), datetime.now(UTC))
