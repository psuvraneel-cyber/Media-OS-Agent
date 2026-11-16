"""Owner-facing alert texts (Telegram HTML). Pure functions; every dynamic value is escaped.

Wording is plain language: say what happened, what it means, and the one thing to do next.
"""

from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

if TYPE_CHECKING:  # type-only: analytics.service imports publish → notify (import cycle)
    from media_os_api.analytics.service import PeriodTotals

PLATFORM_LABEL = {"INSTAGRAM": "Instagram", "FACEBOOK": "Facebook", "YOUTUBE": "YouTube"}
IST = ZoneInfo("Asia/Kolkata")


def e(value: object) -> str:
    """Escape for Telegram HTML."""
    return escape(str(value), quote=False)


def when_ist(value: datetime, tz: str = "Asia/Kolkata") -> str:
    local = value.astimezone(ZoneInfo(tz))
    return local.strftime("%d %b, %I:%M %p").lstrip("0").replace(" 0", " ")


def platform(value: str) -> str:
    return PLATFORM_LABEL.get(value.upper(), value.title())


def short(title: str | None, limit: int = 90) -> str:
    text = " ".join((title or "Untitled post").split())
    text = text.replace("*", "")  # hook titles store *emphasis* markers
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ------------------------------------------------------------------ Postiz / Meta errors


def _meta_errors(raw: str) -> list[dict[str, Any]]:
    """Meta Graph errors buried in Postiz's error JSON (`details[].json` → `{error: {...}}`)."""
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            inner = node.get("json")
            if isinstance(inner, str):
                try:
                    parsed = json.loads(inner)
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                    found.append(parsed["error"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    with contextlib.suppress(ValueError, TypeError):
        walk(json.loads(raw))
    return found


def explain_postiz_error(raw: str | None) -> str:
    """A plain-language reason and fix for a Postiz post error (unescaped text)."""
    text = raw or ""
    errors = _meta_errors(text)
    message = " ".join(str(err.get("message") or "") for err in errors) or text
    codes = {err.get("code") for err in errors}
    low = message.lower()
    if "cannot call api for app" in low:
        return (
            "Meta blocked the app from posting for your account. In the Meta developer "
            "dashboard, make sure the app is Live (or your account has a role on it), then "
            "reconnect the channel in Postiz."
        )
    if 190 in codes or "access token" in low or "session has expired" in low:
        return "The Meta login for this channel expired. Reconnect the channel in Postiz."
    if codes & {4, 17, 32, 613} or "rate limit" in low or "request limit" in low:
        return "Meta's rate limit was hit. It usually clears within an hour; reschedule the post."
    if (
        9004 in codes
        or ("media" in low and any(w in low for w in ("download", "fetch", "url")))
        or "only photo or video" in low
    ):
        return (
            "Meta couldn't download the images. Check that the GitHub Pages media site is "
            "online, then reschedule."
        )
    if codes & {10, 200} or "permission" in low:
        return (
            "Meta refused a permission for this action. Check the app's permissions in the Meta "
            "developer dashboard and reconnect the channel in Postiz."
        )
    if not text.strip():
        return "Postiz marked the post as failed without giving a reason."
    snippet = re.sub(r"\s+", " ", message).strip()[:180]
    return f"Postiz reported: {snippet}"


# ------------------------------------------------------------------ alert texts


@dataclass(frozen=True)
class PostRef:
    title: str | None
    brand: str
    platform: str
    scheduled_at: datetime
    url: str | None = None


def post_published(post: PostRef, late_by_minutes: int = 0) -> str:
    late = f" ({late_by_minutes} min late)" if late_by_minutes >= 10 else ""
    link = f'\n<a href="{e(post.url)}">View the post</a>' if post.url else ""
    return (
        f"✅ <b>Posted on {e(platform(post.platform))}</b>{late}\n"
        f"{e(short(post.title))}\n<i>{e(post.brand)}</i>{link}"
    )


def post_failed(post: PostRef, raw_error: str | None) -> str:
    return (
        f"🔴 <b>Post failed on {e(platform(post.platform))}</b>\n"
        f"{e(short(post.title))}\n<i>{e(post.brand)} · was due {e(when_ist(post.scheduled_at))}"
        f" IST</i>\n\n<b>Why:</b> {e(explain_postiz_error(raw_error))}\n\n"
        "Once that's fixed, reschedule it from Approvals → Approved."
    )


def post_stuck(post: PostRef, minutes_late: int) -> str:
    return (
        f"🟠 <b>Post hasn't gone out</b> ({minutes_late} min past its time)\n"
        f"{e(short(post.title))}\n<i>{e(post.brand)} · {e(platform(post.platform))} · due "
        f"{e(when_ist(post.scheduled_at))} IST</i>\n\n"
        "Postiz still has it queued, which usually means its publishing worker is down or the PC "
        "was asleep. The self-healer restarts Postiz automatically; if this keeps happening, "
        "restart the Postiz app container."
    )


def job_failed(task_name: str, code: str | None, message: str | None) -> str:
    return (
        "🔴 <b>A background job failed for good</b>\n"
        f"<code>{e(task_name)}</code>\n"
        f"<b>Error:</b> {e(code or 'UNKNOWN')} {e((message or '')[:200])}\n\n"
        "It won't retry by itself. Check Runtime Logs in the dashboard."
    )


def postiz_unreachable(detail: str) -> str:
    return (
        "🔴 <b>Postiz isn't responding</b>\n"
        f"{e(detail[:200])}\n\n"
        "Scheduled posts can't go out until it's back. Check that Docker Desktop is running and "
        "the postiz-runtime containers are up."
    )


def channel_disabled(name: str, provider: str) -> str:
    return (
        f"🟠 <b>{e(platform(provider))} channel disabled in Postiz</b>\n"
        f"{e(name)}\n\nPosts to it will fail. Re-enable or reconnect it in Postiz."
    )


def sync_stale(minutes: int) -> str:
    return (
        "🟠 <b>Post status checks have stopped</b>\n"
        f"The last successful check was {minutes} min ago (it normally runs every 15 min).\n\n"
        "Published or failed posts won't be noticed until it recovers. Check that the "
        "media-os-scheduler and media-os-worker containers are running."
    )


@dataclass(frozen=True)
class DigestItem:
    title: str | None
    platform: str
    at: datetime
    url: str | None = None
    note: str | None = None


CROSS_LABEL = {
    "views": "Views",
    "likes_reactions": "Likes & reactions",
    "comments": "Comments",
    "shares": "Shares",
    "saves": "Saves",
}


def number(value: float) -> str:
    return f"{round(value):,}"


def change(current: float, growth: float | None) -> str:
    if growth is None:
        return ""
    if growth == 0:
        return " (=)"
    arrow = "▲" if growth > 0 else "▼"
    return f" ({arrow}{abs(growth):.0f}%)"


def metrics_block(totals: PeriodTotals | None, *, days: int = 7) -> list[str]:
    """The 'last N days' numbers for the nightly report (lines of Telegram HTML)."""
    from media_os_api.analytics.metrics import METRIC_LABELS

    lines = [f"\n📈 <b>Last {days} days</b> (vs the {days} days before)"]
    if totals is None or not totals.platforms:
        lines.append("No published posts with numbers yet.")
        return lines
    if totals.overall:
        lines.append(
            "<b>All platforms:</b> "
            + " · ".join(
                f"{e(CROSS_LABEL[m.key])} {number(m.value)}{change(m.value, m.growth)}"
                for m in totals.overall
            )
        )
    for p in totals.platforms:
        parts = [
            f"{e(METRIC_LABELS.get(m.key, m.key))} {number(m.value)}{change(m.value, m.growth)}"
            for m in p.metrics
        ]
        body = " · ".join(parts) if parts else "no numbers yet"
        lines.append(f"• <b>{e(platform(p.platform))}</b> ({p.posts} posts): {body}")
    if totals.top_post and (totals.top_post.engagement or totals.top_post.views):
        t = totals.top_post
        link = f' · <a href="{e(t.url)}">link</a>' if t.url else ""
        score = (
            f"{number(t.engagement)} interactions" if t.engagement else f"{number(t.views)} views"
        )
        where = f"{e(platform(t.platform))}, {score}"
        lines.append(f"🏆 <b>Top post:</b> {e(short(t.title, 60))} ({where}){link}")
    notes = []
    if "FACEBOOK" in totals.platforms_seen:
        notes.append("Facebook comments and shares aren't available through Postiz")
    if "YOUTUBE" not in totals.platforms_seen:
        notes.append("YouTube isn't connected")
    if totals.data_as_of:
        notes.append(f"numbers as of {when_ist(totals.data_as_of)} IST")
    if any(m.approximate for p in totals.platforms for m in p.metrics):
        notes.append("≈ some values are split across a period edge")
    if notes:
        lines.append(f"<i>{e('; '.join(notes))}.</i>")
    return lines


def daily_digest(
    *,
    day: datetime,
    published: Iterable[DigestItem],
    failed: Iterable[DigestItem],
    upcoming: Iterable[DigestItem],
    awaiting_review: int,
    dashboard_url: str,
    totals: PeriodTotals | None = None,
) -> str:
    """The 22:00 IST nightly report: today's activity plus the last 7 days of numbers."""
    pub, bad, nxt = list(published), list(failed), list(upcoming)
    lines = [f"📊 <b>Media OS nightly report · {e(day.astimezone(IST).strftime('%a %d %b'))}</b>"]
    lines.append(f"\n<b>Posted today:</b> {len(pub)}")
    for item in pub[:10]:
        link = f' · <a href="{e(item.url)}">link</a>' if item.url else ""
        lines.append(f"• {e(platform(item.platform))}: {e(short(item.title, 60))}{link}")
    if bad:
        lines.append(f"\n<b>Failed:</b> {len(bad)}")
        for item in bad[:10]:
            lines.append(f"• {e(platform(item.platform))}: {e(short(item.title, 60))}")
    lines.append(f"\n<b>Coming up (next 24 h):</b> {len(nxt)}")
    for item in nxt[:10]:
        lines.append(
            f"• {e(when_ist(item.at))} · {e(platform(item.platform))}: {e(short(item.title, 50))}"
        )
    if awaiting_review:
        lines.append(
            f"\n👀 <b>{awaiting_review}</b> carousel(s) waiting for your review: "
            f'<a href="{e(dashboard_url)}/approvals">open Approvals</a>'
        )
    if not pub and not nxt:
        lines.append("\nNothing posted today and nothing scheduled for tomorrow.")
    lines.extend(metrics_block(totals))
    return "\n".join(lines)
