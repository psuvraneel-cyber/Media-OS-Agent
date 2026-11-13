"""End-to-end GitHub ingestion through the real runner and database (fixture upstream)."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker
from tests.research_fixtures import GitHubFixtureUpstream, make_github_http

from media_os_api.db import (
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
from media_os_api.research.fetchers.base import SourceFetcher
from media_os_api.research.fetchers.github import GitHubFetcher
from media_os_api.research.http import SafeHttpClient
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import load_checkpoint
from media_os_api.research.runner import run_source_fetch

pytestmark = pytest.mark.integration
NS = (ResourceNamespace.GITHUB_REPOSITORY, ResourceNamespace.GITHUB_RELEASE)
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


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
                    SourceItem.source_provider.in_(NS), SourceItem.external_id.like("fixture-%")
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
            delete(ResearchSourceCheckpoint).where(ResearchSourceCheckpoint.source_code == "GITHUB")
        )
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "GITHUB")
            .values(
                policy_mode="EXPERIMENTAL",
                live_fetch_approved=True,
                approved_by="owner:integration-test",
                approved_at=datetime.now(UTC),
                health_status="DISABLED",
                consecutive_failures=0,
                backoff_until=None,
                config={
                    "search_queries": ["topic:llm"],
                    "watched_repositories": ["fixture-org/agent-kit", "fixture-org/gone"],
                    "min_interval_ms": 6500,
                },
            )
        )


def _make(upstream: GitHubFixtureUpstream) -> Any:
    def make(_f: SourceFetcher, _c: Mapping[str, Any]) -> SafeHttpClient:
        return make_github_http(upstream)

    return make


def test_github_end_to_end_search_and_releases(factory: sessionmaker[Session]) -> None:
    upstream = GitHubFixtureUpstream()
    out = run_source_fetch(
        factory, GitHubFetcher(now=lambda: NOW), http_factory=_make(upstream), env_enabled=True
    )
    by = {s.name: s for s in out.streams}
    assert out.status == "SUCCEEDED"
    assert by["search:topic:llm"].new_sources == 3 and by["search:topic:llm"].malformed == 2
    assert by["releases"].new_sources == 2 and by["releases"].malformed == 1
    assert by["releases"].fetch_errors == {"HTTP_404": 1}

    with session_scope(factory) as db:
        repos = (
            db.execute(
                select(SourceItem).where(
                    SourceItem.source_provider == NS[0], SourceItem.external_id.like("fixture-%")
                )
            )
            .scalars()
            .all()
        )
        assert sorted(r.external_id for r in repos) == [
            "fixture-dev/evil-url",
            "fixture-dev/tiny-llm",
            "fixture-org/agent-kit",
        ]
        assert all(r.canonical_url.startswith("https://github.com/fixture-") for r in repos)
        releases = (
            db.execute(select(SourceItem).where(SourceItem.source_provider == NS[1]))
            .scalars()
            .all()
        )
        assert {r.external_id for r in releases} >= {
            "fixture-org/agent-kit:v2.0.0",
            "fixture-org/agent-kit:v1.9.0",
        }
        cp = load_checkpoint(db, "GITHUB", "releases")
        assert cp.cursor["etags"] == {"fixture-org/agent-kit": '"rel-v1"'}

    upstream.requests.clear()
    out2 = run_source_fetch(
        factory, GitHubFetcher(now=lambda: NOW), http_factory=_make(upstream), env_enabled=True
    )
    assert sum(s.new_sources for s in out2.streams) == 0
    rel_req = [r for r in upstream.requests if r.url.path.endswith("agent-kit/releases")]
    assert rel_req[0].headers["if-none-match"] == '"rel-v1"'
