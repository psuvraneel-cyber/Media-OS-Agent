"""Shared fixture-backed upstreams for research fetcher tests (no network)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from media_os_api.research.fetchers.hackernews import HackerNewsFetcher
from media_os_api.research.http import InMemoryMinIntervalLimiter, RetryPolicy, SafeHttpClient

HN_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research" / "hackernews"


class HackerNewsFixtureUpstream:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.status_override: dict[str, int] = {}
        self.body_override: dict[str, bytes] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "hacker-news.firebaseio.com"
        assert request.url.scheme == "https"
        path = request.url.path
        self.paths.append(path)
        if path in self.status_override:
            return httpx.Response(self.status_override[path])
        if path in self.body_override:
            return httpx.Response(200, content=self.body_override[path])
        rel = path.removeprefix("/v0/").replace("item/", "items/", 1)
        file = HN_FIXTURES / rel
        if not file.is_file():
            return httpx.Response(404)
        return httpx.Response(
            200, content=file.read_bytes(), headers={"content-type": "application/json"}
        )

    def item_requests(self) -> list[str]:
        return [p for p in self.paths if p.startswith("/v0/item/")]


def make_hn_http(upstream: Callable[[httpx.Request], httpx.Response]) -> SafeHttpClient:
    fetcher = HackerNewsFetcher
    return SafeHttpClient(
        source_code=fetcher.source_code,
        policy=fetcher.host_policy,
        rate_limiter=InMemoryMinIntervalLimiter(sleep=lambda _s: None),
        min_interval_ms=fetcher.default_min_interval_ms,
        transport=httpx.MockTransport(upstream),
        resolver=lambda _h, _p: ["151.101.1.1"],
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.01),
        sleep=lambda _s: None,
    )
