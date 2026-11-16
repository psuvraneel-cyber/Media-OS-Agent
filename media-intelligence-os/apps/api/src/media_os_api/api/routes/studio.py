"""Studio: the owner's own reels, photo posts and carousels, the asset library, the brand logo
and on-demand analytics sync.

* `POST   /api/v1/content/reels/upload?brand=&filename=` (CSRF, raw MP4 body) — a new reel, waiting
  for the owner's approval. Checks: real MP4 with a video track, ≤95 MB, 3 s–15 min.
* `POST   /api/v1/content/images/upload?brand=` (CSRF, raw PNG/JPEG/WebP ≤8 MB) — stage one image.
* `POST   /api/v1/content/posts?brand=` (CSRF) — staged images, in order: 1 = photo post,
  2–10 = carousel, waiting for approval.
* `GET    /api/v1/content/studio?brand=` — every owner upload not removed, newest first.
* `PATCH  /api/v1/content/studio/{id}?brand=` (CSRF) — title, caption, hashtags (before approval).
* `PUT    /api/v1/content/reels/{id}/poster?brand=` (CSRF, raw JPEG/PNG) — cover frame for previews.
* `DELETE /api/v1/content/studio/{id}?brand=` (CSRF) — remove an upload that isn't scheduled.
* `POST   /api/v1/content/schedule-batch?brand=` (CSRF) — schedule several approved posts at once.
* `GET    /api/v1/content/library?brand=` — brand logo, reels, cover frames and carousel slides.
* `PUT    /api/v1/brands/{ref}/logo` (CSRF, raw PNG/JPEG/WebP) and `DELETE` — the brand logo.
* `POST   /api/v1/analytics/sync?brand=` (CSRF) — fetch the latest numbers now.
* `GET    /api/v1/analytics/sync` — sync status, for polling after that.

Approving uses the shared `POST /content/carousels/{id}/decision`; approving never publishes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.analytics.service import sync_status
from media_os_api.analytics.sync import SYNC_METRICS_TASK
from media_os_api.api.routes.content import (
    CarouselSummary,
    ScheduleEntry,
    _brand_id,
    _db,
    _package,
    _schedules,
    _scheduling,
    _slide_assets,
    _summary,
)
from media_os_api.auth import SessionPayload, require_csrf, require_session
from media_os_api.content import uploads as up
from media_os_api.db.models import Asset, BrandProfile, ContentPackage
from media_os_api.jobs.ledger import enqueue_durable_job
from media_os_api.jobs.routing import queue_for_task
from media_os_api.publish.service import (
    RESCHEDULABLE_STATES,
    ScheduleRequestError,
    can_schedule,
    package_schedules,
    request_schedule,
)
from media_os_api.services.audit_service import record_audit_event
from media_os_api.workflow.state_machine import ContentLifecycleStatus, transition_content_status

router = APIRouter(tags=["studio"])
BrandParam = Annotated[str, Query(min_length=1, max_length=128)]


def _fail(exc: up.UploadError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": str(exc)})


def _too_big(request: Request, limit: int) -> None:
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            {"code": "TOO_LARGE", "message": f"File is larger than {limit // (1024 * 1024)} MB."},
        )


def _brand(db: Session, ref: str) -> BrandProfile:
    brand = db.get(BrandProfile, _brand_id(db, ref))
    if brand is None:  # pragma: no cover - _brand_id already 404s
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "BRAND_NOT_FOUND"})
    return brand


# --------------------------------------------------------------------------- reels


class ReelItem(CarouselSummary):
    """One owner upload: a reel, a photo post or a carousel."""

    caption: str | None
    hashtags: list[str]
    slide_asset_ids: list[str] = Field(default_factory=list)
    video_asset_id: str | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    file_size: int | None
    qa_report: dict[str, Any] | None


class ReelList(BaseModel):
    items: list[ReelItem]


def _reel(db: Session, pkg: ContentPackage) -> ReelItem:
    videos = up.active_assets(db, pkg, up.REEL_VIDEO)
    video = videos[0] if videos else None
    shown = _slide_assets(db, pkg)  # a reel's cover, or the post's images in order
    first = video or (shown[0] if shown else None)
    return ReelItem(
        **_summary(pkg, shown),
        schedules=_schedules(db, pkg),
        scheduling=_scheduling(db, pkg),
        can_schedule=can_schedule(pkg, package_schedules(db, pkg)),
        caption=pkg.caption,
        hashtags=list(pkg.hashtags or []),
        slide_asset_ids=[] if video else [str(a.id) for a in shown],
        video_asset_id=str(video.id) if video else None,
        duration_seconds=float(video.duration_seconds)
        if video and video.duration_seconds
        else None,
        width=first.width if first else None,
        height=first.height if first else None,
        file_size=video.file_size if video else sum(a.file_size for a in shown) or None,
        qa_report=pkg.qa_report,
    )


def _owner_pkg(db: Session, brand_id: uuid.UUID, package_id: uuid.UUID) -> ContentPackage:
    pkg = _package(db, brand_id, package_id)
    if not up.is_owner_upload(pkg):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, {"code": "NOT_FOUND", "message": "Upload not found."}
        )
    return pkg


def _reel_pkg(db: Session, brand_id: uuid.UUID, package_id: uuid.UUID) -> ContentPackage:
    pkg = _owner_pkg(db, brand_id, package_id)
    if pkg.format != "REEL":
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, {"code": "NOT_FOUND", "message": "Reel not found."}
        )
    return pkg


@router.post("/content/reels/upload", response_model=ReelItem, status_code=201)
async def upload_reel(
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
    filename: Annotated[str, Query(max_length=255)] = "Reel.mp4",
) -> ReelItem:
    _too_big(request, up.MAX_REEL_BYTES)
    with _db(request) as db:
        profile = _brand(db, brand)
        relative, dest = up.new_upload_path(profile, "reels", "mp4")
    try:
        size, digest, _head = await up.save_stream(request.stream(), dest, limit=up.MAX_REEL_BYTES)
    except up.UploadError as exc:
        raise _fail(exc) from exc
    try:
        with _db(request) as db:
            profile = _brand(db, brand)
            pkg = up.create_reel(
                db,
                brand=profile,
                path=dest,
                relative=relative,
                size=size,
                checksum=digest,
                title=up.title_from_filename(filename),
                actor_id=f"owner:{session.subject}",
            )
            db.flush()
            return _reel(db, pkg)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise


class ImageStaged(BaseModel):
    asset_id: str
    width: int | None
    height: int | None
    file_size: int


@router.post("/content/images/upload", response_model=ImageStaged, status_code=201)
async def upload_image(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> ImageStaged:
    """Stage one image for a photo post or carousel (a post only after `POST /content/posts`)."""
    _too_big(request, up.MAX_POST_IMAGE_BYTES)
    data = await _read_small(request, up.MAX_POST_IMAGE_BYTES)
    mime = up.sniff_image(data[:64])
    if mime is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "NOT_AN_IMAGE", "message": "Use PNG, JPEG or WebP images."},
        )
    with _db(request) as db:
        asset = up.stage_image(db, brand=_brand(db, brand), data=data, mime=mime)
        return ImageStaged(
            asset_id=str(asset.id),
            width=asset.width,
            height=asset.height,
            file_size=asset.file_size,
        )


class PostCreate(BaseModel):
    asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=up.MAX_POST_IMAGES)
    title: str = Field(default="", max_length=512)


@router.post("/content/posts", response_model=ReelItem, status_code=201)
def create_post(
    payload: PostCreate,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> ReelItem:
    """Staged images, in order, become a photo post (1) or a carousel (2-10)."""
    with _db(request) as db:
        try:
            pkg = up.create_image_post(
                db,
                brand=_brand(db, brand),
                asset_ids=list(payload.asset_ids),
                title=payload.title.strip(),
                actor_id=f"owner:{session.subject}",
            )
        except up.UploadError as exc:
            raise _fail(exc) from exc
        db.flush()
        return _reel(db, pkg)


@router.get("/content/studio", response_model=ReelList)
def list_studio(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ReelList:
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        pkgs = db.execute(
            select(ContentPackage)
            .where(
                ContentPackage.brand_profile_id == brand_id,
                ContentPackage.format.in_(up.OWNER_FORMATS),
                ContentPackage.qa_report["version"].astext.like(f"{up.OWNER_QA_PREFIX}%"),
                ContentPackage.status != ContentLifecycleStatus.REJECTED.value,
            )
            .order_by(ContentPackage.created_at.desc())
            .limit(limit)
        ).scalars()
        return ReelList(items=[_reel(db, p) for p in pkgs])


class ReelEdit(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    caption: str | None = Field(default=None, max_length=5000)
    hashtags: list[str] | None = Field(default=None, max_length=60)


@router.patch("/content/studio/{package_id}", response_model=ReelItem)
def edit_upload(
    package_id: uuid.UUID,
    payload: ReelEdit,
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> ReelItem:
    with _db(request) as db:
        pkg = _owner_pkg(db, _brand_id(db, brand), package_id)
        if pkg.approval_status != "PENDING":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "ALREADY_DECIDED",
                    "message": "Edit the caption before approving. This post is already "
                    f"{pkg.approval_status.lower()}.",
                },
            )
        try:
            up.update_reel_text(
                pkg, title=payload.title, caption=payload.caption, hashtags=payload.hashtags
            )
        except up.UploadError as exc:
            raise _fail(exc) from exc
        db.flush()
        return _reel(db, pkg)


@router.put("/content/reels/{package_id}/poster", response_model=ReelItem)
async def set_reel_poster(
    package_id: uuid.UUID,
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> ReelItem:
    _too_big(request, up.MAX_IMAGE_BYTES)
    data = await _read_small(request, up.MAX_IMAGE_BYTES)
    mime = up.sniff_image(data[:64])
    if mime not in ("image/jpeg", "image/png"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "NOT_AN_IMAGE", "message": "The cover must be a JPEG or PNG image."},
        )
    with _db(request) as db:
        pkg = _reel_pkg(db, _brand_id(db, brand), package_id)
        profile = _brand(db, brand)
        relative, dest = up.new_upload_path(profile, "posters", up.IMAGE_TYPES[mime])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        for old in up.active_assets(db, pkg, up.REEL_POSTER):
            up.retire_asset(old)
        width, height = up.image_size(data)
        db.add(
            Asset(
                brand_profile_id=profile.id,
                content_package_id=pkg.id,
                asset_type=up.REEL_POSTER,
                local_path=relative,
                mime_type=mime,
                file_size=len(data),
                checksum=f"sha256:{_sha(data)}",
                width=width,
                height=height,
                status="ACTIVE",
            )
        )
        db.flush()
        return _reel(db, pkg)


@router.delete("/content/studio/{package_id}", status_code=204)
def remove_upload(
    package_id: uuid.UUID,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> None:
    with _db(request) as db:
        pkg = _owner_pkg(db, _brand_id(db, brand), package_id)
        live = [s for s in package_schedules(db, pkg) if s.status not in RESCHEDULABLE_STATES]
        if live or _scheduling(db, pkg):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {
                    "code": "SCHEDULED",
                    "message": "This post is scheduled or published. Delete it in Postiz first.",
                },
            )
        pkg.approval_status = "REJECTED"
        transition_content_status(
            db,
            content_package_id=pkg.id,
            to_status=ContentLifecycleStatus.REJECTED,
            actor_id=f"owner:{session.subject}",
            reason="Owner removed the upload",
            expected_brand_id=pkg.brand_profile_id,
        )
        # Only uploaded files are deleted (retire_asset never touches rendered slides).
        for kind in (up.REEL_VIDEO, up.REEL_POSTER, up.SLIDE):
            for asset in up.active_assets(db, pkg, kind):
                up.retire_asset(asset)
        record_audit_event(
            db,
            actor_id=f"owner:{session.subject}",
            action="POST_REMOVED" if pkg.format != "REEL" else "REEL_REMOVED",
            target_type="content_package",
            target_id=str(pkg.id),
            outcome="SUCCESS",
        )


# --------------------------------------------------------------------------- batch schedule


class BatchItem(BaseModel):
    package_id: uuid.UUID
    scheduled_at: datetime


class BatchRequest(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=30)
    platforms: list[Literal["INSTAGRAM", "FACEBOOK"]] = Field(
        default=["INSTAGRAM", "FACEBOOK"], min_length=1, max_length=2
    )


class BatchResult(BaseModel):
    package_id: str
    scheduled_at: str
    ok: bool
    code: str | None = None
    message: str | None = None


class BatchResponse(BaseModel):
    scheduled: int
    results: list[BatchResult]


@router.post("/content/schedule-batch", response_model=BatchResponse)
def schedule_batch(
    payload: BatchRequest,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> BatchResponse:
    """Schedule several approved posts in one go. Each is checked on its own (time window, daily
    cap, 5-minute spacing); the ones that pass are scheduled, the rest say why."""
    actor = f"owner:{session.subject}"
    results: list[BatchResult] = []
    with _db(request) as db:
        profile = _brand(db, brand)
        for item in sorted(payload.items, key=lambda i: i.scheduled_at):
            when = item.scheduled_at
            res = BatchResult(
                package_id=str(item.package_id), scheduled_at=when.isoformat(), ok=False
            )
            try:
                pkg = _package(db, profile.id, item.package_id)
            except HTTPException:
                res.code, res.message = "NOT_FOUND", "Post not found."
                results.append(res)
                continue
            live = [s for s in package_schedules(db, pkg) if s.status not in RESCHEDULABLE_STATES]
            if live or _scheduling(db, pkg):
                res.code, res.message = "ALREADY_SCHEDULED", "Already scheduled."
                results.append(res)
                continue
            try:
                with db.begin_nested():
                    request_schedule(
                        db,
                        pkg=pkg,
                        brand=profile,
                        when=when,
                        platforms=list(payload.platforms),
                        actor_id=actor,
                    )
                    db.flush()
                res.ok = True
            except ScheduleRequestError as exc:
                res.code, res.message = exc.code, str(exc)
            results.append(res)
    return BatchResponse(scheduled=sum(r.ok for r in results), results=results)


# --------------------------------------------------------------------------- library


class LibraryItem(BaseModel):
    asset_id: str
    kind: str  # BRAND_LOGO | REEL_VIDEO | REEL_POSTER | CAROUSEL_SLIDE
    mime_type: str
    file_size: int
    width: int | None
    height: int | None
    duration_seconds: float | None
    content_package_id: str | None
    title: str | None
    created_at: str


class LibraryResponse(BaseModel):
    items: list[LibraryItem]


@router.get("/content/library", response_model=LibraryResponse)
def library(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
    kind: Annotated[
        Literal["BRAND_LOGO", "REEL_VIDEO", "REEL_POSTER", "CAROUSEL_SLIDE"] | None, Query()
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 300,
) -> LibraryResponse:
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        stmt = (
            select(Asset, ContentPackage.title)
            .outerjoin(ContentPackage, ContentPackage.id == Asset.content_package_id)
            .where(
                Asset.brand_profile_id == brand_id,
                Asset.status == "ACTIVE",
                Asset.asset_type.in_(
                    (up.BRAND_LOGO, up.REEL_VIDEO, up.REEL_POSTER, "CAROUSEL_SLIDE")
                ),
            )
        )
        if kind:
            stmt = stmt.where(Asset.asset_type == kind)
        rows = db.execute(stmt.order_by(Asset.created_at.desc()).limit(limit)).all()
        return LibraryResponse(
            items=[
                LibraryItem(
                    asset_id=str(a.id),
                    kind=a.asset_type,
                    mime_type=a.mime_type,
                    file_size=a.file_size,
                    width=a.width,
                    height=a.height,
                    duration_seconds=float(a.duration_seconds) if a.duration_seconds else None,
                    content_package_id=str(a.content_package_id) if a.content_package_id else None,
                    title=(title or "").replace("*", "")[:140] or None,
                    created_at=a.created_at.isoformat(),
                )
                for a, title in rows
            ]
        )


# --------------------------------------------------------------------------- brand logo


class LogoResponse(BaseModel):
    logo_asset_id: str | None


def _sha(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


async def _read_small(request: Request, limit: int) -> bytes:
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                {"code": "TOO_LARGE", "message": f"Images can be up to {limit // 1048576} MB."},
            )
    return bytes(buf)


def _current_logos(db: Session, brand_id: uuid.UUID) -> list[Asset]:
    return list(
        db.execute(
            select(Asset).where(
                Asset.brand_profile_id == brand_id,
                Asset.asset_type == up.BRAND_LOGO,
                Asset.status == "ACTIVE",
            )
        ).scalars()
    )


def _set_logo_id(profile: BrandProfile, asset_id: str | None) -> None:
    identity = dict(profile.visual_identity or {})
    if asset_id:
        identity["logo_asset_id"] = asset_id
    else:
        identity.pop("logo_asset_id", None)
    profile.visual_identity = identity  # reassign so SQLAlchemy sees the JSON change


@router.put("/brands/{brand_ref}/logo", response_model=LogoResponse)
async def set_logo(
    brand_ref: str,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
) -> LogoResponse:
    _too_big(request, up.MAX_IMAGE_BYTES)
    data = await _read_small(request, up.MAX_IMAGE_BYTES)
    mime = up.sniff_image(data[:64])
    if mime is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "NOT_AN_IMAGE", "message": "Use a PNG, JPEG or WebP image."},
        )
    with _db(request) as db:
        profile = _brand(db, brand_ref)
        relative, dest = up.new_upload_path(profile, "brand", up.IMAGE_TYPES[mime])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        for old in _current_logos(db, profile.id):
            up.retire_asset(old)
        width, height = up.image_size(data)
        asset = Asset(
            brand_profile_id=profile.id,
            content_package_id=None,
            asset_type=up.BRAND_LOGO,
            local_path=relative,
            mime_type=mime,
            file_size=len(data),
            checksum=f"sha256:{_sha(data)}",
            width=width,
            height=height,
            status="ACTIVE",
        )
        db.add(asset)
        db.flush()
        _set_logo_id(profile, str(asset.id))
        record_audit_event(
            db,
            actor_id=f"owner:{session.subject}",
            action="BRAND_LOGO_SET",
            target_type="brand_profile",
            target_id=str(profile.id),
            outcome="SUCCESS",
        )
        return LogoResponse(logo_asset_id=str(asset.id))


@router.delete("/brands/{brand_ref}/logo", response_model=LogoResponse)
def remove_logo(
    brand_ref: str,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
) -> LogoResponse:
    with _db(request) as db:
        profile = _brand(db, brand_ref)
        for old in _current_logos(db, profile.id):
            up.retire_asset(old)
        _set_logo_id(profile, None)
        record_audit_event(
            db,
            actor_id=f"owner:{session.subject}",
            action="BRAND_LOGO_REMOVED",
            target_type="brand_profile",
            target_id=str(profile.id),
            outcome="SUCCESS",
        )
        return LogoResponse(logo_asset_id=None)


# --------------------------------------------------------------------------- analytics sync


MANUAL_SYNC_BUCKET_SECONDS = 60


@router.post("/analytics/sync", status_code=202)
def sync_now(
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> dict[str, Any]:
    """Queue an analytics sync of every post up to 30 days old. Repeated clicks within a minute
    reuse the same job."""
    now = datetime.now(UTC)
    bucket = int(now.timestamp()) // MANUAL_SYNC_BUCKET_SECONDS
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        job, created = enqueue_durable_job(
            db,
            task_name=SYNC_METRICS_TASK,
            task_type="ANALYTICS_SYNC",
            payload={"trigger": "manual", "force_recent": True},
            idempotency_key=f"analytics:manual:{bucket}",
            queue_name=queue_for_task(SYNC_METRICS_TASK),
            maximum_attempts=2,
        )
        if created:
            record_audit_event(
                db,
                actor_id=f"owner:{session.subject}",
                action="ANALYTICS_SYNC_REQUESTED",
                target_type="brand_profile",
                target_id=str(brand_id),
                outcome="SUCCESS",
            )
        return {"job_id": str(job.id), "queued_at": now.isoformat(), "sync": sync_status(db)}


@router.get("/analytics/sync")
def get_sync(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
) -> dict[str, Any]:
    with _db(request) as db:
        return sync_status(db)


__all__ = ["router", "ScheduleEntry"]
