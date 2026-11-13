"""Data transfer models and exceptions for Phase 5 research candidate ingestion."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class IngestionError(Exception):
    """Base exception for research candidate ingestion failures."""


class PolicyBlockedError(IngestionError):
    """Raised when an ingestion candidate violates source policy (e.g., Reddit)."""


class ResourceNamespaceError(IngestionError):
    """Raised when an unknown or unauthorized resource namespace is provided."""


@dataclass(frozen=True)
class RawDiscoveryCandidate:
    """Transient, unpersisted research candidate fetched from an external source."""

    source_provider: str
    external_id: str
    raw_url: str
    title: str
    source_label: str
    category: str
    published_at: datetime | None = None
    event_at: datetime | None = None
    summary: str | None = None
    version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestionResult:
    """Outcome of ingesting a candidate into SourceItem, TopicCluster, and BrandTopicAssignment."""

    source_item_id: uuid.UUID
    topic_cluster_id: uuid.UUID
    assignments_created: int
    is_new_source: bool
    is_new_cluster: bool
    source_provider: str
    external_id: str
    dedupe_key: str
