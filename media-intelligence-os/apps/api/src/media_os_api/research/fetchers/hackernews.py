"""Hacker News fetcher — official Firebase API (https://github.com/HackerNews/API).

Endpoints (public, no key):
  GET https://hacker-news.firebaseio.com/v0/{topstories|newstories|beststories}.json  → [int, ...]
  GET https://hacker-news.firebaseio.com/v0/item/{id}.json                           → item | null

Behaviour:
* Reads the first `max_items_per_stream` ids of each configured list (default 30, hard cap 100).
* Skips ids already processed for that stream (bounded `seen_ids` window in the checkpoint), so a
  steady-state hourly run costs one list request per stream plus only genuinely new items.
* Item documents fetched once per run are reused across streams (top/best overlap heavily).
* Per-item isolation: a transient item failure is skipped and retried next run (not marked seen); a
  permanent one (404, oversize) is marked seen so it is not retried forever; if *every* item fetch
  in a stream fails, the stream fails with the first error.
* Only live `type == "story"` items become candidates; jobs, polls, comments, dead or deleted items
  are filtered (not counted as malformed).
* Firebase does not provide ETag/Last-Modified without a non-standard header, so no conditional
  requests are used for this source; the seen-window provides the equivalent saving.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar
from urllib.parse import urlsplit

from media_os_api.research.connectors.hackernews import HackerNewsConnector
from media_os_api.research.fetchers.base import FetchPage, SourceFetcher, StreamSpec
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.errors import PermanentFetchError, RetryableFetchError
from media_os_api.research.http.policy import HostPolicy
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState

API_BASE = "https://hacker-news.firebaseio.com/v0"
ALLOWED_STREAMS = ("topstories", "newstories", "beststories")
DEFAULT_MAX_ITEMS = 30
HARD_MAX_ITEMS = 100
SEEN_WINDOW = 1000
MAX_TITLE_CHARS = 512
MAX_URL_CHARS = 2048


class HackerNewsFetcher(SourceFetcher):
    source_code: ClassVar[str] = "HACKERNEWS"
    host_policy: ClassVar[HostPolicy] = HostPolicy(
        allowed_hosts=frozenset({"hacker-news.firebaseio.com"}),
        max_response_bytes=1024 * 1024,
    )
    allowed_namespaces: ClassVar[frozenset[str]] = frozenset({ResourceNamespace.HACKERNEWS_STORY})
    default_min_interval_ms: ClassVar[int] = 250
    max_pages_per_stream: ClassVar[int] = 1

    def __init__(self) -> None:
        # Per-run cache; construct one fetcher per run (the job handler does).
        self._item_cache: dict[int, Mapping[str, Any] | None] = {}
        self._parser = HackerNewsConnector()

    # ------------------------------------------------------------------ configuration

    def streams(self, config: Mapping[str, Any]) -> list[StreamSpec]:
        configured = config.get("streams") or list(ALLOWED_STREAMS)
        names = [str(s) for s in configured if str(s) in ALLOWED_STREAMS]
        return [StreamSpec(name=n) for n in dict.fromkeys(names)]

    @staticmethod
    def max_items(config: Mapping[str, Any]) -> int:
        try:
            value = int(config.get("max_items_per_stream", DEFAULT_MAX_ITEMS))
        except (TypeError, ValueError):
            value = DEFAULT_MAX_ITEMS
        return max(1, min(value, HARD_MAX_ITEMS))

    # ------------------------------------------------------------------ fetch

    def fetch_page(
        self,
        http: SafeHttpClient,
        stream: StreamSpec,
        checkpoint: CheckpointState,
        config: Mapping[str, Any],
    ) -> FetchPage:
        listing = http.get(f"{API_BASE}/{stream.name}.json", accept="application/json")
        ids = self._parse_id_list(listing.body)[: self.max_items(config)]

        seen: list[int] = [
            int(i) for i in checkpoint.cursor.get("seen_ids", []) if isinstance(i, int)
        ]
        seen_set = set(seen)
        records: list[Mapping[str, Any]] = []
        newly_seen: list[int] = []
        errors: dict[str, int] = {}
        first_error: Exception | None = None
        attempted = 0

        for item_id in ids:
            if item_id in seen_set:
                continue
            attempted += 1
            try:
                item = self._get_item(http, item_id)
            except RetryableFetchError as exc:
                errors[exc.code] = errors.get(exc.code, 0) + 1
                first_error = first_error or exc
                continue  # not marked seen: retried next run
            except PermanentFetchError as exc:
                errors[exc.code] = errors.get(exc.code, 0) + 1
                first_error = first_error or exc
                newly_seen.append(item_id)  # will not succeed later; do not retry forever
                continue
            newly_seen.append(item_id)
            if item is not None:
                records.append(item)

        if attempted and first_error is not None and sum(errors.values()) == attempted:
            raise first_error  # every item failed: surface as a stream failure

        window = (newly_seen + [i for i in seen if i not in set(newly_seen)])[:SEEN_WINDOW]
        cursor = {"seen_ids": window, "last_list_size": len(ids)}
        return FetchPage(
            records=records,
            checkpoint=CheckpointState(cursor=cursor),
            fetch_errors=errors,
        )

    def _get_item(self, http: SafeHttpClient, item_id: int) -> Mapping[str, Any] | None:
        if item_id in self._item_cache:
            return self._item_cache[item_id]
        result = http.get(f"{API_BASE}/item/{item_id}.json", accept="application/json")
        try:
            doc = json.loads(result.body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise PermanentFetchError(
                f"Item {item_id} is not valid JSON.", code="MALFORMED_ITEM"
            ) from exc
        if doc is not None and not isinstance(doc, dict):
            raise PermanentFetchError(f"Item {item_id} is not an object.", code="MALFORMED_ITEM")
        self._item_cache[item_id] = doc
        return doc

    @staticmethod
    def _parse_id_list(body: bytes) -> list[int]:
        doc = json.loads(body)  # ValueError → runner records MALFORMED_DOCUMENT
        if not isinstance(doc, list):
            raise ValueError("Story list is not a JSON array.")
        ids = [i for i in doc if isinstance(i, int) and not isinstance(i, bool) and i > 0]
        if len(ids) != len(doc):
            raise ValueError("Story list contains non-integer ids.")
        return ids

    # ------------------------------------------------------------------ validate + normalise

    def to_candidates(self, record: Mapping[str, Any]) -> list[RawDiscoveryCandidate]:
        item_id = record.get("id")
        if not isinstance(item_id, int) or isinstance(item_id, bool) or item_id <= 0:
            raise ValueError("Item id must be a positive integer.")
        if record.get("type") != "story" or record.get("dead") or record.get("deleted"):
            return []  # not a live story: filtered, not malformed

        title = record.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"Story {item_id} has no title.")
        time_val = record.get("time")
        if time_val is not None and (not isinstance(time_val, int) or isinstance(time_val, bool)):
            raise ValueError(f"Story {item_id} has a non-integer timestamp.")

        cleaned: dict[str, Any] = {
            "id": item_id,
            "title": title.strip()[:MAX_TITLE_CHARS],
            "time": time_val,
            "text": record.get("text") if isinstance(record.get("text"), str) else None,
            "score": record.get("score") if isinstance(record.get("score"), int) else 0,
            "descendants": (
                record.get("descendants") if isinstance(record.get("descendants"), int) else 0
            ),
            "by": record.get("by") if isinstance(record.get("by"), str) else None,
        }
        url = record.get("url")
        if isinstance(url, str) and self._is_web_url(url):
            cleaned["url"] = url.strip()
        # else: fall back to the HN discussion URL (connector default)
        return [self._parser.parse_payload(cleaned)]

    @staticmethod
    def _is_web_url(url: str) -> bool:
        candidate = url.strip()
        if not candidate or len(candidate) > MAX_URL_CHARS:
            return False
        try:
            parts = urlsplit(candidate)
        except ValueError:
            return False
        return parts.scheme in {"http", "https"} and bool(parts.netloc)
