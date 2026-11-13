"""Durable Celery task handlers for Phase 5 research connector ingestion."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from media_os_api.db.models import JobRun
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.research.connectors import (
    ArxivConnector,
    BaseConnector,
    GitHubConnector,
    HackerNewsConnector,
    HuggingFaceConnector,
    OpenAINewsConnector,
    RedditConnector,
)
from media_os_api.research.models import PolicyBlockedError, RawDiscoveryCandidate
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.services.research_service import ingest_discovery_candidate

logger = logging.getLogger("media_os_api.research.tasks")

CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    ResourceNamespace.ARXIV_PAPER: ArxivConnector,
    ResourceNamespace.GITHUB_REPOSITORY: GitHubConnector,
    ResourceNamespace.GITHUB_RELEASE: GitHubConnector,
    ResourceNamespace.HUGGINGFACE_MODEL: HuggingFaceConnector,
    ResourceNamespace.HUGGINGFACE_DATASET: HuggingFaceConnector,
    ResourceNamespace.HUGGINGFACE_SPACE: HuggingFaceConnector,
    ResourceNamespace.HACKERNEWS_STORY: HackerNewsConnector,
    ResourceNamespace.OPENAI_NEWS: OpenAINewsConnector,
    ResourceNamespace.REDDIT_POST: RedditConnector,
}


def handle_research_ingestion(session: Session, job: JobRun) -> dict[str, Any]:
    """Execute durable connector candidate ingestion with full idempotency."""
    payload = dict(job.payload or {})
    provider = str(payload.get("source_provider") or "").strip()
    raw_items = payload.get("items") or []

    if not provider:
        raise PermanentTaskError("Missing 'source_provider' in research ingestion payload.")

    connector_cls = CONNECTOR_REGISTRY.get(provider)
    if connector_cls is None:
        raise PermanentTaskError(f"No connector registered for source provider '{provider}'.")

    try:
        connector: BaseConnector
        if provider in {ResourceNamespace.GITHUB_RELEASE, ResourceNamespace.GITHUB_REPOSITORY}:
            connector = GitHubConnector(resource_type=provider)
        elif provider.startswith("HUGGINGFACE_"):
            connector = HuggingFaceConnector(resource_type=provider)
        else:
            connector = connector_cls()
    except PolicyBlockedError as exc:
        raise PermanentTaskError(str(exc), code="SOURCE_POLICY_BLOCKED") from exc

    ingested_count = 0
    new_sources = 0
    new_clusters = 0
    assignments_created = 0

    for item in raw_items:
        candidate: RawDiscoveryCandidate = connector.parse_payload(item)
        res = ingest_discovery_candidate(session, candidate)
        ingested_count += 1
        if res.is_new_source:
            new_sources += 1
        if res.is_new_cluster:
            new_clusters += 1
        assignments_created += res.assignments_created

    return {
        "status": "success",
        "source_provider": provider,
        "total_items_processed": ingested_count,
        "new_sources": new_sources,
        "new_clusters": new_clusters,
        "assignments_created": assignments_created,
    }
