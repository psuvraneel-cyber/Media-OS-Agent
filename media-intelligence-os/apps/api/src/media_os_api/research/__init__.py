"""Research candidate discovery, normalization, and ingestion for Media Intelligence OS."""

from __future__ import annotations

from media_os_api.research.connectors import (
    ArxivConnector,
    BaseConnector,
    GitHubConnector,
    HackerNewsConnector,
    HuggingFaceConnector,
    OpenAINewsConnector,
    RedditConnector,
)
from media_os_api.research.models import (
    IngestionError,
    IngestionResult,
    PolicyBlockedError,
    RawDiscoveryCandidate,
    ResourceNamespaceError,
)
from media_os_api.research.namespaces import (
    AUTHORIZED_NAMESPACES,
    BLOCKED_NAMESPACES,
    ResourceNamespace,
    SourcePolicyMode,
)
from media_os_api.research.sanitization import (
    compute_dedupe_key,
    parse_arxiv_id,
    sanitize_minimal_summary,
)

__all__ = [
    "AUTHORIZED_NAMESPACES",
    "ArxivConnector",
    "BLOCKED_NAMESPACES",
    "BaseConnector",
    "GitHubConnector",
    "HackerNewsConnector",
    "HuggingFaceConnector",
    "IngestionError",
    "IngestionResult",
    "OpenAINewsConnector",
    "PolicyBlockedError",
    "RawDiscoveryCandidate",
    "RedditConnector",
    "ResourceNamespace",
    "ResourceNamespaceError",
    "SourcePolicyMode",
    "compute_dedupe_key",
    "parse_arxiv_id",
    "sanitize_minimal_summary",
]
