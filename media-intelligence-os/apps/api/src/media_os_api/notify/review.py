"""Review carousels from your phone: a Telegram card with the slides, the caption, the checks and
Approve / Reject / Edit caption buttons.

* When a carousel's slides finish rendering and pass every check, the renderer queues
  `media_os.notify.review_card` (worker: it holds the Telegram token and can read the slides).
  The worker sends the slides as an album, then a card with the buttons.
* Button presses and replies arrive through the same 1-minute Telegram poll as /status
  (`notify.commands`), from the owner's chat only.
  - **Approve**: the same rules as the dashboard (`decide_package`), including auto-scheduling.
  - **Reject**: the bot asks for a reason (reply, or "skip"); the reason reaches the writer.
  - **Edit caption**: the bot sends the current caption and asks for the new one (reply); the
    edit goes through `edit_package`, so the checks run again and a fresh card follows.
* Replies are matched to their carousel by a reference in the question (`[ref:rj:<id>]`), so no
  extra state is stored.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from media_os_api.db.models import ContentPackage, JobRun
from media_os_api.jobs.errors import PermanentTaskError, RetryableTaskError
from media_os_api.jobs.ledger import enqueue_durable_job
from media_os_api.jobs.routing import queue_for_task
from media_os_api.notify import messages as msg
from media_os_api.notify.telegram import TelegramClient, TelegramError, telegram_from_env

REVIEW_TASK = "media_os.notify.review_card"
ACTOR = "owner:telegram"
_REF = re.compile(r"\[ref:(rj|ed):([0-9a-f-]{36})\]")
LANGUAGE = {"BN": "Bengali", "EN": "English", "BN_EN": "Bilingual"}


def enqueue_review_card(session: Session, pkg: ContentPackage) -> None:
    """Called when a carousel becomes ready for review (one card per version)."""
    enqueue_durable_job(
        session,
        task_name=REVIEW_TASK,
        task_type="NOTIFY",
        payload={"content_package_id": str(pkg.id)},
        idempotency_key=f"review-card:{pkg.id}:v{pkg.version}",
        brand_profile_id=pkg.brand_profile_id,
        queue_name=queue_for_task(REVIEW_TASK),
        maximum_attempts=4,
    )


def review_buttons(pkg_id: uuid.UUID | str) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "✅ Approve", "callback_data": f"ap:{pkg_id}"},
            {"text": "❌ Reject", "callback_data": f"rj:{pkg_id}"},
        ],
        [
            {"text": "✏️ Edit caption", "callback_data": f"ed:{pkg_id}"},
            {"text": "Open in Media OS", "url": f"{_dashboard()}/approvals"},
        ],
    ]


def _dashboard() -> str:
    from media_os_api.notify.service import dashboard_url

    return dashboard_url()


def card_text(pkg: ContentPackage) -> str:
    tags = dict(pkg.post_tags or {})
    qa = dict(pkg.qa_report or {})
    warns = [c for c in qa.get("checks", []) if c.get("status") == "WARN"]
    score = dict(pkg.score or {})
    learned = score.get("learned") or {}
    lines = [
        "🗳️ <b>Ready for your review</b>",
        f"<b>{msg.e(msg.short(pkg.title or pkg.hook, 90))}</b>",
        " · ".join(
            msg.e(x)
            for x in (
                str(tags.get("pillar", "")).replace("_", " "),
                LANGUAGE.get(str(tags.get("language")), str(tags.get("language", ""))),
                f"{len(pkg.slide_data or [])} slides",
            )
            if x
        ),
    ]
    if learned.get("active") and learned.get("predicted_lift"):
        lines.append(f"🔮 Expected: {learned['predicted_lift']:.1f}× your usual views")
    elif score.get("total") is not None:
        lines.append(f"Score {score['total']} (heuristic, not a reach prediction)")
    if warns:
        lines.append(
            "⚠️ " + msg.e("; ".join(str(c.get("detail") or c.get("name")) for c in warns)[:300])
        )
    caption = (pkg.caption or "").strip()
    if caption:
        lines += ["", "<b>Caption</b>", msg.e(caption[:700] + ("…" if len(caption) > 700 else ""))]
    if pkg.hashtags:
        lines.append(msg.e(" ".join(pkg.hashtags)))
    lines += [
        "",
        "Approving posts nothing by itself: it's scheduled in Media OS (or auto-scheduled).",
    ]
    return "\n".join(lines)


def handle_review_card(
    session: Session, job: JobRun, *, telegram: Any = telegram_from_env
) -> dict[str, Any]:
    from media_os_api.carousel.service import active_slides
    from media_os_api.content.uploads import resolve_asset_path

    try:
        pkg_id = uuid.UUID(str((job.payload or {}).get("content_package_id")))
    except ValueError as exc:
        raise PermanentTaskError("Invalid payload.", code="BAD_PAYLOAD") from exc
    pkg = session.get(ContentPackage, pkg_id)
    if pkg is None or pkg.status != "QA_PASSED" or pkg.approval_status != "PENDING":
        return {"sent": False, "reason": "NOT_WAITING_FOR_REVIEW"}
    try:
        client = telegram()
    except TelegramError as exc:
        raise PermanentTaskError(str(exc), code=exc.code) from None
    if client is None:
        return {"sent": False, "reason": "TELEGRAM_NOT_CONFIGURED"}
    photos = []
    for a in active_slides(session, pkg):
        path = resolve_asset_path(a.local_path)
        if path is not None and path.is_file() and path.stat().st_size > 0:
            photos.append(path.read_bytes())
    try:
        client.send_photos(photos, silent=True)
        sent = client.send_message(card_text(pkg), buttons=review_buttons(pkg.id))
    except TelegramError as exc:
        if exc.retryable:
            raise RetryableTaskError(str(exc), code=exc.code) from None
        raise PermanentTaskError(str(exc), code=exc.code) from None
    finally:
        client.close()
    return {"sent": True, "slides": len(photos), "message_id": sent.message_id}


# --------------------------------------------------------------------------- owner actions


def _pkg(session: Session, raw: str) -> ContentPackage | None:
    try:
        return session.get(ContentPackage, uuid.UUID(raw))
    except ValueError:
        return None


def handle_button(session: Session, client: TelegramClient, query: dict[str, Any]) -> str:
    """One button press from the owner's chat. Returns what was done (for the job result)."""
    from media_os_api.carousel.service import DecisionError, decide_package

    data = str(query.get("data") or "")
    action, _, raw = data.partition(":")
    message = query.get("message") or {}
    message_id = message.get("message_id") if isinstance(message.get("message_id"), int) else None
    pkg = _pkg(session, raw)
    if action not in ("ap", "rj", "ed") or pkg is None:
        client.answer_button(str(query.get("id")), "That carousel no longer exists.")
        return "unknown"
    title = msg.e(msg.short(pkg.title or pkg.hook, 60))
    if action == "ap":
        try:
            with session.begin_nested():
                scheduled = decide_package(session, pkg, approve=True, reason=None, actor_id=ACTOR)
        except DecisionError as exc:
            client.answer_button(str(query.get("id")), str(exc))
            return f"approve-refused:{exc.code}"
        client.answer_button(str(query.get("id")), "Approved")
        if message_id:
            client.replace_buttons(message_id, None)
        client.send_message(
            f"✅ Approved: {title}\n"
            + (
                "Auto-scheduling put it in the next free posting slot."
                if scheduled
                else "Schedule it under Approvals → Approved (or turn on auto-scheduling)."
            ),
            silent=True,
        )
        return "approved"
    if pkg.approval_status != "PENDING":
        client.answer_button(str(query.get("id")), f"Already {pkg.approval_status.lower()}.")
        return "already-decided"
    client.answer_button(str(query.get("id")))
    if action == "rj":
        client.ask(
            f"❌ Why reject <b>{title}</b>? Reply to this message with the reason (it's given to "
            f"the writer for the next drafts), or reply <i>skip</i>.\n[ref:rj:{pkg.id}]",
            placeholder="Reason, or skip",
        )
        return "asked-reason"
    client.ask(
        f"✏️ New caption for <b>{title}</b>: reply to this message with the full caption. "
        "Copy the current one below and change it; keep the Google Play link.\n"
        f"[ref:ed:{pkg.id}]\n\n<pre>{msg.e((pkg.caption or '')[:3000])}</pre>",
        placeholder="The new caption",
    )
    return "asked-caption"


def handle_reply(session: Session, client: TelegramClient, message: dict[str, Any]) -> str | None:
    """A reply to one of the bot's questions. None when it isn't one."""
    from media_os_api.carousel.service import (
        DecisionError,
        EditError,
        decide_package,
        edit_package,
    )

    asked = message.get("reply_to_message") or {}
    match = _REF.search(str(asked.get("text") or ""))
    if not match:
        return None
    action, raw = match.groups()
    pkg = _pkg(session, raw)
    text = str(message.get("text") or "").strip()
    if pkg is None:
        client.send_message("That carousel no longer exists.", silent=True)
        return "unknown"
    title = msg.e(msg.short(pkg.title or pkg.hook, 60))
    if action == "rj":
        reason = None if text.lower() in ("skip", "-", "") else text
        try:
            with session.begin_nested():
                decide_package(session, pkg, approve=False, reason=reason, actor_id=ACTOR)
        except DecisionError as exc:
            client.send_message(f"Couldn't reject {title}: {msg.e(str(exc))}", silent=True)
            return f"reject-refused:{exc.code}"
        client.send_message(
            f"❌ Rejected: {title}" + ("\nThe writer will see your reason." if reason else ""),
            silent=True,
        )
        return "rejected"
    if not text:
        client.send_message("The caption was empty, so nothing changed.", silent=True)
        return "empty-caption"
    try:
        with session.begin_nested():
            qa = edit_package(session, pkg, actor_id=ACTOR, caption=text)
    except EditError as exc:
        client.send_message(f"Couldn't change the caption: {msg.e(str(exc))}", silent=True)
        return f"edit-refused:{exc.code}"
    if qa is None:
        client.send_message("That's the same caption, so nothing changed.", silent=True)
        return "unchanged"
    if qa.passed:
        client.send_message(
            f"✏️ Caption updated for {title}. The checks passed; a fresh review card follows once "
            "the slides are ready again.",
            silent=True,
        )
        return "edited"
    problems = "; ".join(f"{c.name}: {c.detail}" for c in qa.failures)[:500]
    client.send_message(
        f"✏️ Caption saved for {title}, but it didn't pass the checks: {msg.e(problems)}\n"
        "Fix it in Approvals, or press Edit caption again.",
        silent=True,
    )
    return "edited-failed-qa"


def received_at(message: dict[str, Any]) -> datetime | None:
    sent = message.get("date")
    return datetime.fromtimestamp(sent, UTC) if isinstance(sent, int) else None
