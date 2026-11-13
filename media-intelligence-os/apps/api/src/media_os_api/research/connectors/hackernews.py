"""Hacker News top/best technical story discovery connector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from media_os_api.research.connectors.base import BaseConnector
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace


class HackerNewsConnector(BaseConnector):
    """Connector for official Hacker News stories (Firebase/Algolia API)."""

    provider_namespace = ResourceNamespace.HACKERNEWS_STORY
    rate_limit_per_minute = 60

    def parse_payload(self, raw_item: dict[str, Any]) -> RawDiscoveryCandidate:
        item_id = str(raw_item.get("id") or "").strip()
        if not item_id:
            raise ValueError("Hacker News item missing ID.")

        title = str(raw_item.get("title") or "").strip()
        if not title:
            raise ValueError(f"Hacker News story {item_id} missing title.")

        raw_url = str(
            raw_item.get("url") or f"https://news.ycombinator.com/item?id={item_id}"
        ).strip()

        time_val = raw_item.get("time")
        published_at: datetime | None = None
        if isinstance(time_val, (int, float)):
            published_at = datetime.fromtimestamp(time_val, tz=UTC)
        elif isinstance(time_val, str):
            try:
                published_at = datetime.fromisoformat(time_val)
            except ValueError:
                published_at = datetime.now(UTC)

        return RawDiscoveryCandidate(
            source_provider=self.provider_namespace,
            external_id=item_id,
            raw_url=raw_url,
            title=title,
            source_label="Hacker News Story",
            category=str(raw_item.get("category") or "Tech & Engineering"),
            published_at=published_at,
            summary=raw_item.get("text"),  # transient text, sanitized at ingestion
            metadata={
                "score": raw_item.get("score", 0),
                "comments_count": raw_item.get("descendants", 0),
                "by": raw_item.get("by"),
            },
        )
