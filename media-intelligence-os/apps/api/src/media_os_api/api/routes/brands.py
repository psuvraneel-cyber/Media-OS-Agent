"""Authenticated multi-brand and channel endpoints backed by PostgreSQL (Phase 4)."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.auth import SessionPayload, require_csrf, require_session
from media_os_api.db.models import BrandProfile, ChannelAccount
from media_os_api.db.session import session_scope
from media_os_api.observability import get_request_id
from media_os_api.schemas.brands import (
    BrandActivationRequest,
    BrandListResponse,
    BrandProfileSummary,
    BrandUpdateRequest,
    ChannelAccountSummary,
    ChannelListResponse,
)
from media_os_api.services import (
    BrandNotFoundError,
    BrandPolicyError,
    get_brand,
    get_brand_fixture,
    list_brand_fixtures,
    list_brands,
    list_channel_fixtures,
    list_channels,
    set_brand_activation,
    update_brand,
)

router = APIRouter(tags=["brands"])


def _get_session_factory(request: Request) -> sessionmaker[Session] | None:
    return getattr(request.app.state, "session_factory", None)


def _brand_to_summary(row: BrandProfile) -> BrandProfileSummary:
    prefs = dict(row.publishing_preferences or {})
    return BrandProfileSummary(
        brand_id=str(row.id),
        slug=row.slug,
        name=row.name,
        niche=row.niche,
        description=row.description,
        target_audience=row.target_audience,
        language=row.language,
        tone_of_voice=row.tone_of_voice,
        content_pillars=list(row.content_pillars or []),
        visual_identity=dict(row.visual_identity or {}),
        timezone=row.timezone,
        approval_policy="human_required",
        placeholder_unconfigured=bool(prefs.get("placeholder_unconfigured", False)),
        active=row.is_active,
        data_origin="postgresql",
        created_at_utc=row.created_at.isoformat(),
        updated_at_utc=row.updated_at.isoformat(),
    )


def _channel_to_summary(row: ChannelAccount) -> ChannelAccountSummary:
    platform_norm: Literal["instagram", "youtube"] = (
        "instagram" if row.platform == "INSTAGRAM" else "youtube"
    )
    label = row.external_display_name or row.external_username or f"{row.platform} ({row.status})"
    return ChannelAccountSummary(
        channel_id=str(row.id),
        brand_id=str(row.brand_profile_id),
        platform=platform_norm,
        display_label=label,
        external_username=row.external_username,
        connection_status=row.status,
        publishing_enabled=False,
        postiz_integration_id=row.postiz_integration_id,
        data_origin="postgresql",
    )


@router.get("/brands", response_model=BrandListResponse)
def list_brand_profiles(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    include_inactive: bool = False,
) -> BrandListResponse:
    """List persisted BrandProfiles from PostgreSQL."""
    factory = _get_session_factory(request)
    if factory is None:
        return BrandListResponse(
            is_prototype_fixture=True,
            persistent=False,
            items=list_brand_fixtures(),
        )
    try:
        with session_scope(factory) as db:
            rows = list_brands(db, include_inactive=include_inactive)
            return BrandListResponse(
                is_prototype_fixture=False,
                persistent=True,
                items=[_brand_to_summary(row) for row in rows],
            )
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Brand storage is temporarily unavailable.",
            },
        ) from exc


@router.get("/brands/{brand_ref}", response_model=BrandProfileSummary)
def get_brand_profile(
    brand_ref: str,
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
) -> BrandProfileSummary:
    """Fetch a single persisted BrandProfile by UUID or slug."""
    factory = _get_session_factory(request)
    if factory is None:
        brand = get_brand_fixture(brand_ref)
        if brand is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "BRAND_NOT_FOUND",
                    "message": "Requested brand profile was not found.",
                },
            )
        return brand

    try:
        with session_scope(factory) as db:
            row = get_brand(db, brand_ref)
            return _brand_to_summary(row)
    except BrandNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "BRAND_NOT_FOUND",
                "message": "Requested brand profile was not found.",
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Brand storage is temporarily unavailable.",
            },
        ) from exc


@router.patch("/brands/{brand_ref}", response_model=BrandProfileSummary)
def patch_brand_profile(
    brand_ref: str,
    payload: BrandUpdateRequest,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
) -> BrandProfileSummary:
    """Update persisted BrandProfile metadata for the single authenticated owner."""
    factory = _get_session_factory(request)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Brand mutations require persistent PostgreSQL storage.",
            },
        )
    try:
        with session_scope(factory) as db:
            updated = update_brand(
                db,
                brand_ref=brand_ref,
                actor_id=f"owner:{session.subject}",
                request_id=get_request_id(),
                name=payload.name,
                description=payload.description,
                niche=payload.niche,
                target_audience=payload.target_audience,
                language=payload.language,
                tone_of_voice=payload.tone_of_voice,
                content_pillars=payload.content_pillars,
                timezone=payload.timezone,
            )
            return _brand_to_summary(updated)
    except BrandNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "BRAND_NOT_FOUND",
                "message": "Requested brand profile was not found.",
            },
        ) from exc
    except BrandPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BRAND_POLICY_ERROR",
                "message": str(exc),
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Brand storage is temporarily unavailable.",
            },
        ) from exc


@router.post("/brands/{brand_ref}/activation", response_model=BrandProfileSummary)
def toggle_brand_activation(
    brand_ref: str,
    payload: BrandActivationRequest,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
) -> BrandProfileSummary:
    """Activate or deactivate a persisted BrandProfile."""
    factory = _get_session_factory(request)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Brand activation requires persistent PostgreSQL storage.",
            },
        )
    try:
        with session_scope(factory) as db:
            updated = set_brand_activation(
                db,
                brand_ref=brand_ref,
                is_active=payload.is_active,
                actor_id=f"owner:{session.subject}",
                request_id=get_request_id(),
            )
            return _brand_to_summary(updated)
    except BrandNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "BRAND_NOT_FOUND",
                "message": "Requested brand profile was not found.",
            },
        ) from exc
    except BrandPolicyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "BRAND_POLICY_ERROR",
                "message": str(exc),
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Brand storage is temporarily unavailable.",
            },
        ) from exc


@router.get("/channels", response_model=ChannelListResponse)
def list_channel_accounts(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
) -> ChannelListResponse:
    """List persisted ChannelAccounts strictly filtered by `brand_id` (UUID or slug)."""
    factory = _get_session_factory(request)
    if factory is None:
        if brand_id is not None and get_brand_fixture(brand_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "BRAND_NOT_FOUND",
                    "message": "Requested brand profile was not found.",
                },
            )
        return ChannelListResponse(
            is_prototype_fixture=True,
            persistent=False,
            items=list_channel_fixtures(brand_id=brand_id),
        )

    try:
        with session_scope(factory) as db:
            rows = list_channels(db, brand_ref=brand_id)
            return ChannelListResponse(
                is_prototype_fixture=False,
                persistent=True,
                items=[_channel_to_summary(row) for row in rows],
            )
    except BrandNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "BRAND_NOT_FOUND",
                "message": "Requested brand profile was not found.",
            },
        ) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Channel storage is temporarily unavailable.",
            },
        ) from exc
