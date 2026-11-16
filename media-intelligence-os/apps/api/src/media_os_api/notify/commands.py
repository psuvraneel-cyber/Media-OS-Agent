"""Telegram bot commands: ask Media OS how it's doing, any time, from your phone.

`media_os.notify.telegram_commands` runs every minute. It reads new messages sent to the bot and
answers commands from the owner's chat only (TELEGRAM_CHAT_ID); anything from another chat is
ignored without a reply, so nobody else can query the system.

* /status: health (worker, scheduler, Postiz, status sync), last post, next post, anything stuck
  or failed in the last 24 h, and the review queue.
* /queue: the next scheduled posts.
* /help (and /start): what the bot can do.

The read position (Telegram's update offset) is kept in the job's own result, so no extra storage
is needed: each run continues from the last successful one. Messages older than 10 minutes are
skipped rather than answered late (e.g. the first run after a long outage).

Read-only: commands never change content, schedules or settings.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from media_os_api.db.models import ContentPackage, JobRun, PublishSchedule
from media_os_api.jobs.errors import PermanentTaskError, RetryableTaskError
from media_os_api.notify import messages as msg
from media_os_api.notify.telegram import TelegramClient, TelegramError, telegram_from_env

COMMANDS_TASK = "media_os.notify.telegram_commands"
WATCHDOG_TASK = "media_os.notify.watchdog"
SYNC_TASK = "media_os.publish.sync_status"
MAX_MESSAGE_AGE = timedelta(minutes=10)
STUCK_AFTER = timedelta(minutes=10)

TelegramFactory = Callable[[], TelegramClient | None]

HELP = (
    "🤖 <b>Media OS bot</b>\n"
    "/status · health, last and next post, anything stuck or failed\n"
    "/queue · the next scheduled posts\n"
    "/help · this message\n\n"
    "You'll also get alerts here automatically when something goes wrong, and a report every "
    "night at 22:00 IST."
)


def _telegram() -> TelegramClient | None:
    return telegram_from_env()


def _ago(then: datetime | None, now: datetime) -> str:
    if then is None:
        return "never"
    minutes = int((now - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} h ago" if hours < 48 else f"{hours // 24} days ago"


def _last_success(session: Session, task: str) -> JobRun | None:
    return session.execute(
        select(JobRun)
        .where(JobRun.task_name == task, JobRun.status == "SUCCEEDED")
        .order_by(JobRun.worker_finished_at.desc().nulls_last())
        .limit(1)
    ).scalar_one_or_none()


def _title_of(session: Session, row: PublishSchedule) -> str:
    pkg = session.get(ContentPackage, row.content_package_id)
    return msg.short(pkg.title if pkg else None, 50)


def status_text(session: Session, now: datetime) -> str:
    lines = ["🩺 <b>Media OS status</b>"]

    # The worker is answering this message, so it's alive. The scheduler is alive if the
    # watchdog it schedules every 5 min ran recently.
    lines.append("✅ Worker: running")
    watchdog = _last_success(session, WATCHDOG_TASK)
    wd_at = watchdog.worker_finished_at if watchdog else None
    healthy_scheduler = wd_at is not None and now - wd_at <= timedelta(minutes=12)
    lines.append(
        f"{'✅' if healthy_scheduler else '🔴'} Scheduler: last check {msg.e(_ago(wd_at, now))}"
    )
    postiz = (
        str((watchdog.result_payload or {}).get("postiz", "unknown")) if watchdog else "unknown"
    )
    lines.append(
        f"{'✅' if postiz == 'ok' else '🔴'} Postiz: "
        + ("answering" if postiz == "ok" else msg.e(postiz))
    )
    sync = _last_success(session, SYNC_TASK)
    sync_at = sync.worker_finished_at if sync else None
    sync_ok = sync_at is not None and now - sync_at <= timedelta(minutes=45)
    lines.append(f"{'✅' if sync_ok else '🟠'} Post status checks: {msg.e(_ago(sync_at, now))}")

    last = session.execute(
        select(PublishSchedule)
        .where(PublishSchedule.status == "PUBLISHED")
        .order_by(PublishSchedule.scheduled_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is not None:
        link = (
            f' · <a href="{msg.e(last.remote_post_id)}">link</a>'
            if (last.remote_post_id or "").startswith("http")
            else ""
        )
        lines.append(
            f"\n<b>Last post:</b> {msg.e(msg.when_ist(last.scheduled_at))} · "
            f"{msg.e(msg.platform(last.platform))}: {msg.e(_title_of(session, last))}{link}"
        )
    nxt = session.execute(
        select(PublishSchedule)
        .where(PublishSchedule.status == "SCHEDULED", PublishSchedule.scheduled_at > now)
        .order_by(PublishSchedule.scheduled_at)
        .limit(1)
    ).scalar_one_or_none()
    lines.append(
        f"<b>Next post:</b> {msg.e(msg.when_ist(nxt.scheduled_at))} · "
        f"{msg.e(msg.platform(nxt.platform))}: {msg.e(_title_of(session, nxt))}"
        if nxt is not None
        else "<b>Next post:</b> nothing scheduled"
    )

    stuck = session.execute(
        select(func.count())
        .select_from(PublishSchedule)
        .where(
            PublishSchedule.status.in_(("SCHEDULED", "OVERDUE")),
            PublishSchedule.scheduled_at <= now - STUCK_AFTER,
            PublishSchedule.scheduled_at >= now - timedelta(hours=24),
        )
    ).scalar_one()
    failed = session.execute(
        select(func.count())
        .select_from(PublishSchedule)
        .where(
            PublishSchedule.status == "FAILED",
            PublishSchedule.scheduled_at >= now - timedelta(hours=24),
        )
    ).scalar_one()
    if stuck or failed:
        lines.append(f"\n⚠️ Last 24 h: {stuck} stuck · {failed} failed")
    else:
        lines.append("\n👍 Nothing stuck or failed in the last 24 h")
    awaiting = session.execute(
        select(func.count())
        .select_from(ContentPackage)
        .where(ContentPackage.status == "QA_PASSED", ContentPackage.approval_status == "PENDING")
    ).scalar_one()
    if awaiting:
        lines.append(f"👀 {awaiting} carousel(s) waiting for your review")
    return "\n".join(lines)


def queue_text(session: Session, now: datetime, limit: int = 10) -> str:
    rows = list(
        session.execute(
            select(PublishSchedule)
            .where(PublishSchedule.status == "SCHEDULED", PublishSchedule.scheduled_at > now)
            .order_by(PublishSchedule.scheduled_at, PublishSchedule.platform)
            .limit(limit)
        ).scalars()
    )
    if not rows:
        return "🗓️ <b>Queue</b>\nNothing scheduled."
    lines = [f"🗓️ <b>Next {len(rows)} scheduled post(s)</b>"]
    for row in rows:
        lines.append(
            f"• {msg.e(msg.when_ist(row.scheduled_at))} · {msg.e(msg.platform(row.platform))}: "
            f"{msg.e(_title_of(session, row))}"
        )
    return "\n".join(lines)


def reply_for(session: Session, text: str, now: datetime) -> str:
    command = text.strip().split()[0].split("@")[0].lower() if text.strip() else ""
    if command == "/status":
        return status_text(session, now)
    if command == "/queue":
        return queue_text(session, now)
    return HELP  # /help, /start, and anything else


def handle_telegram_commands(
    session: Session,
    job: JobRun,
    *,
    telegram: TelegramFactory = _telegram,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    previous = _last_success(session, COMMANDS_TASK)
    offset = (previous.result_payload or {}).get("offset") if previous else None
    offset = offset if isinstance(offset, int) else None
    try:
        client = telegram()
    except TelegramError as exc:
        raise PermanentTaskError(str(exc), code=exc.code) from None
    if client is None:
        return {"offset": offset, "handled": 0, "reason": "TELEGRAM_NOT_CONFIGURED"}
    handled = ignored = 0
    try:
        updates = client.get_updates(offset=offset)
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = max(offset or 0, update_id + 1)
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat") or {}
            sent_at = message.get("date")
            text = str(message.get("text") or "")
            if str(chat.get("id")) != client.chat_id:
                ignored += 1  # someone else found the bot: never answer them
                continue
            if (
                not isinstance(sent_at, int)
                or now - datetime.fromtimestamp(sent_at, UTC) > MAX_MESSAGE_AGE
            ):
                continue
            if not text.startswith("/"):
                continue
            client.send_message(reply_for(session, text, now))
            handled += 1
    except TelegramError as exc:
        if exc.retryable:
            raise RetryableTaskError(str(exc), code=exc.code) from None
        raise PermanentTaskError(str(exc), code=exc.code) from None
    finally:
        client.close()
    return {"offset": offset, "handled": handled, "ignored_other_chats": ignored}
