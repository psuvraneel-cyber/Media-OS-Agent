"""Brand-scoped views reporting built on `post_metric_observations` and the delta engine.

Every query is filtered by `brand_profile_id` first; nothing here can return another brand's posts.
"Published post" means a PublishSchedule with status PUBLISHED (drafts, scheduled, failed and
cancelled attempts never count). One PublishSchedule is one platform post: a carousel cross-posted
to Instagram and Facebook is two platform posts that share a content package.

Aggregation happens here, in the API, so the browser never receives raw snapshots.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from media_os_api.analytics.engine import (
    REPORTING_TZ,
    Increment,
    Observation,
    RangeKey,
    Window,
    buckets,
    granularity_for,
    growth_pct,
    increments,
    latest_value,
    previous_window,
    range_window,
    total_in,
)
from media_os_api.analytics.metrics import (
    ENGAGEMENT_KEYS,
    METRIC_LABELS,
    VIEW_DEFINITIONS,
    VIEWS,
)
from media_os_api.analytics.sync import SYNC_METRICS_TASK
from media_os_api.db.models import (
    Asset,
    ChannelAccount,
    ContentPackage,
    ContentStateTransition,
    JobRun,
    PostMetricObservation,
    PublishSchedule,
    ResearchSource,
    ScheduleDefinition,
)
from media_os_api.publish.service import OVERDUE_AFTER

SortKey = Literal["views", "engagement_rate", "published"]
BREAKDOWN_TAGS = ("format", "hook_type", "pillar", "occasion", "language", "visual_style")


@dataclass
class PlatformPost:
    schedule: PublishSchedule
    package: ContentPackage
    channel: ChannelAccount | None
    series: dict[str, list[Observation]] = field(default_factory=dict)
    incs: dict[str, list[Increment]] = field(default_factory=dict)

    @property
    def published_at(self) -> datetime:
        return self.schedule.scheduled_at

    def metric_in(self, key: str, window: Window) -> tuple[float, bool]:
        a = total_in(self.incs.get(key, []), window)
        return a.value, a.approximate

    def engagement_in(self, window: Window) -> tuple[float | None, bool]:
        present = [k for k in ENGAGEMENT_KEYS if k in self.incs]
        if not present:
            return None, False
        value, approx = 0.0, False
        for k in present:
            v, a = self.metric_in(k, window)
            value += v
            approx = approx or a
        return value, approx

    def lifetime(self, key: str) -> float | None:
        return latest_value(self.series.get(key, []))

    def lifetime_engagement(self) -> float | None:
        vals = [self.lifetime(k) for k in ENGAGEMENT_KEYS if k in self.series]
        nums = [v for v in vals if v is not None]
        return sum(nums) if nums else None

    @property
    def first_observed_at(self) -> datetime | None:
        firsts = [s[0].at for s in self.series.values() if s]
        return min(firsts) if firsts else None

    @property
    def last_observed_at(self) -> datetime | None:
        lasts = [s[-1].at for s in self.series.values() if s]
        return max(lasts) if lasts else None


# ------------------------------------------------------------------------------------ loading


def _load_posts(
    db: Session,
    brand_id: uuid.UUID,
    *,
    platform: str | None = None,
    channel_id: uuid.UUID | None = None,
    content_format: str | None = None,
    package_id: uuid.UUID | None = None,
) -> list[PlatformPost]:
    stmt = (
        select(PublishSchedule, ContentPackage, ChannelAccount)
        .join(
            ContentPackage,
            (ContentPackage.id == PublishSchedule.content_package_id)
            & (ContentPackage.brand_profile_id == PublishSchedule.brand_profile_id),
        )
        .outerjoin(ChannelAccount, ChannelAccount.id == PublishSchedule.channel_account_id)
        .where(PublishSchedule.brand_profile_id == brand_id, PublishSchedule.status == "PUBLISHED")
    )
    if platform:
        stmt = stmt.where(PublishSchedule.platform == platform)
    if channel_id:
        stmt = stmt.where(PublishSchedule.channel_account_id == channel_id)
    if content_format:
        stmt = stmt.where(ContentPackage.format == content_format)
    if package_id:
        stmt = stmt.where(PublishSchedule.content_package_id == package_id)
    posts = {
        s.id: PlatformPost(schedule=s, package=p, channel=c) for s, p, c in db.execute(stmt).all()
    }
    if not posts:
        return []
    rows = db.execute(
        select(
            PostMetricObservation.publish_schedule_id,
            PostMetricObservation.metric_key,
            PostMetricObservation.observed_at,
            PostMetricObservation.value,
        ).where(
            PostMetricObservation.brand_profile_id == brand_id,
            PostMetricObservation.publish_schedule_id.in_(list(posts)),
            PostMetricObservation.value_kind == "CUMULATIVE",
        )
    ).all()
    for sid, key, at, value in rows:
        posts[sid].series.setdefault(key, []).append(Observation(at, float(value)))
    for post in posts.values():
        for key, series in post.series.items():
            series.sort(key=lambda o: o.at)
            post.incs[key] = increments(series, published_at=post.published_at)
    return list(posts.values())


def _earliest(posts: Sequence[PlatformPost]) -> datetime | None:
    starts = [i.start for p in posts for incs in p.incs.values() for i in incs]
    starts += [p.published_at for p in posts]
    return min(starts) if starts else None


# ------------------------------------------------------------------------------ small helpers


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat() if dt else None


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _channel_name(c: ChannelAccount | None) -> str:
    if c is None:
        return "Unknown account"
    return c.external_display_name or c.external_username or c.platform.title()


def _thumbnail_ids(db: Session, package_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not package_ids:
        return {}
    rows = db.execute(
        select(Asset.content_package_id, Asset.id, Asset.local_path)
        .where(
            Asset.content_package_id.in_(package_ids),
            Asset.asset_type.in_(("CAROUSEL_SLIDE", "REEL_POSTER")),
            Asset.status == "ACTIVE",
        )
        .order_by(Asset.content_package_id, Asset.local_path)
    ).all()
    out: dict[uuid.UUID, str] = {}
    for pid, aid, _path in rows:
        if pid is not None:
            out.setdefault(pid, str(aid))
    return out


def _post_title(pkg: ContentPackage) -> str:
    raw = pkg.title or pkg.hook or pkg.caption or "Untitled post"
    return raw.replace("*", "").strip()[:140]


def sync_status(db: Session) -> dict[str, Any]:
    last_ok = db.execute(
        select(JobRun.worker_finished_at, JobRun.result_payload)
        .where(JobRun.task_name == SYNC_METRICS_TASK, JobRun.status == "SUCCEEDED")
        .order_by(JobRun.worker_finished_at.desc().nulls_last())
        .limit(1)
    ).first()
    last_any = db.execute(
        select(JobRun.status, JobRun.updated_at, JobRun.last_error_code)
        .where(JobRun.task_name == SYNC_METRICS_TASK)
        .order_by(JobRun.updated_at.desc())
        .limit(1)
    ).first()
    # Several schedules can run the same sync (every 6 h, plus the 21:40 IST nightly refresh).
    flags = list(
        db.execute(
            select(ScheduleDefinition.is_enabled).where(
                ScheduleDefinition.task_name == SYNC_METRICS_TASK
            )
        ).scalars()
    )
    return {
        "last_success_at": _iso(last_ok[0]) if last_ok else None,
        "last_attempt_at": _iso(last_any[1]) if last_any else None,
        "last_status": last_any[0] if last_any else None,
        "last_error_code": last_any[2] if last_any else None,
        "schedule_enabled": any(flags),
        "schedule_configured": bool(flags),
        "source": "Postiz public API · GET /analytics/post/{id}",
    }


# ------------------------------------------------------------------------------ cache


class _TTLCache:
    """Tiny per-process cache keyed by request parameters + a data fingerprint."""

    def __init__(self, ttl: float = 60.0, size: int = 256) -> None:
        self._ttl, self._size = ttl, size
        self._data: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._lock:
            hit = self._data.get(key)
            if hit and time.monotonic() - hit[0] < self._ttl:
                return hit[1]
            return None

    def put(self, key: tuple[Any, ...], value: dict[str, Any]) -> None:
        with self._lock:
            if len(self._data) >= self._size:
                self._data.pop(next(iter(self._data)))
            self._data[key] = (time.monotonic(), value)


_cache = _TTLCache()


def _fingerprint(db: Session, brand_id: uuid.UUID) -> tuple[Any, ...]:
    obs = db.execute(
        select(func.count(), func.max(PostMetricObservation.created_at)).where(
            PostMetricObservation.brand_profile_id == brand_id
        )
    ).one()
    pub = db.execute(
        select(func.count(), func.max(PublishSchedule.updated_at)).where(
            PublishSchedule.brand_profile_id == brand_id
        )
    ).one()
    return (obs[0], obs[1], pub[0], pub[1])


# ------------------------------------------------------------------------------ reports


def views_report(
    db: Session,
    brand_id: uuid.UUID,
    *,
    range_key: RangeKey = "30d",
    platform: str | None = None,
    channel_id: uuid.UUID | None = None,
    content_format: str | None = None,
    now: datetime | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    key = (
        "views",
        brand_id,
        range_key,
        platform,
        channel_id,
        content_format,
        _fingerprint(db, brand_id),
    )
    if use_cache and now is None and (cached := _cache.get(key)) is not None:
        return cached
    report = _views_report(
        db,
        brand_id,
        range_key=range_key,
        platform=platform,
        channel_id=channel_id,
        content_format=content_format,
        now=now or datetime.now(UTC),
    )
    if use_cache and now is None:
        _cache.put(key, report)
    return report


def _views_report(
    db: Session,
    brand_id: uuid.UUID,
    *,
    range_key: RangeKey,
    platform: str | None,
    channel_id: uuid.UUID | None,
    content_format: str | None,
    now: datetime,
) -> dict[str, Any]:
    posts = _load_posts(
        db, brand_id, platform=platform, channel_id=channel_id, content_format=content_format
    )
    window = range_window(range_key, now=now, earliest=_earliest(posts))
    prev = previous_window(range_key, window)
    gran = granularity_for(range_key, window)

    views_total, views_prev, approx = 0.0, 0.0, False
    eng_total, eng_prev, eng_seen = 0.0, 0.0, False
    contributing: list[tuple[PlatformPost, float]] = []
    per_platform: dict[str, dict[str, Any]] = {}
    per_account: dict[str, dict[str, Any]] = {}
    tags: dict[str, dict[str, dict[str, float]]] = {
        t: {} for t in (*BREAKDOWN_TAGS, "posting_hour")
    }

    for p in posts:
        pf = p.schedule.platform
        plat = per_platform.setdefault(
            pf,
            {
                "platform": pf,
                "views": 0.0,
                "unique_viewers": None,
                "reach": None,
                "engagement": None,
                "published_posts": 0,
                "posts_with_views": 0,
                "view_definition": VIEW_DEFINITIONS.get(pf, ""),
            },
        )
        plat["published_posts"] += 1
        acc_id = str(p.schedule.channel_account_id)
        acc = per_account.setdefault(
            acc_id,
            {
                "channel_account_id": acc_id,
                "name": _channel_name(p.channel),
                "platform": pf,
                "views": 0.0,
                "unique_viewers": None,
                "published_posts": 0,
                "posts_with_views": 0,
            },
        )
        acc["published_posts"] += 1

        for extra in ("unique_viewers", "reach"):
            if extra in p.incs:
                v, a = p.metric_in(extra, window)
                plat[extra] = (plat[extra] or 0.0) + v
                if extra == "unique_viewers":
                    acc[extra] = (acc[extra] or 0.0) + v
                approx = approx or a
        eng, a = p.engagement_in(window)
        if eng is not None:
            eng_seen = True
            eng_total += eng
            plat["engagement"] = (plat["engagement"] or 0.0) + eng
            approx = approx or a
            if prev:
                eng_prev += p.engagement_in(prev)[0] or 0.0

        if VIEWS not in p.incs:
            continue
        v, a = p.metric_in(VIEWS, window)
        approx = approx or a
        views_total += v
        plat["views"] += v
        plat["posts_with_views"] += 1
        acc["views"] += v
        acc["posts_with_views"] += 1
        if prev:
            views_prev += p.metric_in(VIEWS, prev)[0]
        if v > 0:
            contributing.append((p, v))
        post_tags = p.package.post_tags or {}
        for t in BREAKDOWN_TAGS:
            val = p.package.format if t == "format" else post_tags.get(t)
            if val:
                cell = tags[t].setdefault(str(val), {"views": 0.0, "posts": 0})
                cell["views"] += v
                cell["posts"] += 1
        hour = f"{p.published_at.astimezone(REPORTING_TZ).hour:02d}:00"
        cell = tags["posting_hour"].setdefault(hour, {"views": 0.0, "posts": 0})
        cell["views"] += v
        cell["posts"] += 1

    trend = []
    for day, w in buckets(window, gran):
        bucket_views = sum(p.metric_in(VIEWS, w)[0] for p in posts if VIEWS in p.incs)
        bucket_eng = sum((p.engagement_in(w)[0] or 0.0) for p in posts)
        trend.append(
            {
                "date": day.isoformat(),
                "start": _iso(w.start),
                "end": _iso(w.end),
                "views": _round(bucket_views),
                "engagement": _round(bucket_eng) if eng_seen else None,
            }
        )

    published_in_window = [p for p in posts if window.start <= p.published_at < window.end]
    with_views = [p for p in posts if VIEWS in p.incs]
    top = max(contributing, key=lambda pv: pv[1], default=None)
    thumbs = _thumbnail_ids(db, {top[0].package.id} if top else set())
    first_obs = [p.first_observed_at for p in posts if p.first_observed_at]
    last_obs = [p.last_observed_at for p in posts if p.last_observed_at]
    has_fb_viewers = any("unique_viewers" in p.incs for p in posts)
    sync_states = [p.schedule.metrics_status for p in posts]

    notes = [
        "Views are platform-reported. Each platform post counts separately, so a carousel posted "
        "to Instagram and Facebook contributes both platforms' views.",
        "Platforms report lifetime totals; Media OS stores a snapshot at every sync and counts "
        "the growth between snapshots. Views are attributed to when they were received, so an old "
        "post still adds to the last 7 days if it was viewed then.",
    ]
    if approx:
        notes.append(
            "Some views fall in a sync interval that crosses the period boundary; those are "
            "split proportionally by time, so the period total is an estimate (≈)."
        )
    if has_fb_viewers:
        notes.append(
            "Facebook image posts only report unique viewers, which are shown separately and are "
            "not added to total views."
        )

    return {
        "brand_id": str(brand_id),
        "range": range_key,
        "timezone": "Asia/Kolkata",
        "generated_at": _iso(now),
        "window": {"start": _iso(window.start), "end": _iso(window.end)},
        "previous_window": (
            {"start": _iso(prev.start), "end": _iso(prev.end)} if prev is not None else None
        ),
        "granularity": gran,
        "totals": {
            "views": _round(views_total),
            "views_previous": _round(views_prev) if prev else None,
            "views_growth_pct": growth_pct(views_total, views_prev) if prev else None,
            "engagement": _round(eng_total) if eng_seen else None,
            "engagement_previous": _round(eng_prev) if (prev and eng_seen) else None,
            "engagement_growth_pct": growth_pct(eng_total, eng_prev)
            if (prev and eng_seen)
            else None,
            "approximate": approx,
            "published_posts": len(posts),
            "published_in_period": len(published_in_window),
            "posts_with_view_data": len(with_views),
            "contributing_posts": len(contributing),
            "avg_views_per_post": (_round(views_total / len(with_views)) if with_views else None),
        },
        "trend": trend,
        "by_platform": sorted(
            (
                {
                    **v,
                    "views": _round(v["views"]),
                    "unique_viewers": _round(v["unique_viewers"]),
                    "reach": _round(v["reach"]),
                    "engagement": _round(v["engagement"]),
                    "included_in_total": True,
                }
                for v in per_platform.values()
            ),
            key=lambda r: -(r["views"] or 0),
        ),
        "by_account": sorted(
            (
                {**v, "views": _round(v["views"]), "unique_viewers": _round(v["unique_viewers"])}
                for v in per_account.values()
            ),
            key=lambda r: -(r["views"] or 0),
        ),
        "top_post": (
            {
                **_post_row(top[0], window),
                "thumbnail_asset_id": thumbs.get(top[0].package.id),
            }
            if top
            else None
        ),
        "breakdowns": {
            t: sorted(
                (
                    {"value": k, "views": _round(c["views"]), "posts": int(c["posts"])}
                    for k, c in cells.items()
                ),
                key=lambda r: -(r["views"] or 0),
            )
            for t, cells in tags.items()
        },
        "coverage": {
            "tracking_since": _iso(min(first_obs)) if first_obs else None,
            "no_data_posts": sync_states.count("NO_DATA"),
            "missing_posts": sync_states.count("MISSING"),
            "error_posts": sync_states.count("ERROR"),
            "unchecked_posts": sync_states.count(None),
            "last_observation_at": _iso(max(last_obs)) if last_obs else None,
            "notes": notes,
        },
        "sync": sync_status(db),
    }


def _post_row(p: PlatformPost, window: Window) -> dict[str, Any]:
    views, approx = p.metric_in(VIEWS, window) if VIEWS in p.incs else (None, False)
    eng, eng_approx = p.engagement_in(window)
    life_views = p.lifetime(VIEWS)
    life_eng = p.lifetime_engagement()
    tags = p.package.post_tags or {}
    url = p.schedule.remote_post_id or ""
    return {
        "schedule_id": str(p.schedule.id),
        "content_package_id": str(p.package.id),
        "title": _post_title(p.package),
        "platform": p.schedule.platform,
        "channel_account_id": str(p.schedule.channel_account_id),
        "account_name": _channel_name(p.channel),
        "format": p.package.format,
        "language": tags.get("language"),
        "hook_type": tags.get("hook_type"),
        "published_at": _iso(p.published_at),
        "status": p.schedule.status,
        "post_url": url if url.startswith("http") else None,
        "views_in_period": _round(views),
        "views_approximate": approx,
        "lifetime_views": _round(life_views),
        "engagement_in_period": _round(eng),
        "engagement_approximate": eng_approx,
        "lifetime_engagement": _round(life_eng),
        "engagement_rate": (
            round(life_eng / life_views * 100, 2) if life_views and life_eng is not None else None
        ),
        "unique_viewers_lifetime": _round(p.lifetime("unique_viewers")),
        "has_metrics": bool(p.series),
        "metrics_status": p.schedule.metrics_status,
        "metrics_detail": p.schedule.metrics_detail,
        "metrics_checked_at": _iso(p.schedule.metrics_checked_at),
        "first_observed_at": _iso(p.first_observed_at),
        "last_observed_at": _iso(p.last_observed_at),
    }


def posts_report(
    db: Session,
    brand_id: uuid.UUID,
    *,
    range_key: RangeKey = "30d",
    platform: str | None = None,
    channel_id: uuid.UUID | None = None,
    content_format: str | None = None,
    sort: SortKey = "views",
    page: int = 1,
    page_size: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    posts = _load_posts(
        db, brand_id, platform=platform, channel_id=channel_id, content_format=content_format
    )
    window = range_window(range_key, now=now, earliest=_earliest(posts))
    rows = [_post_row(p, window) for p in posts]
    if sort == "views":
        rows.sort(key=lambda r: (r["views_in_period"] or 0, r["published_at"] or ""), reverse=True)
    elif sort == "engagement_rate":
        rows.sort(key=lambda r: (r["engagement_rate"] or -1, r["published_at"] or ""), reverse=True)
    else:
        rows.sort(key=lambda r: r["published_at"] or "", reverse=True)
    total = len(rows)
    start = (page - 1) * page_size
    items = rows[start : start + page_size]
    thumbs = _thumbnail_ids(db, {uuid.UUID(r["content_package_id"]) for r in items})
    for r in items:
        r["thumbnail_asset_id"] = thumbs.get(uuid.UUID(r["content_package_id"]))
    return {
        "range": range_key,
        "window": {"start": _iso(window.start), "end": _iso(window.end)},
        "sort": sort,
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


def post_detail(
    db: Session,
    brand_id: uuid.UUID,
    package_id: uuid.UUID,
    *,
    range_key: RangeKey = "30d",
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """All platform posts of one content package, per platform and aggregated."""
    now = now or datetime.now(UTC)
    pkg = db.get(ContentPackage, package_id)
    if pkg is None or pkg.brand_profile_id != brand_id:
        return None
    posts = _load_posts(db, brand_id, package_id=package_id)
    window = range_window(range_key, now=now, earliest=_earliest(posts))
    gran = granularity_for(range_key, window)
    platforms = []
    for p in posts:
        series = []
        for day, w in buckets(window, gran):
            series.append(
                {
                    "date": day.isoformat(),
                    "views": _round(p.metric_in(VIEWS, w)[0]) if VIEWS in p.incs else None,
                }
            )
        metrics = []
        for key in sorted(p.series):
            if key.startswith("other_"):
                continue
            in_period, a = p.metric_in(key, window)
            metrics.append(
                {
                    "key": key,
                    "label": METRIC_LABELS.get(key, key),
                    "lifetime": _round(p.lifetime(key)),
                    "in_period": _round(in_period),
                    "approximate": a,
                }
            )
        history = [
            {"observed_at": _iso(o.at), "views": o.value} for o in p.series.get(VIEWS, [])[-60:]
        ]
        platforms.append(
            {
                **_post_row(p, window),
                "view_definition": VIEW_DEFINITIONS.get(p.schedule.platform, ""),
                "metrics": metrics,
                "trend": series,
                "history": history,
            }
        )
    with_views = [p for p in platforms if p["lifetime_views"] is not None]
    tags = pkg.post_tags or {}
    thumbs = _thumbnail_ids(db, {pkg.id})
    return {
        "content_package_id": str(pkg.id),
        "title": _post_title(pkg),
        "format": pkg.format,
        "tags": {k: tags.get(k) for k in ("pillar", "hook_type", "occasion", "language")},
        "thumbnail_asset_id": thumbs.get(pkg.id),
        "range": range_key,
        "window": {"start": _iso(window.start), "end": _iso(window.end)},
        "granularity": gran,
        "aggregate": {
            "lifetime_views": (
                _round(sum(p["lifetime_views"] for p in with_views)) if with_views else None
            ),
            "views_in_period": (
                _round(sum(p["views_in_period"] or 0 for p in with_views)) if with_views else None
            ),
            "platforms": len(platforms),
        },
        "platforms": platforms,
        "sync": sync_status(db),
    }


def pipeline_summary(db: Session, brand_id: uuid.UUID) -> dict[str, Any]:
    """Counts for the Overview: production, approval and publishing pipeline for one brand."""
    status_counts: dict[str, int] = dict(
        db.execute(
            select(ContentPackage.status, func.count())
            .where(ContentPackage.brand_profile_id == brand_id)
            .group_by(ContentPackage.status)
        )
        .tuples()
        .all()
    )
    awaiting = db.execute(
        select(func.count()).where(
            ContentPackage.brand_profile_id == brand_id,
            ContentPackage.status.in_(("QA_PASSED", "RENDERED", "DRAFTED")),
            ContentPackage.approval_status == "PENDING",
        )
    ).scalar_one()
    approved_unscheduled = db.execute(
        select(func.count()).where(
            ContentPackage.brand_profile_id == brand_id,
            ContentPackage.status == "QA_PASSED",
            ContentPackage.approval_status == "APPROVED",
        )
    ).scalar_one()
    sched_counts: dict[str, int] = dict(
        db.execute(
            select(PublishSchedule.status, func.count())
            .where(PublishSchedule.brand_profile_id == brand_id)
            .group_by(PublishSchedule.status)
        )
        .tuples()
        .all()
    )
    now = datetime.now(UTC)
    # Overdue: the status sync marked it, or it is still "Scheduled" well past its time (e.g. the
    # sync itself could not run because Docker or the PC was off).
    overdue = db.execute(
        select(func.count()).where(
            PublishSchedule.brand_profile_id == brand_id,
            (PublishSchedule.status == "OVERDUE")
            | (
                (PublishSchedule.status == "SCHEDULED")
                & (PublishSchedule.scheduled_at < now - OVERDUE_AFTER)
            ),
        )
    ).scalar_one()
    next_post = db.execute(
        select(PublishSchedule.scheduled_at, PublishSchedule.platform)
        .where(
            PublishSchedule.brand_profile_id == brand_id,
            PublishSchedule.status == "SCHEDULED",
            PublishSchedule.scheduled_at >= now,
        )
        .order_by(PublishSchedule.scheduled_at)
        .limit(1)
    ).first()
    recent = db.execute(
        select(ContentStateTransition, ContentPackage.title, ContentPackage.hook)
        .join(ContentPackage, ContentPackage.id == ContentStateTransition.content_package_id)
        .where(ContentStateTransition.brand_profile_id == brand_id)
        .order_by(ContentStateTransition.created_at.desc())
        .limit(8)
    ).all()
    channels = db.execute(
        select(ChannelAccount).where(ChannelAccount.brand_profile_id == brand_id)
    ).scalars()
    sources = db.execute(select(ResearchSource)).scalars()
    return {
        "brand_id": str(brand_id),
        "content": {
            "total": int(sum(status_counts.values())),
            "by_status": {k: int(v) for k, v in status_counts.items()},
            "awaiting_approval": int(awaiting),
            "approved_unscheduled": int(approved_unscheduled),
        },
        "publishing": {
            "scheduled": int(sched_counts.get("SCHEDULED", 0)),
            "published": int(sched_counts.get("PUBLISHED", 0)),
            "failed": int(sched_counts.get("FAILED", 0)),
            "overdue": int(overdue),
            "cancelled": int(sched_counts.get("CANCELLED", 0)),
            "next_scheduled_at": _iso(next_post[0]) if next_post else None,
            "next_scheduled_platform": next_post[1] if next_post else None,
        },
        "channels": [
            {
                "id": str(c.id),
                "platform": c.platform,
                "name": _channel_name(c),
                "status": c.status,
            }
            for c in channels
        ],
        "research": [
            {
                "code": s.source_code,
                "name": s.display_name,
                "mode": s.policy_mode,
                "health": s.health_status,
                "last_success_at": _iso(s.last_success_at),
            }
            for s in sources
        ],
        "recent_activity": [
            {
                "at": _iso(t.created_at),
                "content_package_id": str(t.content_package_id),
                "title": (title or hook or "Untitled").replace("*", "")[:120],
                "from_status": t.from_status,
                "to_status": t.to_status,
                "actor": t.actor_id,
            }
            for t, title, hook in recent
        ],
    }


__all__ = ["pipeline_summary", "post_detail", "posts_report", "sync_status", "views_report"]


# ------------------------------------------------------------------ nightly report: last N days

# Order in which the report lists metrics. Reach / unique viewers / plays are per-platform only:
# each platform defines them differently, so they are never added up across platforms.
REPORT_METRICS = (
    "views",
    "likes",
    "reactions",
    "comments",
    "shares",
    "saves",
    "reach",
    "unique_viewers",
    "plays",
)
CROSS_PLATFORM = ("views", "likes_reactions", "comments", "shares", "saves")


@dataclass(frozen=True)
class MetricTotal:
    key: str
    value: float
    previous: float | None
    approximate: bool

    @property
    def growth(self) -> float | None:
        return growth_pct(self.value, self.previous)


@dataclass(frozen=True)
class PlatformTotals:
    platform: str
    posts: int
    metrics: tuple[MetricTotal, ...]


@dataclass(frozen=True)
class TopPost:
    title: str
    platform: str
    engagement: float
    views: float
    url: str | None


@dataclass(frozen=True)
class PeriodTotals:
    start: datetime
    end: datetime
    platforms: tuple[PlatformTotals, ...]
    overall: tuple[MetricTotal, ...]
    top_post: TopPost | None
    data_as_of: datetime | None
    platforms_seen: frozenset[str]


def period_totals(db: Session, *, now: datetime, days: int = 7) -> PeriodTotals:
    """Totals of every platform-reported metric gained in the last `days`, across all brands.

    Platforms report lifetime counters; the engine attributes each increase to the time between
    two readings, so "last 7 days" includes new likes on older posts and excludes what happened
    before. The previous `days` are computed the same way for the comparison.
    """
    from datetime import timedelta

    from media_os_api.db.models import BrandProfile

    window = Window(now - timedelta(days=days), now)
    prev = Window(now - timedelta(days=2 * days), now - timedelta(days=days))
    posts: list[PlatformPost] = []
    for brand_id in db.execute(select(BrandProfile.id)).scalars():
        posts.extend(_load_posts(db, brand_id))

    def total(group: Sequence[PlatformPost], key: str, w: Window) -> tuple[float, bool, bool]:
        value, approx, present = 0.0, False, False
        for p in group:
            if key in p.incs:
                present = True
                v, a = p.metric_in(key, w)
                value += v
                approx = approx or a
        return value, approx, present

    platforms: list[PlatformTotals] = []
    sums: dict[str, list[float]] = {k: [0.0, 0.0] for k in CROSS_PLATFORM}
    seen_cross: set[str] = set()
    approx_cross: dict[str, bool] = {k: False for k in CROSS_PLATFORM}
    for platform in sorted({p.schedule.platform for p in posts}):
        group = [p for p in posts if p.schedule.platform == platform]
        metrics: list[MetricTotal] = []
        for key in REPORT_METRICS:
            value, approx, present = total(group, key, window)
            if not present:
                continue
            before, _a, _p = total(group, key, prev)
            metrics.append(MetricTotal(key, value, before, approx))
            cross = "likes_reactions" if key in ("likes", "reactions") else key
            if cross in sums:
                sums[cross][0] += value
                sums[cross][1] += before
                seen_cross.add(cross)
                approx_cross[cross] = approx_cross[cross] or approx
        platforms.append(PlatformTotals(platform, len(group), tuple(metrics)))

    overall = tuple(
        MetricTotal(k, sums[k][0], sums[k][1], approx_cross[k])
        for k in CROSS_PLATFORM
        if k in seen_cross
    )

    best: TopPost | None = None
    best_rank = (0.0, 0.0)
    for p in posts:
        eng, _ = p.engagement_in(window)
        views, _ = p.metric_in(VIEWS, window)
        rank = (eng or 0.0, views)
        if rank > best_rank:
            url = p.schedule.remote_post_id
            best = TopPost(
                title=_post_title(p.package),
                platform=p.schedule.platform,
                engagement=eng or 0.0,
                views=views,
                url=url if (url or "").startswith("http") else None,
            )
            best_rank = rank
    lasts = [p.last_observed_at for p in posts if p.last_observed_at]
    return PeriodTotals(
        start=window.start,
        end=window.end,
        platforms=tuple(platforms),
        overall=overall,
        top_post=best,
        data_as_of=max(lasts) if lasts else None,
        platforms_seen=frozenset(p.schedule.platform for p in posts),
    )
