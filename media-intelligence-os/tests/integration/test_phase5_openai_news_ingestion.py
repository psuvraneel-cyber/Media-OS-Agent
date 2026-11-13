"""End-to-end OpenAI News ingestion through the real runner and database (fixture upstream)."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker
from tests.research_fixtures import OpenAINewsFixtureUpstream, make_openai_http

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
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.research.fetchers.base import SourceFetcher
from media_os_api.research.fetchers.openai_news import OpenAINewsFetcher
from media_os_api.research.http import SafeHttpClient
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import load_checkpoint
from media_os_api.research.runner import run_source_fetch

pytestmark = pytest.mark.integration
NS = ResourceNamespace.OPENAI_NEWS


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
                    SourceItem.source_provider == NS, SourceItem.external_id.like("index/fixture-%")
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
            delete(ResearchSourceCheckpoint).where(
                ResearchSourceCheckpoint.source_code == "OPENAI_NEWS"
            )
        )
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "OPENAI_NEWS")
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


def _make(upstream: OpenAINewsFixtureUpstream) -> Any:
    def make(_f: SourceFetcher, _c: Mapping[str, Any]) -> SafeHttpClient:
        return make_openai_http(upstream)

    return make


def test_openai_news_end_to_end_with_conditional_requests(
    factory: sessionmaker[Session],
) -> None:
    upstream = OpenAINewsFixtureUpstream()
    out = run_source_fetch(
        factory, OpenAINewsFetcher(), http_factory=_make(upstream), env_enabled=True
    )
    assert out.status == "SUCCEEDED" and out.streams[0].new_sources == 3
    with session_scope(factory) as db:
        items = (
            db.execute(
                select(SourceItem).where(
                    SourceItem.source_provider == NS, SourceItem.external_id.like("index/fixture-%")
                )
            )
            .scalars()
            .all()
        )
        assert len(items) == 3
        assert all(i.canonical_url.startswith("https://openai.com/index/fixture-") for i in items)
        cp = load_checkpoint(db, "OPENAI_NEWS", "news")
        assert cp.etag == '"rss-v1"' and len(cp.cursor["seen_ids"]) == 3

    out2 = run_source_fetch(
        factory, OpenAINewsFetcher(), http_factory=_make(upstream), env_enabled=True
    )
    assert out2.streams[0].status == "not_modified" and out2.http_requests == 1
    with session_scope(factory) as db:
        src = db.get(ResearchSource, "OPENAI_NEWS")
        assert src is not None and src.health_status == "HEALTHY"


def test_openai_news_bot_challenge_records_failure_and_backoff(
    factory: sessionmaker[Session],
) -> None:
    upstream = OpenAINewsFixtureUpstream("html_challenge.html")
    upstream.content_type = "text/html"
    with pytest.raises(PermanentTaskError) as excinfo:
        run_source_fetch(
            factory, OpenAINewsFetcher(), http_factory=_make(upstream), env_enabled=True
        )
    assert excinfo.value.code == "SOURCE_FETCH_FAILED"
    assert len(upstream.requests) == 1
    with session_scope(factory) as db:
        src = db.get(ResearchSource, "OPENAI_NEWS")
        assert src is not None
        assert src.health_status == "DEGRADED" and src.last_error_code == "FEED_BLOCKED_OR_HTML"
        assert src.backoff_until is not None
        assert load_checkpoint(db, "OPENAI_NEWS", "news").cursor == {}
