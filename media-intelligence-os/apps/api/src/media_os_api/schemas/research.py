"""Read-only research and topic contracts (Phase 5, Milestone 6).

Global research (sources, items, clusters) is shared; editorial state is brand-specific and is only
ever returned for the single brand named in the request.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckpointSummary(_Strict):
    stream: str
    last_fetched_at_utc: str | None
    last_success_at_utc: str | None
    conditional_request_ready: bool


class ScheduleSummary(_Strict):
    enabled: bool
    next_run_at_utc: str | None
    cadence: dict[str, Any]


class ResearchSourceSummary(_Strict):
    source: str
    display_name: str
    policy_mode: str
    live_fetch_approved: bool
    approved_at_utc: str | None
    health_status: str
    consecutive_failures: int
    last_attempt_at_utc: str | None
    last_success_at_utc: str | None
    last_failure_at_utc: str | None
    last_error_code: str | None
    backoff_until_utc: str | None
    last_run_totals: dict[str, int]
    schedule: ScheduleSummary | None
    checkpoints: list[CheckpointSummary]


class ResearchSourceListResponse(_Strict):
    live_fetch_kill_switch_enabled: bool
    items: list[ResearchSourceSummary]


class ClusterRef(_Strict):
    topic_cluster_id: str
    dedupe_key: str
    discovery_status: str


class SourceItemSummary(_Strict):
    source_item_id: str
    provider: str
    external_id: str
    title: str
    source_label: str
    canonical_url: str
    published_at_utc: str | None
    fetched_at_utc: str
    summary: str | None
    source_policy_mode: str
    clusters: list[ClusterRef]


class SourceItemListResponse(_Strict):
    total: int
    next_before_utc: str | None
    items: list[SourceItemSummary]


class BrandAssignmentSummary(_Strict):
    brand_id: str
    editorial_status: str
    relevance_score: float | None
    priority: int
    assigned_at_utc: str
    evaluated_at_utc: str | None


class TopicSummary(_Strict):
    topic_cluster_id: str
    title: str
    category: str
    dedupe_key: str
    discovery_status: str
    first_seen_at_utc: str
    last_seen_at_utc: str
    source_count: int
    providers: list[str]
    assignment: BrandAssignmentSummary


class TopicListResponse(_Strict):
    brand_id: str
    total: int
    items: list[TopicSummary]


class TopicDetailResponse(_Strict):
    topic: TopicSummary
    sources: list[SourceItemSummary]
