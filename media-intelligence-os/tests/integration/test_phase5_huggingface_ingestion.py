"""End-to-end Hugging Face ingestion through the real runner and database (fixture upstream)."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session, sessionmaker
from tests.research_fixtures import HuggingFaceFixtureUpstream, make_hf_http

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
from media_os_api.research.fetchers.huggingface import STREAM_NAMESPACES, HuggingFaceFetcher
from media_os_api.research.http import SafeHttpClient
from media_os_api.research.runner import run_source_fetch

pytestmark = pytest.mark.integration
NS = tuple(STREAM_NAMESPACES.values())


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
                    SourceItem.source_provider.in_(NS),
                    SourceItem.external_id.like("%fixture%"),
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
                ResearchSourceCheckpoint.source_code == "HUGGINGFACE"
            )
        )
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == "HUGGINGFACE")
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


def _make(upstream: HuggingFaceFixtureUpstream) -> Any:
    def make(_f: SourceFetcher, _c: Mapping[str, Any]) -> SafeHttpClient:
        return make_hf_http(upstream)

    return make


def test_huggingface_end_to_end_with_isolation(factory: sessionmaker[Session]) -> None:
    upstream = HuggingFaceFixtureUpstream()
    upstream.status_override["datasets"] = 503
    out = run_source_fetch(
        factory, HuggingFaceFetcher(), http_factory=_make(upstream), env_enabled=True
    )
    by = {s.name: s for s in out.streams}
    assert out.status == "PARTIAL"
    assert by["models"].new_sources == 3 and by["models"].malformed == 2
    assert by["datasets"].status == "failed" and by["datasets"].error_code == "HTTP_503"
    assert by["spaces"].new_sources == 1
    assert {r.url.path for r in upstream.requests} == {
        "/api/models",
        "/api/datasets",
        "/api/spaces",
    }

    with session_scope(factory) as db:
        items = (
            db.execute(
                select(SourceItem).where(
                    SourceItem.source_provider.in_(NS), SourceItem.external_id.like("%fixture%")
                )
            )
            .scalars()
            .all()
        )
        assert sorted(i.canonical_url for i in items) == [
            "https://huggingface.co/fixture-lab/tiny-coder-1b",
            "https://huggingface.co/fixture-lab/vision-small",
            "https://huggingface.co/gpt2-fixture",
            "https://huggingface.co/spaces/fixture-lab/demo-space",
        ]
        src = db.get(ResearchSource, "HUGGINGFACE")
        assert src is not None and src.health_status == "DEGRADED"

    upstream.status_override.clear()
    out2 = run_source_fetch(
        factory, HuggingFaceFetcher(), http_factory=_make(upstream), env_enabled=True
    )
    by2 = {s.name: s for s in out2.streams}
    assert by2["models"].new_sources == 0 and by2["datasets"].new_sources == 1
    with session_scope(factory) as db:
        src = db.get(ResearchSource, "HUGGINGFACE")
        assert src is not None and src.health_status == "HEALTHY"
