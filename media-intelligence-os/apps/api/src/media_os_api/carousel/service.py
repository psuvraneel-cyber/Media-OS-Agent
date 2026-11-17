"""Persist a written carousel as a brand-owned ContentPackage at status DRAFTED."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.carousel.qa import QaReport
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft
from media_os_api.content.taxonomy import validate_for_brand
from media_os_api.db.models import BrandProfile, ContentPackage, ContentStateTransition
from media_os_api.jobs.ledger import enqueue_durable_job
from media_os_api.jobs.routing import CREATIVE_QUEUE
from media_os_api.services.audit_service import record_audit_event

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
        hook=(first.headline_bn or first.headline_en or "")[:500],
        title=(first.headline_en or first.headline_bn or "Carousel")[:512],
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


def recheck_package(session: Session, pkg: ContentPackage, *, actor_id: str) -> QaReport:
    """Re-run text QA with the current rules on a draft that failed earlier; render if it passes.

    The stored caption is the composed Bengali+English text, so it stands in for both caption
    parts (EN-only posts use it as the English caption only). Slides are unchanged.
    """
    from media_os_api.carousel.brand_kit import kit_for
    from media_os_api.carousel.qa import run_text_qa
    from media_os_api.content.taxonomy import Language

    if pkg.format != "CAROUSEL" or pkg.status != "DRAFTED" or pkg.approval_status != "PENDING":
        raise RecheckError("Only pending drafts can be re-checked.")
    if (pkg.qa_report or {}).get("passed"):
        raise RecheckError("This draft already passed QA.")
    tags = dict(pkg.post_tags or {})
    brand = session.get(BrandProfile, pkg.brand_profile_id)
    if brand is None:
        raise RecheckError("Brand not found.")
    brief = CarouselBrief(
        pillar=tags["pillar"],
        hook_type=tags["hook_type"],
        occasion=tags["occasion"],
        language=tags["language"],
        visual_style=tags["visual_style"],
        call_to_action=tags["call_to_action"],
        posting_slot=tags["posting_slot"],
        angle=pkg.angle,
    )
    english_only = brief.language is Language.EN
    draft = CarouselDraft.model_validate(
        {
            "slides": pkg.slide_data,
            "caption_bn": None if english_only else pkg.caption,
            "caption_en": pkg.caption,
            "hashtags": pkg.hashtags,
            "alt_text_en": pkg.description or "Carousel",
        }
    )
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
