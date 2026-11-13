"""Resource identity namespaces and source policy modes for Phase 5 research connectors."""

from __future__ import annotations

from enum import StrEnum


class ResourceNamespace(StrEnum):
    """Stable, unambiguous resource type namespaces for external discovery."""

    GITHUB_REPOSITORY = "GITHUB_REPOSITORY"
    GITHUB_RELEASE = "GITHUB_RELEASE"
    HUGGINGFACE_MODEL = "HUGGINGFACE_MODEL"
    HUGGINGFACE_DATASET = "HUGGINGFACE_DATASET"
    HUGGINGFACE_SPACE = "HUGGINGFACE_SPACE"
    HACKERNEWS_STORY = "HACKERNEWS_STORY"
    ARXIV_PAPER = "ARXIV_PAPER"
    OPENAI_NEWS = "OPENAI_NEWS"
    REDDIT_POST = "REDDIT_POST"


class SourcePolicyMode(StrEnum):
    """Lifecycle policy modes for external content connectors."""

    OFF = "OFF"
    EXPERIMENTAL = "EXPERIMENTAL"
    ENABLED = "ENABLED"
    BLOCKED = "BLOCKED"


AUTHORIZED_NAMESPACES: frozenset[str] = frozenset(
    {
        ResourceNamespace.GITHUB_REPOSITORY,
        ResourceNamespace.GITHUB_RELEASE,
        ResourceNamespace.HUGGINGFACE_MODEL,
        ResourceNamespace.HUGGINGFACE_DATASET,
        ResourceNamespace.HUGGINGFACE_SPACE,
        ResourceNamespace.HACKERNEWS_STORY,
        ResourceNamespace.ARXIV_PAPER,
        ResourceNamespace.OPENAI_NEWS,
    }
)

BLOCKED_NAMESPACES: frozenset[str] = frozenset(
    {
        ResourceNamespace.REDDIT_POST,
        "REDDIT",
    }
)
