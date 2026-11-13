"""Fixture-based unit tests for the OpenAI News RSS fetcher (no network, no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.research_fixtures import OpenAINewsFixtureUpstream, make_openai_http

from media_os_api.research.fetchers.base import FetchPage, StreamSpec
from media_os_api.research.fetchers.openai_news import (
    FEED_URL,
    HARD_MAX_ITEMS,
    OpenAINewsFetcher,
)
from media_os_api.research.http import PermanentFetchError
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState
from media_os_api.research.sanitization import sanitize_minimal_summary

pytestmark = pytest.mark.unit

NEWS = StreamSpec("news")


def _fetch(
    upstream: OpenAINewsFixtureUpstream,
    checkpoint: CheckpointState | None = None,
    config: dict[str, object] | None = None,
) -> FetchPage:
    with make_openai_http(upstream) as http:
        return OpenAINewsFetcher().fetch_page(
            http, NEWS, checkpoint or CheckpointState(), config or {}
        )


def test_official_feed_and_policy() -> None:
    assert FEED_URL == "https://openai.com/news/rss.xml"
    f = OpenAINewsFetcher
    assert f.host_policy.allowed_hosts == frozenset({"openai.com", "www.openai.com"})
    assert f.host_policy.auth_hosts == frozenset()
    assert f.allowed_namespaces == frozenset({ResourceNamespace.OPENAI_NEWS})
    assert [s.name for s in OpenAINewsFetcher().streams({})] == ["news"]
    assert OpenAINewsFetcher.max_items({"max_items": 9999}) == HARD_MAX_ITEMS


def test_first_fetch_parses_items_and_stores_validators() -> None:
    upstream = OpenAINewsFixtureUpstream()
    page = _fetch(upstream)
    assert [r["item_key"] for r in page.records] == [
        "index/fixture-model-update",
        "index/fixture-research-note",
        "index/fixture-safety-post",
    ]
    assert page.checkpoint.etag == '"rss-v1"'
    assert page.checkpoint.last_modified == "Thu, 12 Nov 2026 17:05:00 GMT"
    assert page.checkpoint.cursor["seen_ids"][0] == "index/fixture-model-update"
    req = upstream.requests[0]
    assert "if-none-match" not in req.headers
    assert "rss" in req.headers["accept"]


def test_conditional_request_returns_not_modified() -> None:
    upstream = OpenAINewsFixtureUpstream()
    first = _fetch(upstream)
    second = _fetch(upstream, first.checkpoint)
    assert second.not_modified and second.records == []
    assert upstream.requests[1].headers["if-none-match"] == '"rss-v1"'
    assert upstream.requests[1].headers["if-modified-since"] == "Thu, 12 Nov 2026 17:05:00 GMT"
    assert second.checkpoint.cursor == first.checkpoint.cursor  # window preserved


def test_seen_window_skips_known_items_when_feed_changes() -> None:
    upstream = OpenAINewsFixtureUpstream()
    first = _fetch(upstream)
    upstream.etag = '"rss-v2"'  # feed changed, but items overlap
    second = _fetch(upstream, first.checkpoint)
    assert not second.not_modified and second.records == []
    assert second.checkpoint.etag == '"rss-v2"'


def test_max_items_cap() -> None:
    page = _fetch(OpenAINewsFixtureUpstream(), config={"max_items": 2})
    assert len(page.records) == 2
    assert len(page.checkpoint.cursor["seen_ids"]) == 2  # the third is fetched next run


def test_html_bot_challenge_is_permanent_and_not_circumvented() -> None:
    upstream = OpenAINewsFixtureUpstream("html_challenge.html")
    upstream.content_type = "text/html; charset=utf-8"
    with pytest.raises(PermanentFetchError) as excinfo:
        _fetch(upstream)
    assert excinfo.value.code == "FEED_BLOCKED_OR_HTML"
    assert len(upstream.requests) == 1  # one request, no retries or workarounds

    sneaky = OpenAINewsFixtureUpstream("html_challenge.html")  # HTML mislabelled as RSS
    with pytest.raises(PermanentFetchError):
        _fetch(sneaky)


@pytest.mark.parametrize("fixture", ["attack_entities.xml", "not_rss.xml"])
def test_attacks_and_non_rss_are_rejected(fixture: str) -> None:
    with pytest.raises(ValueError):
        _fetch(OpenAINewsFixtureUpstream(fixture))


def test_upstream_403_is_permanent() -> None:
    upstream = OpenAINewsFixtureUpstream()
    upstream.status = 403
    with pytest.raises(PermanentFetchError) as excinfo:
        _fetch(upstream)
    assert excinfo.value.code == "HTTP_403"


def test_to_candidates_normalises_item() -> None:
    page = _fetch(OpenAINewsFixtureUpstream())
    (c,) = OpenAINewsFetcher().to_candidates(page.records[0])
    assert c.source_provider == ResourceNamespace.OPENAI_NEWS
    assert c.external_id == "index/fixture-model-update"
    assert c.raw_url == "https://openai.com/index/fixture-model-update"
    assert c.published_at == datetime(2026, 11, 12, 17, 0, tzinfo=UTC)
    assert c.metadata["topic"] == "Product"
    summary = sanitize_minimal_summary(c.summary)
    assert summary == "We are announcing a fixture & test item."


def test_malformed_items_raise_individually() -> None:
    page = _fetch(OpenAINewsFixtureUpstream("rss_malformed_items.xml"))
    results = []
    for record in page.records:
        try:
            OpenAINewsFetcher().to_candidates(record)
            results.append("ok")
        except ValueError:
            results.append("malformed")
    assert results == ["ok", "malformed", "malformed", "malformed", "malformed"]
