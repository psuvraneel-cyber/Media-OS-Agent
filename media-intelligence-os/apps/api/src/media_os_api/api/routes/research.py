"""Authenticated, read-only research and topic endpoints (Phase 5, Milestone 6).

* `GET /api/v1/research/sources` — registry, policy, approval, health, schedule, checkpoints.
* `GET /api/v1/research/items` — shared SourceItems (newest first) with their clusters.
* `GET /api/v1/topics?brand=<ref>` — clusters assigned to that brand with *that brand's*
  assignment only.
* `GET /api/v1/topics/{id}?brand=<ref>` — one topic with provenance sources.

No endpoint mutates state; none returns another brand's editorial decisions or any secret.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.auth import SessionPayload, require_session
from media_os_api.db.models import (
    BrandTopicAssignment,
    ResearchSource,
    ResearchSourceCheckpoint,
    ScheduleDefinition,
    SourceItem,
    TopicCluster,
    TopicClusterSource,
)
from media_os_api.db.session import session_scope
from media_os_api.research.jobs import fetch_task_name
from media_os_api.research.registry import live_fetch_env_enabled
from media_os_api.schemas.research import (
    BrandAssignmentSummary,
    CheckpointSummary,
    ClusterRef,
    ResearchSourceListResponse,
    ResearchSourceSummary,
    ScheduleSummary,
    SourceItemListResponse,
    SourceItemSummary,
    TopicDetailResponse,
    TopicListResponse,
    TopicSummary,
)
from media_os_api.services import BrandNotFoundError, get_brand

router = APIRouter(tags=["research"])

_TOTAL_KEYS = ("records_seen", "new_sources", "new_clusters", "malformed", "candidates_ingested")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@contextmanager
def _db(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] | None = getattr(request.app.state, "session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_NOT_CONFIGURED",
                "message": "Research storage is not configured.",
            },
        )
    try:
        with session_scope(factory) as session:
            yield session
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "DATABASE_UNAVAILABLE",
                "message": "Research storage is temporarily unavailable.",
            },
        ) from exc


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail={"code": code, "message": message}
    )


def _resolve_brand_id(db: Session, brand_ref: str) -> uuid.UUID:
    try:
        return get_brand(db, brand_ref).id
    except BrandNotFoundError as exc:
        raise _not_found("BRAND_NOT_FOUND", "Requested brand profile was not found.") from exc


def _clusters_for(db: Session, item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[ClusterRef]]:
    out: dict[uuid.UUID, list[ClusterRef]] = defaultdict(list)
    if not item_ids:
        return out
    rows = db.execute(
        select(TopicClusterSource.source_item_id, TopicCluster)
        .join(TopicCluster, TopicCluster.id == TopicClusterSource.topic_cluster_id)
        .where(TopicClusterSource.source_item_id.in_(item_ids))
    ).all()
    for item_id, cluster in rows:
        out[item_id].append(
            ClusterRef(
                topic_cluster_id=str(cluster.id),
                dedupe_key=cluster.dedupe_key,
                discovery_status=cluster.status,
            )
        )
    return out


def _item_summary(item: SourceItem, clusters: list[ClusterRef]) -> SourceItemSummary:
    return SourceItemSummary(
        source_item_id=str(item.id),
        provider=item.source_provider,
        external_id=item.external_id,
        title=item.title,
        source_label=item.source_label,
        canonical_url=item.canonical_url,
        published_at_utc=_iso(item.published_at),
        fetched_at_utc=item.fetched_at.isoformat(),
        summary=item.minimal_permitted_summary,
        source_policy_mode=item.source_policy_mode,
        clusters=clusters,
    )


@router.get("/research/sources", response_model=ResearchSourceListResponse)
def list_research_sources(
    request: Request, _session: Annotated[SessionPayload, Depends(require_session)]
) -> ResearchSourceListResponse:
    with _db(request) as db:
        sources = db.execute(select(ResearchSource).order_by(ResearchSource.source_code)).scalars()
        checkpoints: dict[str, list[CheckpointSummary]] = defaultdict(list)
        for cp in db.execute(
            select(ResearchSourceCheckpoint).order_by(ResearchSourceCheckpoint.stream_name)
        ).scalars():
            checkpoints[cp.source_code].append(
                CheckpointSummary(
                    stream=cp.stream_name,
                    last_fetched_at_utc=_iso(cp.last_fetched_at),
                    last_success_at_utc=_iso(cp.last_success_at),
                    conditional_request_ready=bool(cp.etag or cp.last_modified),
                )
            )
        schedules = {
            s.task_name: s
            for s in db.execute(
                select(ScheduleDefinition).where(ScheduleDefinition.brand_profile_id.is_(None))
            ).scalars()
        }
        items: list[ResearchSourceSummary] = []
        for src in sources:
            sched = schedules.get(fetch_task_name(src.source_code))
            totals_raw = dict((src.last_run_stats or {}).get("totals") or {})
            items.append(
                ResearchSourceSummary(
                    source=src.source_code,
                    display_name=src.display_name,
                    policy_mode=src.policy_mode,
                    live_fetch_approved=src.live_fetch_approved,
                    approved_at_utc=_iso(src.approved_at),
                    health_status=src.health_status,
                    consecutive_failures=src.consecutive_failures,
                    last_attempt_at_utc=_iso(src.last_attempt_at),
                    last_success_at_utc=_iso(src.last_success_at),
                    last_failure_at_utc=_iso(src.last_failure_at),
                    last_error_code=src.last_error_code,
                    backoff_until_utc=_iso(src.backoff_until),
                    last_run_totals={
                        k: int(totals_raw.get(k, 0)) for k in _TOTAL_KEYS if k in totals_raw
                    },
                    schedule=ScheduleSummary(
                        enabled=sched.is_enabled,
                        next_run_at_utc=_iso(sched.next_run_at),
                        cadence=dict(sched.schedule_config or {}),
                    )
                    if sched
                    else None,
                    checkpoints=checkpoints.get(src.source_code, []),
                )
            )
        return ResearchSourceListResponse(
            live_fetch_kill_switch_enabled=live_fetch_env_enabled(), items=items
        )


@router.get("/research/items", response_model=SourceItemListResponse)
def list_research_items(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    provider: Annotated[str | None, Query(max_length=64, pattern=r"^[A-Z_]+$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    before: Annotated[datetime | None, Query()] = None,
) -> SourceItemListResponse:
    with _db(request) as db:
        filters = []
        if provider:
            filters.append(SourceItem.source_provider == provider)
        total = int(
            db.execute(select(func.count()).select_from(SourceItem).where(*filters)).scalar_one()
        )
        page_filters = [*filters, SourceItem.fetched_at < before] if before else filters
        rows = list(
            db.execute(
                select(SourceItem)
                .where(*page_filters)
                .order_by(SourceItem.fetched_at.desc(), SourceItem.id)
                .limit(limit + 1)
            ).scalars()
        )
        page = rows[:limit]
        clusters = _clusters_for(db, [r.id for r in page])
        return SourceItemListResponse(
            total=total,
            next_before_utc=page[-1].fetched_at.isoformat() if len(rows) > limit else None,
            items=[_item_summary(r, clusters.get(r.id, [])) for r in page],
        )


def _topic_summaries(
    db: Session, brand_id: uuid.UUID, where: Sequence[Any], limit: int
) -> tuple[int, list[TopicSummary]]:
    base = (
        select(TopicCluster, BrandTopicAssignment)
        .join(BrandTopicAssignment, BrandTopicAssignment.topic_cluster_id == TopicCluster.id)
        .where(BrandTopicAssignment.brand_profile_id == brand_id, *where)
    )
    total = int(db.execute(select(func.count()).select_from(base.subquery())).scalar_one())
    rows = db.execute(
        base.order_by(TopicCluster.last_seen_at.desc(), TopicCluster.id).limit(limit)
    ).all()
    cluster_ids = [c.id for c, _a in rows]
    providers: dict[uuid.UUID, set[str]] = defaultdict(set)
    counts: dict[uuid.UUID, int] = defaultdict(int)
    if cluster_ids:
        for cid, prov in db.execute(
            select(TopicClusterSource.topic_cluster_id, SourceItem.source_provider)
            .join(SourceItem, SourceItem.id == TopicClusterSource.source_item_id)
            .where(TopicClusterSource.topic_cluster_id.in_(cluster_ids))
        ).all():
            providers[cid].add(prov)
            counts[cid] += 1
    return total, [
        TopicSummary(
            topic_cluster_id=str(c.id),
            title=c.normalized_title,
            category=c.category,
            dedupe_key=c.dedupe_key,
            discovery_status=c.status,
            first_seen_at_utc=c.first_seen_at.isoformat(),
            last_seen_at_utc=c.last_seen_at.isoformat(),
            source_count=counts.get(c.id, 0),
            providers=sorted(providers.get(c.id, set())),
            assignment=BrandAssignmentSummary(
                brand_id=str(a.brand_profile_id),
                editorial_status=a.editorial_status,
                relevance_score=float(a.relevance_score) if a.relevance_score is not None else None,
                priority=a.priority,
                assigned_at_utc=a.assigned_at.isoformat(),
                evaluated_at_utc=_iso(a.evaluated_at),
            ),
        )
        for c, a in rows
    ]


@router.get("/topics", response_model=TopicListResponse)
def list_topics(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
    editorial_status: Annotated[str | None, Query(max_length=32, pattern=r"^[A-Z_]+$")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> TopicListResponse:
    with _db(request) as db:
        brand_id = _resolve_brand_id(db, brand)
        where = (
            [BrandTopicAssignment.editorial_status == editorial_status] if editorial_status else []
        )
        total, items = _topic_summaries(db, brand_id, where, limit)
        return TopicListResponse(brand_id=str(brand_id), total=total, items=items)


@router.get("/topics/{topic_id}", response_model=TopicDetailResponse)
def get_topic(
    topic_id: uuid.UUID,
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: Annotated[str, Query(min_length=1, max_length=128)],
) -> TopicDetailResponse:
    with _db(request) as db:
        brand_id = _resolve_brand_id(db, brand)
        _total, items = _topic_summaries(db, brand_id, [TopicCluster.id == topic_id], 1)
        if not items:
            raise _not_found("TOPIC_NOT_FOUND", "Topic not found for this brand.")
        sources = list(
            db.execute(
                select(SourceItem)
                .join(TopicClusterSource, TopicClusterSource.source_item_id == SourceItem.id)
                .where(TopicClusterSource.topic_cluster_id == topic_id)
                .order_by(SourceItem.fetched_at.desc())
            ).scalars()
        )
        clusters = _clusters_for(db, [s.id for s in sources])
        return TopicDetailResponse(
            topic=items[0], sources=[_item_summary(s, clusters.get(s.id, [])) for s in sources]
        )
