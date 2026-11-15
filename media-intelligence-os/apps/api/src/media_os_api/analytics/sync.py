"""Job `media_os.analytics.sync_post_metrics`: read post analytics from Postiz, store observations.

Read-only towards every platform: it calls only Postiz `GET /public/v1/analytics/post/{id}` for
posts Media OS published (PublishSchedule.status == PUBLISHED with a Postiz post id). It never
touches the Postiz database.

Idempotency: an observation's `observed_at` is the sync time truncated to the hour (UTC), and rows
are upserted on (schedule, metric, observed_at). Re-running a job inside the same hour
overwrites the same rows with the freshest values; it never adds a second observation.

Failures are per post: one post's error is recorded in the result and the others continue. A
retryable Postiz error on every post (Postiz down, rate-limited) raises so the job ledger retries.

Call budget (Meta rate limits): the job runs every 6 h, but each post is only re-read when its
age tier says it is due — every run for the first 30 days, daily up to 90 days, weekly after
that. Posts whose platform returned nothing (NO_DATA) or that were removed (MISSING) back off to
daily/weekly. At most MAX_POSTS_PER_RUN posts are read per run (stalest first; the rest wait for
the next run) with a pause between calls. Each post's outcome is stored on its PublishSchedule
(`metrics_status`, `metrics_checked_at`) so the UI can say why a post has no numbers.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from media_os_api.analytics.metrics import metric_key
from media_os_api.db.models import JobRun, PostMetricObservation, PublishSchedule
from media_os_api.jobs.errors import PermanentTaskError, RetryableTaskError
from media_os_api.publish.postiz import PostizClient, PostizError
from media_os_api.publish.service import ClientFactory, _clients

logger = logging.getLogger(__name__)

SYNC_METRICS_TASK = "media_os.analytics.sync_post_metrics"
SOURCE = "postiz.public.analytics.post"

# (max post age, minimum time between reads). Intervals sit a little under the nominal cadence so
# scheduler jitter never skips a cycle.
AGE_TIERS: tuple[tuple[timedelta | None, timedelta], ...] = (
    (timedelta(days=30), timedelta(hours=5)),  # every 6-hour run
    (timedelta(days=90), timedelta(hours=23)),  # daily
    (None, timedelta(days=6, hours=20)),  # weekly
)
STATUS_FLOOR = {
    "NO_DATA": timedelta(hours=23),  # platform returned nothing: retry daily, not every run
    "MISSING": timedelta(days=6, hours=20),  # removed on the platform: weekly
}
MAX_POSTS_PER_RUN = 120
PAUSE_SECONDS = 1.0


def check_interval(age: timedelta, status: str | None) -> timedelta:
    interval = AGE_TIERS[-1][1]
    for max_age, every in AGE_TIERS:
        if max_age is None or age <= max_age:
            interval = every
            break
    return max(interval, STATUS_FLOOR.get(status or "", timedelta(0)))


def is_due(sched: PublishSchedule, now: datetime) -> bool:
    if sched.metrics_checked_at is None:
        return True
    interval = check_interval(now - sched.scheduled_at, sched.metrics_status)
    return now - sched.metrics_checked_at >= interval


def observation_hour(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def parse_metrics(platform: str, payload: list[dict[str, Any]]) -> dict[str, tuple[Decimal, str]]:
    """Postiz `[{label, data:[{total, date}]}]` → {metric_key: (value, raw_label)}.

    Meta post insights carry one lifetime value; if several points come back, the last is the
    current one. Non-numeric or negative values are dropped rather than guessed.
    """
    out: dict[str, tuple[Decimal, str]] = {}
    for item in payload:
        label = str(item.get("label") or "")
        key = metric_key(platform, label)
        points = item.get("data") or []
        if key is None or not isinstance(points, list) or not points:
            continue
        raw = points[-1].get("total") if isinstance(points[-1], dict) else None
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            continue
        if not value.is_finite() or value < 0:
            continue
        out[key] = (value, label[:64])
    return out


def handle_sync_post_metrics(
    session: Session,
    job: JobRun,
    *,
    clients: ClientFactory = _clients,
    sleep: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    published = list(
        session.execute(
            select(PublishSchedule).where(
                PublishSchedule.status == "PUBLISHED",
                PublishSchedule.postiz_post_id.is_not(None),
            )
        ).scalars()
    )
    due = [s for s in published if is_due(s, current)]
    # Never-checked first, then the longest-waiting.
    due.sort(key=lambda s: (s.metrics_checked_at is not None, s.metrics_checked_at or current))
    batch = due[:MAX_POSTS_PER_RUN]
    summary: dict[str, Any] = {
        "posts": len(published),
        "due": len(due),
        "checked": len(batch),
        "deferred": len(due) - len(batch),
        "not_due": len(published) - len(due),
        "observations": 0,
    }
    if not batch:
        return summary
    host, postiz = clients()
    host.close()  # GitHub host is not needed for analytics
    observed_at = observation_hour(current)
    errors: list[dict[str, str]] = []
    retryable_failures = 0
    missing: list[str] = []
    no_data: list[str] = []
    try:
        for i, sched in enumerate(batch):
            if i:
                sleep(PAUSE_SECONDS)
            try:
                payload = postiz.post_analytics(str(sched.postiz_post_id))
            except PostizError as exc:
                if exc.code == "POSTIZ_AUTH":
                    raise PermanentTaskError(str(exc), code=exc.code) from exc
                retryable_failures += int(exc.retryable)
                errors.append({"schedule_id": str(sched.id), "code": exc.code})
                # Not marked checked: the next run retries it.
                sched.metrics_status, sched.metrics_detail = "ERROR", exc.code[:200]
                continue
            sched.metrics_checked_at = current
            if payload is None:
                missing.append(str(sched.id))
                sched.metrics_status = "MISSING"
                sched.metrics_detail = "Postiz reports the post no longer exists on the platform."
                continue
            metrics = parse_metrics(sched.platform, payload)
            if not metrics:
                no_data.append(str(sched.id))
                sched.metrics_status = "NO_DATA"
                sched.metrics_detail = (
                    "The platform returned no metrics for this post (it may not support "
                    "insights for this media type, or the insights request was rejected)."
                )
                continue
            summary["observations"] += _upsert(session, sched, metrics, observed_at)
            sched.metrics_status, sched.metrics_detail = "OK", None
        if retryable_failures and retryable_failures == len(batch):
            raise RetryableTaskError(
                "Postiz analytics unavailable for every post.", code="POSTIZ_ANALYTICS_UNAVAILABLE"
            )
        session.flush()
        return {
            **summary,
            "observed_at": observed_at.isoformat(),
            "missing_on_platform": missing,
            "no_data": no_data,
            "errors": errors,
        }
    finally:
        postiz.close()


def _upsert(
    session: Session,
    sched: PublishSchedule,
    metrics: dict[str, tuple[Decimal, str]],
    observed_at: datetime,
) -> int:
    rows = [
        {
            "id": uuid.uuid4(),
            "brand_profile_id": sched.brand_profile_id,
            "channel_account_id": sched.channel_account_id,
            "content_package_id": sched.content_package_id,
            "publish_schedule_id": sched.id,
            "platform": sched.platform,
            "metric_key": key,
            "value_kind": "CUMULATIVE",
            "value": value,
            "observed_at": observed_at,
            "source": SOURCE,
            "source_label": label,
        }
        for key, (value, label) in metrics.items()
    ]
    stmt = insert(PostMetricObservation).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_post_metric_obs_schedule_metric_time",
        set_={"value": stmt.excluded.value, "source_label": stmt.excluded.source_label},
    )
    session.execute(stmt)
    return len(rows)


__all__ = ["SYNC_METRICS_TASK", "handle_sync_post_metrics", "parse_metrics", "PostizClient"]
