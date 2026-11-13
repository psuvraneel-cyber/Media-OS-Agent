"""Fixture-based unit tests for the Hacker News fetcher (no network, no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.research_fixtures import HN_FIXTURES, HackerNewsFixtureUpstream, make_hn_http

from media_os_api.research.fetchers.base import StreamSpec
from media_os_api.research.fetchers.hackernews import (
    API_BASE,
    HARD_MAX_ITEMS,
    SEEN_WINDOW,
    HackerNewsFetcher,
)
from media_os_api.research.http import (
    RetryableFetchError,
)
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState
from media_os_api.research.sanitization import sanitize_minimal_summary

pytestmark = pytest.mark.unit

FIXTURES = HN_FIXTURES
FixtureUpstream = HackerNewsFixtureUpstream
make_http = make_hn_http


def test_official_endpoint_and_policy() -> None:
    assert API_BASE == "https://hacker-news.firebaseio.com/v0"
    assert HackerNewsFetcher.host_policy.allowed_hosts == frozenset({"hacker-news.firebaseio.com"})
    assert HackerNewsFetcher.host_policy.auth_hosts == frozenset()  # no credentials ever sent
    assert HackerNewsFetcher.allowed_namespaces == frozenset({ResourceNamespace.HACKERNEWS_STORY})


def test_streams_are_restricted_to_official_lists() -> None:
    f = HackerNewsFetcher()
    assert [s.name for s in f.streams({})] == ["topstories", "newstories", "beststories"]
    names = f.streams({"streams": ["beststories", "askstories", "../admin", "beststories"]})
    assert [s.name for s in names] == ["beststories"]
    assert f.streams({"streams": ["nope"]}) == []
    assert f.max_items({"max_items_per_stream": 10_000}) == HARD_MAX_ITEMS
    assert f.max_items({"max_items_per_stream": "bad"}) == 30
    assert f.min_interval_ms({"min_interval_ms": 10}) == 250  # config cannot loosen the floor


def test_fetch_page_reads_list_and_items_with_isolation() -> None:
    upstream = FixtureUpstream()
    fetcher = HackerNewsFetcher()
    with make_http(upstream) as http:
        page = fetcher.fetch_page(http, StreamSpec("topstories"), CheckpointState(), {})

    assert upstream.paths[0] == "/v0/topstories.json"
    assert len(upstream.item_requests()) == 9
    ids = sorted(r["id"] for r in page.records)
    assert ids == [9001, 9002, 9003, 9004, 9005, 9006, 9007, 9008]  # null item 9009 dropped
    assert page.checkpoint.cursor["seen_ids"][:3] == [9001, 9002, 9003]
    assert 9009 in page.checkpoint.cursor["seen_ids"]
    assert page.has_more is False and page.not_modified is False
    assert page.fetch_errors == {}


def test_cap_and_seen_window_skip_known_items() -> None:
    upstream = FixtureUpstream()
    fetcher = HackerNewsFetcher()
    with make_http(upstream) as http:
        page = fetcher.fetch_page(
            http,
            StreamSpec("topstories"),
            CheckpointState(cursor={"seen_ids": [9001, 9002, 9003]}),
            {"max_items_per_stream": 5},
        )
    assert upstream.item_requests() == ["/v0/item/9004.json", "/v0/item/9005.json"]
    assert [r["id"] for r in page.records] == [9004, 9005]
    assert page.checkpoint.cursor["seen_ids"] == [9004, 9005, 9001, 9002, 9003]
    assert page.checkpoint.cursor["last_list_size"] == 5


def test_seen_window_is_bounded() -> None:
    upstream = FixtureUpstream()
    old = list(range(1, SEEN_WINDOW + 50))
    with make_http(upstream) as http:
        page = HackerNewsFetcher().fetch_page(
            http, StreamSpec("newstories"), CheckpointState(cursor={"seen_ids": old}), {}
        )
    assert len(page.checkpoint.cursor["seen_ids"]) == SEEN_WINDOW
    assert page.checkpoint.cursor["seen_ids"][:2] == [9010, 9002]


def test_item_cache_is_shared_across_streams_within_a_run() -> None:
    upstream = FixtureUpstream()
    fetcher = HackerNewsFetcher()
    with make_http(upstream) as http:
        fetcher.fetch_page(http, StreamSpec("topstories"), CheckpointState(), {})
        best = fetcher.fetch_page(http, StreamSpec("beststories"), CheckpointState(), {})
    assert upstream.item_requests().count("/v0/item/9001.json") == 1
    assert sorted(r["id"] for r in best.records) == [9001, 9010]


def test_transient_item_failure_is_isolated_and_retried_next_run() -> None:
    upstream = FixtureUpstream()
    upstream.status_override["/v0/item/9002.json"] = 503
    upstream.status_override["/v0/item/9003.json"] = 404
    with make_http(upstream) as http:
        page = HackerNewsFetcher().fetch_page(
            http, StreamSpec("topstories"), CheckpointState(), {"max_items_per_stream": 4}
        )
    assert [r["id"] for r in page.records] == [9001, 9004]
    assert page.fetch_errors == {"HTTP_503": 1, "HTTP_404": 1}
    seen = page.checkpoint.cursor["seen_ids"]
    assert 9002 not in seen  # transient → retried next run
    assert 9003 in seen  # permanent → not retried forever


def test_all_items_failing_fails_the_stream() -> None:
    upstream = FixtureUpstream()
    for n in (9010, 9002):
        upstream.status_override[f"/v0/item/{n}.json"] = 503
    with make_http(upstream) as http, pytest.raises(RetryableFetchError):
        HackerNewsFetcher().fetch_page(http, StreamSpec("newstories"), CheckpointState(), {})


@pytest.mark.parametrize("body", [b"{not json", b'{"a": 1}', b'[1, 2, "x"]', b"[true]"])
def test_malformed_story_list_raises_value_error(body: bytes) -> None:
    upstream = FixtureUpstream()
    upstream.body_override["/v0/topstories.json"] = body
    with make_http(upstream) as http, pytest.raises(ValueError):
        HackerNewsFetcher().fetch_page(http, StreamSpec("topstories"), CheckpointState(), {})


def test_malformed_item_document_is_isolated() -> None:
    upstream = FixtureUpstream()
    upstream.body_override["/v0/item/9010.json"] = b"[1,2,3]"
    with make_http(upstream) as http:
        page = HackerNewsFetcher().fetch_page(http, StreamSpec("newstories"), CheckpointState(), {})
    assert [r["id"] for r in page.records] == [9002]
    assert page.fetch_errors == {"MALFORMED_ITEM": 1}


def _item(n: int) -> dict[str, object]:
    import json

    doc: dict[str, object] = json.loads((FIXTURES / "items" / f"{n}.json").read_text("utf-8"))
    return doc


def test_to_candidates_normalises_live_stories() -> None:
    f = HackerNewsFetcher()
    (c,) = f.to_candidates(_item(9001))
    assert c.source_provider == ResourceNamespace.HACKERNEWS_STORY
    assert c.external_id == "9001"
    assert c.raw_url == "https://example.org/agent-framework-1-0"
    assert c.published_at == datetime.fromtimestamp(1763035200, tz=UTC)
    assert c.metadata["score"] == 311 and c.metadata["comments_count"] == 42

    (ask,) = f.to_candidates(_item(9002))
    assert ask.raw_url == "https://news.ycombinator.com/item?id=9002"  # discussion URL fallback
    summary = sanitize_minimal_summary(ask.summary)
    assert summary is not None
    assert "I'm curious" in summary and "&amp;" not in summary and "<" not in summary
    assert "script" not in summary  # encoded markup is decoded *and* re-stripped
    assert "alert(1)" in summary  # surrounding text survives as inert plain text

    (unsafe,) = f.to_candidates(_item(9007))
    assert unsafe.raw_url == "https://news.ycombinator.com/item?id=9007"


@pytest.mark.parametrize("n", [9003, 9004, 9005, 9006])
def test_non_story_dead_deleted_are_filtered_not_malformed(n: int) -> None:
    assert HackerNewsFetcher().to_candidates(_item(n)) == []


@pytest.mark.parametrize(
    "record",
    [
        _item(9008),  # no title
        {"id": "9001", "type": "story", "title": "x"},  # string id
        {"id": True, "type": "story", "title": "x"},
        {"id": 5, "type": "story", "title": "x", "time": "yesterday"},
        {"type": "story", "title": "x"},
    ],
)
def test_malformed_story_records_raise(record: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        HackerNewsFetcher().to_candidates(record)


def test_sanitizer_decodes_entities_and_restrips_markup() -> None:
    assert sanitize_minimal_summary("Tom &amp; Jerry&#x27;s") == "Tom & Jerry's"
    assert sanitize_minimal_summary("&lt;b&gt;bold&lt;/b&gt;") == "bold"
    assert sanitize_minimal_summary("a \x00\x07b") == "a b"
    assert sanitize_minimal_summary("1 &lt; 2") == "1 < 2"


def test_bounded_config_narrows_to_one_list_and_n_items() -> None:
    f = HackerNewsFetcher()
    cfg = f.bounded_config({"streams": ["topstories", "newstories"]}, StreamSpec("newstories"), 4)
    assert [s.name for s in f.streams(cfg)] == ["newstories"] and f.max_items(cfg) == 4
