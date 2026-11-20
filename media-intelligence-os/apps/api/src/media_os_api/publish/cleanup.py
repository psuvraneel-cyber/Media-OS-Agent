"""Remove published media from the public GitHub Pages repo once Instagram and Facebook have it.

Every scheduled post commits its slides (or reel) to the owner's public media repo, and nothing
ever deleted them: a reel can be up to 95 MB, and GitHub recommends keeping a repo under about
1 GB. Instagram and Facebook copy the media when they publish, so the hosted copy is only needed
until then.

`media_os.publish.cleanup_media` (daily) deletes, in one commit, the files of every post that has
settled:

* every platform finished (no SCHEDULED or OVERDUE schedule, no schedule or cancel job running);
* the last post time is at least 3 days ago, or 15 days when a platform FAILED (the owner can
  retry a failed post by hand in Postiz for 14 days, and that retry downloads the same files).

Rescheduling a cleaned-up post is safe: scheduling always uploads the files again. Each cleaned
post gets a MEDIA_UNHOSTED audit event, so it's never looked at again.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.db.models import (
    AuditEvent,
    BrandProfile,
    ContentPackage,
    JobRun,
    PublishSchedule,
)
from media_os_api.jobs.errors import PermanentTaskError, RetryableTaskError
from media_os_api.publish.media_host import MediaHostError
from media_os_api.services.audit_service import record_audit_event

logger = logging.getLogger("media_os_api.publish.cleanup")

CLEANUP_TASK = "media_os.publish.cleanup_media"
ACTOR = "worker:publish"
KEEP_AFTER_PUBLISH = timedelta(days=3)
KEEP_AFTER_FAILURE = timedelta(days=15)
UNSETTLED = ("SCHEDULED", "OVERDUE")
POST_TASKS = ("media_os.publish.schedule_carousel", "media_os.publish.cancel_post")
IN_FLIGHT = ("PENDING", "DISPATCHED", "RUNNING", "FAILED_RETRYABLE")
MAX_PER_RUN = 200


def settled_packages(session: Session, now: datetime) -> list[tuple[ContentPackage, str]]:
    """(package, brand slug) for posts whose hosted media can go, oldest first."""
    cleaned = {
        str(t)
        for t in session.execute(
            select(AuditEvent.target_id).where(AuditEvent.action == "MEDIA_UNHOSTED")
        ).scalars()
    }
    busy = {
        str(p)
        for p in session.execute(
            select(JobRun.payload["content_package_id"].astext).where(
                JobRun.task_name.in_(POST_TASKS), JobRun.status.in_(IN_FLIGHT)
            )
        ).scalars()
    }
    by_pkg: dict[uuid.UUID, list[PublishSchedule]] = {}
    for row in session.execute(select(PublishSchedule)).scalars():
        by_pkg.setdefault(row.content_package_id, []).append(row)
    out: list[tuple[ContentPackage, str]] = []
    for package_id, rows in sorted(
        by_pkg.items(), key=lambda kv: max(r.scheduled_at for r in kv[1])
    ):
        if str(package_id) in cleaned or str(package_id) in busy:
            continue
        if any(r.status in UNSETTLED for r in rows):
            continue
        keep = KEEP_AFTER_FAILURE if any(r.status == "FAILED" for r in rows) else KEEP_AFTER_PUBLISH
        if max(r.scheduled_at for r in rows) > now - keep:
            continue
        pkg = session.get(ContentPackage, package_id)
        brand = session.get(BrandProfile, rows[0].brand_profile_id)
        if pkg is None or brand is None:
            continue
        out.append((pkg, brand.slug))
        if len(out) >= MAX_PER_RUN:
            break
    return out


def belongs_to(path: str, brand_slug: str, package_id: uuid.UUID) -> bool:
    """Media paths are `<brand>/<YYYY-MM>/<package id>/v<version>/<file>`."""
    parts = path.split("/")
    return len(parts) >= 4 and parts[0] == brand_slug and parts[2] == str(package_id)


def handle_cleanup_media(
    session: Session,
    job: JobRun,
    *,
    host_factory: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    todo = settled_packages(session, now)
    if not todo:
        return {"packages": 0, "files": 0}
    if host_factory is None:
        from media_os_api.publish.service import _clients

        def host_factory() -> Any:
            host, postiz = _clients()
            postiz.close()
            return host

    host = host_factory()
    wanted = {(slug, str(pkg.id)) for pkg, slug in todo}
    try:
        removed = host.remove_matching(
            lambda p: len(parts := p.split("/")) >= 4 and (parts[0], parts[2]) in wanted,
            message=f"Remove media of {len(todo)} published post(s)",
        )
    except MediaHostError as exc:
        if exc.retryable:
            raise RetryableTaskError(str(exc), code=exc.code) from exc
        raise PermanentTaskError(str(exc), code=exc.code) from exc
    finally:
        host.close()
    for pkg, slug in todo:
        files = [p for p in removed if belongs_to(p, slug, pkg.id)]
        record_audit_event(
            session,
            actor_id=ACTOR,
            action="MEDIA_UNHOSTED",
            target_type="content_package",
            target_id=str(pkg.id),
            outcome="SUCCESS",
            metadata={"files": len(files)},
        )
    logger.info("media cleanup", extra={"packages": len(todo), "files": len(removed)})
    return {"packages": len(todo), "files": len(removed)}
