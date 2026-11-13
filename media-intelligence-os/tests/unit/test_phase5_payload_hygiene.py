"""Unit tests: untrusted research content is bounded and scrubbed from durable job payloads."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from typing import Any

import pytest

from media_os_api.db.models import JobRun
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.jobs.ledger import (
    ITEMS_REDACTED_MARKER,
    _payloads_equivalent,
    redact_items_payload,
)
from media_os_api.research import tasks as research_tasks
from media_os_api.research.models import IngestionResult, RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace

pytestmark = pytest.mark.unit


class _FakeSession:
    def begin_nested(self) -> Any:
        return nullcontext()


def _job(payload: dict[str, Any]) -> JobRun:
    return JobRun(task_name=research_tasks.MANUAL_INGEST_TASK_NAME, payload=payload)


@pytest.fixture
def ingested(monkeypatch: pytest.MonkeyPatch) -> list[RawDiscoveryCandidate]:
    seen: list[RawDiscoveryCandidate] = []

    def _fake_ingest(_session: Any, candidate: RawDiscoveryCandidate) -> IngestionResult:
        seen.append(candidate)
        return IngestionResult(
            source_item_id=uuid.uuid4(),
            topic_cluster_id=uuid.uuid4(),
            assignments_created=2,
            is_new_source=True,
            is_new_cluster=True,
            source_provider=candidate.source_provider,
            external_id=candidate.external_id,
            dedupe_key="k",
        )

    monkeypatch.setattr(research_tasks, "ingest_discovery_candidate", _fake_ingest)
    return seen


def test_builder_strips_html_caps_text_and_drops_nested_objects() -> None:
    payload = research_tasks.build_manual_ingestion_payload(
        ResourceNamespace.HACKERNEWS_STORY,
        [{"id": 1, "title": "T", "text": "<p>" + "x" * 5000 + "</p>", "kids": [1, 2], "raw": {}}],
    )
    item = payload["items"][0]
    assert len(item["text"]) <= 500
    assert "<p>" not in item["text"]
    assert "raw" not in item
    assert item["kids"] == [1, 2]
    assert payload["item_count"] == 1
    assert len(payload["items_sha256"]) == 64


def test_builder_rejects_oversized_batches() -> None:
    too_many = [{"id": i, "title": "t"} for i in range(research_tasks.MAX_MANUAL_INGEST_ITEMS + 1)]
    with pytest.raises(ValueError):
        research_tasks.build_manual_ingestion_payload(ResourceNamespace.HACKERNEWS_STORY, too_many)


def test_handler_scrubs_items_and_isolates_malformed_records(
    ingested: list[RawDiscoveryCandidate],
) -> None:
    payload = research_tasks.build_manual_ingestion_payload(
        ResourceNamespace.HACKERNEWS_STORY,
        [
            {"id": 1, "title": "Good story", "text": "<b>hello</b>"},
            {"id": 2},  # malformed: no title
            "not-a-mapping",
            {"id": 3, "title": "Another good story"},
        ],
    )
    job = _job(payload)
    result = research_tasks.handle_research_ingestion(_FakeSession(), job)  # type: ignore[arg-type]

    assert result["status"] == "partial_success"
    assert result["total_items_processed"] == 2
    assert result["malformed_items"] == 2
    assert [c.external_id for c in ingested] == ["1", "3"]
    assert ingested[0].summary == "hello"

    assert "items" not in job.payload
    assert job.payload[ITEMS_REDACTED_MARKER] is True
    assert job.payload["item_count"] == 4
    assert job.payload["items_sha256"] == payload["items_sha256"]


def test_handler_sanitizes_raw_payloads_that_bypassed_the_builder(
    ingested: list[RawDiscoveryCandidate],
) -> None:
    job = _job(
        {
            "source_provider": ResourceNamespace.HACKERNEWS_STORY,
            "items": [{"id": 9, "title": "Raw", "text": "<i>" + "y" * 3000 + "</i>"}],
        }
    )
    research_tasks.handle_research_ingestion(_FakeSession(), job)  # type: ignore[arg-type]
    assert ingested[0].summary is not None and len(ingested[0].summary) <= 500
    assert "items" not in job.payload


def test_handler_rejects_oversized_and_blocked_payloads() -> None:
    big = _job(
        {
            "source_provider": ResourceNamespace.HACKERNEWS_STORY,
            "items": [{"id": i, "title": "t"} for i in range(60)],
        }
    )
    with pytest.raises(PermanentTaskError):
        research_tasks.handle_research_ingestion(_FakeSession(), big)  # type: ignore[arg-type]

    reddit = _job({"source_provider": ResourceNamespace.REDDIT_POST, "items": []})
    with pytest.raises(PermanentTaskError) as excinfo:
        research_tasks.handle_research_ingestion(_FakeSession(), reddit)  # type: ignore[arg-type]
    assert excinfo.value.code == "SOURCE_POLICY_BLOCKED"


def test_redacted_payload_remains_idempotent_for_reenqueue() -> None:
    payload = research_tasks.build_manual_ingestion_payload(
        ResourceNamespace.HACKERNEWS_STORY, [{"id": 1, "title": "a"}]
    )
    redacted = redact_items_payload(payload)
    assert redact_items_payload(redacted) == redacted
    assert _payloads_equivalent(redacted, payload)

    different = research_tasks.build_manual_ingestion_payload(
        ResourceNamespace.HACKERNEWS_STORY, [{"id": 2, "title": "b"}]
    )
    assert not _payloads_equivalent(redacted, different)
    assert _payloads_equivalent({"a": 1}, {"a": 1})
