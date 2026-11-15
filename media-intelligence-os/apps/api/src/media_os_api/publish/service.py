"""Roadmap N3: schedule owner-approved carousels to Instagram and Facebook through Postiz.

Flow (nothing is published without the owner's approval AND an explicit schedule action):

1. API `request_schedule` (owner, CSRF): validates the carousel is APPROVED + QA_PASSED, the time
   is in the allowed window and under the brand's daily caps, then enqueues a durable job.
2. Job `media_os.publish.schedule_carousel` (worker, holds the keys):
   sync Postiz channels → re-check caps → slides PNG→JPEG (Instagram only accepts JPEG) →
   one commit to the owner's GitHub Pages media repo → wait until every URL is live →
   create ONE Postiz "schedule" post for all target channels → PublishSchedule rows →
   QA_PASSED → POSTIZ_DRAFT → USER_APPROVED → SCHEDULED.
   Re-runs are idempotent: the Postiz group id is derived from the package and time, and an
   existing post with that group is never created twice.
3. Job `media_os.publish.sync_status` (every 15 min once enabled): reads Postiz post states and
   marks schedules PUBLISHED / FAILED / CANCELLED; the package becomes PUBLISHED when every
   platform has settled and at least one published. A post still queued 30 min after its time
   becomes OVERDUE (usually the PC or Docker was off): it stops counting toward the daily cap,
   is shown as a warning, and keeps being checked in case Postiz publishes it late. FAILED posts
   from the last 14 days are re-checked too, so a post the owner retried by hand in Postiz is
   picked up as PUBLISHED (and then gets analytics).

Postiz performs the actual publishing at the scheduled time (Meta Graph API).
"""

from __future__ import annotations

import io
import logging
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from media_os_api.content.caption import CAPTION_MAX, full_caption
from media_os_api.db.models import (
    Asset,
    BrandProfile,
    ChannelAccount,
    ContentPackage,
    JobRun,
    PublishSchedule,
)
from media_os_api.jobs.errors import PermanentTaskError, PolicyRefusalError, RetryableTaskError
from media_os_api.jobs.ledger import enqueue_durable_job
from media_os_api.jobs.routing import queue_for_task
from media_os_api.publish.media_host import GitHubPagesHost, MediaHostError
from media_os_api.publish.postiz import Integration, PostizClient, PostizError
from media_os_api.services.audit_service import record_audit_event
from media_os_api.workflow.state_machine import ContentLifecycleStatus, transition_content_status

logger = logging.getLogger("media_os_api.publish")

SCHEDULE_TASK = "media_os.publish.schedule_carousel"
SYNC_TASK = "media_os.publish.sync_status"
ACTOR = "worker:publish"
PLATFORMS = ("INSTAGRAM", "FACEBOOK")
PROVIDER_TO_PLATFORM = {
    "instagram": "INSTAGRAM",
    "instagram-standalone": "INSTAGRAM",
    "facebook": "FACEBOOK",
}
MIN_LEAD = timedelta(minutes=20)  # GitHub Pages build + Postiz queueing
MAX_LEAD = timedelta(days=30)
ACTIVE_SCHEDULE_STATES = ("SCHEDULED", "PUBLISHED")
RESCHEDULABLE_STATES = ("FAILED", "CANCELLED")
# Two carousels on one channel must be at least this far apart: retry recovery identifies a
# carousel's Postiz post by channel + minute, so near-simultaneous posts would be confused.
SLOT_GAP = timedelta(minutes=5)
OVERDUE_AFTER = timedelta(minutes=30)
FAILED_RECHECK_WINDOW = timedelta(days=14)
PENDING_SCHEDULE_STATES = ("SCHEDULED", "OVERDUE")
JPEG_QUALITY = 90
GROUP_NAMESPACE = uuid.UUID("b5f2a3c4-6d1e-4f7a-9b8c-0d1e2f3a4b5c")


class ScheduleRequestError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- shared rules


def daily_cap(brand: BrandProfile, platform: str) -> int:
    caps = (brand.publishing_preferences or {}).get("daily_caps") or {}
    value = caps.get(platform)
    return int(value) if isinstance(value, int) and value >= 0 else 2


def local_day_bounds(when: datetime, tz: str) -> tuple[datetime, datetime]:
    local = when.astimezone(ZoneInfo(tz))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def scheduled_count(
    session: Session, brand_id: uuid.UUID, platform: str, when: datetime, tz: str
) -> int:
    start, end = local_day_bounds(when, tz)
    return int(
        session.execute(
            select(func.count())
            .select_from(PublishSchedule)
            .where(
                PublishSchedule.brand_profile_id == brand_id,
                PublishSchedule.platform == platform,
                PublishSchedule.status.in_(ACTIVE_SCHEDULE_STATES),
                PublishSchedule.scheduled_at >= start,
                PublishSchedule.scheduled_at < end,
            )
        ).scalar_one()
    )


def group_id(package_id: uuid.UUID, when: datetime) -> str:
    return str(uuid.uuid5(GROUP_NAMESPACE, f"{package_id}:{when.astimezone(UTC).isoformat()}"))


def existing_posts(
    postiz: PostizClient,
    when: datetime,
    integration_ids: set[str],
    claimed: set[str] | None = None,
) -> dict[str, str]:
    """{integration_id: post_id} for API-created posts at this exact minute on these channels.

    `claimed` holds Postiz post ids already linked to a PublishSchedule; they belong to another
    carousel (or an earlier attempt) and are never adopted, so two carousels can never share one
    Postiz post.
    """
    taken = claimed or set()
    found: dict[str, str] = {}
    for p in postiz.posts_between(when - timedelta(minutes=2), when + timedelta(minutes=2)):
        integ = str((p.get("integration") or {}).get("id") or "")
        try:
            at = datetime.fromisoformat(str(p.get("publishDate")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if (
            integ in integration_ids
            and abs((at - when).total_seconds()) < 60
            and str(p.get("creationMethod") or "API") == "API"
            and p.get("id")
            and str(p["id"]) not in taken
        ):
            found[integ] = str(p["id"])
    return found


def claimed_post_ids(session: Session) -> set[str]:
    return {
        str(pid)
        for pid in session.execute(
            select(PublishSchedule.postiz_post_id).where(
                PublishSchedule.postiz_post_id.is_not(None)
            )
        ).scalars()
    }


def compose_caption(pkg: ContentPackage) -> str:
    """Exact caption sent to Postiz. Never truncated: over-long captions are refused instead."""
    return full_caption(pkg.caption, pkg.hashtags or [])


def package_schedules(session: Session, pkg: ContentPackage) -> list[PublishSchedule]:
    return list(
        session.execute(
            select(PublishSchedule).where(PublishSchedule.content_package_id == pkg.id)
        ).scalars()
    )


def is_reschedule(pkg: ContentPackage, schedules: list[PublishSchedule]) -> bool:
    """A carousel whose every platform post failed or was cancelled (e.g. deleted in Postiz)."""
    return (
        pkg.status == ContentLifecycleStatus.FAILED_RETRYABLE.value
        and pkg.previous_status == ContentLifecycleStatus.SCHEDULED.value
        and bool(schedules)
        and all(s.status in RESCHEDULABLE_STATES for s in schedules)
    )


def can_schedule(pkg: ContentPackage, schedules: list[PublishSchedule]) -> bool:
    if pkg.format != "CAROUSEL" or pkg.approval_status != "APPROVED":
        return False
    if pkg.status == ContentLifecycleStatus.QA_PASSED.value:
        return not any(s.status in ACTIVE_SCHEDULE_STATES for s in schedules)
    return is_reschedule(pkg, schedules)


def slot_taken(
    session: Session, brand_id: uuid.UUID, platform: str, when: datetime, package_id: uuid.UUID
) -> bool:
    """Another carousel already posts on this platform within SLOT_GAP of `when`."""
    return (
        session.execute(
            select(PublishSchedule.id).where(
                PublishSchedule.brand_profile_id == brand_id,
                PublishSchedule.platform == platform,
                PublishSchedule.status.in_(ACTIVE_SCHEDULE_STATES),
                PublishSchedule.content_package_id != package_id,
                PublishSchedule.scheduled_at > when - SLOT_GAP,
                PublishSchedule.scheduled_at < when + SLOT_GAP,
            )
        ).first()
        is not None
    )


# --------------------------------------------------------------------------- API side


def request_schedule(
    session: Session,
    *,
    pkg: ContentPackage,
    brand: BrandProfile,
    when: datetime,
    platforms: list[str],
    actor_id: str,
    now: datetime | None = None,
) -> JobRun:
    now = now or datetime.now(UTC)
    if when.tzinfo is None:
        raise ScheduleRequestError("Time must include a timezone.", code="NAIVE_TIME")
    if pkg.format != "CAROUSEL":
        raise ScheduleRequestError("Only carousels can be scheduled.", code="NOT_CAROUSEL")
    schedules = package_schedules(session, pkg)
    if not can_schedule(pkg, schedules):
        raise ScheduleRequestError(
            "Only approved, QA-passed carousels (or ones whose earlier post failed or was "
            "cancelled) can be scheduled.",
            code="NOT_APPROVED",
        )
    if any(s.scheduled_at == when for s in schedules):
        raise ScheduleRequestError(
            "This carousel was already scheduled for that exact time; pick a different time.",
            code="SAME_TIME",
        )
    if len(compose_caption(pkg)) > CAPTION_MAX:
        raise ScheduleRequestError(
            f"Caption with hashtags is longer than Instagram's {CAPTION_MAX} characters.",
            code="CAPTION_TOO_LONG",
        )
    if when < now + MIN_LEAD:
        raise ScheduleRequestError(
            "Pick a time at least 20 minutes from now (images need time to go live).",
            code="TOO_SOON",
        )
    if when > now + MAX_LEAD:
        raise ScheduleRequestError("Pick a time within the next 30 days.", code="TOO_FAR")
    chosen = [p for p in PLATFORMS if p in set(platforms)]
    if not chosen:
        raise ScheduleRequestError("Choose Instagram and/or Facebook.", code="NO_PLATFORM")
    for platform in chosen:
        if scheduled_count(session, brand.id, platform, when, brand.timezone) >= daily_cap(
            brand, platform
        ):
            raise ScheduleRequestError(
                f"{platform.title()} already has {daily_cap(brand, platform)} posts that day "
                "(daily cap).",
                code="DAILY_CAP",
            )
        if slot_taken(session, brand.id, platform, when, pkg.id):
            raise ScheduleRequestError(
                f"Another post is scheduled on {platform.title()} within 5 minutes of that time.",
                code="SLOT_TAKEN",
            )
    job, _created = enqueue_durable_job(
        session,
        task_name=SCHEDULE_TASK,
        task_type="PUBLISH",
        payload={
            "content_package_id": str(pkg.id),
            "scheduled_at": when.astimezone(UTC).isoformat(),
            "platforms": chosen,
        },
        idempotency_key=f"publish:{group_id(pkg.id, when)}",
        brand_profile_id=brand.id,
        queue_name=queue_for_task(SCHEDULE_TASK),
        maximum_attempts=4,
    )
    record_audit_event(
        session,
        actor_id=actor_id,
        action="CAROUSEL_SCHEDULE_REQUESTED",
        target_type="content_package",
        target_id=str(pkg.id),
        outcome="SUCCESS",
        metadata={"scheduled_at": when.astimezone(UTC).isoformat(), "platforms": chosen},
    )
    return job


# --------------------------------------------------------------------------- worker side


def media_root() -> Path:
    return Path(os.environ.get("MEDIA_OS_MEDIA_ROOT", "/data/media"))


def to_jpeg(png_bytes: bytes) -> bytes:
    from PIL import Image

    with Image.open(io.BytesIO(png_bytes)) as im:
        rgb = im.convert("RGB")
        out = io.BytesIO()
        rgb.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=True)
        return out.getvalue()


def sync_channels(session: Session, brand: BrandProfile, postiz: PostizClient) -> list[Integration]:
    """Mirror Postiz's connected channels into ChannelAccount rows (Postiz owns the OAuth)."""
    found = [i for i in postiz.integrations() if i.provider in PROVIDER_TO_PLATFORM]
    now = datetime.now(UTC)
    for integ in found:
        platform = PROVIDER_TO_PLATFORM[integ.provider]
        row = session.execute(
            select(ChannelAccount).where(ChannelAccount.postiz_integration_id == integ.id)
        ).scalar_one_or_none()
        status = "ERROR" if integ.disabled else "CONNECTED"
        if row is None:
            session.add(
                ChannelAccount(
                    brand_profile_id=brand.id,
                    platform=platform,
                    external_account_id=f"postiz:{integ.id}",
                    external_display_name=integ.name[:255],
                    postiz_integration_id=integ.id,
                    status=status,
                    connected_at=now,
                    last_verified_at=now,
                )
            )
        else:
            row.status = status
            row.external_display_name = integ.name[:255]
            row.last_verified_at = now
    session.flush()
    return found


def _slides(session: Session, pkg: ContentPackage) -> list[bytes]:
    assets = session.execute(
        select(Asset)
        .where(
            Asset.content_package_id == pkg.id,
            Asset.asset_type == "CAROUSEL_SLIDE",
            Asset.status == "ACTIVE",
        )
        .order_by(Asset.local_path)
    ).scalars()
    root = media_root().resolve()
    out = []
    for a in assets:
        path = (root / a.local_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise PermanentTaskError(f"Slide file missing: {a.local_path}", code="SLIDE_MISSING")
        out.append(path.read_bytes())
    if not 2 <= len(out) <= 10:
        raise PermanentTaskError(
            f"Instagram carousels need 2-10 slides, found {len(out)}.", code="SLIDE_COUNT"
        )
    return out


def _advance(
    session: Session,
    pkg: ContentPackage,
    target: ContentLifecycleStatus,
    reason: str,
    *,
    attempt: str = "",
) -> None:
    key = f"{target.value.lower()}:{pkg.id}:v{pkg.version}"
    transition_content_status(
        session,
        content_package_id=pkg.id,
        to_status=target,
        actor_id=ACTOR,
        reason=reason,
        # A reschedule repeats SCHEDULED, so it needs its own key or it would replay the first.
        idempotency_key=f"{key}:{attempt}" if attempt else key,
        expected_brand_id=pkg.brand_profile_id,
    )
    session.refresh(pkg)


def _clients() -> tuple[GitHubPagesHost, PostizClient]:
    env = os.environ
    host = GitHubPagesHost(
        token=env.get("NIMONTRON_MEDIA_GITHUB_TOKEN", ""),
        repo=env.get("NIMONTRON_MEDIA_GITHUB_REPO", ""),
        base_url=env.get("NIMONTRON_MEDIA_BASE_URL", ""),
    )
    postiz = PostizClient(
        base_url=env.get("POSTIZ_API_BASE", "http://host.docker.internal:4007"),
        api_key=env.get("POSTIZ_API_KEY", ""),
    )
    return host, postiz


ClientFactory = Callable[[], tuple[GitHubPagesHost, PostizClient]]


def handle_schedule_carousel(
    session: Session, job: JobRun, *, clients: ClientFactory = _clients
) -> dict[str, Any]:
    payload = dict(job.payload or {})
    try:
        package_id = uuid.UUID(str(payload["content_package_id"]))
        when = datetime.fromisoformat(str(payload["scheduled_at"]))
        platforms = [p for p in payload.get("platforms", []) if p in PLATFORMS]
    except (KeyError, ValueError) as exc:
        raise PermanentTaskError("Invalid publish payload.", code="BAD_PAYLOAD") from exc
    pkg = session.get(ContentPackage, package_id, with_for_update=True)
    if pkg is None:
        raise PermanentTaskError("Content package not found.", code="NOT_FOUND")
    if pkg.status == ContentLifecycleStatus.SCHEDULED.value:
        return {"skipped": True, "status": pkg.status}
    rescheduling = is_reschedule(pkg, package_schedules(session, pkg))
    if pkg.approval_status != "APPROVED" or not (
        rescheduling
        or pkg.status
        in (ContentLifecycleStatus.QA_PASSED.value, ContentLifecycleStatus.POSTIZ_DRAFT.value)
    ):
        raise PolicyRefusalError("Carousel is not approved for publishing.", code="NOT_APPROVED")
    brand = session.get(BrandProfile, pkg.brand_profile_id)
    if brand is None:
        raise PermanentTaskError("Brand not found.", code="NOT_FOUND")
    caption = compose_caption(pkg)
    if len(caption) > CAPTION_MAX:
        raise PermanentTaskError(
            f"Caption is {len(caption)} characters; Instagram allows {CAPTION_MAX}.",
            code="CAPTION_TOO_LONG",
        )

    host, postiz = clients()
    try:
        integrations = sync_channels(session, brand, postiz)
        targets = [
            i
            for i in integrations
            if not i.disabled and PROVIDER_TO_PLATFORM[i.provider] in platforms
        ]
        if not targets:
            raise PermanentTaskError(
                "No connected Instagram/Facebook channel in Postiz.", code="NO_CHANNEL"
            )
        for platform in {PROVIDER_TO_PLATFORM[t.provider] for t in targets}:
            if scheduled_count(session, brand.id, platform, when, brand.timezone) >= daily_cap(
                brand, platform
            ):
                raise PolicyRefusalError(f"Daily cap reached for {platform}.", code="DAILY_CAP")
            if slot_taken(session, brand.id, platform, when, pkg.id):
                raise PolicyRefusalError(
                    f"Another post is scheduled on {platform} within 5 minutes.", code="SLOT_TAKEN"
                )

        jpegs = [to_jpeg(b) for b in _slides(session, pkg)]
        stamp = when.astimezone(ZoneInfo(brand.timezone)).strftime("%Y-%m")
        prefix = f"{brand.slug}/{stamp}/{pkg.id}/v{pkg.version}"
        files = [(f"{prefix}/slide-{i:02d}.jpg", data) for i, data in enumerate(jpegs, 1)]
        hosted = host.publish(
            files, message=f"Carousel {pkg.id} v{pkg.version} ({len(files)} slides)"
        )
        host.wait_until_live(hosted)

        if pkg.status == ContentLifecycleStatus.QA_PASSED.value:
            _advance(
                session, pkg, ContentLifecycleStatus.POSTIZ_DRAFT, "Images live on GitHub Pages"
            )

        group = group_id(pkg.id, when)
        # Re-run safety: Postiz assigns its own ids, so an earlier attempt that created posts but
        # crashed before saving is recognised by channel + publish time + API origin. Ids already
        # linked to another schedule are excluded, so another carousel's post is never adopted.
        target_ids = {t.id for t in targets}
        claimed = claimed_post_ids(session)
        existing = existing_posts(postiz, when, target_ids, claimed)
        missing = [t for t in targets if t.id not in existing]
        if missing:
            existing.update(
                postiz.schedule_carousel(
                    when=when,
                    caption=caption,
                    image_urls=[f.url for f in hosted],
                    targets=missing,
                    image_prefix=group,
                )
            )
            unconfirmed = target_ids - existing.keys()
            if unconfirmed:
                # Postiz accepted the request but did not report every post id: look them up.
                existing.update(existing_posts(postiz, when, unconfirmed, claimed))
        if target_ids - existing.keys():
            # Never save a schedule without its Postiz id (status sync would later mark it
            # CANCELLED even if it published). A retry recovers the ids via existing_posts.
            raise RetryableTaskError(
                "Postiz did not confirm a post id for every channel.", code="POSTIZ_NO_POST_ID"
            )
        channel_ids = {
            c.postiz_integration_id: c.id
            for c in session.execute(
                select(ChannelAccount).where(ChannelAccount.brand_profile_id == brand.id)
            ).scalars()
        }
        for t in targets:
            platform = PROVIDER_TO_PLATFORM[t.provider]
            key = f"{group}:{platform}"
            if session.execute(
                select(PublishSchedule.id).where(PublishSchedule.idempotency_key == key)
            ).first():
                continue
            session.add(
                PublishSchedule(
                    brand_profile_id=brand.id,
                    channel_account_id=channel_ids[t.id],
                    content_package_id=pkg.id,
                    platform=platform,
                    postiz_post_id=existing.get(t.id),
                    scheduled_at=when,
                    timezone=brand.timezone,
                    status="SCHEDULED",
                    idempotency_key=key,
                    attempt_count=job.attempt_count,
                )
            )
        session.flush()
        if rescheduling:
            # FAILED_RETRYABLE resumes straight to SCHEDULED (its recorded previous state).
            _advance(
                session,
                pkg,
                ContentLifecycleStatus.SCHEDULED,
                f"Rescheduled in Postiz for {when.isoformat()}",
                attempt=group,
            )
        else:
            _advance(session, pkg, ContentLifecycleStatus.USER_APPROVED, "Owner approved carousel")
            _advance(
                session,
                pkg,
                ContentLifecycleStatus.SCHEDULED,
                f"Scheduled in Postiz for {when.isoformat()}",
            )
        record_audit_event(
            session,
            actor_id=ACTOR,
            action="CAROUSEL_SCHEDULED",
            target_type="content_package",
            target_id=str(pkg.id),
            outcome="SUCCESS",
            metadata={
                "scheduled_at": when.astimezone(UTC).isoformat(),
                "platforms": sorted({PROVIDER_TO_PLATFORM[t.provider] for t in targets}),
                "slides": len(hosted),
                "postiz_posts": sorted(existing.values()),
            },
        )
        return {
            "status": pkg.status,
            "postiz_posts": sorted(existing.values()),
            "platforms": sorted({PROVIDER_TO_PLATFORM[t.provider] for t in targets}),
            "images": [f.url for f in hosted],
        }
    except (MediaHostError, PostizError) as exc:
        if exc.retryable:
            raise RetryableTaskError(str(exc), code=exc.code) from exc
        raise PermanentTaskError(str(exc), code=exc.code) from exc
    finally:
        host.close()
        postiz.close()


POSTIZ_PUBLISHED = {"PUBLISHED"}
POSTIZ_FAILED = {"ERROR"}


def handle_sync_status(
    session: Session,
    job: JobRun,
    *,
    clients: ClientFactory = _clients,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reflect Postiz post states (QUEUE/PUBLISHED/ERROR, deleted) back into Media OS."""
    now = now or datetime.now(UTC)
    due = list(
        session.execute(
            select(PublishSchedule).where(
                (
                    PublishSchedule.status.in_(PENDING_SCHEDULE_STATES)
                    & (PublishSchedule.scheduled_at <= now + timedelta(minutes=1))
                )
                | (
                    (PublishSchedule.status == "FAILED")
                    & (PublishSchedule.scheduled_at >= now - FAILED_RECHECK_WINDOW)
                )
            )
        ).scalars()
    )
    if not due:
        return {"checked": 0}
    host, postiz = clients()
    updated = 0
    try:
        start = min(s.scheduled_at for s in due) - timedelta(days=1)
        posts = postiz.posts_between(start, now + timedelta(days=1))
        by_id = {str(p.get("id")): p for p in posts if p.get("id")}
        for s in due:
            new_status = _next_status(s, by_id.get(s.postiz_post_id or ""), now)
            if new_status is None or new_status == s.status:
                continue
            if new_status == "PUBLISHED":
                post = by_id[s.postiz_post_id or ""]
                release = str(post.get("releaseURL") or post.get("releaseId") or "")
                s.remote_post_id = release[:128] or None
            s.status = new_status
            updated += 1
        session.flush()
        for package_id in {s.content_package_id for s in due}:
            _settle_package(session, package_id)
        return {"checked": len(due), "updated": updated}
    except PostizError as exc:
        if exc.retryable:
            raise RetryableTaskError(str(exc), code=exc.code) from exc
        raise PermanentTaskError(str(exc), code=exc.code) from exc
    finally:
        host.close()
        postiz.close()


def _next_status(s: PublishSchedule, post: dict[str, Any] | None, now: datetime) -> str | None:
    """New status for one schedule given its Postiz post (None = leave unchanged)."""
    if post is None:
        if not s.postiz_post_id:
            return None  # no Postiz id recorded: deleted and published look the same, leave it
        if s.status in PENDING_SCHEDULE_STATES and s.scheduled_at < now - OVERDUE_AFTER:
            return "CANCELLED"  # deleted in Postiz
        return None
    state = str(post.get("state") or "").upper()
    if state in POSTIZ_PUBLISHED:
        return "PUBLISHED"
    if state in POSTIZ_FAILED:
        return "FAILED"
    # Still queued in Postiz.
    if s.status == "SCHEDULED" and s.scheduled_at < now - OVERDUE_AFTER:
        return "OVERDUE"
    return None


def _settle_package(session: Session, package_id: uuid.UUID) -> None:
    pkg = session.get(ContentPackage, package_id)
    if pkg is None:
        return
    # A carousel that failed everywhere can still publish if the owner retries it in Postiz.
    recovering = (
        pkg.status == ContentLifecycleStatus.FAILED_RETRYABLE.value
        and pkg.previous_status == ContentLifecycleStatus.SCHEDULED.value
    )
    if pkg.status != ContentLifecycleStatus.SCHEDULED.value and not recovering:
        return
    rows = list(
        session.execute(
            select(PublishSchedule).where(PublishSchedule.content_package_id == package_id)
        ).scalars()
    )
    states = [s.status for s in rows]
    # Each schedule attempt (one publish time) settles separately, so a carousel that is
    # rescheduled and fails again gets a fresh FAILED_RETRYABLE transition, not a replay.
    attempt = max(s.scheduled_at for s in rows).astimezone(UTC).isoformat() if rows else ""
    if not states or any(st in PENDING_SCHEDULE_STATES for st in states):
        return
    if "PUBLISHED" in states:
        if recovering:
            _advance(
                session,
                pkg,
                ContentLifecycleStatus.SCHEDULED,
                "Published after a retry in Postiz",
                attempt=f"recovered:{attempt}",
            )
        _advance(session, pkg, ContentLifecycleStatus.PUBLISHING, "Postiz started publishing")
        _advance(session, pkg, ContentLifecycleStatus.PUBLISHED, "Published by Postiz")
        record_audit_event(
            session,
            actor_id=ACTOR,
            action="CAROUSEL_PUBLISHED",
            target_type="content_package",
            target_id=str(pkg.id),
            outcome="SUCCESS",
            metadata={"platforms": states},
        )
    elif not recovering and all(s in RESCHEDULABLE_STATES for s in states):
        _advance(
            session,
            pkg,
            ContentLifecycleStatus.FAILED_RETRYABLE,
            "All platforms failed or were cancelled",
            attempt=attempt,
        )
        record_audit_event(
            session,
            actor_id=ACTOR,
            action="CAROUSEL_PUBLISH_FAILED",
            target_type="content_package",
            target_id=str(pkg.id),
            outcome="FAILURE",
            metadata={"platforms": states},
        )
