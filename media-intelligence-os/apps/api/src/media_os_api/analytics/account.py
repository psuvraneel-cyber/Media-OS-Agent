"""Account-wide statistics for each connected channel (all posts, including ones published
outside Media OS), read from Postiz's channel analytics.

Job `media_os.analytics.sync_account_metrics` (daily 21:35 IST, and on "Sync now") stores the
last-7 and last-30 day totals per metric as `AccountMetricSnapshot` rows, one per IST day.

What Postiz returns (Meta Graph API):
* Instagram: likes, views, comments, shares, saves, replies as one total for the whole window,
  plus reach (and follower_count once the account has 100+ followers) as daily values.
* Facebook page: media views, unique media viewers, post engagements, new follows, all daily.

Daily values are summed over the window. Sums of daily unique counts (reach, unique viewers)
aren't unique people, so they're stored but never added across platforms or shown as reach.
Instagram's account insights can lag up to ~48 hours behind post-level numbers.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from media_os_api.db.models import AccountMetricSnapshot, ChannelAccount, JobRun
from media_os_api.jobs.errors import RetryableTaskError
from media_os_api.publish.postiz import PostizError
from media_os_api.publish.service import ClientFactory, _clients

logger = logging.getLogger("media_os_api.analytics.account")

ACCOUNT_TASK = "media_os.analytics.sync_account_metrics"
WINDOWS = (7, 30)
IST = ZoneInfo("Asia/Kolkata")
PLATFORMS = ("INSTAGRAM", "FACEBOOK")

_LABELS: dict[str, dict[str, str]] = {
    "INSTAGRAM": {
        "views": "views",
        "likes": "likes",
        "comments": "comments",
        "shares": "shares",
        "saves": "saves",
        "replies": "replies",
        "reach": "reach_daily_sum",
        "follower count": "new_followers",
        "follower_count": "new_followers",
    },
    "FACEBOOK": {
        "media views": "views",
        "page impressions": "unique_viewers_daily_sum",
        "posts engagement": "engagements",
        "page followers": "new_followers",
    },
}

# Shown in the dashboard and the report, in this order. Daily-unique sums are left out.
DISPLAY: dict[str, list[tuple[str, str]]] = {
    "INSTAGRAM": [
        ("views", "Views"),
        ("likes", "Likes"),
        ("comments", "Comments"),
        ("shares", "Shares"),
        ("saves", "Saves"),
        ("new_followers", "New followers"),
    ],
    "FACEBOOK": [
        ("views", "Media views"),
        ("engagements", "Engagements"),
        ("new_followers", "New followers"),
    ],
}
# Metrics that mean the same on both platforms, so they can be added up.
COMBINED = ("views", "new_followers")


def account_metric_key(platform: str, label: str) -> str | None:
    norm = (label or "").strip().lower()
    if not norm:
        return None
    known = _LABELS.get(platform.upper(), {})
    if norm in known:
        return known[norm]
    slug = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")
    return f"other_{slug}"[:64] if slug else None


def _number(raw: Any) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return value if value.is_finite() and value >= 0 else None


def window_totals(platform: str, payload: list[dict[str, Any]]) -> dict[str, tuple[Decimal, str]]:
    """Postiz `[{label, data: [{total, date}]}]` → {metric_key: (sum over the window, label)}."""
    out: dict[str, tuple[Decimal, str]] = {}
    for item in payload:
        label = str(item.get("label") or "")
        key = account_metric_key(platform, label)
        points = item.get("data")
        if key is None or not isinstance(points, list):
            continue
        values = [
            v for p in points if isinstance(p, dict) and (v := _number(p.get("total"))) is not None
        ]
        if not values:
            continue
        out[key] = (sum(values, Decimal(0)), label[:128])
    return out


def ist_today(now: datetime) -> date:
    return now.astimezone(IST).date()


def handle_sync_account_metrics(
    session: Session,
    job: JobRun,
    *,
    clients: ClientFactory = _clients,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    today = ist_today(now)
    channels = list(
        session.execute(
            select(ChannelAccount).where(
                ChannelAccount.postiz_integration_id.is_not(None),
                ChannelAccount.platform.in_(PLATFORMS),
                ChannelAccount.status == "CONNECTED",
            )
        ).scalars()
    )
    if not channels:
        return {"channels": 0, "stored": 0, "errors": []}
    host, postiz = clients()
    stored, errors, retryable = 0, [], 0
    try:
        for ch in channels:
            for days in WINDOWS:
                try:
                    payload = postiz.account_analytics(str(ch.postiz_integration_id), days)
                except PostizError as exc:
                    errors.append({"channel": str(ch.id), "window": days, "code": exc.code})
                    retryable += int(exc.retryable)
                    continue
                for key, (value, label) in window_totals(ch.platform, payload).items():
                    stmt = insert(AccountMetricSnapshot).values(
                        id=uuid.uuid4(),
                        brand_profile_id=ch.brand_profile_id,
                        channel_account_id=ch.id,
                        platform=ch.platform,
                        metric_key=key,
                        raw_label=label,
                        window_days=days,
                        value=value,
                        observed_on=today,
                        observed_at=now,
                    )
                    session.execute(
                        stmt.on_conflict_do_update(
                            constraint="uq_account_metric_channel_metric_window_day",
                            set_={"value": value, "raw_label": label, "observed_at": now},
                        )
                    )
                    stored += 1
    finally:
        host.close()
        postiz.close()
    attempts = len(channels) * len(WINDOWS)
    if errors and len(errors) == attempts and retryable:
        raise RetryableTaskError("Postiz account analytics unavailable.", code="POSTIZ_ACCOUNT")
    return {"channels": len(channels), "stored": stored, "errors": errors, "day": today.isoformat()}


# --------------------------------------------------------------------------- read side


def account_report(session: Session, brand_id: uuid.UUID, window_days: int) -> dict[str, Any]:
    """Latest stored totals per connected channel for one window, for dashboard and report."""
    latest = (
        select(
            AccountMetricSnapshot.channel_account_id,
            func.max(AccountMetricSnapshot.observed_on).label("day"),
        )
        .where(
            AccountMetricSnapshot.brand_profile_id == brand_id,
            AccountMetricSnapshot.window_days == window_days,
        )
        .group_by(AccountMetricSnapshot.channel_account_id)
        .subquery()
    )
    rows = session.execute(
        select(AccountMetricSnapshot, ChannelAccount)
        .join(
            latest,
            (latest.c.channel_account_id == AccountMetricSnapshot.channel_account_id)
            & (latest.c.day == AccountMetricSnapshot.observed_on),
        )
        .join(ChannelAccount, ChannelAccount.id == AccountMetricSnapshot.channel_account_id)
        .where(AccountMetricSnapshot.window_days == window_days)
        .order_by(ChannelAccount.platform)
    ).all()
    channels: dict[uuid.UUID, dict[str, Any]] = {}
    for snap, ch in rows:
        entry = channels.setdefault(
            ch.id,
            {
                "channel_id": str(ch.id),
                "platform": ch.platform,
                "name": ch.external_display_name or ch.external_username or ch.platform.title(),
                "observed_on": snap.observed_on.isoformat(),
                "observed_at": snap.observed_at.isoformat(),
                "metrics": {},
            },
        )
        entry["metrics"][snap.metric_key] = float(snap.value)
    out = list(channels.values())
    for entry in out:
        entry["display"] = [
            {"key": k, "label": label, "value": entry["metrics"].get(k)}
            for k, label in DISPLAY.get(entry["platform"], [])
        ]
    combined = {
        k: sum(e["metrics"].get(k, 0.0) for e in out)
        if any(k in e["metrics"] for e in out)
        else None
        for k in COMBINED
    }
    return {"window_days": window_days, "channels": out, "combined": combined}


def account_channels_all(session: Session, window_days: int) -> list[dict[str, Any]]:
    """Latest totals for every brand's channels (the nightly report covers all brands)."""
    brand_ids = session.execute(
        select(AccountMetricSnapshot.brand_profile_id)
        .where(AccountMetricSnapshot.window_days == window_days)
        .distinct()
    ).scalars()
    out: list[dict[str, Any]] = []
    for bid in brand_ids:
        out.extend(account_report(session, bid, window_days)["channels"])
    return out
