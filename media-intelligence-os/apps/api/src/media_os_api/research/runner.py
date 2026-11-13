"""Shared research ingestion runner: gate, fetch, validate, normalise, ingest, checkpoint, health.

Transaction and isolation model:

* The live-fetch gate is evaluated first; a refusal raises before any HTTP request is made and does
  not touch source health.
* Network I/O never happens inside a database transaction.
* Each fetched page is committed in **one transaction together with its checkpoint**, so a crash
  re-fetches at most the uncommitted page (deduplication absorbs the repeat).
* Each record is ingested inside a SAVEPOINT: a malformed record is counted and skipped.
* Each stream is isolated: a failing stream is recorded and the remaining streams still run.
* Database errors are not swallowed — they abort the run and surface as retryable job failures.
* Health is recorded once per run: all streams OK → HEALTHY; some failed → DEGRADED (no backoff);
  all failed → DEGRADED/FAILING with backoff, then `SOURCE_FETCH_FAILED` (permanent for this job;
  the next scheduled run retries after the per-source backoff).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from media_os_api.db.session import session_scope
from media_os_api.jobs.errors import PermanentTaskError, TaskExecutionError
from media_os_api.research.fetchers.base import FetchPage, SourceFetcher, StreamSpec
from media_os_api.research.http.client import SafeHttpClient
from media_os_api.research.http.errors import RetryableFetchError
from media_os_api.research.models import IngestionError
from media_os_api.research.registry import (
    assert_live_fetch_permitted,
    get_research_source,
    live_fetch_env_enabled,
    load_checkpoint,
    record_fetch_failure,
    record_fetch_success,
    save_checkpoint,
)
from media_os_api.services.research_service import ingest_discovery_candidate

logger = logging.getLogger("media_os_api.research.runner")

HttpFactory = Callable[[SourceFetcher, Mapping[str, Any]], SafeHttpClient]


@dataclass
class StreamOutcome:
    name: str
    status: str = "pending"  # ok | not_modified | failed
    pages: int = 0
    records_seen: int = 0
    candidates_ingested: int = 0
    new_sources: int = 0
    new_clusters: int = 0
    assignments_created: int = 0
    malformed: int = 0
    malformed_codes: dict[str, int] = field(default_factory=dict)
    fetch_errors: dict[str, int] = field(default_factory=dict)
    error_code: str | None = None
    retry_after_seconds: float | None = None


@dataclass
class RunOutcome:
    source_code: str
    started_at: str
    status: str = "PENDING"  # SUCCEEDED | PARTIAL | FAILED
    http_requests: int = 0
    streams: list[StreamOutcome] = field(default_factory=list)

    def totals(self) -> dict[str, int]:
        keys = (
            "records_seen",
            "candidates_ingested",
            "new_sources",
            "new_clusters",
            "assignments_created",
            "malformed",
            "pages",
        )
        return {k: sum(int(getattr(s, k)) for s in self.streams) for k in keys}

    def stats(self) -> dict[str, Any]:
        """Compact, content-free summary stored in `research_sources.last_run_stats`."""
        return {
            "status": self.status,
            "started_at": self.started_at,
            "http_requests": self.http_requests,
            "totals": self.totals(),
            "streams": {
                s.name: {
                    "status": s.status,
                    "error_code": s.error_code,
                    "pages": s.pages,
                    "new_sources": s.new_sources,
                    "malformed": s.malformed,
                    "fetch_errors": s.fetch_errors,
                }
                for s in self.streams
            },
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self.stats(), "stream_details": [asdict(s) for s in self.streams]}


def _ingest_page(
    session: Session, fetcher: SourceFetcher, page: FetchPage, outcome: StreamOutcome
) -> None:
    for record in page.records:
        outcome.records_seen += 1
        try:
            with session.begin_nested():
                candidates = fetcher.to_candidates(record)
                for candidate in candidates:
                    if candidate.source_provider not in fetcher.allowed_namespaces:
                        raise ValueError(
                            f"Fetcher emitted unexpected namespace '{candidate.source_provider}'."
                        )
                    res = ingest_discovery_candidate(session, candidate)
                    outcome.candidates_ingested += 1
                    outcome.new_sources += int(res.is_new_source)
                    outcome.new_clusters += int(res.is_new_cluster)
                    outcome.assignments_created += res.assignments_created
        except (ValueError, TypeError, KeyError, IngestionError) as exc:
            # IngestionError covers PolicyBlockedError: blocked records are rejected, never stored.
            outcome.malformed += 1
            code = type(exc).__name__
            outcome.malformed_codes[code] = outcome.malformed_codes.get(code, 0) + 1


def _run_stream(
    session_factory: sessionmaker[Session],
    fetcher: SourceFetcher,
    http: SafeHttpClient,
    stream: StreamSpec,
    config: Mapping[str, Any],
    now: Callable[[], datetime],
    outcome: StreamOutcome,
) -> None:
    """Mutates `outcome` so committed-page counts survive a later page's failure."""
    with session_scope(session_factory) as session:
        checkpoint = load_checkpoint(session, fetcher.source_code, stream.name)

    for _ in range(max(fetcher.max_pages_per_stream, 1)):
        page = fetcher.fetch_page(http, stream, checkpoint, config)  # network: outside any tx
        outcome.pages += 1
        for code, count in page.fetch_errors.items():
            outcome.fetch_errors[code] = outcome.fetch_errors.get(code, 0) + int(count)
        with session_scope(session_factory) as session:
            _ingest_page(session, fetcher, page, outcome)
            save_checkpoint(session, fetcher.source_code, stream.name, page.checkpoint, now=now())
        checkpoint = page.checkpoint
        if page.not_modified:
            outcome.status = "not_modified"
            return
        if not page.has_more:
            break
    outcome.status = "ok"


def run_source_fetch(
    session_factory: sessionmaker[Session],
    fetcher: SourceFetcher,
    *,
    http_factory: HttpFactory,
    env_enabled: bool | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RunOutcome:
    """Run one ingestion pass for `fetcher.source_code`. See module docstring for guarantees."""
    started = now()
    outcome = RunOutcome(source_code=fetcher.source_code, started_at=started.isoformat())
    enabled = live_fetch_env_enabled() if env_enabled is None else env_enabled

    with session_scope(session_factory) as session:
        source = get_research_source(session, fetcher.source_code)
        assert_live_fetch_permitted(source, env_enabled=enabled, now=started)  # before any HTTP
        config: dict[str, Any] = dict(source.config or {})

    streams = fetcher.streams(config)
    if not streams:
        raise PermanentTaskError(
            f"No streams configured for '{fetcher.source_code}'.", code="NO_STREAMS_CONFIGURED"
        )

    http = http_factory(fetcher, config)
    try:
        for stream in streams:
            stream_outcome = StreamOutcome(name=stream.name)
            outcome.streams.append(stream_outcome)
            try:
                _run_stream(session_factory, fetcher, http, stream, config, now, stream_outcome)
            except SQLAlchemyError:
                raise  # our own persistence is down: abort the run, let the ledger retry
            except Exception as exc:  # noqa: BLE001 - per-stream isolation is the requirement
                failed = stream_outcome
                failed.status = "failed"
                if isinstance(exc, TaskExecutionError):
                    failed.error_code = getattr(exc, "code", type(exc).__name__)
                elif isinstance(exc, ValueError):
                    failed.error_code = "MALFORMED_DOCUMENT"
                else:
                    failed.error_code = f"UNEXPECTED_{type(exc).__name__}"[:64]
                if isinstance(exc, RetryableFetchError) and exc.retry_after is not None:
                    failed.retry_after_seconds = exc.retry_after.total_seconds()
                logger.warning(
                    "research_stream_failed",
                    extra={
                        "source": fetcher.source_code,
                        "stream": stream.name,
                        "error_code": failed.error_code,
                    },
                )
    finally:
        outcome.http_requests = http.request_count
        http.close()

    failed_streams = [s for s in outcome.streams if s.status == "failed"]
    finished = now()
    with session_scope(session_factory) as session:
        if not failed_streams:
            outcome.status = "SUCCEEDED"
            record_fetch_success(session, fetcher.source_code, stats=outcome.stats(), now=finished)
        elif len(failed_streams) < len(outcome.streams):
            outcome.status = "PARTIAL"
            first = failed_streams[0]
            record_fetch_success(
                session,
                fetcher.source_code,
                stats=outcome.stats(),
                now=finished,
                degraded_error=(
                    str(first.error_code),
                    f"{len(failed_streams)}/{len(outcome.streams)} streams failed; "
                    f"first: {first.name} ({first.error_code}).",
                ),
            )
        else:
            outcome.status = "FAILED"
            waits = [s.retry_after_seconds for s in failed_streams if s.retry_after_seconds]
            record_fetch_failure(
                session,
                fetcher.source_code,
                error_code=str(failed_streams[0].error_code),
                message=f"All {len(failed_streams)} streams failed: "
                + ", ".join(f"{s.name}={s.error_code}" for s in failed_streams),
                now=finished,
                stats=outcome.stats(),
                retry_after=timedelta(seconds=max(waits)) if waits else None,
            )

    if outcome.status == "FAILED":
        raise PermanentTaskError(
            f"All streams failed for '{fetcher.source_code}'; per-source backoff recorded.",
            code="SOURCE_FETCH_FAILED",
        )
    return outcome
