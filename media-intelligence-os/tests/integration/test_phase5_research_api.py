"""Milestone 6: read-only research/topic API — auth, shape, pagination and brand isolation."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

from media_os_api.auth import create_redis_client, hash_password
from media_os_api.config import load_settings
from media_os_api.db import (
    BRAND_APP_PROMO_ID,
    BRAND_TECH_AI_ID,
    AuthSession,
    BrandTopicAssignment,
    OwnerUser,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from media_os_api.main import create_app
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.schemas.system import PostizReachabilitySummary
from media_os_api.services.research_service import ingest_discovery_candidate

pytestmark = pytest.mark.integration

OWNER = "m6-owner"
PASSWORD = "M6-Strong-Test-Password!2026"
SECRET = "m6-test-session-secret-0123456789abcdef0123456789abcdef0123"
ORIGIN = {"Origin": "http://localhost:3000"}


def _urls() -> tuple[str, str]:
    db_url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    redis_url = (os.environ.get("MEDIA_OS_TEST_REDIS_URL") or "").strip()
    if not db_url or not redis_url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL / MEDIA_OS_TEST_REDIS_URL not set.")
    return db_url, redis_url


@pytest.fixture
def seeded() -> Iterator[dict[str, str]]:
    """Seed one two-version arXiv paper (one cluster, two sources) and one HN story."""
    db_url, redis_url = _urls()
    rc = create_redis_client(redis_url)
    for key in rc.scan_iter("media_os:rl:login:*"):
        rc.delete(key)
    rc.close()
    engine = create_db_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)
    tag = uuid.uuid4().hex[:6]
    with session_scope(factory) as db:
        db.execute(delete(AuthSession))
        db.execute(delete(OwnerUser))
        now = datetime.now(UTC)
        paper = f"2611.{int(tag, 16) % 90000 + 10000:05d}"
        r1 = ingest_discovery_candidate(
            db,
            RawDiscoveryCandidate(
                source_provider=ResourceNamespace.ARXIV_PAPER,
                external_id=f"{paper}v1",
                raw_url=f"https://arxiv.org/abs/{paper}v1",
                title=f"M6 paper {tag}",
                source_label="arXiv Paper",
                category="AI Research",
                published_at=now,
                summary="<b>Paper</b> summary",
            ),
        )
        ingest_discovery_candidate(
            db,
            RawDiscoveryCandidate(
                source_provider=ResourceNamespace.ARXIV_PAPER,
                external_id=f"{paper}v2",
                raw_url=f"https://arxiv.org/abs/{paper}v2",
                title=f"M6 paper {tag} (v2)",
                source_label="arXiv Paper",
                category="AI Research",
                published_at=now,
            ),
        )
        r3 = ingest_discovery_candidate(
            db,
            RawDiscoveryCandidate(
                source_provider=ResourceNamespace.HACKERNEWS_STORY,
                external_id=f"m6-{tag}",
                raw_url=f"https://example.org/m6-{tag}",
                title=f"M6 story {tag}",
                source_label="Hacker News Story",
                category="Tech & Engineering",
                published_at=now,
            ),
        )
        # Brand 1 rejects the paper; brand 2 leaves it pending.
        db.execute(
            update(BrandTopicAssignment)
            .where(
                BrandTopicAssignment.brand_profile_id == BRAND_TECH_AI_ID,
                BrandTopicAssignment.topic_cluster_id == r1.topic_cluster_id,
            )
            .values(editorial_status="REJECTED", rejection_reason="m6 test")
        )
    engine.dispose()
    yield {
        "paper_cluster": str(r1.topic_cluster_id),
        "story_cluster": str(r3.topic_cluster_id),
        "tag": tag,
        "db_url": db_url,
        "redis_url": redis_url,
    }


@pytest.fixture
def client(seeded: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    async def _probe(base_url: str, *, timeout_seconds: float = 2.0) -> PostizReachabilitySummary:
        return PostizReachabilitySummary(
            status="reachable", checked_at_utc="2026-11-14T00:00:00+00:00", detail="mock"
        )

    monkeypatch.setattr("media_os_api.api.routes.system.probe_postiz_reachability", _probe)
    settings = load_settings(
        {
            "APP_ENV": "test",
            "OWNER_USERNAME": OWNER,
            "OWNER_PASSWORD_HASH": hash_password(PASSWORD),
            "SESSION_SECRET": SECRET,
            "COOKIE_SECURE": "false",
            "CORS_ALLOWED_ORIGINS": "http://localhost:3000",
            "DATABASE_URL": seeded["db_url"],
            "REDIS_URL": seeded["redis_url"],
        }
    )
    with TestClient(create_app(settings)) as c:
        yield c


def _login(c: TestClient) -> None:
    r = c.post("/api/v1/auth/login", json={"username": OWNER, "password": PASSWORD}, headers=ORIGIN)
    assert r.status_code == 200


def test_endpoints_require_authentication(client: TestClient, seeded: dict[str, str]) -> None:
    for path in (
        "/api/v1/research/sources",
        "/api/v1/research/items",
        "/api/v1/topics?brand=brand-tech-ai",
        f"/api/v1/topics/{seeded['paper_cluster']}?brand=brand-tech-ai",
    ):
        assert client.get(path).status_code == 401, path


def test_sources_expose_registry_without_secrets(client: TestClient) -> None:
    _login(client)
    body = client.get("/api/v1/research/sources").json()
    assert body["live_fetch_kill_switch_enabled"] is False
    by = {s["source"]: s for s in body["items"]}
    assert by["REDDIT"]["policy_mode"] == "BLOCKED"
    assert by["HACKERNEWS"]["schedule"]["cadence"] == {"kind": "interval", "seconds": 3600}
    forbidden = {"config", "approved_by", "last_error_message", "etag", "cursor"}
    for item in body["items"]:
        assert forbidden.isdisjoint(item), item.keys()


def test_items_pagination_filter_and_validation(client: TestClient, seeded: dict[str, str]) -> None:
    _login(client)
    page = client.get("/api/v1/research/items?provider=ARXIV_PAPER&limit=1").json()
    assert page["total"] >= 2 and len(page["items"]) == 1 and page["next_before_utc"]
    nxt = client.get(
        "/api/v1/research/items",
        params={"provider": "ARXIV_PAPER", "limit": 1, "before": page["next_before_utc"]},
    ).json()
    assert nxt["items"][0]["source_item_id"] != page["items"][0]["source_item_id"]
    item = page["items"][0]
    assert item["clusters"] and item["clusters"][0]["dedupe_key"].startswith("arxiv:paper:")
    assert client.get("/api/v1/research/items?provider=bad;drop").status_code == 422
    assert client.get("/api/v1/research/items?limit=1000").status_code == 422


def test_topics_are_brand_isolated(client: TestClient, seeded: dict[str, str]) -> None:
    _login(client)
    paper = seeded["paper_cluster"]

    def topic(brand: str) -> dict[str, object]:
        body = client.get(f"/api/v1/topics?brand={brand}&limit=100").json()
        return next(t for t in body["items"] if t["topic_cluster_id"] == paper)

    tech = topic("brand-tech-ai")
    promo = topic("brand-app-promo")
    assert tech["assignment"]["editorial_status"] == "REJECTED"  # type: ignore[index]
    assert tech["assignment"]["brand_id"] == str(BRAND_TECH_AI_ID)  # type: ignore[index]
    assert promo["assignment"]["editorial_status"] == "PENDING_EVALUATION"  # type: ignore[index]
    assert promo["assignment"]["brand_id"] == str(BRAND_APP_PROMO_ID)  # type: ignore[index]
    assert promo["assignment"]["relevance_score"] is None  # type: ignore[index]
    assert tech["source_count"] == 2 and tech["providers"] == ["ARXIV_PAPER"]
    assert tech["discovery_status"] == "DISCOVERED"  # global field untouched by brand decision

    rejected = client.get("/api/v1/topics?brand=brand-app-promo&editorial_status=REJECTED").json()
    assert all(t["topic_cluster_id"] != paper for t in rejected["items"])

    assert client.get("/api/v1/topics?brand=no-such-brand").status_code == 404
    assert client.get("/api/v1/topics").status_code == 422


def test_topic_detail_with_provenance(client: TestClient, seeded: dict[str, str]) -> None:
    _login(client)
    paper = seeded["paper_cluster"]
    body = client.get(f"/api/v1/topics/{paper}?brand=brand-app-promo").json()
    assert body["topic"]["assignment"]["editorial_status"] == "PENDING_EVALUATION"
    assert len(body["sources"]) == 2
    assert {s["external_id"][-2:] for s in body["sources"]} == {"v1", "v2"}
    summaries = [s["summary"] for s in body["sources"] if s["summary"]]
    assert summaries == ["Paper summary"]  # sanitised at ingestion
    missing = client.get(f"/api/v1/topics/{uuid.uuid4()}?brand=brand-app-promo")
    assert missing.status_code == 404
    assert client.get("/api/v1/topics/not-a-uuid?brand=brand-app-promo").status_code == 422


def test_api_is_read_only(client: TestClient, seeded: dict[str, str]) -> None:
    _login(client)
    for method in ("post", "put", "patch", "delete"):
        r = getattr(client, method)("/api/v1/research/sources", headers=ORIGIN)
        assert r.status_code == 405, method
