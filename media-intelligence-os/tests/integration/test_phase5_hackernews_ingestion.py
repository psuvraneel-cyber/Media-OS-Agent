"""End-to-end Hacker News ingestion through the real runner and database (fixture upstream).

Disposable stack only. Verifies the HN fetcher satisfies the shared connector contract: fetch,
validate, normalise, deduplicate, provenance, brand assignment, checkpoint, rate-limit path,
isolated failures, and health.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker
from tests.research_fixtures import HackerNewsFixtureUpstream, make_hn_http

from media_os_api.db import (
    BrandProfile,
    BrandTopicAssignment,
    ResearchSource,
    ResearchSourceCheckpoint,
    SourceItem,
    TopicClusterSource,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.jobs.errors import PolicyRefusalError
from media_os_api.research.fetchers.base import SourceFetcher
from media_os_api.research.fetchers.hackernews import HackerNewsFetcher
from media_os_api.research.http import SafeHttpClient
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import load_checkpoint
from media_os_api.research.runner import run_source_fetch

pytestmark = pytest.mark.integration

HN = ResourceNamespace.HACKERNEWS_STORY
FIXTURE_IDS = [str(n) for n in range(9001, 9011)]


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
    """Remove prior HN fixture rows and reset the registry row (disposable DB only)."""
    with session_scope(factory) as db:
        ids = (
            db.execute(
                select(SourceItem.id).where(
                    SourceItem.source_provider == HN, SourceItem.external_id.in_(FIXTURE_IDS)
                )
            )
            .scalars()
            .all()
        )
        if ids:
            cluster_ids = (
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
                    BrandTopicAssignment.topic_cluster_id.in_(cluster_ids)
                )
            )
            db.execute(delete(TopicClusterSource).where(TopicClusterSource.source_item_id.in_(ids)))
            db.execute(delete(SourceItem).where(SourceItem.id.in_(ids)))
        db.execute(
            delete(ResearchSourceCheckpoint).where(
                ResearchSourceCheckpoint.source_code == "HACKERNEWS"
            )
        )
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "HACKERNEWS")
            .values(
                policy_mode="EXPERIMENTAL",
                live_fetch_approved=True,
                approved_by="owner:integration-test",
                approved_at=datetime.now(UTC),
                health_status="DISABLED",
                consecutive_failures=0,
                backoff_until=None,
            )
        )


def _factory_for(
    upstream: HackerNewsFixtureUpstream,
) -> Any:
    def make(_fetcher: SourceFetcher, _config: Mapping[str, Any]) -> SafeHttpClient:
        return make_hn_http(upstream)

    return make


def test_hackernews_end_to_end_ingestion(factory: sessionmaker[Session]) -> None:
    upstream = HackerNewsFixtureUpstream()
    outcome = run_source_fetch(
        factory, HackerNewsFetcher(), http_factory=_factory_for(upstream), env_enabled=True
    )
    assert outcome.status == "SUCCEEDED"
    by_name = {s.name: s for s in outcome.streams}
    assert set(by_name) == {"topstories", "newstories", "beststories"}
    top = by_name["topstories"]
    # 9 ids, null dropped → 8 records; stories 9001/9002/9007 ingested; 9003-9006 filtered;
    # 9008 (no title) malformed.
    assert top.records_seen == 8 and top.new_sources == 3 and top.malformed == 1
    # Item cache: 9001/9002/9010 appear in several lists but are fetched once each.
    item_paths = upstream.item_requests()
    assert len(item_paths) == len(set(item_paths)) == 10

    with session_scope(factory) as db:
        items = {
            i.external_id: i
            for i in db.execute(
                select(SourceItem).where(
                    SourceItem.source_provider == HN, SourceItem.external_id.in_(FIXTURE_IDS)
                )
            ).scalars()
        }
        assert set(items) == {"9001", "9002", "9007", "9010"}
        assert items["9001"].canonical_url.startswith("https://example.org/agent-framework-1-0")
        assert items["9007"].canonical_url.startswith("https://news.ycombinator.com/item")
        summary = items["9002"].minimal_permitted_summary or ""
        assert "I'm curious" in summary and "<" not in summary and "script" not in summary
        assert items["9001"].source_policy_mode == "EXPERIMENTAL"

        brands = int(
            db.execute(
                select(func.count())
                .select_from(BrandProfile)
                .where(BrandProfile.is_active.is_(True))
            ).scalar_one()
        )
        clusters = (
            db.execute(
                select(TopicClusterSource.topic_cluster_id).where(
                    TopicClusterSource.source_item_id.in_([i.id for i in items.values()])
                )
            )
            .scalars()
            .all()
        )
        assert len(clusters) == 4
        n_assign = int(
            db.execute(
                select(func.count())
                .select_from(BrandTopicAssignment)
                .where(
                    BrandTopicAssignment.topic_cluster_id.in_(list(clusters)),
                    BrandTopicAssignment.editorial_status == "PENDING_EVALUATION",
                    BrandTopicAssignment.relevance_score.is_(None),
                )
            ).scalar_one()
        )
        assert n_assign == 4 * brands

        top_cp = load_checkpoint(db, "HACKERNEWS", "topstories")
        assert set(top_cp.cursor["seen_ids"]) == set(range(9001, 9010))
        src = db.get(ResearchSource, "HACKERNEWS")
        assert src is not None and src.health_status == "HEALTHY"
        assert src.last_run_stats["totals"]["new_sources"] == 4

    # Steady state: second run issues only the 3 list requests (every id already seen).
    upstream2 = HackerNewsFixtureUpstream()
    again = run_source_fetch(
        factory, HackerNewsFetcher(), http_factory=_factory_for(upstream2), env_enabled=True
    )
    assert upstream2.item_requests() == []
    assert len(upstream2.paths) == 3
    assert sum(s.new_sources for s in again.streams) == 0


def test_hackernews_partial_outage_is_isolated(factory: sessionmaker[Session]) -> None:
    upstream = HackerNewsFixtureUpstream()
    upstream.status_override["/v0/newstories.json"] = 503  # whole list down
    upstream.status_override["/v0/item/9002.json"] = 503  # one item down
    outcome = run_source_fetch(
        factory, HackerNewsFetcher(), http_factory=_factory_for(upstream), env_enabled=True
    )
    by_name = {s.name: s for s in outcome.streams}
    assert outcome.status == "PARTIAL"
    assert by_name["newstories"].status == "failed"
    assert by_name["newstories"].error_code == "HTTP_503"
    assert by_name["topstories"].fetch_errors == {"HTTP_503": 1}
    assert by_name["beststories"].status == "ok"
    with session_scope(factory) as db:
        assert 9002 not in load_checkpoint(db, "HACKERNEWS", "topstories").cursor["seen_ids"]
        src = db.get(ResearchSource, "HACKERNEWS")
        assert src is not None and src.health_status == "DEGRADED"
        assert src.last_run_stats["streams"]["topstories"]["fetch_errors"] == {"HTTP_503": 1}


def test_hackernews_gate_blocks_all_traffic_when_not_approved(
    factory: sessionmaker[Session],
) -> None:
    with session_scope(factory) as db:
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "HACKERNEWS")
            .values(live_fetch_approved=False, approved_by=None, approved_at=None)
        )
    upstream = HackerNewsFixtureUpstream()
    with pytest.raises(PolicyRefusalError):
        run_source_fetch(
            factory, HackerNewsFetcher(), http_factory=_factory_for(upstream), env_enabled=True
        )
    assert upstream.paths == []
