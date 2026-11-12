"""Multi-brand authoritative repository and cross-brand isolation service."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from media_os_api.db.models import (
    Asset,
    BrandProfile,
    BrandTopicAssignment,
    ChannelAccount,
    Claim,
    ClaimSource,
    ContentPackage,
    ContentStateTransition,
    MetricsSnapshot,
    PublishSchedule,
    ResearchPacket,
    ScheduleDefinition,
    SourceItem,
    TopicCluster,
)
from media_os_api.db.normalization import compute_record_hash
from media_os_api.services.audit_service import record_audit_event

_BRAND_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class BrandDomainError(ValueError):
    """Base error for multi-brand domain operations."""


class BrandNotFoundError(BrandDomainError):
    """Raised when a requested BrandProfile does not exist."""


class BrandIsolationError(BrandDomainError):
    """Raised when an operation attempts cross-brand data access or linking."""


class BrandPolicyError(BrandDomainError):
    """Raised when a brand-level policy invariant would be violated."""


class ClaimVerificationError(BrandDomainError):
    """Raised when attempting to verify a Claim without a valid ClaimSource."""


def _require_non_blank(field_name: str, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise BrandPolicyError(f"{field_name} must not be blank.")
    return cleaned


def _try_parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value.strip())
    except (ValueError, AttributeError):
        return None


def list_brands(session: Session, *, include_inactive: bool = False) -> list[BrandProfile]:
    """List persisted BrandProfiles ordered deterministically by slug."""
    stmt = select(BrandProfile)
    if not include_inactive:
        stmt = stmt.where(BrandProfile.is_active.is_(True))
    stmt = stmt.order_by(BrandProfile.created_at.asc(), BrandProfile.slug.asc())
    return list(session.execute(stmt).scalars().all())


def get_brand(session: Session, brand_ref: str) -> BrandProfile:
    """Resolve a persisted BrandProfile by UUID or slug."""
    parsed_uuid = _try_parse_uuid(brand_ref)
    if parsed_uuid is not None:
        brand = session.execute(
            select(BrandProfile).where(BrandProfile.id == parsed_uuid)
        ).scalar_one_or_none()
    else:
        brand = session.execute(
            select(BrandProfile).where(BrandProfile.slug == brand_ref.strip())
        ).scalar_one_or_none()
    if brand is None:
        raise BrandNotFoundError(f"BrandProfile '{brand_ref}' not found.")
    return brand


def _brand_hash(brand: BrandProfile) -> str:
    return compute_record_hash(
        {
            "id": str(brand.id),
            "slug": brand.slug,
            "name": brand.name,
            "description": brand.description,
            "niche": brand.niche,
            "target_audience": brand.target_audience,
            "language": brand.language,
            "tone_of_voice": brand.tone_of_voice,
            "content_pillars": brand.content_pillars,
            "timezone": brand.timezone,
            "logo_asset_id": str(brand.logo_asset_id) if brand.logo_asset_id else None,
            "is_active": brand.is_active,
        }
    )


def create_brand(
    session: Session,
    *,
    name: str,
    slug: str,
    description: str,
    niche: str,
    target_audience: str,
    language: str,
    tone_of_voice: str,
    content_pillars: list[str] | None = None,
    timezone: str = "Asia/Kolkata",
    actor_id: str,
    request_id: str = "-",
) -> BrandProfile:
    """Create a new BrandProfile on behalf of the single owner with audit logging."""
    cleaned_slug = slug.strip().lower()
    if not _BRAND_SLUG_RE.fullmatch(cleaned_slug):
        raise BrandPolicyError(
            "Brand slug must be lowercase alphanumeric with single hyphens only."
        )
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise BrandPolicyError(f"Invalid timezone: {timezone}") from exc

    existing = session.execute(
        select(BrandProfile).where(BrandProfile.slug == cleaned_slug)
    ).scalar_one_or_none()
    if existing is not None:
        raise BrandPolicyError(f"Brand slug '{cleaned_slug}' already exists.")

    brand = BrandProfile(
        name=_require_non_blank("name", name),
        slug=cleaned_slug,
        description=_require_non_blank("description", description),
        niche=_require_non_blank("niche", niche),
        target_audience=_require_non_blank("target_audience", target_audience),
        language=_require_non_blank("language", language),
        tone_of_voice=_require_non_blank("tone_of_voice", tone_of_voice),
        content_pillars=list(content_pillars or []),
        visual_identity={"configured": False},
        caption_preferences={},
        hashtag_preferences={},
        call_to_action_preferences={},
        timezone=timezone,
        publishing_preferences={
            "approval_policy": "human_required",
            "placeholder_unconfigured": True,
        },
        is_active=True,
    )
    session.add(brand)
    session.flush()

    record_audit_event(
        session,
        actor_id=actor_id,
        action="BRAND_CREATED",
        target_type="BrandProfile",
        target_id=str(brand.id),
        outcome="SUCCESS",
        request_id=request_id,
        after_hash=_brand_hash(brand),
        metadata={"slug": brand.slug, "name": brand.name},
    )
    return brand


def update_brand(
    session: Session,
    *,
    brand_ref: str,
    actor_id: str,
    request_id: str = "-",
    name: str | None = None,
    description: str | None = None,
    niche: str | None = None,
    target_audience: str | None = None,
    language: str | None = None,
    tone_of_voice: str | None = None,
    content_pillars: list[str] | None = None,
    timezone: str | None = None,
) -> BrandProfile:
    """Update allowed BrandProfile attributes for the single owner."""
    brand = get_brand(session, brand_ref)
    before = _brand_hash(brand)

    if timezone is not None:
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise BrandPolicyError(f"Invalid timezone: {timezone}") from exc
        brand.timezone = timezone

    if name is not None:
        brand.name = _require_non_blank("name", name)
    if description is not None:
        brand.description = _require_non_blank("description", description)
    if niche is not None:
        brand.niche = _require_non_blank("niche", niche)
    if target_audience is not None:
        brand.target_audience = _require_non_blank("target_audience", target_audience)
    if language is not None:
        brand.language = _require_non_blank("language", language)
    if tone_of_voice is not None:
        brand.tone_of_voice = _require_non_blank("tone_of_voice", tone_of_voice)
    if content_pillars is not None:
        brand.content_pillars = list(content_pillars)

    session.flush()
    after = _brand_hash(brand)
    record_audit_event(
        session,
        actor_id=actor_id,
        action="BRAND_UPDATED",
        target_type="BrandProfile",
        target_id=str(brand.id),
        outcome="SUCCESS",
        request_id=request_id,
        before_hash=before,
        after_hash=after,
        metadata={"slug": brand.slug},
    )
    return brand


def set_brand_logo_asset_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    logo_asset_id: uuid.UUID | None,
    actor_id: str,
    request_id: str = "-",
) -> BrandProfile:
    """Assign or clear BrandProfile.logo_asset_id enforcing strict same-brand asset ownership."""
    brand = session.get(BrandProfile, brand_profile_id)
    if brand is None:
        raise BrandNotFoundError(f"BrandProfile {brand_profile_id} not found.")
    before = _brand_hash(brand)

    if logo_asset_id is not None:
        asset = session.get(Asset, logo_asset_id)
        if asset is None:
            raise BrandDomainError(f"Asset {logo_asset_id} not found.")
        if asset.brand_profile_id != brand_profile_id:
            raise BrandIsolationError(
                "Cross-brand logo assignment rejected: Asset belongs to a different brand."
            )

    brand.logo_asset_id = logo_asset_id
    session.flush()
    after = _brand_hash(brand)
    record_audit_event(
        session,
        actor_id=actor_id,
        action="BRAND_LOGO_UPDATED",
        target_type="BrandProfile",
        target_id=str(brand.id),
        outcome="SUCCESS",
        request_id=request_id,
        before_hash=before,
        after_hash=after,
        metadata={
            "slug": brand.slug,
            "logo_asset_id": str(logo_asset_id) if logo_asset_id else None,
        },
    )
    return brand


def set_brand_activation(
    session: Session,
    *,
    brand_ref: str,
    is_active: bool,
    actor_id: str,
    request_id: str = "-",
) -> BrandProfile:
    """Activate or deactivate a BrandProfile while ensuring at least one brand remains active."""
    brand = get_brand(session, brand_ref)
    before = _brand_hash(brand)

    if not is_active and brand.is_active:
        active_others = (
            session.execute(
                select(BrandProfile.id).where(
                    BrandProfile.is_active.is_(True),
                    BrandProfile.id != brand.id,
                )
            )
            .scalars()
            .all()
        )
        if not active_others:
            raise BrandPolicyError(
                "Cannot deactivate the last active BrandProfile in Media Intelligence OS."
            )

    brand.is_active = is_active
    session.flush()
    after = _brand_hash(brand)
    record_audit_event(
        session,
        actor_id=actor_id,
        action="BRAND_ACTIVATION_CHANGED",
        target_type="BrandProfile",
        target_id=str(brand.id),
        outcome="SUCCESS",
        request_id=request_id,
        before_hash=before,
        after_hash=after,
        metadata={"slug": brand.slug, "is_active": is_active},
    )
    return brand


def list_channels(
    session: Session,
    *,
    brand_ref: str | None = None,
) -> list[ChannelAccount]:
    """List ChannelAccounts strictly scoped to `brand_ref` when provided."""
    stmt = select(ChannelAccount)
    if brand_ref is not None:
        brand = get_brand(session, brand_ref)
        stmt = stmt.where(ChannelAccount.brand_profile_id == brand.id)
    stmt = stmt.order_by(ChannelAccount.created_at.asc())
    return list(session.execute(stmt).scalars().all())


def register_channel_account(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    platform: str,
    external_account_id: str,
    external_username: str | None = None,
    external_display_name: str | None = None,
    postiz_integration_id: str | None = None,
    status: str = "DISCONNECTED",
) -> ChannelAccount:
    """Create a ChannelAccount strictly bound to one BrandProfile."""
    brand = session.get(BrandProfile, brand_profile_id)
    if brand is None:
        raise BrandNotFoundError(f"BrandProfile {brand_profile_id} not found.")
    if postiz_integration_id:
        conflict = session.execute(
            select(ChannelAccount).where(
                ChannelAccount.postiz_integration_id == postiz_integration_id
            )
        ).scalar_one_or_none()
        if conflict is not None and conflict.brand_profile_id != brand_profile_id:
            raise BrandIsolationError(
                "Postiz integration ID is already bound to a different BrandProfile."
            )
    channel = ChannelAccount(
        brand_profile_id=brand_profile_id,
        platform=platform,
        external_account_id=external_account_id,
        external_username=external_username,
        external_display_name=external_display_name,
        postiz_integration_id=postiz_integration_id,
        status=status,
        connected_at=datetime.now(UTC) if status == "CONNECTED" else None,
    )
    session.add(channel)
    session.flush()
    return channel


VALID_EDITORIAL_STATUSES = {
    "PENDING_EVALUATION",
    "CONSIDERED",
    "SHORTLISTED",
    "APPROVED_FOR_RESEARCH",
    "REJECTED",
    "EXPIRED",
}


def get_brand_topic_assignment_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    topic_cluster_id: uuid.UUID,
) -> BrandTopicAssignment | None:
    """Retrieve the brand-specific topic assignment for a cluster, or None."""
    return session.execute(
        select(BrandTopicAssignment).where(
            BrandTopicAssignment.brand_profile_id == brand_profile_id,
            BrandTopicAssignment.topic_cluster_id == topic_cluster_id,
        )
    ).scalar_one_or_none()


def list_brand_topic_assignments_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    editorial_status: str | None = None,
) -> list[BrandTopicAssignment]:
    """List topic assignments for a brand ordered by priority (desc) then assigned_at (desc)."""
    stmt = (
        select(BrandTopicAssignment)
        .where(BrandTopicAssignment.brand_profile_id == brand_profile_id)
        .order_by(BrandTopicAssignment.priority.desc(), BrandTopicAssignment.assigned_at.desc())
    )
    if editorial_status is not None:
        stmt = stmt.where(BrandTopicAssignment.editorial_status == editorial_status)
    return list(session.execute(stmt).scalars().all())


def create_brand_topic_assignment_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    topic_cluster_id: uuid.UUID,
    editorial_status: str = "PENDING_EVALUATION",
    relevance_score: Decimal | float | None = None,
    score_breakdown: dict[str, Any] | None = None,
    rejection_reason: str | None = None,
    editorial_notes: str | None = None,
    priority: int = 0,
) -> BrandTopicAssignment:
    """Create a brand-specific assignment for a shared topic cluster."""
    get_brand(session, str(brand_profile_id))
    topic = session.execute(
        select(TopicCluster).where(TopicCluster.id == topic_cluster_id)
    ).scalar_one_or_none()
    if topic is None:
        raise BrandDomainError(f"TopicCluster '{topic_cluster_id}' not found.")

    if editorial_status not in VALID_EDITORIAL_STATUSES:
        raise BrandPolicyError(f"Invalid editorial status: {editorial_status}")

    score_dec: Decimal | None = None
    if relevance_score is not None:
        score_dec = Decimal(str(relevance_score))
        if score_dec < Decimal("0.0000") or score_dec > Decimal("1.0000"):
            raise BrandPolicyError(
                f"Relevance score must be between 0.0000 and 1.0000: {relevance_score}"
            )

    evaluated_at = datetime.now(UTC) if editorial_status != "PENDING_EVALUATION" else None

    assignment = BrandTopicAssignment(
        brand_profile_id=brand_profile_id,
        topic_cluster_id=topic_cluster_id,
        editorial_status=editorial_status,
        relevance_score=score_dec,
        score_breakdown=dict(score_breakdown or {}),
        rejection_reason=rejection_reason,
        editorial_notes=editorial_notes,
        priority=priority,
        evaluated_at=evaluated_at,
    )
    session.add(assignment)
    session.flush()
    return assignment


def update_brand_topic_assignment_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    topic_cluster_id: uuid.UUID,
    editorial_status: str | None = None,
    relevance_score: Decimal | float | None = None,
    score_breakdown: dict[str, Any] | None = None,
    rejection_reason: str | None = None,
    editorial_notes: str | None = None,
    priority: int | None = None,
    evaluated_at: datetime | None = None,
) -> BrandTopicAssignment:
    """Update editorial review state, relevance score or notes for a brand topic assignment."""
    assignment = get_brand_topic_assignment_scoped(
        session, brand_profile_id=brand_profile_id, topic_cluster_id=topic_cluster_id
    )
    if assignment is None:
        raise BrandDomainError(
            f"BrandTopicAssignment not found for brand {brand_profile_id} and topic {topic_cluster_id}."
        )

    if editorial_status is not None:
        if editorial_status not in VALID_EDITORIAL_STATUSES:
            raise BrandPolicyError(f"Invalid editorial status: {editorial_status}")
        assignment.editorial_status = editorial_status
        if evaluated_at is not None:
            assignment.evaluated_at = evaluated_at
        elif assignment.evaluated_at is None and editorial_status != "PENDING_EVALUATION":
            assignment.evaluated_at = datetime.now(UTC)

    if relevance_score is not None:
        score_dec = Decimal(str(relevance_score))
        if score_dec < Decimal("0.0000") or score_dec > Decimal("1.0000"):
            raise BrandPolicyError(
                f"Relevance score must be between 0.0000 and 1.0000: {relevance_score}"
            )
        assignment.relevance_score = score_dec

    if score_breakdown is not None:
        assignment.score_breakdown = dict(score_breakdown)
    if rejection_reason is not None:
        assignment.rejection_reason = rejection_reason
    if editorial_notes is not None:
        assignment.editorial_notes = editorial_notes
    if priority is not None:
        assignment.priority = priority

    session.flush()
    return assignment


def create_research_packet_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    topic_cluster_id: uuid.UUID,
    editorial_angle: str,
    key_findings: list[Any] | None = None,
    caveats: list[Any] | None = None,
    unresolved_questions: list[Any] | None = None,
    source_manifest: list[Any] | None = None,
) -> ResearchPacket:
    """Create a brand-owned ResearchPacket, requiring an approved/shortlisted assignment."""
    assignment = get_brand_topic_assignment_scoped(
        session, brand_profile_id=brand_profile_id, topic_cluster_id=topic_cluster_id
    )
    if assignment is None:
        raise BrandDomainError(
            f"Cannot create ResearchPacket: No topic assignment found for brand {brand_profile_id} "
            f"and topic {topic_cluster_id}."
        )

    eligible_statuses = {"APPROVED_FOR_RESEARCH", "SHORTLISTED"}
    if assignment.editorial_status not in eligible_statuses:
        raise BrandPolicyError(
            f"Cannot create ResearchPacket: Topic assignment status '{assignment.editorial_status}' "
            f"is not eligible for research (must be in {sorted(eligible_statuses)})."
        )

    packet = ResearchPacket(
        brand_profile_id=brand_profile_id,
        topic_cluster_id=topic_cluster_id,
        status="DRAFT",
        editorial_angle=editorial_angle,
        key_findings=list(key_findings or []),
        caveats=list(caveats or []),
        unresolved_questions=list(unresolved_questions or []),
        source_manifest=list(source_manifest or []),
    )
    session.add(packet)
    session.flush()
    return packet


def list_research_packets_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
) -> list[ResearchPacket]:
    return list(
        session.execute(
            select(ResearchPacket)
            .where(ResearchPacket.brand_profile_id == brand_profile_id)
            .order_by(ResearchPacket.created_at.desc())
        )
        .scalars()
        .all()
    )


def create_claim_for_packet(
    session: Session,
    *,
    research_packet_id: uuid.UUID,
    claim_text: str,
    confidence: Decimal = Decimal("0.000"),
) -> Claim:
    claim = Claim(
        research_packet_id=research_packet_id,
        claim_text=claim_text,
        confidence=confidence,
        verification_status="UNVERIFIED",
    )
    session.add(claim)
    session.flush()
    return claim


def attach_claim_source(
    session: Session,
    *,
    claim_id: uuid.UUID,
    source_item_id: uuid.UUID,
    supporting_excerpt: str | None = None,
) -> ClaimSource:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise BrandDomainError(f"Claim {claim_id} not found.")
    source = session.get(SourceItem, source_item_id)
    if source is None:
        raise BrandDomainError(f"SourceItem {source_item_id} not found.")
    link = ClaimSource(
        claim_id=claim_id,
        source_item_id=source_item_id,
        supporting_excerpt=supporting_excerpt,
    )
    session.add(link)
    session.flush()
    return link


def verify_claim(
    session: Session,
    *,
    claim_id: uuid.UUID,
    confidence: Decimal | None = None,
) -> Claim:
    """Mark a Claim as VERIFIED ONLY if at least one valid ClaimSource exists."""
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise BrandDomainError(f"Claim {claim_id} not found.")
    has_source = (
        session.execute(
            select(ClaimSource.id).where(ClaimSource.claim_id == claim_id).limit(1)
        ).scalar_one_or_none()
        is not None
    )
    if not has_source:
        raise ClaimVerificationError(
            "Cannot mark a factual claim as VERIFIED without an associated SourceItem."
        )
    if confidence is not None:
        claim.confidence = confidence
    claim.verification_status = "VERIFIED"
    session.flush()
    return claim


def create_content_package_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    platform: str,
    format_name: str,
    research_packet_id: uuid.UUID | None = None,
    title: str | None = None,
    angle: str | None = None,
    actor_id: str = "system:content",
) -> ContentPackage:
    """Create a ContentPackage enforcing ResearchPacket brand ownership."""
    if research_packet_id is not None:
        packet = session.get(ResearchPacket, research_packet_id)
        if packet is None:
            raise BrandDomainError(f"ResearchPacket {research_packet_id} not found.")
        if packet.brand_profile_id != brand_profile_id:
            raise BrandIsolationError(
                "Cross-brand reference rejected: ResearchPacket belongs to a different brand."
            )

    pkg = ContentPackage(
        brand_profile_id=brand_profile_id,
        research_packet_id=research_packet_id,
        platform=platform,
        format=format_name,
        status="DISCOVERED",
        title=title,
        angle=angle,
    )
    session.add(pkg)
    session.flush()

    session.add(
        ContentStateTransition(
            content_package_id=pkg.id,
            brand_profile_id=brand_profile_id,
            from_status=None,
            to_status="DISCOVERED",
            actor_id=actor_id,
            reason="Initial creation",
            content_version=pkg.version,
        )
    )
    session.flush()
    return pkg


def get_content_package_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    content_package_id: uuid.UUID,
) -> ContentPackage:
    """Fetch a ContentPackage strictly within `brand_profile_id`."""
    pkg = session.execute(
        select(ContentPackage).where(
            ContentPackage.id == content_package_id,
            ContentPackage.brand_profile_id == brand_profile_id,
        )
    ).scalar_one_or_none()
    if pkg is None:
        raise BrandIsolationError(
            "Requested ContentPackage was not found within the specified BrandProfile."
        )
    return pkg


def list_content_packages_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
) -> list[ContentPackage]:
    return list(
        session.execute(
            select(ContentPackage)
            .where(ContentPackage.brand_profile_id == brand_profile_id)
            .order_by(ContentPackage.created_at.desc())
        )
        .scalars()
        .all()
    )


def reassign_content_package_brand(
    session: Session,
    *,
    content_package_id: uuid.UUID,
    new_brand_id: uuid.UUID,
    actor_id: str,
    reason: str,
    request_id: str = "-",
) -> ContentPackage:
    """Deliberate audited brand reassignment for an otherwise immutable ContentPackage."""
    if not reason.strip():
        raise BrandPolicyError("Audited reassignment requires an explicit reason.")
    pkg = session.get(ContentPackage, content_package_id)
    if pkg is None:
        raise BrandDomainError(f"ContentPackage {content_package_id} not found.")
    target_brand = session.get(BrandProfile, new_brand_id)
    if target_brand is None:
        raise BrandNotFoundError(f"Target BrandProfile {new_brand_id} not found.")
    if pkg.brand_profile_id == new_brand_id:
        return pkg

    # Refuse reassignment if child assets, schedules, metrics, or packet link
    # would violate cross-brand FKs
    has_assets = (
        session.execute(
            select(Asset.id).where(Asset.content_package_id == pkg.id).limit(1)
        ).scalar_one_or_none()
        is not None
    )
    has_schedules = (
        session.execute(
            select(PublishSchedule.id).where(PublishSchedule.content_package_id == pkg.id).limit(1)
        ).scalar_one_or_none()
        is not None
    )
    has_metrics = (
        session.execute(
            select(MetricsSnapshot.id).where(MetricsSnapshot.content_package_id == pkg.id).limit(1)
        ).scalar_one_or_none()
        is not None
    )
    if pkg.research_packet_id is not None or has_assets or has_schedules or has_metrics:
        raise BrandIsolationError(
            "Cannot reassign ContentPackage with brand-bound packet, assets, schedules, or metrics."
        )

    old_brand_id = pkg.brand_profile_id
    session.execute(text("SET LOCAL media_os.allow_brand_reassignment = 'true'"))
    # Clear transitions' brand mirror only by updating inside explicit audited reassignment
    pkg.brand_profile_id = new_brand_id
    session.flush()

    record_audit_event(
        session,
        actor_id=actor_id,
        action="CONTENT_PACKAGE_BRAND_REASSIGNED",
        target_type="ContentPackage",
        target_id=str(pkg.id),
        outcome="SUCCESS",
        request_id=request_id,
        approval_reference=reason.strip(),
        metadata={
            "previous_brand_id": str(old_brand_id),
            "new_brand_id": str(new_brand_id),
            "reason": reason.strip(),
        },
    )
    return pkg


def create_asset_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    content_package_id: uuid.UUID | None,
    asset_type: str,
    local_path: str,
    mime_type: str,
    file_size: int,
    checksum: str,
    status: str = "ACTIVE",
    retention_expires_at: datetime | None = None,
) -> Asset:
    """Create a brand-owned Asset enforcing same-brand ContentPackage linkage."""
    if content_package_id is not None:
        pkg = session.get(ContentPackage, content_package_id)
        if pkg is None:
            raise BrandDomainError(f"ContentPackage {content_package_id} not found.")
        if pkg.brand_profile_id != brand_profile_id:
            raise BrandIsolationError(
                "Cross-brand asset assignment rejected: ContentPackage belongs to another brand."
            )

    asset = Asset(
        brand_profile_id=brand_profile_id,
        content_package_id=content_package_id,
        asset_type=asset_type,
        local_path=local_path,
        mime_type=mime_type,
        file_size=file_size,
        checksum=checksum,
        status=status,
        retention_expires_at=retention_expires_at,
    )
    session.add(asset)
    session.flush()
    return asset


def create_publish_schedule_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    channel_account_id: uuid.UUID,
    content_package_id: uuid.UUID,
    scheduled_at: datetime,
    idempotency_key: str,
    timezone: str = "Asia/Kolkata",
) -> PublishSchedule:
    """Create a PublishSchedule enforcing brand and platform match across channel and content."""
    channel = session.get(ChannelAccount, channel_account_id)
    if channel is None:
        raise BrandDomainError(f"ChannelAccount {channel_account_id} not found.")
    if channel.brand_profile_id != brand_profile_id:
        raise BrandIsolationError(
            "Cross-brand schedule rejected: ChannelAccount belongs to another brand."
        )

    pkg = session.get(ContentPackage, content_package_id)
    if pkg is None:
        raise BrandDomainError(f"ContentPackage {content_package_id} not found.")
    if pkg.brand_profile_id != brand_profile_id:
        raise BrandIsolationError(
            "Cross-brand schedule rejected: ContentPackage belongs to another brand."
        )

    if channel.platform != pkg.platform:
        raise BrandPolicyError(
            f"Platform mismatch: ContentPackage is {pkg.platform} while ChannelAccount is "
            f"{channel.platform}."
        )

    schedule = PublishSchedule(
        brand_profile_id=brand_profile_id,
        channel_account_id=channel_account_id,
        content_package_id=content_package_id,
        platform=channel.platform,
        scheduled_at=scheduled_at,
        timezone=timezone,
        status="DRAFT",
        idempotency_key=idempotency_key,
    )
    session.add(schedule)
    session.flush()
    return schedule


def create_schedule_definition_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    name: str,
    task_name: str,
    task_type: str,
    schedule_config: dict[str, Any],
    next_run_at: datetime,
    timezone: str = "Asia/Kolkata",
    channel_account_id: uuid.UUID | None = None,
    payload_template: dict[str, Any] | None = None,
    is_enabled: bool = True,
) -> ScheduleDefinition:
    """Create a persistent ScheduleDefinition strictly scoped to its BrandProfile."""
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise BrandPolicyError(f"Invalid schedule timezone: {timezone}") from exc

    if channel_account_id is not None:
        channel = session.get(ChannelAccount, channel_account_id)
        if channel is None:
            raise BrandDomainError(f"ChannelAccount {channel_account_id} not found.")
        if channel.brand_profile_id != brand_profile_id:
            raise BrandIsolationError(
                "Cross-brand schedule definition rejected: ChannelAccount belongs to another brand."
            )

    sched = ScheduleDefinition(
        brand_profile_id=brand_profile_id,
        channel_account_id=channel_account_id,
        name=name.strip(),
        task_name=task_name.strip(),
        task_type=task_type.strip(),
        schedule_config=schedule_config,
        payload_template=dict(payload_template or {}),
        timezone=timezone,
        next_run_at=next_run_at.astimezone(UTC),
        is_enabled=is_enabled,
    )
    session.add(sched)
    session.flush()
    return sched


def list_metrics_scoped(
    session: Session,
    *,
    brand_profile_id: uuid.UUID,
    channel_account_id: uuid.UUID | None = None,
) -> list[MetricsSnapshot]:
    """Return metrics strictly isolated to `brand_profile_id` and optional `channel_account_id`."""
    stmt = select(MetricsSnapshot).where(MetricsSnapshot.brand_profile_id == brand_profile_id)
    if channel_account_id is not None:
        channel = session.get(ChannelAccount, channel_account_id)
        if channel is None or channel.brand_profile_id != brand_profile_id:
            raise BrandIsolationError(
                "ChannelAccount does not belong to the requested BrandProfile."
            )
        stmt = stmt.where(MetricsSnapshot.channel_account_id == channel_account_id)
    return list(session.execute(stmt.order_by(MetricsSnapshot.fetched_at.desc())).scalars().all())
