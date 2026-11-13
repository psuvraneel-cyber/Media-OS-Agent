"""Owner admin CLI for research sources: status, policy mode, live-fetch approval, schedules.

Every change is recorded as an AuditEvent. Reddit is refused here and by a database CHECK.
Nothing here makes a network request; enabling a schedule only lets the scheduler enqueue jobs.

Usage (inside the API container, against the runtime DB):
    python -m media_os_api.research.admin status
    python -m media_os_api.research.admin set-mode --source HACKERNEWS --mode EXPERIMENTAL
    python -m media_os_api.research.admin approve-live --source HACKERNEWS --approver owner
    python -m media_os_api.research.admin revoke-live --source HACKERNEWS
    python -m media_os_api.research.admin schedule --source HACKERNEWS --enable|--disable
    python -m media_os_api.research.admin run-now --source HACKERNEWS
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.db.models import ResearchSource, ResearchSourceCheckpoint, ScheduleDefinition
from media_os_api.db.session import session_scope
from media_os_api.jobs.ledger import enqueue_durable_job
from media_os_api.jobs.routing import RESEARCH_QUEUE
from media_os_api.jobs.scheduler import compute_next_run_at
from media_os_api.research.fetchers import FETCHERS
from media_os_api.research.jobs import RESEARCH_TASK_TYPE, fetch_task_name
from media_os_api.research.registry import BLOCKED_SOURCE_CODES, get_research_source
from media_os_api.services.audit_service import record_audit_event

ACTOR = "cli:research_admin"
SETTABLE_MODES = ("OFF", "EXPERIMENTAL", "ENABLED")


class AdminError(RuntimeError):
    """Refused or invalid admin operation (message is safe to print)."""


def _require_source(session: Session, code: str) -> ResearchSource:
    code = code.strip().upper()
    if code in BLOCKED_SOURCE_CODES:
        raise AdminError(f"{code} is BLOCKED by policy and cannot be changed here.")
    if code not in FETCHERS:
        raise AdminError(f"Unknown source '{code}'. Known: {', '.join(sorted(FETCHERS))}.")
    return get_research_source(session, code, for_update=True)


def _schedule(session: Session, code: str) -> ScheduleDefinition:
    sched = session.execute(
        select(ScheduleDefinition)
        .where(
            ScheduleDefinition.brand_profile_id.is_(None),
            ScheduleDefinition.task_name == fetch_task_name(code),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if sched is None:
        raise AdminError(f"No global schedule found for {code}.")
    return sched


def _audit(session: Session, action: str, code: str, meta: dict[str, Any], now: datetime) -> None:
    record_audit_event(
        session,
        actor_id=ACTOR,
        action=action,
        target_type="ResearchSource",
        target_id=code,
        outcome="SUCCESS",
        metadata=meta,
        now=now,
    )


def status(factory: sessionmaker[Session]) -> list[dict[str, Any]]:
    with session_scope(factory) as db:
        sources = db.execute(select(ResearchSource).order_by(ResearchSource.source_code)).scalars()
        rows: list[dict[str, Any]] = []
        for s in sources:
            sched = db.execute(
                select(ScheduleDefinition).where(
                    ScheduleDefinition.brand_profile_id.is_(None),
                    ScheduleDefinition.task_name == fetch_task_name(s.source_code),
                )
            ).scalar_one_or_none()
            checkpoints = (
                db.execute(
                    select(ResearchSourceCheckpoint.stream_name).where(
                        ResearchSourceCheckpoint.source_code == s.source_code
                    )
                )
                .scalars()
                .all()
            )
            rows.append(
                {
                    "source": s.source_code,
                    "mode": s.policy_mode,
                    "live_fetch_approved": s.live_fetch_approved,
                    "approved_by": s.approved_by,
                    "health": s.health_status,
                    "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
                    "last_error_code": s.last_error_code,
                    "schedule_enabled": bool(sched and sched.is_enabled),
                    "next_run_at": sched.next_run_at.isoformat() if sched else None,
                    "checkpoints": sorted(checkpoints),
                }
            )
        return rows


def set_mode(factory: sessionmaker[Session], code: str, mode: str, now: datetime) -> None:
    mode = mode.strip().upper()
    if mode not in SETTABLE_MODES:
        raise AdminError(f"Mode must be one of {SETTABLE_MODES}.")
    with session_scope(factory) as db:
        src = _require_source(db, code)
        before = src.policy_mode
        src.policy_mode = mode
        if mode == "OFF":
            # The DB requires an approval to be withdrawn when a source is switched off,
            # and an OFF source must not keep a schedule enabled.
            src.live_fetch_approved = False
            src.approved_by = None
            src.approved_at = None
            src.health_status = "DISABLED"
            _schedule(db, src.source_code).is_enabled = False
        _audit(
            db,
            "RESEARCH_SOURCE_MODE_CHANGED",
            src.source_code,
            {"before": before, "after": mode},
            now,
        )


def approve_live(factory: sessionmaker[Session], code: str, approver: str, now: datetime) -> None:
    approver = approver.strip()
    if not approver:
        raise AdminError("An approver name is required.")
    with session_scope(factory) as db:
        src = _require_source(db, code)
        if src.policy_mode not in ("EXPERIMENTAL", "ENABLED"):
            raise AdminError(
                f"{src.source_code} is {src.policy_mode}; set-mode EXPERIMENTAL first."
            )
        src.live_fetch_approved = True
        src.approved_by = approver[:128]
        src.approved_at = now
        if src.health_status == "DISABLED":
            src.health_status = "UNKNOWN"
        _audit(
            db,
            "RESEARCH_LIVE_FETCH_APPROVED",
            src.source_code,
            {"approved_by": src.approved_by},
            now,
        )


def revoke_live(factory: sessionmaker[Session], code: str, now: datetime) -> None:
    with session_scope(factory) as db:
        src = _require_source(db, code)
        src.live_fetch_approved = False
        src.approved_by = None
        src.approved_at = None
        _schedule(db, src.source_code).is_enabled = False
        _audit(db, "RESEARCH_LIVE_FETCH_REVOKED", src.source_code, {}, now)


def set_schedule(factory: sessionmaker[Session], code: str, enable: bool, now: datetime) -> None:
    with session_scope(factory) as db:
        src = _require_source(db, code)
        sched = _schedule(db, src.source_code)
        if enable:
            if not src.live_fetch_approved or src.policy_mode not in ("EXPERIMENTAL", "ENABLED"):
                raise AdminError(
                    f"{src.source_code} must be EXPERIMENTAL/ENABLED and approved before its "
                    "schedule can be enabled."
                )
            # Next future slot (no burst of missed runs).
            sched.next_run_at = compute_next_run_at(
                sched.schedule_config, sched.timezone, previous_run=now, now=now
            )
        sched.is_enabled = enable
        _audit(
            db,
            "RESEARCH_SCHEDULE_ENABLED" if enable else "RESEARCH_SCHEDULE_DISABLED",
            src.source_code,
            {"next_run_at": sched.next_run_at.isoformat()},
            now,
        )


def run_now(factory: sessionmaker[Session], code: str, now: datetime) -> uuid.UUID:
    """Enqueue one manual fetch (JobRun + OutboxEvent); the gate still applies at execution."""
    with session_scope(factory) as db:
        src = _require_source(db, code)
        job, _created = enqueue_durable_job(
            db,
            task_name=fetch_task_name(src.source_code),
            task_type=RESEARCH_TASK_TYPE,
            payload={"source": src.source_code, "trigger": "manual"},
            idempotency_key=f"research:{src.source_code}:manual:{now.isoformat()}",
            queue_name=RESEARCH_QUEUE,
            maximum_attempts=2,
        )
        _audit(
            db, "RESEARCH_MANUAL_RUN_ENQUEUED", src.source_code, {"job_run_id": str(job.id)}, now
        )
        return job.id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("set-mode")
    p.add_argument("--source", required=True)
    p.add_argument("--mode", required=True, choices=SETTABLE_MODES)
    p = sub.add_parser("approve-live")
    p.add_argument("--source", required=True)
    p.add_argument("--approver", required=True)
    p = sub.add_parser("revoke-live")
    p.add_argument("--source", required=True)
    p = sub.add_parser("schedule")
    p.add_argument("--source", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    p = sub.add_parser("run-now")
    p.add_argument("--source", required=True)
    args = parser.parse_args(argv)

    from media_os_api.config import load_settings
    from media_os_api.db.session import create_db_engine_from_settings, create_session_factory

    engine = create_db_engine_from_settings(load_settings())
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    try:
        if args.cmd == "status":
            print(json.dumps(status(factory), indent=2))
        elif args.cmd == "set-mode":
            set_mode(factory, args.source, args.mode, now)
        elif args.cmd == "approve-live":
            approve_live(factory, args.source, args.approver, now)
        elif args.cmd == "revoke-live":
            revoke_live(factory, args.source, now)
        elif args.cmd == "schedule":
            set_schedule(factory, args.source, bool(args.enable), now)
        elif args.cmd == "run-now":
            print(f"Enqueued job {run_now(factory, args.source, now)}")
    except AdminError as exc:
        print(f"[REFUSED] {exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()
    if args.cmd != "status":
        print("[OK]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
