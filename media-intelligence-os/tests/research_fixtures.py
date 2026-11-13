"""Shared fixture-backed upstreams for research fetcher tests (no network)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from media_os_api.research.fetchers.hackernews import HackerNewsFetcher
from media_os_api.research.http import (
    InMemoryMinIntervalLimiter,
    RateLimiter,
    RetryPolicy,
    SafeHttpClient,
)

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


# --------------------------------------------------------------------------- arXiv

ARXIV_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research" / "arxiv"


def atom_feed(entries: list[tuple[str, str, str]], total: int | None = None) -> bytes:
    """Build a synthetic arXiv-shaped Atom feed from (paper_id, published_iso, title)."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom" '
        'xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" '
        'xmlns:arxiv="http://arxiv.org/schemas/atom">',
        "<id>http://arxiv.org/api/fixture</id>",
        f"<opensearch:totalResults>{total if total is not None else len(entries)}"
        "</opensearch:totalResults>",
    ]
    for paper_id, published, title in entries:
        parts.append(
            f"<entry><id>http://arxiv.org/abs/{paper_id}</id>"
            f"<published>{published}</published><updated>{published}</updated>"
            f"<title>{title}</title><summary>Synthetic abstract for {paper_id}.</summary>"
            "<author><name>Fixture Author</name></author>"
            f'<link title="pdf" href="http://arxiv.org/pdf/{paper_id}" rel="related"/>'
            '<arxiv:primary_category term="cs.AI"/></entry>'
        )
    parts.append("</feed>")
    return "\n".join(parts).encode("utf-8")


class ArxivFixtureUpstream:
    """Serves `export.arxiv.org/api/query` from per-category entry lists (newest first)."""

    def __init__(self, entries_by_category: dict[str, list[tuple[str, str, str]]]) -> None:
        self.entries = entries_by_category
        self.requests: list[httpx.Request] = []
        self.raw_override: dict[str, bytes] = {}  # category -> raw body
        self.status_override: dict[str, int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "export.arxiv.org"
        assert request.url.scheme == "https"
        assert request.url.path == "/api/query"
        self.requests.append(request)
        query = request.url.params["search_query"]
        assert query.startswith("cat:")
        category = query.removeprefix("cat:")
        if category in self.status_override:
            return httpx.Response(self.status_override[category])
        if category in self.raw_override:
            return httpx.Response(200, content=self.raw_override[category])
        start = int(request.url.params["start"])
        size = int(request.url.params["max_results"])
        all_entries = self.entries.get(category, [])
        body = atom_feed(all_entries[start : start + size], total=len(all_entries))
        return httpx.Response(200, content=body, headers={"content-type": "application/atom+xml"})

    def starts_for(self, category: str) -> list[int]:
        return [
            int(r.url.params["start"])
            for r in self.requests
            if r.url.params["search_query"] == f"cat:{category}"
        ]


def make_arxiv_http(
    upstream: Callable[[httpx.Request], httpx.Response], limiter: RateLimiter | None = None
) -> SafeHttpClient:
    from media_os_api.research.fetchers.arxiv import ArxivFetcher

    return SafeHttpClient(
        source_code=ArxivFetcher.source_code,
        policy=ArxivFetcher.host_policy,
        rate_limiter=limiter or InMemoryMinIntervalLimiter(sleep=lambda _s: None),
        min_interval_ms=ArxivFetcher.default_min_interval_ms,
        transport=httpx.MockTransport(upstream),
        resolver=lambda _h, _p: ["128.84.21.199"],
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0.01),
        sleep=lambda _s: None,
    )
