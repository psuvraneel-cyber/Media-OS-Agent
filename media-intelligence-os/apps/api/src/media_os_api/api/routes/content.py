"""Carousel review queue (roadmap N2). Owner-authenticated, brand-scoped.

* `GET  /api/v1/content/carousels?brand=<ref>&queue=review|all` — carousels with QA and score.
* `GET  /api/v1/content/carousels/{id}?brand=<ref>` — one carousel with slides and assets.
* `GET  /api/v1/content/assets/{id}/file?brand=<ref>` — a rendered slide PNG (read-only volume).
* `PATCH /api/v1/content/carousels/{id}` (CSRF) — edit the text of a carousel awaiting review;
  QA runs again and the slides re-render.
* `POST /api/v1/content/carousels/{id}/rerender` (CSRF) — render the slides again (lost files,
  or a failed render check).
* `POST /api/v1/content/carousels/{id}/decision` (CSRF) — approve or reject.
* `POST /api/v1/content/carousels/{id}/cancel` (CSRF) — cancel a scheduled post on every platform,
  or move it to a new time (`move_to`). The worker deletes it in Postiz.
* `POST /api/v1/content/carousels/{id}/schedule` (CSRF) — schedule an APPROVED carousel to
  Instagram/Facebook at a chosen time (N3). The worker uploads the images and creates the post in
  Postiz; Postiz publishes at that time. Approving alone never publishes anything.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.auth import SessionPayload, require_csrf, require_session
from media_os_api.carousel.service import RecheckError, recheck_package
from media_os_api.content.uploads import OWNER_QA_PREFIX, resolve_asset_path
from media_os_api.db.models import Asset, BrandProfile, ContentPackage, PublishSchedule
from media_os_api.db.session import session_scope
from media_os_api.publish.service import (
    OVERDUE_AFTER,
    RESCHEDULABLE_STATES,
    SCHEDULABLE_FORMATS,
    ScheduleRequestError,
    can_cancel,
    can_schedule,
    package_schedules,
    request_cancel,
    request_schedule,
)
from media_os_api.services import BrandNotFoundError, get_brand
from media_os_api.services.audit_service import record_audit_event
from media_os_api.workflow.state_machine import ContentLifecycleStatus, transition_content_status

router = APIRouter(tags=["content"])

SERVABLE_TYPES = ("image/png", "image/jpeg", "image/webp", "video/mp4")
THUMBNAIL_TYPES = ("CAROUSEL_SLIDE", "REEL_POSTER")

REVIEW_STATUSES = ("QA_PASSED", "RENDERED", "DRAFTED")
PUBLISH_STATUSES = (
    "QA_PASSED",
    "POSTIZ_DRAFT",
    "USER_APPROVED",
    "SCHEDULED",
    "PUBLISHING",
    "PUBLISHED",
    "FAILED_RETRYABLE",
)


class ScheduleEntry(BaseModel):
    platform: str
    status: str
    scheduled_at: str
    timezone: str
    post_url: str | None


class ScheduleRequest(BaseModel):
    scheduled_at: datetime
    platforms: list[Literal["INSTAGRAM", "FACEBOOK"]] = Field(
        default=["INSTAGRAM", "FACEBOOK"], min_length=1, max_length=2
    )


class SlideAsset(BaseModel):
    id: str
    index: int
    width: int | None
    height: int | None


class CarouselSummary(BaseModel):
    id: str
    format: str = "CAROUSEL"  # CAROUSEL | REEL
    status: str
    approval_status: str
    title: str | None
    hook: str | None
    language: str | None
    pillar: str | None
    hook_type: str | None
    slide_count: int
    qa_passed: bool | None
    qa_warnings: int
    score_total: int | None
    score_label: str | None
    thumbnail_asset_id: str | None
    created_at: str
    schedules: list[ScheduleEntry] = Field(default_factory=list)
    scheduling: bool = False
    can_schedule: bool = False  # QA-passed, or every earlier platform post failed/cancelled
    can_cancel: bool = False  # scheduled, and more than 5 minutes before the post time
    slides_missing: int = 0  # rendered slides missing or empty on disk (re-render to fix)
    render_failed: bool = False  # the render job gave up; re-render to try again


class CarouselDetail(CarouselSummary):
    caption: str | None
    hashtags: list[str]
    alt_text: str | None
    slides: list[dict[str, Any]]
    assets: list[SlideAsset]
    qa_report: dict[str, Any] | None
    score: dict[str, Any] | None
    post_tags: dict[str, Any] | None


class CarouselList(BaseModel):
    items: list[CarouselSummary]


class DecisionRequest(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    reason: str | None = Field(default=None, max_length=500)


@contextmanager
def _db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] | None = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": "DATABASE_NOT_CONFIGURED", "message": "Content storage is not configured."},
        )
    try:
        with session_scope(factory) as session:
            yield session
    except SQLAlchemyError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            {"code": "DATABASE_UNAVAILABLE", "message": "Content storage is unavailable."},
        ) from exc


def _brand_id(db: Session, ref: str) -> uuid.UUID:
    try:
        return get_brand(db, ref).id
    except BrandNotFoundError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, {"code": "BRAND_NOT_FOUND", "message": "Unknown brand."}
        ) from exc


def _package(db: Session, brand_id: uuid.UUID, package_id: uuid.UUID) -> ContentPackage:
    pkg = db.get(ContentPackage, package_id)
    if pkg is None or pkg.brand_profile_id != brand_id or pkg.format not in SCHEDULABLE_FORMATS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, {"code": "NOT_FOUND", "message": "Post not found."}
        )
    return pkg


def _slide_assets(db: Session, pkg: ContentPackage) -> list[Asset]:
    """A carousel's slides, or a reel's cover frame: the first one is the thumbnail."""
    kind = "REEL_POSTER" if pkg.format == "REEL" else "CAROUSEL_SLIDE"
    rows = db.execute(
        select(Asset)
        .where(
            Asset.content_package_id == pkg.id,
            Asset.asset_type == kind,
            Asset.status == "ACTIVE",
        )
        .order_by(Asset.local_path)
    ).scalars()
    return list(rows)


def _schedules(db: Session, pkg: ContentPackage) -> list[ScheduleEntry]:
    rows = db.execute(
        select(PublishSchedule)
        .where(PublishSchedule.content_package_id == pkg.id)
        .order_by(PublishSchedule.platform)
    ).scalars()
    return [
        ScheduleEntry(
            platform=r.platform,
            status=r.status,
            scheduled_at=r.scheduled_at.isoformat(),
            timezone=r.timezone,
            post_url=r.remote_post_id if (r.remote_post_id or "").startswith("http") else None,
        )
        for r in rows
    ]


def _scheduling(db: Session, pkg: ContentPackage) -> bool:
    from media_os_api.db.models import JobRun

    return (
        db.execute(
            select(JobRun.id).where(
                JobRun.task_name.in_(
                    ("media_os.publish.schedule_carousel", "media_os.publish.cancel_post")
                ),
                JobRun.status.in_(("PENDING", "DISPATCHED", "RUNNING", "FAILED_RETRYABLE")),
                JobRun.payload["content_package_id"].astext == str(pkg.id),
            )
        ).first()
        is not None
    )


def _summary(pkg: ContentPackage, assets: list[Asset]) -> dict[str, Any]:
    from sqlalchemy.orm import object_session

    from media_os_api.carousel.service import expected_slides, missing_slides, render_failed

    tags = pkg.post_tags or {}
    db = object_session(pkg)
    qa = pkg.qa_report or {}
    score = pkg.score or {}
    return {
        "id": str(pkg.id),
        "format": pkg.format,
        "status": pkg.status,
        "approval_status": pkg.approval_status,
        # Older carousels stored *emphasis* markers in the title and hook.
        "title": pkg.title.replace("*", "") if pkg.title else pkg.title,
        "hook": pkg.hook.replace("*", "") if pkg.hook else pkg.hook,
        "slides_missing": (
            missing_slides(assets, expected_slides(pkg)) if pkg.format == "CAROUSEL" else 0
        ),
        "render_failed": render_failed(db, pkg) if db is not None else False,
        "language": tags.get("language"),
        "pillar": tags.get("pillar"),
        "hook_type": tags.get("hook_type"),
        "slide_count": len(pkg.slide_data or []),
        "qa_passed": qa.get("passed") if qa else None,
        "qa_warnings": sum(1 for c in qa.get("checks", []) if c.get("status") == "WARN"),
        "score_total": score.get("total"),
        "score_label": score.get("label"),
        "thumbnail_asset_id": str(assets[0].id) if assets else None,
        "created_at": pkg.created_at.isoformat(),
    }


@router.get("/content/carousels", response_model=CarouselList)
def list_carousels(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
    queue: Annotated[Literal["review", "publish", "all"], Query()] = "review",
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> CarouselList:
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        # Review covers generated carousels (owner reels are reviewed in Studio); the publish
        # queue holds every approved carousel and reel.
        stmt = select(ContentPackage).where(
            ContentPackage.brand_profile_id == brand_id,
            ContentPackage.format.in_(SCHEDULABLE_FORMATS),
        )
        if queue == "review":
            stmt = stmt.where(
                ContentPackage.format == "CAROUSEL",
                func.coalesce(ContentPackage.qa_report["version"].astext, "").not_like(
                    f"{OWNER_QA_PREFIX}%"
                ),
                ContentPackage.status.in_(REVIEW_STATUSES),
                ContentPackage.approval_status == "PENDING",
            )
        elif queue == "publish":
            stmt = stmt.where(
                ContentPackage.status.in_(PUBLISH_STATUSES),
                ContentPackage.approval_status == "APPROVED",
            )
        pkgs = db.execute(stmt.order_by(ContentPackage.created_at.desc()).limit(limit)).scalars()
        items = [
            CarouselSummary(
                **_summary(p, _slide_assets(db, p)),
                schedules=_schedules(db, p),
                scheduling=_scheduling(db, p),
                can_schedule=can_schedule(p, package_schedules(db, p)),
                can_cancel=can_cancel(db, p, datetime.now(UTC)),
            )
            for p in pkgs
        ]
        return CarouselList(items=items)


@router.get("/content/carousels/{package_id}", response_model=CarouselDetail)
def get_carousel(
    package_id: uuid.UUID,
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> CarouselDetail:
    with _db(request) as db:
        pkg = _package(db, _brand_id(db, brand), package_id)
        assets = _slide_assets(db, pkg)
        return CarouselDetail(
            **_summary(pkg, assets),
            schedules=_schedules(db, pkg),
            scheduling=_scheduling(db, pkg),
            can_schedule=can_schedule(pkg, package_schedules(db, pkg)),
            can_cancel=can_cancel(db, pkg, datetime.now(UTC)),
            caption=pkg.caption,
            hashtags=list(pkg.hashtags or []),
            alt_text=pkg.description,
            slides=list(pkg.slide_data or []),
            assets=[
                SlideAsset(id=str(a.id), index=i, width=a.width, height=a.height)
                for i, a in enumerate(assets, 1)
            ],
            qa_report=pkg.qa_report,
            score=pkg.score,
            post_tags=pkg.post_tags,
        )


@router.get("/content/assets/{asset_id}/file")
def get_asset_file(
    asset_id: uuid.UUID,
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> FileResponse:
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        asset = db.get(Asset, asset_id)
        if (
            asset is None
            or asset.brand_profile_id != brand_id
            or asset.status != "ACTIVE"
            or asset.mime_type not in SERVABLE_TYPES
        ):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, {"code": "NOT_FOUND", "message": "Asset not found."}
            )
        relative, mime = asset.local_path, asset.mime_type
    path = resolve_asset_path(relative)
    if path is None or not path.is_file():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, {"code": "NOT_FOUND", "message": "Asset file missing."}
        )
    # Videos are streamed with Range support, so the browser can seek without downloading all.
    return FileResponse(path, media_type=mime, headers={"Cache-Control": "private"})


class CalendarEntry(BaseModel):
    schedule_id: str
    content_package_id: str
    title: str
    platform: str
    status: str  # SCHEDULED | OVERDUE | PUBLISHED | FAILED | CANCELLED
    overdue: bool
    scheduled_at: str
    post_url: str | None
    thumbnail_asset_id: str | None


class CalendarResponse(BaseModel):
    start: str
    end: str
    items: list[CalendarEntry]


CALENDAR_MAX_SPAN = timedelta(days=93)


@router.get("/content/schedules", response_model=CalendarResponse)
def list_schedules(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
    start: Annotated[datetime, Query()],
    end: Annotated[datetime, Query()],
) -> CalendarResponse:
    """Every platform post of this brand scheduled in [start, end), for the publishing calendar."""
    if start.tzinfo is None or end.tzinfo is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "NAIVE_TIME", "message": "start and end must include a timezone."},
        )
    if not start < end <= start + CALENDAR_MAX_SPAN:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "BAD_RANGE", "message": "end must be after start and within 93 days."},
        )
    now = datetime.now(UTC)
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        rows = db.execute(
            select(PublishSchedule, ContentPackage)
            .join(ContentPackage, ContentPackage.id == PublishSchedule.content_package_id)
            .where(
                PublishSchedule.brand_profile_id == brand_id,
                PublishSchedule.scheduled_at >= start,
                PublishSchedule.scheduled_at < end,
            )
            .order_by(PublishSchedule.scheduled_at, PublishSchedule.platform)
        ).all()
        thumbs: dict[uuid.UUID, str] = {}
        for pkg_id in {p.id for _s, p in rows}:
            first = db.execute(
                select(Asset.id)
                .where(
                    Asset.content_package_id == pkg_id,
                    Asset.asset_type.in_(THUMBNAIL_TYPES),
                    Asset.status == "ACTIVE",
                )
                .order_by(Asset.local_path)
                .limit(1)
            ).scalar_one_or_none()
            if first is not None:
                thumbs[pkg_id] = str(first)
        items = [
            CalendarEntry(
                schedule_id=str(sched.id),
                content_package_id=str(pkg.id),
                title=(pkg.title or pkg.hook or "Untitled").replace("*", "")[:140],
                platform=sched.platform,
                status=sched.status,
                overdue=sched.status == "OVERDUE"
                or (sched.status == "SCHEDULED" and sched.scheduled_at < now - OVERDUE_AFTER),
                scheduled_at=sched.scheduled_at.isoformat(),
                post_url=(
                    sched.remote_post_id
                    if (sched.remote_post_id or "").startswith("http")
                    else None
                ),
                thumbnail_asset_id=thumbs.get(pkg.id),
            )
            for sched, pkg in rows
        ]
    return CalendarResponse(start=start.isoformat(), end=end.isoformat(), items=items)


@router.post("/content/carousels/{package_id}/decision", response_model=CarouselSummary)
def decide_carousel(
    package_id: uuid.UUID,
    payload: DecisionRequest,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> CarouselSummary:
    actor = f"owner:{session.subject}"
    with _db(request) as db:
        pkg = _package(db, _brand_id(db, brand), package_id)
        if pkg.approval_status != "PENDING":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"code": "ALREADY_DECIDED", "message": f"Already {pkg.approval_status}."},
            )
        if payload.decision == "APPROVE":
            if pkg.status != "QA_PASSED":
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {
                        "code": "QA_NOT_PASSED",
                        "message": "Only QA-passed carousels can be approved.",
                    },
                )
            from media_os_api.carousel.service import expected_slides, missing_slides

            if pkg.format == "CAROUSEL" and missing_slides(
                _slide_assets(db, pkg), expected_slides(pkg)
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    {
                        "code": "SLIDES_MISSING",
                        "message": "Some slide images are missing. Re-render the slides first.",
                    },
                )
            pkg.approval_status = "APPROVED"
        else:
            pkg.approval_status = "REJECTED"
            transition_content_status(
                db,
                content_package_id=pkg.id,
                to_status=ContentLifecycleStatus.REJECTED,
                actor_id=actor,
                reason=(payload.reason or "Rejected in review")[:500],
                expected_brand_id=pkg.brand_profile_id,
            )
            db.refresh(pkg)
        record_audit_event(
            db,
            actor_id=actor,
            action="CAROUSEL_APPROVED" if payload.decision == "APPROVE" else "CAROUSEL_REJECTED",
            target_type="content_package",
            target_id=str(pkg.id),
            outcome="SUCCESS",
            metadata={"reason": (payload.reason or "")[:200]},
        )
        db.flush()
        # Autopilot: an approved Media OS carousel goes straight into the next free posting slot
        # when the brand has auto-scheduling on (owner uploads are scheduled by hand).
        scheduled = False
        if payload.decision == "APPROVE":
            from media_os_api.content.autopilot import auto_schedule

            profile = db.get(BrandProfile, pkg.brand_profile_id)
            scheduled = profile is not None and auto_schedule(db, pkg, profile) is not None
            db.flush()
        return CarouselSummary(
            **_summary(pkg, _slide_assets(db, pkg)),
            schedules=_schedules(db, pkg),
            scheduling=scheduled or _scheduling(db, pkg),
            can_schedule=can_schedule(pkg, package_schedules(db, pkg)),
        )


@router.post("/content/carousels/{package_id}/schedule", response_model=CarouselSummary)
def schedule_carousel(
    package_id: uuid.UUID,
    payload: ScheduleRequest,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> CarouselSummary:
    """Owner schedules an APPROVED carousel; the worker uploads images, then creates the post.

    Also reschedules a carousel whose every earlier platform post failed or was cancelled
    (package FAILED_RETRYABLE): the worker creates new Postiz posts under a new group id.
    """
    actor = f"owner:{session.subject}"
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        pkg = _package(db, brand_id, package_id)
        profile = db.get(BrandProfile, brand_id)
        if profile is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, {"code": "BRAND_NOT_FOUND", "message": "Unknown brand."}
            )
        # Failed / cancelled posts stay as history but do not block a new attempt; any live
        # row (SCHEDULED, OVERDUE, PUBLISHED) or an in-flight schedule job still does.
        live = [s for s in package_schedules(db, pkg) if s.status not in RESCHEDULABLE_STATES]
        if _scheduling(db, pkg) or live:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"code": "ALREADY_SCHEDULED", "message": "This carousel is already scheduled."},
            )
        try:
            request_schedule(
                db,
                pkg=pkg,
                brand=profile,
                when=payload.scheduled_at,
                platforms=list(payload.platforms),
                actor_id=actor,
            )
        except ScheduleRequestError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, {"code": exc.code, "message": str(exc)}
            ) from exc
        db.flush()
        return CarouselSummary(
            **_summary(pkg, _slide_assets(db, pkg)),
            schedules=_schedules(db, pkg),
            scheduling=True,
        )


@router.post("/content/carousels/{package_id}/recheck", response_model=CarouselSummary)
def recheck_carousel(
    package_id: uuid.UUID,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> CarouselSummary:
    """Re-run QA with the latest rules on a draft that failed; renders it if it now passes."""
    with _db(request) as db:
        pkg = _package(db, _brand_id(db, brand), package_id)
        try:
            recheck_package(db, pkg, actor_id=f"owner:{session.subject}")
        except RecheckError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, {"code": "NOT_RECHECKABLE", "message": str(exc)}
            ) from exc
        return CarouselSummary(**_summary(pkg, _slide_assets(db, pkg)))


class SlideEdit(BaseModel):
    """Only the fields sent are changed; send null to clear one."""

    kicker: str | None = Field(default=None, max_length=28)
    headline_bn: str | None = Field(default=None, max_length=96)
    body_bn: str | None = Field(default=None, max_length=170)
    headline_en: str | None = Field(default=None, max_length=96)
    body_en: str | None = Field(default=None, max_length=170)
    sticker: str | None = Field(default=None, max_length=26)


class CarouselEdit(BaseModel):
    slides: list[SlideEdit] | None = Field(default=None, max_length=10)
    caption: str | None = Field(default=None, max_length=2200)
    hashtags: list[str] | None = Field(default=None, max_length=12)


@router.patch("/content/carousels/{package_id}", response_model=CarouselDetail)
def edit_carousel(
    package_id: uuid.UUID,
    payload: CarouselEdit,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> CarouselDetail:
    """Change a carousel's text before approving it. QA runs again and the slides re-render;
    the carousel comes back to the review queue once the new slides pass."""
    from media_os_api.carousel.service import EditError, edit_package

    with _db(request) as db:
        pkg = _package(db, _brand_id(db, brand), package_id)
        try:
            edit_package(
                db,
                pkg,
                actor_id=f"owner:{session.subject}",
                slides=(
                    [s.model_dump(exclude_unset=True) for s in payload.slides]
                    if payload.slides is not None
                    else None
                ),
                caption=payload.caption,
                hashtags=payload.hashtags,
            )
        except EditError as exc:
            code = status.HTTP_409_CONFLICT if exc.code == "NOT_EDITABLE" else 422
            raise HTTPException(code, {"code": exc.code, "message": str(exc)}) from exc
    return get_carousel(package_id, request, session, brand)


class CancelRequest(BaseModel):
    move_to: datetime | None = None  # None = just cancel
    platforms: list[Literal["INSTAGRAM", "FACEBOOK"]] | None = Field(
        default=None, min_length=1, max_length=2
    )


@router.post(
    "/content/carousels/{package_id}/cancel", response_model=CarouselSummary, status_code=202
)
def cancel_post(
    package_id: uuid.UUID,
    payload: CancelRequest,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> CarouselSummary:
    """Cancel a scheduled post (all platforms: Postiz deletes a post everywhere at once), or move
    it to `move_to`. Works for generated carousels and Studio uploads alike."""
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        pkg = _package(db, brand_id, package_id)
        profile = db.get(BrandProfile, brand_id)
        if profile is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, {"code": "NOT_FOUND", "message": "Brand not found."}
            )
        try:
            request_cancel(
                db,
                pkg=pkg,
                brand=profile,
                actor_id=f"owner:{session.subject}",
                move_to=payload.move_to,
                platforms=list(payload.platforms) if payload.platforms else None,
            )
        except ScheduleRequestError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, {"code": exc.code, "message": str(exc)}
            ) from exc
        db.flush()
        return CarouselSummary(
            **_summary(pkg, _slide_assets(db, pkg)),
            schedules=_schedules(db, pkg),
            scheduling=True,
            can_schedule=False,
            can_cancel=False,
        )


@router.post("/content/carousels/{package_id}/rerender", response_model=CarouselSummary)
def rerender_carousel(
    package_id: uuid.UUID,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> CarouselSummary:
    """Render the slides again. Use when slide images are missing or a render check failed."""
    from media_os_api.carousel.service import EditError, rerender_package

    with _db(request) as db:
        pkg = _package(db, _brand_id(db, brand), package_id)
        try:
            rerender_package(db, pkg, actor_id=f"owner:{session.subject}")
        except EditError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, {"code": exc.code, "message": str(exc)}
            ) from exc
        return CarouselSummary(**_summary(pkg, _slide_assets(db, pkg)))
