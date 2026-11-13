"""End-to-end arXiv ingestion through the real runner and database (fixture upstream).

Disposable stack only. Covers cross-listed papers (one SourceItem), version updates (new SourceItem,
same TopicCluster, no new brand assignments), high-water checkpointing, malformed entries, and an
XML attack payload isolated to one category.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from tests.research_fixtures import ARXIV_FIXTURES, ArxivFixtureUpstream, make_arxiv_http

from media_os_api.db import (
    BrandProfile,
    BrandTopicAssignment,
    ResearchSource,
    ResearchSourceCheckpoint,
    SourceItem,
    TopicCluster,
    TopicClusterSource,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.research.fetchers.arxiv import ArxivFetcher
from media_os_api.research.fetchers.base import SourceFetcher
from media_os_api.research.http import SafeHttpClient
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import load_checkpoint
from media_os_api.research.runner import run_source_fetch

pytestmark = pytest.mark.integration

AX = ResourceNamespace.ARXIV_PAPER
PREFIX = "2611.7"  # test-only id range, cleaned before each test


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine: Engine = create_db_engine(_db_url(), pool_size=4, max_overflow=2)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as db:
        ids = (
            db.execute(
                select(SourceItem.id).where(
                    SourceItem.source_provider == AX, SourceItem.external_id.like(f"{PREFIX}%")
                )
            )
            .scalars()
            .all()
        )
        if ids:
            clusters = (
                db.execute(
                    select(TopicClusterSource.topic_cluster_id).where(
                        TopicClusterSource.source_item_id.in_(ids)
                    )
                )
                .scalars()
                .all()
            )
            db.execute(
                delete(BrandTopicAssignment).where(
                    BrandTopicAssignment.topic_cluster_id.in_(clusters)
                )
            )
            db.execute(delete(TopicClusterSource).where(TopicClusterSource.source_item_id.in_(ids)))
            db.execute(delete(SourceItem).where(SourceItem.id.in_(ids)))
            db.execute(delete(TopicCluster).where(TopicCluster.id.in_(clusters)))
        db.execute(
            delete(ResearchSourceCheckpoint).where(ResearchSourceCheckpoint.source_code == "ARXIV")
        )
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "ARXIV")
            .values(
                policy_mode="EXPERIMENTAL",
                live_fetch_approved=True,
                approved_by="owner:integration-test",
                approved_at=datetime.now(UTC),
                health_status="DISABLED",
                consecutive_failures=0,
                backoff_until=None,
                config={
                    "categories": ["cs.AI", "cs.CL"],
                    "max_results": 10,
                    "min_interval_ms": 3100,
                },
            )
        )


def _make(upstream: ArxivFixtureUpstream) -> Any:
    def make(_f: SourceFetcher, _c: Mapping[str, Any]) -> SafeHttpClient:
        return make_arxiv_http(upstream)

    return make


def _brands(db: Session) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(BrandProfile).where(BrandProfile.is_active.is_(True))
        ).scalar_one()
    )


def test_arxiv_end_to_end_cross_listing_versions_and_checkpoints(
    factory: sessionmaker[Session],
) -> None:
    cross = (f"{PREFIX}0002v1", "2026-11-12T10:00:00Z", "Cross-listed paper")
    upstream = ArxivFixtureUpstream(
        {
            "cs.AI": [(f"{PREFIX}0001v1", "2026-11-12T11:00:00Z", "AI paper"), cross],
            "cs.CL": [cross, (f"{PREFIX}0003v1", "2026-11-12T09:00:00Z", "CL paper")],
        }
    )
    out = run_source_fetch(factory, ArxivFetcher(), http_factory=_make(upstream), env_enabled=True)
    assert out.status == "SUCCEEDED"
    assert sum(s.new_sources for s in out.streams) == 3  # cross-listed paper stored once
    assert sum(s.candidates_ingested for s in out.streams) == 4
    assert out.http_requests == 2

    with session_scope(factory) as db:
        items = (
            db.execute(
                select(SourceItem).where(
                    SourceItem.source_provider == AX, SourceItem.external_id.like(f"{PREFIX}%")
                )
            )
            .scalars()
            .all()
        )
        assert sorted(i.external_id for i in items) == [
            f"{PREFIX}0001v1",
            f"{PREFIX}0002v1",
            f"{PREFIX}0003v1",
        ]
        assert all(i.canonical_url.startswith("https://arxiv.org/abs/") for i in items)
        cp_ai = load_checkpoint(db, "ARXIV", "cat:cs.AI")
        assert cp_ai.cursor == {"next_start": 0, "high_water": "2026-11-12T11:00:00+00:00"}
        brands = _brands(db)
        n_assign = int(
            db.execute(
                select(func.count())
                .select_from(BrandTopicAssignment)
                .join(
                    TopicClusterSource,
                    TopicClusterSource.topic_cluster_id == BrandTopicAssignment.topic_cluster_id,
                )
                .where(TopicClusterSource.source_item_id.in_([i.id for i in items]))
            ).scalar_one()
        )
        assert n_assign == 3 * brands

    # Second run. arXiv entries carry the *latest* version id but keep the v1 `published` time and
    # are sorted by submission date, so a revision is only seen when it sits at/above the category's
    # high-water mark (>= boundary). cs.CL's mark is 10:00, so the revised cross-listed paper is
    # included there; cs.AI gains one genuinely new paper.
    upstream.entries["cs.AI"] = [
        (f"{PREFIX}0004v1", "2026-11-13T08:00:00Z", "Newer AI paper"),
        *upstream.entries["cs.AI"],
    ]
    upstream.entries["cs.CL"] = [
        (f"{PREFIX}0002v2", "2026-11-12T10:00:00Z", "Cross-listed paper (revised)"),
        (f"{PREFIX}0003v1", "2026-11-12T09:00:00Z", "CL paper"),
    ]
    upstream.requests.clear()
    out2 = run_source_fetch(factory, ArxivFetcher(), http_factory=_make(upstream), env_enabled=True)
    ai = next(s for s in out2.streams if s.name == "cat:cs.AI")
    cl = next(s for s in out2.streams if s.name == "cat:cs.CL")
    assert (ai.new_sources, ai.new_clusters) == (1, 1)  # 0004v1
    assert (cl.new_sources, cl.new_clusters, cl.assignments_created) == (1, 0, 0)  # 0002v2
    with session_scope(factory) as db:
        v1 = db.execute(
            select(SourceItem).where(SourceItem.external_id == f"{PREFIX}0002v1")
        ).scalar_one()
        v2 = db.execute(
            select(SourceItem).where(SourceItem.external_id == f"{PREFIX}0002v2")
        ).scalar_one()
        c1 = db.execute(
            select(TopicClusterSource.topic_cluster_id).where(
                TopicClusterSource.source_item_id == v1.id
            )
        ).scalar_one()
        c2 = db.execute(
            select(TopicClusterSource.topic_cluster_id).where(
                TopicClusterSource.source_item_id == v2.id
            )
        ).scalar_one()
        assert c1 == c2  # version update joins the existing paper cluster
        n = db.execute(
            select(func.count())
            .select_from(BrandTopicAssignment)
            .where(BrandTopicAssignment.topic_cluster_id == c1)
        ).scalar_one()
        assert int(n) == _brands(db)  # no duplicate brand assignments for the revision
        assert load_checkpoint(db, "ARXIV", "cat:cs.AI").cursor["high_water"] == (
            "2026-11-13T08:00:00+00:00"
        )


def test_arxiv_attack_payload_and_malformed_entries_are_isolated(
    factory: sessionmaker[Session],
) -> None:
    upstream = ArxivFixtureUpstream(
        {"cs.CL": [(f"{PREFIX}0010v1", "2026-11-12T10:00:00Z", "Clean CL paper")]}
    )
    upstream.raw_override["cs.AI"] = (ARXIV_FIXTURES / "attack_billion_laughs.xml").read_bytes()
    out = run_source_fetch(factory, ArxivFetcher(), http_factory=_make(upstream), env_enabled=True)
    by_name = {s.name: s for s in out.streams}
    assert out.status == "PARTIAL"
    assert by_name["cat:cs.AI"].status == "failed"
    assert by_name["cat:cs.AI"].error_code == "MALFORMED_DOCUMENT"
    assert by_name["cat:cs.CL"].new_sources == 1

    with session_scope(factory) as db:
        src = db.get(ResearchSource, "ARXIV")
        assert src is not None and src.health_status == "DEGRADED"
        assert src.last_error_code == "MALFORMED_DOCUMENT"
        assert load_checkpoint(db, "ARXIV", "cat:cs.AI").cursor == {}  # never advanced

    # Malformed entries inside an otherwise valid feed are skipped individually (fresh checkpoint so
    # the high-water filter does not hide them).
    with session_scope(factory) as db:
        db.execute(
            delete(ResearchSourceCheckpoint).where(ResearchSourceCheckpoint.source_code == "ARXIV")
        )
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "ARXIV")
            .values(backoff_until=None)
        )
    upstream2 = ArxivFixtureUpstream({})
    upstream2.raw_override["cs.CL"] = (ARXIV_FIXTURES / "cs_CL_malformed_entries.xml").read_bytes()
    upstream2.raw_override["cs.AI"] = (ARXIV_FIXTURES / "empty.xml").read_bytes()
    out2 = run_source_fetch(
        factory, ArxivFetcher(), http_factory=_make(upstream2), env_enabled=True
    )
    cl = next(s for s in out2.streams if s.name == "cat:cs.CL")
    assert cl.records_seen == 4 and cl.malformed == 3
