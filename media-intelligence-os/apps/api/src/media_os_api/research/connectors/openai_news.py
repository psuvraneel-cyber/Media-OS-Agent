"""OpenAI official announcements and research news connector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from media_os_api.research.connectors.base import BaseConnector
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace


class OpenAINewsConnector(BaseConnector):
    """Connector for official OpenAI research and product announcements."""

    provider_namespace = ResourceNamespace.OPENAI_NEWS
    rate_limit_per_minute = 10

    def parse_payload(self, raw_item: dict[str, Any]) -> RawDiscoveryCandidate:
        slug = str(raw_item.get("slug") or raw_item.get("id") or "").strip()
        if not slug:
            raise ValueError("OpenAI news item missing slug or ID.")

        title = str(raw_item.get("title") or "").strip()
        if not title:
            raise ValueError(f"OpenAI news item {slug} missing title.")

        raw_url = str(raw_item.get("url") or f"https://openai.com/index/{slug}").strip()

        published_raw = raw_item.get("published_at") or raw_item.get("date")
        published_at: datetime | None = None
        if isinstance(published_raw, datetime):
            published_at = published_raw
        elif isinstance(published_raw, str):
            try:
                published_at = datetime.fromisoformat(published_raw)
            except ValueError:
                published_at = datetime.now(UTC)

        return RawDiscoveryCandidate(
            source_provider=self.provider_namespace,
            external_id=slug,
            raw_url=raw_url,
            title=title,
            source_label="OpenAI Official News",
            category=str(raw_item.get("category") or "AI Announcements"),
            published_at=published_at,
            summary=raw_item.get("summary") or raw_item.get("snippet"),
            metadata={
                "topic": raw_item.get("topic"),
                "author": raw_item.get("author"),
            },
        )
