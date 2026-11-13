"""Unit tests: research job routing, task registry, handler guards, HTTP factory token scoping."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from media_os_api.db.models import JobRun
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.jobs.outbox import CeleryTaskPublisher
from media_os_api.jobs.routing import (
    DEFAULT_CELERY_TASK,
    RESEARCH_CELERY_TASK,
    celery_task_for_queue,
    queue_for_task,
)
from media_os_api.jobs.tasks import PHASE4_TASK_REGISTRY
from media_os_api.research.fetchers import FETCHERS, GitHubFetcher, HackerNewsFetcher
from media_os_api.research.http import InMemoryMinIntervalLimiter
from media_os_api.research.jobs import fetch_task_name, make_fetch_handler, production_http_factory

pytestmark = pytest.mark.unit


def test_routing_sends_fetches_to_research_queue() -> None:
    assert queue_for_task("media_os.research.fetch.arxiv") == "research"
    assert queue_for_task("media_os.research.ingest") == "control"
    assert queue_for_task("media_os.internal.ping") == "control"
    assert celery_task_for_queue("research") == RESEARCH_CELERY_TASK
    assert celery_task_for_queue("control") == DEFAULT_CELERY_TASK


def test_registry_has_one_fetch_handler_per_fetcher_and_never_reddit() -> None:
    fetch_tasks = {t for t in PHASE4_TASK_REGISTRY if t.startswith("media_os.research.fetch.")}
    assert fetch_tasks == {fetch_task_name(code) for code in FETCHERS}
    assert len(fetch_tasks) == 5
    assert "media_os.research.fetch.reddit" not in PHASE4_TASK_REGISTRY


def test_publisher_uses_research_celery_task() -> None:
    sent: list[dict[str, Any]] = []

    class FakeApp:
        def send_task(self, name: str, **kwargs: Any) -> Any:
            sent.append({"name": name, **kwargs})

            class R:
                id = kwargs["task_id"]

            return R()

    pub = CeleryTaskPublisher(FakeApp())
    for queue in ("research", "control"):
        pub.publish_job(
            job_run_id=uuid.uuid4(), task_name="t", queue_name=queue, attempt=0, request_id="-"
        )
    assert [s["name"] for s in sent] == [RESEARCH_CELERY_TASK, DEFAULT_CELERY_TASK]
    assert sent[0]["queue"] == "research" and sent[0]["kwargs"].keys() == {"job_run_id"}


@pytest.mark.parametrize(
    ("task_name", "payload", "code"),
    [
        ("media_os.research.fetch.reddit", {"source": "REDDIT"}, "UNKNOWN_RESEARCH_SOURCE"),
        ("media_os.research.fetch.x", {}, "UNKNOWN_RESEARCH_SOURCE"),
        ("media_os.research.fetch.arxiv", {"source": "HACKERNEWS"}, "TASK_SOURCE_MISMATCH"),
    ],
)
def test_handler_guards_reject_before_any_work(
    task_name: str, payload: dict[str, Any], code: str
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("must not build HTTP or DB resources")

    handler = make_fetch_handler(http_factory=boom, session_factory_provider=boom)
    with pytest.raises(PermanentTaskError) as excinfo:
        handler(None, JobRun(task_name=task_name, payload=payload))  # type: ignore[arg-type]
    assert excinfo.value.code == code


def test_production_factory_sends_github_token_only_to_github() -> None:
    limiter = InMemoryMinIntervalLimiter(sleep=lambda _s: None)
    factory = production_http_factory(limiter, environ={"GITHUB_TOKEN": "test-token-value"})
    gh = factory(GitHubFetcher(), {})
    hn = factory(HackerNewsFetcher(), {})
    try:
        assert gh._token == "test-token-value"  # noqa: SLF001 - verifying secret scoping
        assert hn._token is None  # noqa: SLF001
        assert gh.policy.auth_hosts == frozenset({"api.github.com"})
    finally:
        gh.close()
        hn.close()
    no_token = production_http_factory(limiter, environ={})(GitHubFetcher(), {})
    try:
        assert no_token._token is None  # noqa: SLF001
    finally:
        no_token.close()
