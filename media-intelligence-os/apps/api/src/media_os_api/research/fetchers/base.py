"""Common contract every research source fetcher implements.

Responsibilities are split so each stage is independently testable:

* `streams(config)`            — which logical feeds to read (HN top/new/best, arXiv categories);
* `fetch_page(http, ...)`      — **fetch** one page through `SafeHttpClient` and return untrusted
                                 records plus the **checkpoint** to persist once they are committed;
* `to_candidates(record)`      — **validate + normalise** one untrusted record into typed
                                 `RawDiscoveryCandidate`s (raise `ValueError` if malformed).

Deduplication, provenance, brand assignment, checkpoint persistence, rate limiting, retries and
health recording are performed by the shared runner and HTTP layer, never re-implemented per source.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.policy import HostPolicy
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.registry import CheckpointState


@dataclass(frozen=True)
class StreamSpec:
    """One logical feed within a source (also the checkpoint's `stream_name`)."""

    name: str
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FetchPage:
    """Result of fetching one page of a stream. `records` are untrusted parsed data."""

    records: list[Mapping[str, Any]]
    checkpoint: CheckpointState
    not_modified: bool = False
    has_more: bool = False
    # Sub-request failures the fetcher isolated (e.g. one item of a listing), by error code.
    fetch_errors: Mapping[str, int] = field(default_factory=dict)


class SourceFetcher(ABC):
    source_code: ClassVar[str]
    host_policy: ClassVar[HostPolicy]
    # Namespaces this fetcher may emit; anything else is treated as a malformed record.
    allowed_namespaces: ClassVar[frozenset[str]]
    default_min_interval_ms: ClassVar[int] = 1000
    max_pages_per_stream: ClassVar[int] = 5

    @abstractmethod
    def streams(self, config: Mapping[str, Any]) -> list[StreamSpec]: ...

    @abstractmethod
    def fetch_page(
        self,
        http: SafeHttpClient,
        stream: StreamSpec,
        checkpoint: CheckpointState,
        config: Mapping[str, Any],
    ) -> FetchPage: ...

    @abstractmethod
    def to_candidates(self, record: Mapping[str, Any]) -> list[RawDiscoveryCandidate]: ...

    def bounded_config(
        self, config: Mapping[str, Any], stream: StreamSpec, max_items: int
    ) -> dict[str, Any]:
        """Config narrowed to one stream and at most `max_items` items (owner-gated live checks)."""
        return {**config, "streams": [stream.name], "max_items_per_stream": max_items}

    def min_interval_ms(self, config: Mapping[str, Any]) -> int:
        """Registry config may only make spacing *stricter* than the fetcher's floor."""
        try:
            configured = int(config.get("min_interval_ms", 0))
        except (TypeError, ValueError):
            configured = 0
        return max(configured, self.default_min_interval_ms)
