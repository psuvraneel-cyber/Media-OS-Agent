"""Persist a written carousel as a brand-owned ContentPackage at status DRAFTED."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.carousel.qa import QaReport
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft, plain
from media_os_api.content.taxonomy import validate_for_brand
from media_os_api.db.models import BrandProfile, ContentPackage, ContentStateTransition
from media_os_api.jobs.ledger import enqueue_durable_job
from media_os_api.jobs.routing import CREATIVE_QUEUE
from media_os_api.services.audit_service import record_audit_event
from media_os_api.workflow.state_machine import ContentLifecycleStatus, transition_content_status

ACTOR = "system:carousel"


def compose_caption(draft: CarouselDraft) -> str:
    parts = [p.strip() for p in (draft.caption_bn, draft.caption_en) if p and p.strip()]
    return "\n\n".join(parts)


def create_carousel_package(
    session: Session,
    *,
    brand_slug: str,
    brief: CarouselBrief,
    draft: CarouselDraft,
    qa: QaReport,
    score: dict[str, object],
    written_by: str,
    actor_id: str = ACTOR,
    design: dict[str, object] | None = None,
) -> ContentPackage:
    brand = session.execute(
        select(BrandProfile).where(BrandProfile.slug == brand_slug)
    ).scalar_one()
    tags = validate_for_brand(brief.post_tags(len(draft.slides)), list(brand.content_pillars or []))
    first = draft.slides[0]
    pkg = ContentPackage(
        brand_profile_id=brand.id,
        research_packet_id=None,
        platform="INSTAGRAM",
        format="CAROUSEL",
        status="DRAFTED",
        approval_status="PENDING",
        angle=brief.angle,
        hook=plain(first.headline_bn or first.headline_en or "")[:500],
        title=plain(first.headline_en or first.headline_bn or "Carousel")[:512],
        caption=compose_caption(draft),
        hashtags=list(draft.hashtags),
        slide_data=[s.model_dump(mode="json") for s in draft.slides],
        source_manifest=[{"kind": "brand_kit", "brand": brand_slug, "written_by": written_by}]
        + ([design] if design else []),
        post_tags=tags.model_dump(mode="json"),
        qa_report=qa.to_json(),
        score=dict(score),
    )
    pkg.description = draft.alt_text_en
    session.add(pkg)
    session.flush()
    session.add(
        ContentStateTransition(
            content_package_id=pkg.id,
            brand_profile_id=brand.id,
            from_status=None,
            to_status="DRAFTED",
            actor_id=actor_id,
            reason=f"Carousel written by {written_by}",
            content_version=pkg.version,
        )
    )
    record_audit_event(
        session,
        actor_id=actor_id,
        action="CAROUSEL_DRAFTED",
        target_type="content_package",
        target_id=str(pkg.id),
        outcome="SUCCESS" if qa.passed else "QA_FAILED",
        metadata={
            "brand": brand_slug,
            "language": brief.language.value,
            "pillar": brief.pillar,
            "slides": len(draft.slides),
        },
    )
    if qa.passed:
        enqueue_render(session, pkg)
    return pkg


def enqueue_render(session: Session, pkg: ContentPackage) -> None:
    from media_os_api.carousel.render_job import RENDER_TASK

    enqueue_durable_job(
        session,
        task_name=RENDER_TASK,
        task_type="CREATIVE",
        payload={"content_package_id": str(pkg.id)},
        idempotency_key=f"render:{pkg.id}:v{pkg.version}",
        brand_profile_id=pkg.brand_profile_id,
        queue_name=CREATIVE_QUEUE,
        maximum_attempts=2,
    )


class RecheckError(ValueError):
    pass


def _stored_brief(pkg: ContentPackage) -> CarouselBrief:
    tags = dict(pkg.post_tags or {})
    return CarouselBrief(
        pillar=tags["pillar"],
        hook_type=tags["hook_type"],
        occasion=tags["occasion"],
        language=tags["language"],
        visual_style=tags["visual_style"],
        call_to_action=tags["call_to_action"],
        posting_slot=tags["posting_slot"],
        angle=pkg.angle,
    )


def _stored_draft(
    pkg: ContentPackage,
    brief: CarouselBrief,
    *,
    slides: list[dict[str, Any]] | None = None,
    caption: str | None = None,
    hashtags: list[str] | None = None,
) -> CarouselDraft:
    """The stored caption is the composed Bengali+English text, so it stands in for both caption
    parts (EN-only posts use it as the English caption only)."""
    from media_os_api.content.taxonomy import Language

    text = pkg.caption if caption is None else caption
    english_only = brief.language is Language.EN
    return CarouselDraft.model_validate(
        {
            "slides": pkg.slide_data if slides is None else slides,
            "caption_bn": None if english_only else text,
            "caption_en": text,
            "hashtags": pkg.hashtags if hashtags is None else hashtags,
            "alt_text_en": pkg.description or "Carousel",
        }
    )


def recheck_package(session: Session, pkg: ContentPackage, *, actor_id: str) -> QaReport:
    """Re-run text QA with the current rules on a draft that failed earlier; render if it passes.

    Slides are unchanged.
    """
    from media_os_api.carousel.brand_kit import kit_for
    from media_os_api.carousel.qa import run_text_qa

    if pkg.format != "CAROUSEL" or pkg.status != "DRAFTED" or pkg.approval_status != "PENDING":
        raise RecheckError("Only pending drafts can be re-checked.")
    if (pkg.qa_report or {}).get("passed"):
        raise RecheckError("This draft already passed QA.")
    brand = session.get(BrandProfile, pkg.brand_profile_id)
    if brand is None:
        raise RecheckError("Brand not found.")
    brief = _stored_brief(pkg)
    draft = _stored_draft(pkg, brief)
    qa = run_text_qa(kit_for(brand.slug), brief, draft)
    pkg.qa_report = qa.to_json()
    record_audit_event(
        session,
        actor_id=actor_id,
        action="CAROUSEL_QA_RECHECKED",
        target_type="content_package",
        target_id=str(pkg.id),
        outcome="SUCCESS" if qa.passed else "QA_FAILED",
        metadata={"failures": [c.name for c in qa.failures]},
    )
    if qa.passed:
        enqueue_render(session, pkg)
    session.flush()
    return qa


# --------------------------------------------------------------------------- owner edits

EDITABLE_FIELDS = ("kicker", "headline_bn", "body_bn", "headline_en", "body_en", "sticker")
EDITABLE_STATUSES = ("DRAFTED", "RENDERED", "QA_PASSED")
EDIT_REASON_PREFIX = "Owner edit: "


class EditError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EditError("Slide text must be text.", code="BAD_EDIT")
    text = value.strip()
    return text or None


def _quote(text: str | None) -> str:
    return "'" + " ".join(plain(text or "").split())[:60] + "'" if text else "(empty)"


def edit_package(
    session: Session,
    pkg: ContentPackage,
    *,
    actor_id: str,
    slides: list[dict[str, Any]] | None = None,
    caption: str | None = None,
    hashtags: list[str] | None = None,
) -> QaReport | None:
    """Apply the owner's text edits to a carousel awaiting review, re-run text QA and re-render.

    `slides` has one entry per slide (same order); each entry holds only the fields to change
    (kicker, headlines, bodies, sticker). Roles, visuals and claim ids never change here. Returns
    the new QA report, or None when nothing changed.

    The version goes up, so the slides render into a new folder and the old render's jobs can
    never apply to the new text. The edit is recorded on a state transition, and the writer sees
    it later as one of the owner's notes.
    """
    from media_os_api.carousel.brand_kit import kit_for
    from media_os_api.carousel.qa import Check, run_text_qa
    from media_os_api.content.uploads import is_owner_upload

    lock_package(session, pkg)
    if pkg.format != "CAROUSEL" or is_owner_upload(pkg):
        raise EditError("Only generated carousels can be edited here.", code="NOT_EDITABLE")
    if pkg.approval_status != "PENDING" or pkg.status not in EDITABLE_STATUSES:
        raise EditError("Only carousels waiting for review can be edited.", code="NOT_EDITABLE")
    brand = session.get(BrandProfile, pkg.brand_profile_id)
    if brand is None:
        raise EditError("Brand not found.", code="NOT_FOUND")

    new_slides = [dict(s) for s in (pkg.slide_data or [])]
    changes: list[str] = []
    if slides is not None:
        if len(slides) != len(new_slides):
            raise EditError(
                f"Send one entry per slide ({len(new_slides)} slides).", code="BAD_EDIT"
            )
        for i, (edit, slide) in enumerate(zip(slides, new_slides, strict=True), 1):
            unknown = set(edit) - set(EDITABLE_FIELDS)
            if unknown:
                raise EditError(f"Can't edit {sorted(unknown)} here.", code="BAD_EDIT")
            for field_name, value in edit.items():
                new = _clean(value)
                if new != slide.get(field_name):
                    changes.append(
                        f"slide {i} {field_name} {_quote(slide.get(field_name))} → {_quote(new)}"
                    )
                    slide[field_name] = new
    new_caption = pkg.caption
    if caption is not None and caption.strip() != (pkg.caption or "").strip():
        new_caption = caption.strip()
        changes.append("caption")
    new_tags = list(pkg.hashtags or [])
    if hashtags is not None:
        cleaned = [h.strip() for h in hashtags if h and h.strip()]
        if cleaned != new_tags:
            new_tags = cleaned
            changes.append("hashtags")
    if not changes:
        return None

    brief = _stored_brief(pkg)
    try:
        draft = _stored_draft(pkg, brief, slides=new_slides, caption=new_caption, hashtags=new_tags)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err.get("loc", ()))
        raise EditError(f"{where}: {err.get('msg', 'invalid')}", code="BAD_EDIT") from exc
    qa = run_text_qa(kit_for(brand.slug), brief, draft)
    # Keep the writer's novelty verdict: what the post is about didn't change.
    novelty = [c for c in (pkg.qa_report or {}).get("checks", []) if c.get("name") == "novelty"]
    qa.checks += [Check.model_validate(c) for c in novelty]

    first = draft.slides[0]
    pkg.slide_data = [s.model_dump(mode="json") for s in draft.slides]
    pkg.caption = new_caption
    pkg.hashtags = new_tags
    pkg.hook = plain(first.headline_bn or first.headline_en or "")[:500]
    pkg.title = plain(first.headline_en or first.headline_bn or "Carousel")[:512]
    pkg.version = (pkg.version or 1) + 1
    pkg.qa_report = qa.to_json()
    session.flush()
    reason = (EDIT_REASON_PREFIX + "; ".join(changes[:4]))[:500]
    if pkg.status != ContentLifecycleStatus.DRAFTED.value:
        transition_content_status(
            session,
            content_package_id=pkg.id,
            to_status=ContentLifecycleStatus.DRAFTED,
            actor_id=actor_id,
            reason=reason,
            expected_brand_id=pkg.brand_profile_id,
        )
        session.refresh(pkg)
    else:
        session.add(
            ContentStateTransition(
                content_package_id=pkg.id,
                brand_profile_id=pkg.brand_profile_id,
                from_status=pkg.status,
                to_status=pkg.status,
                actor_id=actor_id,
                reason=reason,
                content_version=pkg.version,
            )
        )
    record_audit_event(
        session,
        actor_id=actor_id,
        action="CAROUSEL_EDITED",
        target_type="content_package",
        target_id=str(pkg.id),
        outcome="SUCCESS" if qa.passed else "QA_FAILED",
        metadata={"changes": len(changes), "version": pkg.version},
    )
    if qa.passed:
        enqueue_render(session, pkg)
    session.flush()
    return qa


# --------------------------------------------------------------------------- re-render


def expected_slides(pkg: ContentPackage) -> int | None:
    """Slides a generated carousel should have once rendered (None when there's no render yet,
    or for owner uploads, whose images are counted when they're uploaded)."""
    from media_os_api.content.uploads import is_owner_upload

    if pkg.format != "CAROUSEL" or is_owner_upload(pkg) or pkg.status == "DRAFTED":
        return None
    return len(pkg.slide_data or [])


def render_failed(session: Session, pkg: ContentPackage) -> bool:
    """A drafted carousel whose text passed QA but has no live render job for its current
    version: the job gave up (e.g. the renderer crashed), or its history was purged. It will never
    finish on its own, so the owner needs to re-render."""
    from media_os_api.carousel.render_job import RENDER_TASK
    from media_os_api.db.models import JobRun

    if pkg.format != "CAROUSEL" or pkg.status != ContentLifecycleStatus.DRAFTED.value:
        return False
    if not (pkg.qa_report or {}).get("passed"):
        return False  # failed text QA: never rendered on purpose
    status = session.execute(
        select(JobRun.status).where(
            JobRun.task_name == RENDER_TASK,
            JobRun.idempotency_key == f"render:{pkg.id}:v{pkg.version}",
        )
    ).scalar_one_or_none()
    return status not in LIVE_JOB_STATES


RERENDER_STATUSES = ("DRAFTED", "RENDERED", "QA_PASSED")
LIVE_JOB_STATES = ("PENDING", "DISPATCHED", "RUNNING", "FAILED_RETRYABLE", "SUCCEEDED")


def lock_package(session: Session, pkg: ContentPackage) -> None:
    """Lock the carousel's row and re-read it, so edits, re-renders, approvals and the renderer
    act one at a time on its current state (never on a copy another request has changed)."""
    session.refresh(pkg, with_for_update=True)


def missing_slides(assets: list[Any], expected: int | None = None) -> int:
    """How many rendered slides are missing on disk, empty, or cut short (smaller than saved).

    `expected` is how many slides the carousel should have; slides without an asset row count
    as missing too. The API mounts the media volume read-only, so this only looks at file
    sizes."""
    from media_os_api.content.uploads import resolve_asset_path

    bad = max(0, expected - len(assets)) if expected is not None else 0
    for a in assets:
        path = resolve_asset_path(a.local_path)
        try:
            size = path.stat().st_size if path is not None else -1
        except OSError:
            size = -1
        if size <= 0 or (a.file_size and size < a.file_size):
            bad += 1
    return bad


def rerender_package(session: Session, pkg: ContentPackage, *, actor_id: str) -> None:
    """Render a carousel's slides again (new version folder), e.g. after the files were lost or a
    render check failed. Text QA results are kept; render checks are redone by the renderer."""
    from media_os_api.carousel.render_job import RENDER_TASK
    from media_os_api.content.uploads import is_owner_upload
    from media_os_api.db.models import JobRun

    lock_package(session, pkg)
    if pkg.format != "CAROUSEL" or is_owner_upload(pkg):
        raise EditError("Only generated carousels can be re-rendered.", code="NOT_RERENDERABLE")
    if pkg.status not in RERENDER_STATUSES or pkg.approval_status != "PENDING":
        # An approved carousel keeps the slides that were approved; new slides need a new review.
        raise EditError(
            "Only carousels waiting for review can be re-rendered.", code="NOT_RERENDERABLE"
        )
    report = dict(pkg.qa_report or {})
    text_checks = [
        c for c in report.get("checks", []) if not str(c.get("name", "")).startswith("render_")
    ]
    if any(c.get("status") == "FAIL" for c in text_checks):
        raise EditError(
            "The text didn't pass the checks, so it can't be rendered. Edit the text instead.",
            code="NOT_RERENDERABLE",
        )
    busy = session.execute(
        select(JobRun.id).where(
            JobRun.task_name == RENDER_TASK,
            JobRun.status.in_(("PENDING", "DISPATCHED", "RUNNING", "FAILED_RETRYABLE")),
            JobRun.payload["content_package_id"].astext == str(pkg.id),
        )
    ).first()
    if busy is not None:
        raise EditError("The slides are already being rendered.", code="ALREADY_RENDERING")

    pkg.version = (pkg.version or 1) + 1
    pkg.qa_report = {**report, "checks": text_checks, "passed": True}
    session.flush()
    reason = "Re-render requested by the owner"
    if pkg.status != ContentLifecycleStatus.DRAFTED.value:
        transition_content_status(
            session,
            content_package_id=pkg.id,
            to_status=ContentLifecycleStatus.DRAFTED,
            actor_id=actor_id,
            reason=reason,
            expected_brand_id=pkg.brand_profile_id,
        )
        session.refresh(pkg)
    record_audit_event(
        session,
        actor_id=actor_id,
        action="CAROUSEL_RERENDER_REQUESTED",
        target_type="content_package",
        target_id=str(pkg.id),
        outcome="SUCCESS",
        metadata={"version": pkg.version},
    )
    enqueue_render(session, pkg)
    session.flush()


# --------------------------------------------------------------------------- decisions


class DecisionError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def active_slides(session: Session, pkg: ContentPackage) -> list[Any]:
    from media_os_api.db.models import Asset

    return list(
        session.execute(
            select(Asset)
            .where(
                Asset.content_package_id == pkg.id,
                Asset.asset_type == "CAROUSEL_SLIDE",
                Asset.status == "ACTIVE",
            )
            .order_by(Asset.local_path)
        ).scalars()
    )


def decide_package(
    session: Session,
    pkg: ContentPackage,
    *,
    approve: bool,
    reason: str | None,
    actor_id: str,
) -> bool:
    """Approve or reject a carousel awaiting review: the one set of rules the dashboard and the
    Telegram buttons share. Returns True when auto-scheduling put an approved carousel in a slot.
    """
    lock_package(session, pkg)  # one at a time with re-renders and edits of this carousel
    if pkg.approval_status != "PENDING":
        raise DecisionError(f"Already {pkg.approval_status}.", code="ALREADY_DECIDED")
    note = (reason or "").strip()[:500]
    if approve:
        if pkg.status != "QA_PASSED":
            raise DecisionError("Only QA-passed carousels can be approved.", code="QA_NOT_PASSED")
        if pkg.format == "CAROUSEL" and missing_slides(
            active_slides(session, pkg), expected_slides(pkg)
        ):
            raise DecisionError(
                "Some slide images are missing. Re-render the slides first.",
                code="SLIDES_MISSING",
            )
        pkg.approval_status = "APPROVED"
    else:
        pkg.approval_status = "REJECTED"
        transition_content_status(
            session,
            content_package_id=pkg.id,
            to_status=ContentLifecycleStatus.REJECTED,
            actor_id=actor_id,
            reason=note or "Rejected in review",
            expected_brand_id=pkg.brand_profile_id,
        )
        session.refresh(pkg)
    record_audit_event(
        session,
        actor_id=actor_id,
        action="CAROUSEL_APPROVED" if approve else "CAROUSEL_REJECTED",
        target_type="content_package",
        target_id=str(pkg.id),
        outcome="SUCCESS",
        metadata={"reason": note[:200]},
    )
    session.flush()
    if not approve:
        return False
    # Autopilot: an approved Media OS carousel goes straight into the next free posting slot when
    # the brand has auto-scheduling on (owner uploads are scheduled by hand).
    from media_os_api.content.autopilot import auto_schedule

    brand = session.get(BrandProfile, pkg.brand_profile_id)
    scheduled = brand is not None and auto_schedule(session, pkg, brand) is not None
    session.flush()
    return scheduled
