"""Durable Celery task handlers for Phase 5 research connector ingestion.

`media_os.research.ingest` is the *manual* ingestion path: a caller supplies already-fetched records
in the job payload. Because job payloads are persisted in `job_runs`, this module keeps untrusted
third-party content out of the ledger as far as possible:

* `build_manual_ingestion_payload()` sanitises items *before* enqueue (field allowlist, HTML
  stripped, free text capped at 500 chars) and attaches an item digest.
* The handler re-applies the same sanitisation defensively, caps the item count, and on success
  replaces the stored `items` with a count + digest (`redact_items_payload`).
* Malformed records are skipped and counted; they never abort the rest of the batch.

Scheduled source fetchers (Milestones 3-5) never place content in payloads at all.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.orm import Session

from media_os_api.db.models import JobRun
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.jobs.ledger import items_digest, redact_items_payload
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
    PolicyBlockedError,
    RawDiscoveryCandidate,
)
from media_os_api.research.namespaces import ResourceNamespace
from media_os_api.research.sanitization import sanitize_minimal_summary
from media_os_api.services.research_service import ingest_discovery_candidate

logger = logging.getLogger("media_os_api.research.tasks")

MANUAL_INGEST_TASK_NAME = "media_os.research.ingest"
MAX_MANUAL_INGEST_ITEMS = 50
MAX_SCALAR_CHARS = 2048
MAX_LIST_ENTRIES = 50

# Free-text fields that may carry third-party prose; reduced to the discovery summary boundary.
_FREE_TEXT_FIELDS = frozenset({"summary", "abstract", "description", "body", "text", "snippet"})

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


def _sanitize_value(key: str, value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if key in _FREE_TEXT_FIELDS:
            return sanitize_minimal_summary(value, max_length=500)
        return value[:MAX_SCALAR_CHARS]
    if isinstance(value, (list, tuple)):
        return [
            v[:256] if isinstance(v, str) else v
            for v in list(value)[:MAX_LIST_ENTRIES]
            if v is None or isinstance(v, (str, bool, int, float))
        ]
    # Nested objects (e.g. raw API sub-documents) are not needed by any parser's persisted fields.
    return None


def sanitize_manual_items(items: Sequence[Any]) -> list[dict[str, Any]]:
    """Reduce caller-supplied records to bounded, flat, JSON-safe dictionaries."""
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            cleaned.append({})  # kept positionally; rejected as malformed by the handler
            continue
        out: dict[str, Any] = {}
        for k, v in item.items():
            key = str(k)[:64]
            value = _sanitize_value(key, v)
            if value is None and v is not None:
                continue  # dropped (unsupported nested object or empty after sanitising)
            out[key] = value
        cleaned.append(out)
    return cleaned


def build_manual_ingestion_payload(source_provider: str, items: Sequence[Any]) -> dict[str, Any]:
    """Build a safe `media_os.research.ingest` payload; use this instead of passing raw records."""
    if len(items) > MAX_MANUAL_INGEST_ITEMS:
        raise ValueError(f"Manual ingestion is limited to {MAX_MANUAL_INGEST_ITEMS} items per job.")
    sanitized = sanitize_manual_items(items)
    return {
        "source_provider": source_provider,
        "items": sanitized,
        "item_count": len(sanitized),
        "items_sha256": items_digest(sanitized),
    }


def _build_connector(provider: str) -> BaseConnector:
    connector_cls = CONNECTOR_REGISTRY.get(provider)
    if connector_cls is None:
        raise PermanentTaskError(f"No connector registered for source provider '{provider}'.")
    try:
        if provider in {ResourceNamespace.GITHUB_RELEASE, ResourceNamespace.GITHUB_REPOSITORY}:
            return GitHubConnector(resource_type=provider)
        if provider.startswith("HUGGINGFACE_"):
            return HuggingFaceConnector(resource_type=provider)
        return connector_cls()
    except PolicyBlockedError as exc:
        raise PermanentTaskError(str(exc), code="SOURCE_POLICY_BLOCKED") from exc


def handle_research_ingestion(session: Session, job: JobRun) -> dict[str, Any]:
    """Ingest caller-supplied records with per-record isolation and ledger scrubbing."""
    payload = dict(job.payload or {})
    provider = str(payload.get("source_provider") or "").strip()
    raw_items = payload.get("items") or []

    if not provider:
        raise PermanentTaskError("Missing 'source_provider' in research ingestion payload.")
    if not isinstance(raw_items, list):
        raise PermanentTaskError("'items' must be a list.", code="INVALID_PAYLOAD")
    if len(raw_items) > MAX_MANUAL_INGEST_ITEMS:
        raise PermanentTaskError(
            f"Manual ingestion payload exceeds {MAX_MANUAL_INGEST_ITEMS} items.",
            code="PAYLOAD_TOO_LARGE",
        )

    connector = _build_connector(provider)

    processed = new_sources = new_clusters = assignments_created = malformed = 0
    error_codes: dict[str, int] = {}

    for item in sanitize_manual_items(raw_items):
        try:
            with session.begin_nested():
                candidate: RawDiscoveryCandidate = connector.parse_payload(item)
                res = ingest_discovery_candidate(session, candidate)
        except PolicyBlockedError:
            raise
        except (ValueError, TypeError, KeyError, IngestionError) as exc:
            malformed += 1
            code = type(exc).__name__
            error_codes[code] = error_codes.get(code, 0) + 1
            continue
        processed += 1
        new_sources += int(res.is_new_source)
        new_clusters += int(res.is_new_cluster)
        assignments_created += res.assignments_created

    # Raw records are no longer needed once the batch committed; keep only count + digest.
    job.payload = redact_items_payload(payload)

    if malformed:
        logger.warning(
            "research_ingest_malformed_records",
            extra={"source_provider": provider, "malformed": malformed, "codes": error_codes},
        )

    return {
        "status": "success" if not malformed else "partial_success",
        "source_provider": provider,
        "total_items_processed": processed,
        "malformed_items": malformed,
        "malformed_error_codes": error_codes,
        "new_sources": new_sources,
        "new_clusters": new_clusters,
        "assignments_created": assignments_created,
    }
