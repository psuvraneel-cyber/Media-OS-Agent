"""Research source fetchers (one module per source; Milestones 4a-4e)."""

from __future__ import annotations

from media_os_api.research.fetchers.arxiv import ArxivFetcher
from media_os_api.research.fetchers.base import FetchPage, SourceFetcher, StreamSpec
from media_os_api.research.fetchers.hackernews import HackerNewsFetcher

# source_code -> fetcher class. Only sources with a complete, tested fetcher are listed.
FETCHERS: dict[str, type[SourceFetcher]] = {
    HackerNewsFetcher.source_code: HackerNewsFetcher,
    ArxivFetcher.source_code: ArxivFetcher,
}

__all__ = [
    "FETCHERS",
    "ArxivFetcher",
    "FetchPage",
    "HackerNewsFetcher",
    "SourceFetcher",
    "StreamSpec",
]
