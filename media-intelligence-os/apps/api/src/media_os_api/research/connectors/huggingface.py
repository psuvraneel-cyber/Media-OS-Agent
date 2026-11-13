"""Hugging Face model, dataset, and space discovery connector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from media_os_api.research.connectors.base import BaseConnector
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace


class HuggingFaceConnector(BaseConnector):
    """Connector for Hugging Face Hub models, datasets, and spaces."""

    provider_namespace: str = ResourceNamespace.HUGGINGFACE_MODEL.value
    rate_limit_per_minute = 60

    def __init__(
        self,
        resource_type: str = ResourceNamespace.HUGGINGFACE_MODEL.value,
        policy_mode: Any = None,
    ) -> None:
        self.provider_namespace = resource_type
        super().__init__(policy_mode)

    def parse_payload(self, raw_item: dict[str, Any]) -> RawDiscoveryCandidate:
        model_id = str(raw_item.get("id") or raw_item.get("modelId") or "").strip()
        if not model_id:
            raise ValueError("Hugging Face payload missing resource ID.")

        sub_type = self.provider_namespace.replace("HUGGINGFACE_", "").lower()
        if sub_type == "model":
            raw_url = f"https://huggingface.co/{model_id}"
            source_label = "Hugging Face Model"
        elif sub_type == "dataset":
            raw_url = f"https://huggingface.co/datasets/{model_id}"
            source_label = "Hugging Face Dataset"
        elif sub_type == "space":
            raw_url = f"https://huggingface.co/spaces/{model_id}"
            source_label = "Hugging Face Space"
        else:
            raw_url = f"https://huggingface.co/{model_id}"
            source_label = "Hugging Face Resource"

        title = str(raw_item.get("title") or f"{model_id} ({source_label})").strip()

        published_raw = raw_item.get("created_at") or raw_item.get("lastModified")
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
            external_id=model_id,
            raw_url=raw_url,
            title=title,
            source_label=source_label,
            category=str(raw_item.get("category") or "AI Models"),
            published_at=published_at,
            summary=raw_item.get("description") or raw_item.get("pipeline_tag"),
            metadata={
                "downloads": raw_item.get("downloads", 0),
                "likes": raw_item.get("likes", 0),
                "pipeline_tag": raw_item.get("pipeline_tag"),
            },
        )
