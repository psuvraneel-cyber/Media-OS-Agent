"""Fixture-based unit tests for the GitHub fetcher (no network, no database)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest
from tests.research_fixtures import GitHubFixtureUpstream, make_github_http

from media_os_api.research.fetchers.base import FetchPage, StreamSpec
from media_os_api.research.fetchers.github import API_BASE, MAX_WATCHED, GitHubFetcher
from media_os_api.research.http import PermanentFetchError, RetryableFetchError
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState
from media_os_api.research.sanitization import sanitize_minimal_summary

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
SEARCH = StreamSpec("search:topic:llm", {"query": "topic:llm"})
RELEASES = StreamSpec("releases")
WATCH = {"watched_repositories": ["fixture-org/agent-kit"]}


def _fetcher() -> GitHubFetcher:
    return GitHubFetcher(now=lambda: NOW)


def _fetch(
    upstream: GitHubFixtureUpstream,
    stream: StreamSpec,
    checkpoint: CheckpointState | None = None,
    config: Mapping[str, object] | None = None,
    token: str | None = None,
) -> FetchPage:
    with make_github_http(upstream, token=token) as http:
        return _fetcher().fetch_page(http, stream, checkpoint or CheckpointState(), config or {})


def test_official_api_policy_and_rate_floor() -> None:
    assert API_BASE == "https://api.github.com"
    assert GitHubFetcher.host_policy.allowed_hosts == frozenset({"api.github.com"})
    assert GitHubFetcher.host_policy.auth_hosts == frozenset({"api.github.com"})
    assert GitHubFetcher.allowed_namespaces == frozenset(
        {ResourceNamespace.GITHUB_REPOSITORY, ResourceNamespace.GITHUB_RELEASE}
    )
    assert _fetcher().min_interval_ms({"min_interval_ms": 100}) == 6500


def test_streams_validate_queries_and_watch_list() -> None:
    f = _fetcher()
    streams = f.streams(
        {
            "search_queries": ["topic:llm", "topic:llm", "x pushed:>2020-01-01", "bad;rm -rf", ""],
            "watched_repositories": ["fixture-org/agent-kit", "../etc/passwd", "a b/c"],
        }
    )
    assert [s.name for s in streams] == ["search:topic:llm", "releases"]
    many = {"watched_repositories": [f"o/r{i}" for i in range(50)]}
    assert len(f.watched(many)) == MAX_WATCHED
    assert f.streams({}) == []


def test_search_request_shape_and_token_scoping() -> None:
    upstream = GitHubFixtureUpstream()
    _fetch(upstream, SEARCH, token="ghp_test_only_value")
    req = upstream.requests[0]
    since = (NOW - timedelta(days=7)).date().isoformat()
    assert req.url.path == "/search/repositories"
    assert dict(req.url.params) == {
        "q": f"topic:llm pushed:>{since}",
        "sort": "stars",
        "order": "desc",
        "per_page": "30",
    }
    assert req.headers["accept"] == "application/vnd.github+json"
    assert req.headers["authorization"] == "Bearer ghp_test_only_value"

    anon = GitHubFixtureUpstream()
    _fetch(anon, SEARCH)
    assert "authorization" not in anon.requests[0].headers


def test_search_records_seen_window_and_filtering() -> None:
    upstream = GitHubFixtureUpstream()
    page = _fetch(upstream, SEARCH)
    assert len(page.records) == 7
    assert page.checkpoint.cursor["seen"][0] == "fixture-org/agent-kit"
    f = _fetcher()
    outcomes: list[str] = []
    for r in page.records:
        try:
            outcomes.append("ok" if f.to_candidates(r) else "filtered")
        except ValueError:
            outcomes.append("malformed")
    assert outcomes == ["ok", "filtered", "filtered", "ok", "malformed", "malformed", "ok"]

    again = _fetch(GitHubFixtureUpstream(), SEARCH, page.checkpoint)
    assert again.records == []  # all known: nothing re-processed


def test_repo_candidate_rebuilds_url_and_sanitises() -> None:
    page = _fetch(GitHubFixtureUpstream(), SEARCH)
    f = _fetcher()
    (main,) = f.to_candidates(page.records[0])
    assert main.source_provider == ResourceNamespace.GITHUB_REPOSITORY
    assert main.external_id == "fixture-org/agent-kit"
    assert main.raw_url == "https://github.com/fixture-org/agent-kit"
    assert main.metadata == {"stars": 4200, "language": "Python", "license": "MIT"}
    (evil,) = f.to_candidates(page.records[6])
    assert evil.raw_url == "https://github.com/fixture-dev/evil-url"  # feed URL ignored
    (tiny,) = f.to_candidates(page.records[3])
    assert sanitize_minimal_summary(tiny.summary) == "Tiny LLM runtime & tools"
    assert tiny.metadata["license"] is None


def test_releases_etag_prerelease_and_seen() -> None:
    upstream = GitHubFixtureUpstream()
    page = _fetch(upstream, RELEASES, config=WATCH)
    tags = [r["tag_name"] for r in page.records]
    assert tags == ["v2.0.0", "v1.9.0", "bad tag with spaces"]  # prerelease skipped
    assert page.checkpoint.cursor["etags"] == {"fixture-org/agent-kit": '"rel-v1"'}
    req = upstream.requests[0]
    assert req.url.path == "/repos/fixture-org/agent-kit/releases"
    assert req.url.params["per_page"] == "5"

    again = _fetch(upstream, RELEASES, page.checkpoint, WATCH)
    assert again.records == [] and upstream.requests[1].headers["if-none-match"] == '"rel-v1"'

    with_pre = _fetch(
        GitHubFixtureUpstream(), RELEASES, config={**WATCH, "include_prereleases": True}
    )
    assert "v2.1.0-rc1" in [r["tag_name"] for r in with_pre.records]


def test_release_candidates() -> None:
    page = _fetch(GitHubFixtureUpstream(), RELEASES, config=WATCH)
    f = _fetcher()
    (rel,) = f.to_candidates(page.records[0])
    assert rel.source_provider == ResourceNamespace.GITHUB_RELEASE
    assert rel.external_id == "fixture-org/agent-kit:v2.0.0"
    assert rel.raw_url == "https://github.com/fixture-org/agent-kit/releases/tag/v2.0.0"
    assert rel.title == "fixture-org/agent-kit Agent Kit 2.0" and rel.version == "v2.0.0"
    assert "<script>" not in (sanitize_minimal_summary(rel.summary) or "")
    (unnamed,) = f.to_candidates(page.records[1])
    assert unnamed.title == "fixture-org/agent-kit v1.9.0"
    with pytest.raises(ValueError):
        f.to_candidates(page.records[2])


def test_release_repo_isolation_and_all_failed() -> None:
    upstream = GitHubFixtureUpstream()
    cfg = {"watched_repositories": ["fixture-org/agent-kit", "fixture-org/gone"]}
    page = _fetch(upstream, RELEASES, config=cfg)
    assert page.fetch_errors == {"HTTP_404": 1} and len(page.records) == 3

    with pytest.raises(PermanentFetchError):
        _fetch(GitHubFixtureUpstream(), RELEASES, config={"watched_repositories": ["x/gone"]})


def test_rate_limit_403_is_retryable_with_reset() -> None:
    upstream = GitHubFixtureUpstream()
    reset = int((datetime.now(UTC) + timedelta(minutes=30)).timestamp())
    upstream.status_override["/search/repositories"] = 403
    upstream.extra_headers["/search/repositories"] = {
        "x-ratelimit-remaining": "0",
        "x-ratelimit-reset": str(reset),
    }
    with pytest.raises(RetryableFetchError) as excinfo:
        _fetch(upstream, SEARCH)
    assert excinfo.value.code == "HTTP_403_RATE_LIMITED"
    assert excinfo.value.retry_after is not None
    assert excinfo.value.retry_after > timedelta(minutes=25)
    assert len(upstream.requests) == 1  # long wait is not slept in-process


@pytest.mark.parametrize("body", [b"{bad", b"[]", b'{"items": "x"}'])
def test_malformed_search_documents(body: bytes) -> None:
    upstream = GitHubFixtureUpstream()
    upstream.search_body = body
    with pytest.raises(ValueError):
        _fetch(upstream, SEARCH)


def test_bounded_config() -> None:
    f = _fetcher()
    cfg = f.bounded_config({"search_queries": ["topic:llm", "topic:x"], **WATCH}, SEARCH, 3)
    assert [s.name for s in f.streams(cfg)] == ["search:topic:llm"]
    assert cfg["max_repositories_per_query"] == 3
    rcfg = f.bounded_config({"search_queries": ["topic:llm"], **WATCH}, RELEASES, 2)
    assert [s.name for s in f.streams(rcfg)] == ["releases"]
