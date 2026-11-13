"""GitHub repository and release discovery connector."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from media_os_api.research.connectors.base import BaseConnector
from media_os_api.research.models import RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace


class GitHubConnector(BaseConnector):
    """Connector for official GitHub repositories and tagged releases."""

    provider_namespace: str = ResourceNamespace.GITHUB_REPOSITORY.value
    rate_limit_per_minute = 30

    def __init__(
        self,
        resource_type: str = ResourceNamespace.GITHUB_REPOSITORY.value,
        policy_mode: Any = None,
    ) -> None:
        self.provider_namespace = resource_type
        super().__init__(policy_mode)

    def parse_payload(self, raw_item: dict[str, Any]) -> RawDiscoveryCandidate:
        owner_repo = str(raw_item.get("full_name") or raw_item.get("repo") or "").strip()
        if not owner_repo or "/" not in owner_repo:
            raise ValueError("GitHub payload missing valid 'owner/repo' identifier.")

        is_release = self.provider_namespace == ResourceNamespace.GITHUB_RELEASE
        tag_name = str(raw_item.get("tag_name") or raw_item.get("version") or "").strip()

        if is_release:
            if not tag_name:
                raise ValueError(f"GitHub release for {owner_repo} missing tag name.")
            external_id = f"{owner_repo}:{tag_name}"
            raw_url = str(
                raw_item.get("html_url")
                or f"https://github.com/{owner_repo}/releases/tag/{tag_name}"
            )
            title = str(
                raw_item.get("title") or raw_item.get("name") or f"{owner_repo} {tag_name} Release"
            ).strip()
            source_label = "GitHub Release"
        else:
            external_id = owner_repo
            raw_url = str(raw_item.get("html_url") or f"https://github.com/{owner_repo}")
            title = str(
                raw_item.get("title")
                or f"{owner_repo}: {raw_item.get('description', 'Repository')}"
            ).strip()
            source_label = "GitHub Repository"

        published_raw = raw_item.get("published_at") or raw_item.get("created_at")
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
            external_id=external_id,
            raw_url=raw_url,
            title=title,
            source_label=source_label,
            category=str(raw_item.get("category") or "Developer Tools"),
            published_at=published_at,
            summary=raw_item.get("description") or raw_item.get("body"),
            version=tag_name if is_release else None,
            metadata={
                "stars": raw_item.get("stargazers_count", 0),
                "language": raw_item.get("language"),
                "license": raw_item.get("license"),
            },
        )
