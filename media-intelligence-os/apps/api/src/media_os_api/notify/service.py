"""Owner alerts: queue them durably, send them, watch publishing, and send the nightly report.

* `queue_alert` turns an alert into a durable job (`media_os.notify.telegram`). The job key is
  the alert key, so the same alert is never sent twice (e.g. the watchdog and the status sync both
  noticing one stuck post), and a Telegram outage only delays alerts: they retry with backoff.
* `handle_send_telegram` sends one alert. Without TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID it skips
  quietly, so Media OS works the same with or without Telegram.
* `handle_watchdog` (every 5 min) checks what the 15-min status sync can't: a post still queued in
  Postiz 10 min after its time (the publishing worker is down), Postiz not answering, a disabled
  channel, and the status sync itself having stopped.
* `handle_daily_digest` (22:00 IST, the nightly report) summarises the day and the last 7 days of
  platform numbers (views, likes and reactions, comments, shares, saves, reach).

Alert texts live in `notify.messages`. Nothing here changes content or publishing state.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from media_os_api.db.models import (
    BrandProfile,
    ContentPackage,
    JobRun,
    PublishSchedule,
    ScheduleDefinition,
)
from media_os_api.jobs.errors import PermanentTaskError, RetryableTaskError
from media_os_api.jobs.ledger import enqueue_durable_job
from media_os_api.jobs.routing import queue_for_task
from media_os_api.notify import messages as msg
from media_os_api.notify.telegram import TelegramClient, TelegramError, telegram_from_env

logger = logging.getLogger("media_os_api.notify")

NOTIFY_TASK = "media_os.notify.telegram"
WATCHDOG_TASK = "media_os.notify.watchdog"
DIGEST_TASK = "media_os.notify.daily_digest"
NOTIFY_PREFIX = "media_os.notify."

STUCK_AFTER = timedelta(minutes=10)
STUCK_LOOKBACK = timedelta(hours=24)
SYNC_STALE_AFTER = timedelta(minutes=45)
SYNC_TASK = "media_os.publish.sync_status"  # mirrored here to avoid importing publish.service
DEFAULT_DASHBOARD_URL = "http://127.0.0.1:3100"

TelegramFactory = Callable[[], TelegramClient | None]


def _telegram() -> TelegramClient | None:
    return telegram_from_env()


def dashboard_url() -> str:
    return (os.environ.get("MEDIA_OS_DASHBOARD_URL") or DEFAULT_DASHBOARD_URL).rstrip("/")


# ------------------------------------------------------------------ queue + send


def queue_alert(
    session: Session,
    *,
    key: str,
    text: str,
    severity: str = "info",
    silent: bool = False,
) -> bool:
    """Queue one alert inside the caller's transaction. Returns False if it was already queued.

    The key identifies the event, not the wording: the same stuck post seen 15 and then 45 min
    late is one alert. So an existing job with the key wins, whatever its text (the job ledger
    would otherwise refuse a reused key with a different payload).
    """
    idempotency_key = f"notify:{key}"[:192]
    exists = session.execute(
        select(JobRun.id).where(JobRun.idempotency_key == idempotency_key)
    ).first()
    if exists is not None:
        return False
    _job, created = enqueue_durable_job(
        session,
        task_name=NOTIFY_TASK,
        task_type="NOTIFY",
        payload={"text": text, "severity": severity, "silent": silent},
        idempotency_key=idempotency_key,
        queue_name=queue_for_task(NOTIFY_TASK),
        maximum_attempts=6,
    )
    return created


def handle_send_telegram(
    _session: Session, job: JobRun, *, telegram: TelegramFactory = _telegram
) -> dict[str, Any]:
    payload = dict(job.payload or {})
    text = str(payload.get("text") or "").strip()
    if not text:
        raise PermanentTaskError("Alert has no text.", code="NOTIFY_EMPTY")
    try:
        client = telegram()
    except TelegramError as exc:  # malformed token / chat id: fix the .env, retrying won't help
        raise PermanentTaskError(str(exc), code=exc.code) from None
    if client is None:
        return {"sent": False, "reason": "TELEGRAM_NOT_CONFIGURED"}
    try:
        sent = client.send_message(text, silent=bool(payload.get("silent")))
    except TelegramError as exc:
        if exc.retryable:
            raise RetryableTaskError(str(exc), code=exc.code) from None
        raise PermanentTaskError(str(exc), code=exc.code) from None
    finally:
        client.close()
    return {"sent": True, "message_id": sent.message_id, "severity": payload.get("severity")}


# ------------------------------------------------------------------ alerts about posts


def post_ref(session: Session, row: PublishSchedule) -> msg.PostRef:
    pkg = session.get(ContentPackage, row.content_package_id)
    brand = session.get(BrandProfile, row.brand_profile_id)
    url = row.remote_post_id if (row.remote_post_id or "").startswith("http") else None
    return msg.PostRef(
        title=pkg.title if pkg else None,
        brand=brand.name if brand else "Media OS",
        platform=row.platform,
        scheduled_at=row.scheduled_at,
        url=url,
    )


def alert_post_status(
    session: Session,
    row: PublishSchedule,
    new_status: str,
    post: dict[str, Any] | None,
    now: datetime,
) -> None:
    """Called by the status sync when a schedule row changes status."""
    if new_status == "PUBLISHED":
        late = int(max(0, (now - row.scheduled_at).total_seconds()) // 60)
        queue_alert(
            session,
            key=f"published:{row.id}",
            text=msg.post_published(post_ref(session, row), late_by_minutes=late),
            severity="success",
            silent=True,
        )
    elif new_status == "FAILED":
        raw = str((post or {}).get("error") or "")
        queue_alert(
            session,
            key=f"failed:{row.id}:{row.scheduled_at.isoformat()}",
            text=msg.post_failed(post_ref(session, row), raw),
            severity="error",
        )
    elif new_status == "OVERDUE":
        minutes = int((now - row.scheduled_at).total_seconds() // 60)
        queue_alert(
            session,
            key=f"stuck:{row.id}",
            text=msg.post_stuck(post_ref(session, row), minutes),
            severity="warning",
        )


def alert_job_failed(session: Session, job: JobRun) -> None:
    """Called when a durable job ends FAILED_FINAL (never for alert jobs themselves)."""
    if job.task_name.startswith(NOTIFY_PREFIX):
        return
    queue_alert(
        session,
        key=f"job-failed:{job.id}",
        text=msg.job_failed(job.task_name, job.last_error_code, job.last_error_message),
        severity="error",
    )


# ------------------------------------------------------------------ watchdog


# Returns a Postiz client (publish.postiz.PostizClient or a test fake) with integrations(),
# posts_between() and close().
PostizFactory = Callable[[], Any]


def _postiz() -> Any:
    from media_os_api.publish.postiz import PostizClient

    return PostizClient(
        base_url=os.environ.get("POSTIZ_API_BASE", "http://host.docker.internal:4007"),
        api_key=os.environ.get("POSTIZ_API_KEY", ""),
    )


def handle_watchdog(
    session: Session,
    job: JobRun,
    *,
    postiz: PostizFactory = _postiz,
    now: datetime | None = None,
) -> dict[str, Any]:
    from media_os_api.publish.postiz import PostizError

    now = now or datetime.now(UTC)
    hour = now.astimezone(UTC).strftime("%Y%m%d%H")
    result: dict[str, Any] = {"stuck": 0, "postiz": "ok", "sync": "ok", "disabled_channels": 0}

    # 1. Is the status sync itself still running?
    sync_enabled = session.execute(
        select(func.count())
        .select_from(ScheduleDefinition)
        .where(ScheduleDefinition.task_name == SYNC_TASK, ScheduleDefinition.is_enabled.is_(True))
    ).scalar_one()
    if sync_enabled:
        last_ok = session.execute(
            select(func.max(JobRun.worker_finished_at)).where(
                JobRun.task_name == SYNC_TASK, JobRun.status == "SUCCEEDED"
            )
        ).scalar_one()
        if last_ok is None or now - last_ok > SYNC_STALE_AFTER:
            minutes = int((now - last_ok).total_seconds() // 60) if last_ok else 999
            queue_alert(
                session, key=f"sync-stale:{hour}", text=msg.sync_stale(minutes), severity="warning"
            )
            result["sync"] = f"stale {minutes} min"

    # 2. Posts past their time that Postiz still hasn't published.
    candidates = list(
        session.execute(
            select(PublishSchedule).where(
                PublishSchedule.status.in_(("SCHEDULED", "OVERDUE")),
                PublishSchedule.scheduled_at <= now - STUCK_AFTER,
                PublishSchedule.scheduled_at >= now - STUCK_LOOKBACK,
                PublishSchedule.postiz_post_id.is_not(None),
            )
        ).scalars()
    )
    client = postiz()
    try:
        try:
            integrations = list(client.integrations())
            posts: list[dict[str, Any]] = []
            if candidates:
                start = min(r.scheduled_at for r in candidates) - timedelta(days=1)
                posts = client.posts_between(start, now + timedelta(days=1))
        except PostizError as exc:
            queue_alert(
                session,
                key=f"postiz-down:{hour}",
                text=msg.postiz_unreachable(f"{exc.code}: {exc}"),
                severity="error",
            )
            result["postiz"] = exc.code
            return result

        day = now.astimezone(UTC).strftime("%Y%m%d")
        for integ in integrations:
            if getattr(integ, "disabled", False) and str(integ.provider) in (
                "instagram",
                "instagram-standalone",
                "facebook",
            ):
                queue_alert(
                    session,
                    key=f"channel-disabled:{integ.id}:{day}",
                    text=msg.channel_disabled(integ.name, integ.provider),
                    severity="warning",
                )
                result["disabled_channels"] += 1

        by_id = {str(p.get("id")): p for p in posts if p.get("id")}
        for row in candidates:
            post = by_id.get(row.postiz_post_id or "")
            state = str((post or {}).get("state") or "").upper()
            if post is None or state in ("PUBLISHED", "ERROR"):
                continue  # deleted, published or failed: the status sync reports those
            minutes = int((now - row.scheduled_at).total_seconds() // 60)
            queue_alert(
                session,
                key=f"stuck:{row.id}",
                text=msg.post_stuck(post_ref(session, row), minutes),
                severity="warning",
            )
            result["stuck"] += 1
    finally:
        client.close()
    return result


# ------------------------------------------------------------------ daily digest


def handle_daily_digest(
    session: Session, job: JobRun, *, now: datetime | None = None, tz: str = "Asia/Kolkata"
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    zone = ZoneInfo(tz)
    local_day = now.astimezone(zone).date()
    day_start = datetime.combine(local_day, time.min, tzinfo=zone).astimezone(UTC)

    def items(*conditions: Any) -> list[msg.DigestItem]:
        rows = session.execute(
            select(PublishSchedule, ContentPackage.title)
            .join(ContentPackage, ContentPackage.id == PublishSchedule.content_package_id)
            .where(*conditions)
            .order_by(PublishSchedule.scheduled_at, PublishSchedule.platform)
        ).all()
        return [
            msg.DigestItem(
                title=title,
                platform=row.platform,
                at=row.scheduled_at,
                url=row.remote_post_id if (row.remote_post_id or "").startswith("http") else None,
            )
            for row, title in rows
        ]

    published = items(
        PublishSchedule.status == "PUBLISHED",
        PublishSchedule.scheduled_at >= day_start - timedelta(hours=6),
        PublishSchedule.scheduled_at <= now,
    )
    failed = items(
        PublishSchedule.status == "FAILED",
        PublishSchedule.scheduled_at >= day_start,
        PublishSchedule.scheduled_at <= now,
    )
    upcoming = items(
        PublishSchedule.status == "SCHEDULED",
        PublishSchedule.scheduled_at > now,
        PublishSchedule.scheduled_at <= now + timedelta(hours=24),
    )
    awaiting = session.execute(
        select(func.count())
        .select_from(ContentPackage)
        .where(ContentPackage.status == "QA_PASSED", ContentPackage.approval_status == "PENDING")
    ).scalar_one()
    # Last 7 days of platform numbers. Imported here: analytics.service imports the publish
    # service, which imports this module.
    from media_os_api.analytics.service import period_totals

    totals = period_totals(session, now=now, days=7)
    queue_alert(
        session,
        key=f"digest:{local_day.isoformat()}:{job.id}",
        text=msg.daily_digest(
            day=now,
            published=published,
            failed=failed,
            upcoming=upcoming,
            awaiting_review=int(awaiting),
            dashboard_url=dashboard_url(),
            totals=totals,
        ),
        severity="info",
        silent=True,
    )
    return {
        "published": len(published),
        "failed": len(failed),
        "upcoming": len(upcoming),
        "awaiting_review": int(awaiting),
        "week": {m.key: round(m.value) for m in totals.overall},
    }
