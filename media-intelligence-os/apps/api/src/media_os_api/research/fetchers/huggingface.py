"""Hugging Face fetcher — official Hub API, public metadata only (https://huggingface.co/docs/hub/api).

Endpoints (public, no token):
  GET https://huggingface.co/api/models?sort=<s>&direction=-1&limit=<n>
  GET https://huggingface.co/api/datasets?sort=<s>&direction=-1&limit=<n>
  GET https://huggingface.co/api/spaces?sort=<s>&direction=-1&limit=<n>

Behaviour:
* One stream per configured resource type (models, datasets, spaces). Default sort is
  `trendingScore` (what is gaining attention now); config may choose another allowed sort key.
* Listing metadata only: no model weights, dataset files or Space code are ever requested, and the
  allowlist contains only `huggingface.co`.
* Private or disabled entries are filtered; ids are validated and URLs rebuilt from them.
* A bounded `seen` window per stream skips resources already processed; ≤ 30 per stream per run.
* The listing API offers no ETag-based change detection worth relying on, so the seen window is the
  incremental mechanism.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, ClassVar

from media_os_api.research.connectors.huggingface import HuggingFaceConnector
from media_os_api.research.fetchers.base import FetchPage, SourceFetcher, StreamSpec
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.policy import HostPolicy
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.registry import CheckpointState

API_BASE = "https://huggingface.co/api"
STREAM_NAMESPACES: dict[str, str] = {
    "models": ResourceNamespace.HUGGINGFACE_MODEL,
    "datasets": ResourceNamespace.HUGGINGFACE_DATASET,
    "spaces": ResourceNamespace.HUGGINGFACE_SPACE,
}
ALLOWED_SORTS = frozenset({"trendingScore", "lastModified", "likes", "downloads", "createdAt"})
DEFAULT_SORT = "trendingScore"
DEFAULT_LIMIT = 30
HARD_MAX_LIMIT = 100
SEEN_WINDOW = 1000
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?$")


class HuggingFaceFetcher(SourceFetcher):
    source_code: ClassVar[str] = "HUGGINGFACE"
    host_policy: ClassVar[HostPolicy] = HostPolicy(
        allowed_hosts=frozenset({"huggingface.co"}),
        max_response_bytes=2 * 1024 * 1024,
    )
    allowed_namespaces: ClassVar[frozenset[str]] = frozenset(STREAM_NAMESPACES.values())
    default_min_interval_ms: ClassVar[int] = 1000
    max_pages_per_stream: ClassVar[int] = 1

    def __init__(self) -> None:
        self._parsers = {
            name: HuggingFaceConnector(resource_type=ns) for name, ns in STREAM_NAMESPACES.items()
        }

    def streams(self, config: Mapping[str, Any]) -> list[StreamSpec]:
        configured = config.get("streams") or list(STREAM_NAMESPACES)
        names = [str(s) for s in configured if str(s) in STREAM_NAMESPACES]
        return [StreamSpec(name=n) for n in dict.fromkeys(names)]

    @staticmethod
    def limit(config: Mapping[str, Any]) -> int:
        try:
            value = int(config.get("limit", DEFAULT_LIMIT))
        except (TypeError, ValueError):
            value = DEFAULT_LIMIT
        return max(1, min(value, HARD_MAX_LIMIT))

    @staticmethod
    def sort_key(config: Mapping[str, Any]) -> str:
        value = str(config.get("sort") or DEFAULT_SORT)
        return value if value in ALLOWED_SORTS else DEFAULT_SORT

    def bounded_config(
        self, config: Mapping[str, Any], stream: StreamSpec, max_items: int
    ) -> dict[str, Any]:
        return {**config, "streams": [stream.name], "limit": max_items}

    def fetch_page(
        self,
        http: SafeHttpClient,
        stream: StreamSpec,
        checkpoint: CheckpointState,
        config: Mapping[str, Any],
    ) -> FetchPage:
        limit = self.limit(config)
        result = http.get(
            f"{API_BASE}/{stream.name}",
            params={"sort": self.sort_key(config), "direction": -1, "limit": limit},
            accept="application/json",
        )
        try:
            doc = json.loads(result.body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Hugging Face listing is not valid JSON.") from exc
        if not isinstance(doc, list):
            raise ValueError("Hugging Face listing is not a JSON array.")

        seen = [s for s in checkpoint.cursor.get("seen", []) if isinstance(s, str)]
        seen_set = set(seen)
        records: list[Mapping[str, Any]] = []
        new_ids: list[str] = []
        for entry in doc[:limit]:
            if not isinstance(entry, dict):
                records.append({"_stream": stream.name})  # → malformed in to_candidates
                continue
            rid = str(entry.get("id") or "")
            if rid and rid.lower() in seen_set:
                continue
            records.append({"_stream": stream.name, **entry})
            if rid:
                new_ids.append(rid.lower())
        window = (new_ids + [s for s in seen if s not in set(new_ids)])[:SEEN_WINDOW]
        return FetchPage(records=records, checkpoint=CheckpointState(cursor={"seen": window}))

    def to_candidates(self, record: Mapping[str, Any]) -> list[RawDiscoveryCandidate]:
        stream = str(record.get("_stream") or "")
        if stream not in STREAM_NAMESPACES:
            raise ValueError("Record has no known Hugging Face resource type.")
        rid = str(record.get("id") or "")
        if not _ID_PATTERN.match(rid):
            raise ValueError("Hugging Face resource id is missing or invalid.")
        if record.get("private") or record.get("disabled"):
            return []
        created = record.get("createdAt") or record.get("lastModified")
        if not isinstance(created, str):
            raise ValueError(f"Hugging Face resource {rid} has no timestamp.")
        pipeline = record.get("pipeline_tag")
        payload = {
            "id": rid,
            "created_at": created,
            "pipeline_tag": pipeline if isinstance(pipeline, str) else None,
            "description": None,  # listing has no description; pipeline tag is the summary
            "downloads": record.get("downloads") if isinstance(record.get("downloads"), int) else 0,
            "likes": record.get("likes") if isinstance(record.get("likes"), int) else 0,
            "category": "AI Models",
        }
        return [self._parsers[stream].parse_payload(payload)]
