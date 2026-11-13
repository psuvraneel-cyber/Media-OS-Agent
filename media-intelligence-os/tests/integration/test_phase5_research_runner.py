"""Runner integration tests: real PostgreSQL + Redis (disposable stack), mocked HTTP upstream.

Covers: live-fetch gate (zero HTTP when refused), fetch → validate → normalise → dedupe →
provenance → brand assignment, pagination + checkpointing, checkpoint recovery after a mid-stream
failure, malformed records, malformed documents, stream isolation, conditional requests,
concurrent ingestion, and the Redis limiter (spacing + fail-closed).
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, ClassVar

import httpx
import pytest
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

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
from media_os_api.jobs.errors import PermanentTaskError, PolicyRefusalError
from media_os_api.research.fetchers.base import FetchPage, SourceFetcher, StreamSpec
from media_os_api.research.http import (
    HostPolicy,
    InMemoryMinIntervalLimiter,
    RetryableFetchError,
    RetryPolicy,
    SafeHttpClient,
    build_redis_rate_limiter,
)
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState, load_checkpoint
from media_os_api.research.runner import run_source_fetch
from media_os_api.services.research_service import ingest_discovery_candidate

pytestmark = pytest.mark.integration

SOURCE = "HACKERNEWS"
HOST = "api.example.org"


def _db_url() -> str:
    url = (os.environ.get("MEDIA_OS_TEST_DATABASE_URL") or "").strip()
    if not url:
        pytest.skip("MEDIA_OS_TEST_DATABASE_URL not set; skipping live PostgreSQL tests.")
    return url


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_db_engine(_db_url(), pool_size=6, max_overflow=4)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def factory(engine: Engine) -> sessionmaker[Session]:
    return create_session_factory(engine)


@pytest.fixture(autouse=True)
def _reset_source(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as db:
        db.execute(
            delete(ResearchSourceCheckpoint).where(ResearchSourceCheckpoint.source_code == SOURCE)
        )
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == SOURCE)
            .values(
                policy_mode="OFF",
                live_fetch_approved=False,
                approved_by=None,
                approved_at=None,
                health_status="DISABLED",
                consecutive_failures=0,
                backoff_until=None,
                last_error_code=None,
                last_error_message=None,
                last_run_stats={},
            )
        )


def _approve(factory: sessionmaker[Session]) -> None:
    with session_scope(factory) as db:
        db.execute(
            update(ResearchSource)
            .where(ResearchSource.source_code == SOURCE)
            .values(
                policy_mode="EXPERIMENTAL",
                live_fetch_approved=True,
                approved_by="owner:integration-test",
                approved_at=datetime.now(UTC),
            )
        )


class FakeUpstream:
    """Serves `https://api.example.org/<stream>?page=N` from an in-memory page table."""

    def __init__(self, pages: dict[str, list[list[dict[str, Any]]]]) -> None:
        self.pages = pages
        self.requests: list[httpx.Request] = []
        self.fail: dict[tuple[str, int], int] = {}  # (stream, page) -> status
        self.etags: dict[str, str] = {}
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append(request)
        stream = request.url.path.strip("/")
        page = int(request.url.params.get("page", "1"))
        if (stream, page) in self.fail:
            return httpx.Response(self.fail[(stream, page)])
        etag = self.etags.get(stream)
        if etag and request.headers.get("if-none-match") == etag:
            return httpx.Response(304)
        if stream == "broken":
            return httpx.Response(200, content=b"{not json")
        table = self.pages.get(stream, [])
        items = table[page - 1] if page <= len(table) else []
        body = {"items": items, "next": page + 1 if page < len(table) else None}
        headers = {"etag": etag} if etag else {}
        return httpx.Response(200, content=json.dumps(body).encode(), headers=headers)


class FakeFetcher(SourceFetcher):
    source_code: ClassVar[str] = SOURCE
    host_policy: ClassVar[HostPolicy] = HostPolicy(allowed_hosts=frozenset({HOST}))
    allowed_namespaces: ClassVar[frozenset[str]] = frozenset({ResourceNamespace.HACKERNEWS_STORY})
    default_min_interval_ms: ClassVar[int] = 0

    def __init__(self, stream_names: list[str], tag: str) -> None:
        self.stream_names = stream_names
        self.tag = tag

    def streams(self, config: Mapping[str, Any]) -> list[StreamSpec]:
        return [StreamSpec(name=n) for n in self.stream_names]

    def fetch_page(
        self,
        http: SafeHttpClient,
        stream: StreamSpec,
        checkpoint: CheckpointState,
        config: Mapping[str, Any],
    ) -> FetchPage:
        page = int(checkpoint.cursor.get("next_page", 1))
        result = http.get(
            f"https://{HOST}/{stream.name}", params={"page": page}, etag=checkpoint.etag
        )
        if result.not_modified:
            return FetchPage(records=[], checkpoint=checkpoint, not_modified=True)
        doc = json.loads(result.body)  # ValueError on malformed documents
        nxt = doc.get("next")
        cursor = {"next_page": nxt} if nxt else {"next_page": 1, "cycle_complete": True}
        return FetchPage(
            records=list(doc["items"]),
            checkpoint=CheckpointState(cursor=cursor, etag=result.etag),
            has_more=bool(nxt),
        )

    def to_candidates(self, record: Mapping[str, Any]) -> list[RawDiscoveryCandidate]:
        title = str(record.get("title") or "").strip()
        if not title:
            raise ValueError("missing title")
        namespace = (
            ResourceNamespace.REDDIT_POST
            if record.get("reddit")
            else ResourceNamespace.HACKERNEWS_STORY
        )
        ext = f"{self.tag}-{record['id']}"
        return [
            RawDiscoveryCandidate(
                source_provider=namespace,
                external_id=ext,
                raw_url=f"https://news.example.org/item?id={ext}",
                title=title,
                source_label="Hacker News Story",
                category="Tech & Engineering",
                summary=record.get("text"),
            )
        ]


def _http_factory(
    upstream: FakeUpstream,
) -> Callable[[SourceFetcher, Mapping[str, Any]], SafeHttpClient]:
    def factory(fetcher: SourceFetcher, config: Mapping[str, Any]) -> SafeHttpClient:
        return SafeHttpClient(
            source_code=fetcher.source_code,
            policy=fetcher.host_policy,
            rate_limiter=InMemoryMinIntervalLimiter(sleep=lambda _s: None),
            min_interval_ms=fetcher.min_interval_ms(config),
            transport=httpx.MockTransport(upstream),
            resolver=lambda _h, _p: ["140.82.112.5"],
            retry_policy=RetryPolicy(max_attempts=3, base_delay_seconds=0.01),
            sleep=lambda _s: None,
        )

    return factory


def _count(db: Session, model: Any, *where: Any) -> int:
    return int(db.execute(select(func.count()).select_from(model).where(*where)).scalar_one())


def _active_brands(db: Session) -> int:
    return _count(db, BrandProfile, BrandProfile.is_active.is_(True))


def _tag() -> str:
    return f"rt{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- gate


def test_gate_refusal_makes_zero_http_requests_and_keeps_health(
    factory: sessionmaker[Session],
) -> None:
    upstream = FakeUpstream({"s1": [[{"id": 1, "title": "x"}]]})
    fetcher = FakeFetcher(["s1"], _tag())

    with pytest.raises(PolicyRefusalError) as excinfo:
        run_source_fetch(factory, fetcher, http_factory=_http_factory(upstream), env_enabled=True)
    assert excinfo.value.code == "SOURCE_DISABLED"

    _approve(factory)
    with pytest.raises(PolicyRefusalError) as excinfo:
        run_source_fetch(factory, fetcher, http_factory=_http_factory(upstream), env_enabled=False)
    assert excinfo.value.code == "LIVE_FETCH_KILL_SWITCH"

    assert upstream.requests == []
    with session_scope(factory) as db:
        src = db.get(ResearchSource, SOURCE)
        assert src is not None and src.last_attempt_at is None


# --------------------------------------------------------------------------- happy path


def test_fetch_validate_normalise_dedupe_provenance_assign_checkpoint_health(
    factory: sessionmaker[Session],
) -> None:
    _approve(factory)
    tag = _tag()
    upstream = FakeUpstream(
        {
            "s1": [
                [
                    {"id": 1, "title": "Story one", "text": "<p>" + "a" * 900 + "</p>"},
                    {"id": 2, "title": ""},  # malformed: no title
                    {"id": 3, "title": "Story three"},
                ],
                [
                    {"id": 4, "title": "Story four"},
                    {"id": 1, "title": "Story one"},  # duplicate within the run
                    {"id": 5, "title": "Sneaky", "reddit": True},  # blocked namespace
                ],
            ]
        }
    )
    fetcher = FakeFetcher(["s1"], tag)
    outcome = run_source_fetch(
        factory, fetcher, http_factory=_http_factory(upstream), env_enabled=True
    )

    stream = outcome.streams[0]
    assert outcome.status == "SUCCEEDED" and stream.status == "ok"
    assert stream.pages == 2 and stream.records_seen == 6
    assert stream.new_sources == 3 and stream.new_clusters == 3
    assert stream.malformed == 2
    assert outcome.http_requests == 2

    with session_scope(factory) as db:
        brands = _active_brands(db)
        assert stream.assignments_created == 3 * brands
        items = (
            db.execute(select(SourceItem).where(SourceItem.external_id.like(f"{tag}-%")))
            .scalars()
            .all()
        )
        assert {i.external_id for i in items} == {f"{tag}-1", f"{tag}-3", f"{tag}-4"}
        assert all(i.source_provider == ResourceNamespace.HACKERNEWS_STORY for i in items)
        one = next(i for i in items if i.external_id == f"{tag}-1")
        assert one.minimal_permitted_summary is not None
        assert (
            len(one.minimal_permitted_summary) <= 500 and "<p>" not in one.minimal_permitted_summary
        )
        assert _count(db, SourceItem, SourceItem.external_id == f"{tag}-5") == 0

        cluster_ids = [
            row
            for row in db.execute(
                select(TopicClusterSource.topic_cluster_id).where(
                    TopicClusterSource.source_item_id.in_([i.id for i in items])
                )
            ).scalars()
        ]
        assert len(cluster_ids) == 3
        assignments = (
            db.execute(
                select(BrandTopicAssignment).where(
                    BrandTopicAssignment.topic_cluster_id.in_(cluster_ids)
                )
            )
            .scalars()
            .all()
        )
        assert len(assignments) == 3 * brands
        assert all(a.editorial_status == "PENDING_EVALUATION" for a in assignments)
        assert all(a.relevance_score is None for a in assignments)
        clusters = (
            db.execute(select(TopicCluster).where(TopicCluster.id.in_(cluster_ids))).scalars().all()
        )
        assert all(c.status == "DISCOVERED" and c.score_breakdown == {} for c in clusters)

        cp = load_checkpoint(db, SOURCE, "s1")
        assert cp.cursor == {"next_page": 1, "cycle_complete": True}
        src = db.get(ResearchSource, SOURCE)
        assert src is not None and src.health_status == "HEALTHY"
        assert src.last_run_stats["totals"]["new_sources"] == 3

    # Re-running is idempotent: nothing new, no extra assignments.
    again = run_source_fetch(
        factory, fetcher, http_factory=_http_factory(upstream), env_enabled=True
    )
    s = again.streams[0]
    assert (s.new_sources, s.new_clusters, s.assignments_created) == (0, 0, 0)
    assert s.candidates_ingested == 4  # 1, 3, 4, 1 seen again; dedupe absorbed them


# --------------------------------------------------------------------------- recovery


def test_checkpoint_recovery_after_mid_stream_failure(factory: sessionmaker[Session]) -> None:
    _approve(factory)
    tag = _tag()
    pages = [[{"id": n, "title": f"T{n}"}] for n in range(1, 4)]
    upstream = FakeUpstream({"s1": pages})
    upstream.fail[("s1", 2)] = 404  # permanent on page 2
    fetcher = FakeFetcher(["s1"], tag)

    with pytest.raises(PermanentTaskError) as excinfo:
        run_source_fetch(factory, fetcher, http_factory=_http_factory(upstream), env_enabled=True)
    assert excinfo.value.code == "SOURCE_FETCH_FAILED"

    with session_scope(factory) as db:
        assert _count(db, SourceItem, SourceItem.external_id == f"{tag}-1") == 1  # page 1 kept
        assert load_checkpoint(db, SOURCE, "s1").cursor == {"next_page": 2}
        src = db.get(ResearchSource, SOURCE)
        assert src is not None
        assert src.health_status == "DEGRADED" and src.consecutive_failures == 1
        assert src.last_error_code == "HTTP_404" and src.backoff_until is not None
        db.execute(  # owner/operator clears the backoff for the test
            update(ResearchSource)
            .where(ResearchSource.source_code == SOURCE)
            .values(backoff_until=None)
        )

    upstream.fail.clear()
    upstream.requests.clear()
    outcome = run_source_fetch(
        factory, fetcher, http_factory=_http_factory(upstream), env_enabled=True
    )
    assert upstream.requests[0].url.params["page"] == "2"  # resumed, page 1 not re-fetched
    assert outcome.streams[0].new_sources == 2
    with session_scope(factory) as db:
        assert _count(db, SourceItem, SourceItem.external_id.like(f"{tag}-%")) == 3
        src = db.get(ResearchSource, SOURCE)
        assert src is not None and src.health_status == "HEALTHY"
        assert src.consecutive_failures == 0


# --------------------------------------------------------------------------- isolation


def test_failing_stream_does_not_block_other_streams(factory: sessionmaker[Session]) -> None:
    _approve(factory)
    tag = _tag()
    upstream = FakeUpstream({"good": [[{"id": 1, "title": "Good"}]], "bad": [[]]})
    upstream.fail[("bad", 1)] = 503
    fetcher = FakeFetcher(["bad", "broken", "good"], tag)

    outcome = run_source_fetch(
        factory, fetcher, http_factory=_http_factory(upstream), env_enabled=True
    )
    by_name = {s.name: s for s in outcome.streams}
    assert outcome.status == "PARTIAL"
    assert by_name["bad"].status == "failed" and by_name["bad"].error_code == "HTTP_503"
    assert by_name["broken"].status == "failed"
    assert by_name["broken"].error_code == "MALFORMED_DOCUMENT"
    assert by_name["good"].status == "ok" and by_name["good"].new_sources == 1
    bad_requests = [r for r in upstream.requests if r.url.path == "/bad"]
    assert len(bad_requests) == 3  # bounded in-run retries

    with session_scope(factory) as db:
        src = db.get(ResearchSource, SOURCE)
        assert src is not None
        assert src.health_status == "DEGRADED" and src.backoff_until is None
        assert src.consecutive_failures == 0 and src.last_error_code == "HTTP_503"


def test_conditional_request_not_modified(factory: sessionmaker[Session]) -> None:
    _approve(factory)
    upstream = FakeUpstream({"feed": [[{"id": 1, "title": "Only"}]]})
    upstream.etags["feed"] = '"v7"'
    fetcher = FakeFetcher(["feed"], _tag())

    first = run_source_fetch(
        factory, fetcher, http_factory=_http_factory(upstream), env_enabled=True
    )
    assert first.streams[0].new_sources == 1
    second = run_source_fetch(
        factory, fetcher, http_factory=_http_factory(upstream), env_enabled=True
    )
    assert second.streams[0].status == "not_modified"
    assert second.streams[0].records_seen == 0 and second.status == "SUCCEEDED"
    assert upstream.requests[-1].headers["if-none-match"] == '"v7"'
    with session_scope(factory) as db:
        assert load_checkpoint(db, SOURCE, "feed").etag == '"v7"'


# --------------------------------------------------------------------------- concurrency


def test_concurrent_ingestion_creates_no_duplicates(factory: sessionmaker[Session]) -> None:
    tag = _tag()
    candidates = [
        RawDiscoveryCandidate(
            source_provider=ResourceNamespace.HACKERNEWS_STORY,
            external_id=f"{tag}-{n}",
            raw_url=f"https://news.example.org/item?id={tag}-{n}",
            title=f"Concurrent {n}",
            source_label="Hacker News Story",
            category="Tech & Engineering",
        )
        for n in range(6)
    ]
    workers = 4
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []
    new_source_flags: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            with session_scope(factory) as db:
                for c in candidates:
                    res = ingest_discovery_candidate(db, c)
                    with lock:
                        new_source_flags.append(res.is_new_source)
        except BaseException as exc:  # noqa: BLE001 - surfaced via assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert sum(new_source_flags) == len(candidates)
    with session_scope(factory) as db:
        items = (
            db.execute(select(SourceItem).where(SourceItem.external_id.like(f"{tag}-%")))
            .scalars()
            .all()
        )
        assert len(items) == len(candidates)
        links = (
            db.execute(
                select(TopicClusterSource.topic_cluster_id).where(
                    TopicClusterSource.source_item_id.in_([i.id for i in items])
                )
            )
            .scalars()
            .all()
        )
        assert len(links) == len(candidates)
        n_assign = _count(
            db, BrandTopicAssignment, BrandTopicAssignment.topic_cluster_id.in_(list(links))
        )
        assert n_assign == len(candidates) * _active_brands(db)


def test_concurrent_runner_passes_are_safe(factory: sessionmaker[Session]) -> None:
    _approve(factory)
    tag = _tag()
    upstream = FakeUpstream({"s1": [[{"id": n, "title": f"C{n}"} for n in range(8)]]})
    errors: list[BaseException] = []

    def run() -> None:
        try:
            run_source_fetch(
                factory,
                FakeFetcher(["s1"], tag),
                http_factory=_http_factory(upstream),
                env_enabled=True,
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    with session_scope(factory) as db:
        assert _count(db, SourceItem, SourceItem.external_id.like(f"{tag}-%")) == 8


# --------------------------------------------------------------------------- redis limiter


def test_redis_limiter_spacing_and_fail_closed() -> None:
    redis_url = (os.environ.get("MEDIA_OS_TEST_REDIS_URL") or "").strip()
    if not redis_url:
        pytest.skip("MEDIA_OS_TEST_REDIS_URL not set.")
    limiter = build_redis_rate_limiter(redis_url)
    code = f"T{uuid.uuid4().hex[:10].upper()}"
    start = time.monotonic()
    limiter.acquire(code, 400)
    limiter.acquire(code, 400)
    assert time.monotonic() - start >= 0.35

    dead = build_redis_rate_limiter("redis://127.0.0.1:1/15", socket_timeout=0.5)
    with pytest.raises(RetryableFetchError) as excinfo:
        dead.acquire(code, 400)
    assert excinfo.value.code == "RATE_LIMITER_UNAVAILABLE"
