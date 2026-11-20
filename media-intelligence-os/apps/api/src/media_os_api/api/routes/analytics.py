"""Views analytics (read-only, owner-authenticated, brand-scoped).

* `GET /api/v1/analytics/views?brand=<ref>&range=7d|30d|60d|all[&platform][&account][&format]`
  — totals, period-over-period growth, trend, platform/account breakdowns, top post, tag breakdowns.
* `GET /api/v1/analytics/posts?brand=<ref>&range=…&sort=views|engagement_rate|published&page=`
  — server-paginated platform posts with period and lifetime metrics.
* `GET /api/v1/analytics/posts/{content_package_id}?brand=<ref>&range=…` — one post, per platform.
* `GET /api/v1/analytics/pipeline?brand=<ref>` — production / approval / publishing counts.
* `GET /api/v1/analytics/account?brand=<ref>&window=7|30` — account-wide totals per channel.

All numbers come from stored platform observations; nothing is simulated.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from media_os_api.analytics import service
from media_os_api.api.routes.content import _brand_id, _db
from media_os_api.auth import SessionPayload, require_session

router = APIRouter(tags=["analytics"])

RangeParam = Annotated[Literal["7d", "30d", "60d", "all"], Query(alias="range")]
PlatformParam = Annotated[Literal["INSTAGRAM", "FACEBOOK", "YOUTUBE"] | None, Query()]
BrandParam = Annotated[str, Query(min_length=1, max_length=128)]
CACHE = "private, max-age=30"


@router.get("/analytics/views")
def get_views(
    request: Request,
    response: Response,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
    range_key: RangeParam = "30d",
    platform: PlatformParam = None,
    account: Annotated[uuid.UUID | None, Query()] = None,
    content_format: Annotated[
        Literal["CAROUSEL", "REEL", "POST", "SHORT", "LONG_VIDEO"] | None, Query(alias="format")
    ] = None,
) -> dict[str, Any]:
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        report = service.views_report(
            db,
            brand_id,
            range_key=range_key,
            platform=platform,
            channel_id=account,
            content_format=content_format,
        )
    response.headers["Cache-Control"] = CACHE
    return report


@router.get("/analytics/posts")
def list_posts(
    request: Request,
    response: Response,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
    range_key: RangeParam = "30d",
    platform: PlatformParam = None,
    account: Annotated[uuid.UUID | None, Query()] = None,
    content_format: Annotated[
        Literal["CAROUSEL", "REEL", "POST", "SHORT", "LONG_VIDEO"] | None, Query(alias="format")
    ] = None,
    sort: Annotated[Literal["views", "engagement_rate", "published"], Query()] = "views",
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        result = service.posts_report(
            db,
            brand_id,
            range_key=range_key,
            platform=platform,
            channel_id=account,
            content_format=content_format,
            sort=sort,
            page=page,
            page_size=page_size,
        )
    response.headers["Cache-Control"] = CACHE
    return result


@router.get("/analytics/posts/{package_id}")
def get_post(
    package_id: uuid.UUID,
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
    range_key: RangeParam = "30d",
) -> dict[str, Any]:
    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        detail = service.post_detail(db, brand_id, package_id, range_key=range_key)
    if detail is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, {"code": "NOT_FOUND", "message": "Post not found."}
        )
    return detail


@router.get("/analytics/pipeline")
def get_pipeline(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
) -> dict[str, Any]:
    with _db(request) as db:
        return service.pipeline_summary(db, _brand_id(db, brand))


@router.get("/analytics/account")
def get_account(
    request: Request,
    response: Response,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
    window: Annotated[int, Query()] = 7,
) -> dict[str, Any]:
    """Account-wide totals per connected channel (every post, including ones posted directly)."""
    from media_os_api.analytics.account import WINDOWS, account_report

    if window not in WINDOWS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "BAD_WINDOW", "message": "window must be 7 or 30."},
        )

    with _db(request) as db:
        report = account_report(db, _brand_id(db, brand), window)
    response.headers["Cache-Control"] = CACHE
    return report


@router.get("/analytics/learning")
def get_learning(
    request: Request,
    response: Response,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
    platform: Annotated[Literal["INSTAGRAM", "FACEBOOK"], Query()] = "INSTAGRAM",
) -> dict[str, Any]:
    """What's working: per-tag results, the learned score with predicted vs actual for every
    post, best posting times and the hooks given to the writer as examples."""
    from media_os_api.learning.service import learning_report
    from media_os_api.services import BrandNotFoundError, get_brand

    with _db(request) as db:
        brand_id = _brand_id(db, brand)
        try:
            profile = get_brand(db, str(brand_id))
        except BrandNotFoundError as exc:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, {"code": "NOT_FOUND", "message": "Brand not found."}
            ) from exc
        report = learning_report(db, profile, platform=platform)
    response.headers["Cache-Control"] = CACHE
    return report
