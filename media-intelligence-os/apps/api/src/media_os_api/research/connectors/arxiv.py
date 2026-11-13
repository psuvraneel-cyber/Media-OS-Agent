"""arXiv research paper discovery connector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from media_os_api.research.connectors.base import BaseConnector
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.sanitization import parse_arxiv_id


class ArxivConnector(BaseConnector):
    """Connector for official arXiv scientific paper discoveries."""

    provider_namespace = ResourceNamespace.ARXIV_PAPER
    rate_limit_per_minute = 20  # arXiv requests 1 req / 3 sec

    def parse_payload(self, raw_item: dict[str, Any]) -> RawDiscoveryCandidate:
        raw_id = str(raw_item.get("id") or raw_item.get("external_id") or "").strip()
        base_id, version = parse_arxiv_id(raw_id)
        if not base_id:
            raise ValueError("arXiv payload missing valid paper identifier.")

        title = str(raw_item.get("title") or "").strip()
        if not title:
            raise ValueError(f"arXiv paper {raw_id} missing title.")

        raw_url = str(
            raw_item.get("url")
            or raw_item.get("canonical_url")
            or f"https://arxiv.org/abs/{raw_id}"
        ).strip()

        published_raw = raw_item.get("published_at") or raw_item.get("published")
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
            external_id=raw_id,
            raw_url=raw_url,
            title=title,
            source_label="arXiv Paper",
            category=str(raw_item.get("category") or "AI Research"),
            published_at=published_at,
            summary=raw_item.get("summary") or raw_item.get("abstract"),
            version=version,
            metadata={
                "base_id": base_id,
                "version": version,
                "primary_category": raw_item.get("primary_category"),
                "authors_count": len(raw_item.get("authors", [])),
            },
        )
