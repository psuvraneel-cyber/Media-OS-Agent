"""Data retention policy registry, YouTube-specific rules, and dry-run cleanup engine."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.orm import Session

from media_os_api.config import Settings
from media_os_api.db.models import (
    Asset,
    AuditEvent,
    AuthSession,
    BrandProfile,
    ContentPackage,
    MetricsSnapshot,
    PublishSchedule,
    SourceItem,
)

TERMINAL_CONTENT_STATUSES = frozenset({"ARCHIVED", "REJECTED", "FAILED_FINAL"})
ACTIVE_SCHEDULE_STATUSES = frozenset({"DRAFT", "SCHEDULED", "PUBLISHING", "FAILED_RETRYABLE"})
YOUTUBE_POLICY_MAX_DAYS = 30


@dataclass(frozen=True)
class RetentionPolicyRule:
    """Explicit retention specification for a data class or provider."""

    policy_id: str
    category: str
    max_retention_days: int | None
    verification_interval_days: int | None
    notes: str


@dataclass(frozen=True)
class DiskUsageReport:
    """Storage capacity check for local media volume."""

    path: str
    exists: bool
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: float
    warning_threshold_percent: int
    warning_triggered: bool


@dataclass(frozen=True)
class RetentionReport:
    """Counts and actions from a retention evaluation or execution pass."""

    dry_run: bool
    evaluated_at_utc: str
    expired_auth_sessions: int
    expired_source_items: int
    expired_metrics_snapshots: int
    stale_youtube_authorizations: int
    expired_temp_assets: int
    protected_referenced_assets: int
    expired_audit_events: int
    deleted_auth_sessions: int
    deleted_source_items: int
    deleted_metrics_snapshots: int
    deleted_temp_assets: int
    deleted_audit_events: int
    disk_usage: DiskUsageReport | None


class RetentionPolicyRegistry:
    """Central registry codifying Phase 4 provider and storage retention rules."""

    def __init__(self, settings: Settings | None = None) -> None:
        source_default = settings.retention_source_default_days if settings else 365
        yt_non_auth = min(
            YOUTUBE_POLICY_MAX_DAYS,
            settings.retention_youtube_non_auth_days if settings else 30,
        )
        yt_verify = min(
            YOUTUBE_POLICY_MAX_DAYS,
            settings.retention_youtube_verify_interval_days if settings else 30,
        )
        app_log_days = settings.retention_app_log_days if settings else 30
        audit_days = settings.retention_audit_event_days if settings else 365
        failed_temp_days = settings.retention_failed_temp_media_days if settings else 7

        self._rules: dict[str, RetentionPolicyRule] = {
            "SOURCE_NON_YOUTUBE_DEFAULT": RetentionPolicyRule(
                policy_id="SOURCE_NON_YOUTUBE_DEFAULT",
                category="Research metadata (non-YouTube)",
                max_retention_days=source_default,
                verification_interval_days=None,
                notes="Metadata only; full raw third-party text is discarded post-normalization.",
            ),
            "SOURCE_GOOGLE_TRENDS": RetentionPolicyRule(
                policy_id="SOURCE_GOOGLE_TRENDS",
                category="Google Trends experimental feed",
                max_retention_days=min(source_default, 30),
                verification_interval_days=None,
                notes="Shorter per-provider metadata window for noisy trend feeds.",
            ),
            "YOUTUBE_NON_AUTHORIZED": RetentionPolicyRule(
                policy_id="YOUTUBE_NON_AUTHORIZED",
                category="YouTube non-authorized public API data",
                max_retention_days=yt_non_auth,
                verification_interval_days=yt_verify,
                notes=(
                    "Must be refreshed or deleted within 30 calendar days per YouTube API Policy."
                ),
            ),
            "YOUTUBE_AUTHORIZED": RetentionPolicyRule(
                policy_id="YOUTUBE_AUTHORIZED",
                category="YouTube authorized Analytics/Reporting channel data",
                max_retention_days=None,
                verification_interval_days=yt_verify,
                notes=(
                    "Consent-backed retention; authorization & video status verified every 30 days."
                ),
            ),
            "APPLICATION_LOGS": RetentionPolicyRule(
                policy_id="APPLICATION_LOGS",
                category="General application logs",
                max_retention_days=app_log_days,
                verification_interval_days=None,
                notes="30-day default; zero secrets retained.",
            ),
            "AUDIT_EVENTS": RetentionPolicyRule(
                policy_id="AUDIT_EVENTS",
                category="Security and workflow audit events",
                max_retention_days=audit_days,
                verification_interval_days=None,
                notes="365-day security & audit retention.",
            ),
            "MEDIA_TEMP_SUCCESS": RetentionPolicyRule(
                policy_id="MEDIA_TEMP_SUCCESS",
                category="Successful temporary render intermediates",
                max_retention_days=0,
                verification_interval_days=None,
                notes="Eligible for deletion immediately after final asset QA passes.",
            ),
            "MEDIA_TEMP_FAILED": RetentionPolicyRule(
                policy_id="MEDIA_TEMP_FAILED",
                category="Failed temporary render intermediates",
                max_retention_days=failed_temp_days,
                verification_interval_days=None,
                notes="Retained 7 days for debugging before deletion unless still referenced.",
            ),
            "MEDIA_FINAL": RetentionPolicyRule(
                policy_id="MEDIA_FINAL",
                category="Final generated brand assets",
                max_retention_days=None,
                verification_interval_days=None,
                notes="Retained until explicitly deleted by owner; protected while scheduled.",
            ),
        }

    def all_rules(self) -> list[RetentionPolicyRule]:
        return list(self._rules.values())

    def get(self, policy_id: str) -> RetentionPolicyRule:
        if policy_id not in self._rules:
            raise KeyError(f"Unknown retention policy ID: {policy_id}")
        return self._rules[policy_id]

    def compute_source_expiry(
        self,
        *,
        source_provider: str,
        fetched_at: datetime,
        custom_days: int | None = None,
    ) -> datetime:
        """Compute deterministic metadata expiration respecting provider-specific limits."""
        provider_key = source_provider.strip().upper()
        if "REDDIT" in provider_key:
            raise ValueError("Reddit source ingestion is BLOCKED by policy.")
        if "YOUTUBE" in provider_key:
            max_days = self._rules["YOUTUBE_NON_AUTHORIZED"].max_retention_days or 30
        elif provider_key == "GOOGLE_TRENDS":
            max_days = self._rules["SOURCE_GOOGLE_TRENDS"].max_retention_days or 30
        else:
            max_days = self._rules["SOURCE_NON_YOUTUBE_DEFAULT"].max_retention_days or 365

        if custom_days is not None:
            if custom_days < 1:
                raise ValueError("custom_days must be >= 1")
            effective_days = min(custom_days, max_days)
        else:
            effective_days = max_days
        return fetched_at.astimezone(UTC) + timedelta(days=effective_days)

    def compute_youtube_metrics_expiry(
        self,
        *,
        authorization_class: str,
        fetched_at: datetime,
    ) -> datetime:
        """Enforce YouTube-specific 30-day policy boundaries."""
        utc_fetched = fetched_at.astimezone(UTC)
        if authorization_class == "NON_AUTHORIZED_PUBLIC":
            return utc_fetched + timedelta(
                days=self._rules["YOUTUBE_NON_AUTHORIZED"].max_retention_days or 30
            )
        if authorization_class == "AUTHORIZED_CHANNEL":
            # Verification checkpoint is still every 30 days even for consented data.
            return utc_fetched + timedelta(
                days=self._rules["YOUTUBE_AUTHORIZED"].verification_interval_days or 30
            )
        raise ValueError(f"Invalid authorization_class: {authorization_class}")


def check_media_disk_usage(
    media_dir: Path,
    *,
    warn_percent: int = 80,
) -> DiskUsageReport:
    """Measure disk utilization on the media storage path without raising on missing dir."""
    if not media_dir.exists():
        return DiskUsageReport(
            path=str(media_dir),
            exists=False,
            total_bytes=0,
            used_bytes=0,
            free_bytes=0,
            used_percent=0.0,
            warning_threshold_percent=warn_percent,
            warning_triggered=False,
        )
    usage = shutil.disk_usage(media_dir)
    used_pct = round((usage.used / usage.total) * 100.0, 2) if usage.total > 0 else 0.0
    return DiskUsageReport(
        path=str(media_dir),
        exists=True,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        used_percent=used_pct,
        warning_threshold_percent=warn_percent,
        warning_triggered=used_pct >= float(warn_percent),
    )


def run_retention_evaluation(
    session: Session,
    *,
    dry_run: bool = True,
    actor_id: str = "system:retention",
    now: datetime | None = None,
    media_dir: Path | None = None,
    warn_percent: int = 80,
    audit_retention_days: int = 365,
    request_id: str = "-",
) -> RetentionReport:
    """Evaluate (and optionally purge when `dry_run=False`) expired records safely.

    Never deletes final media assets or temporary assets linked to active/scheduled content.
    """
    current = (now or datetime.now(UTC)).astimezone(UTC)

    expired_session_ids = list(
        session.execute(select(AuthSession.id).where(AuthSession.expires_at <= current))
        .scalars()
        .all()
    )
    expired_source_ids = list(
        session.execute(select(SourceItem.id).where(SourceItem.retention_expires_at <= current))
        .scalars()
        .all()
    )
    expired_metrics_ids = list(
        session.execute(
            select(MetricsSnapshot.id).where(MetricsSnapshot.retention_expires_at <= current)
        )
        .scalars()
        .all()
    )
    stale_yt_cutoff = current - timedelta(days=YOUTUBE_POLICY_MAX_DAYS)
    stale_yt_count = len(
        session.execute(
            select(MetricsSnapshot.id).where(
                MetricsSnapshot.platform == "YOUTUBE",
                (
                    MetricsSnapshot.last_verified_at.is_(None)
                    | (MetricsSnapshot.last_verified_at <= stale_yt_cutoff)
                ),
            )
        )
        .scalars()
        .all()
    )

    # Identify protected assets (referenced by BrandProfile logo or active/scheduled content)
    logo_asset_ids = set(
        session.execute(
            select(BrandProfile.logo_asset_id).where(BrandProfile.logo_asset_id.is_not(None))
        )
        .scalars()
        .all()
    )
    active_pkg_ids = set(
        session.execute(
            select(ContentPackage.id).where(ContentPackage.status.not_in(TERMINAL_CONTENT_STATUSES))
        )
        .scalars()
        .all()
    )
    scheduled_pkg_ids = set(
        session.execute(
            select(PublishSchedule.content_package_id).where(
                PublishSchedule.status.in_(ACTIVE_SCHEDULE_STATUSES)
            )
        )
        .scalars()
        .all()
    )
    protected_pkg_ids = active_pkg_ids | scheduled_pkg_ids

    candidate_temp_assets = (
        session.execute(
            select(Asset).where(
                Asset.status.in_(("TEMP_SUCCESS", "TEMP_FAILED")),
                Asset.retention_expires_at.is_not(None),
                Asset.retention_expires_at <= current,
            )
        )
        .scalars()
        .all()
    )
    deletable_asset_ids: list[Any] = []
    protected_asset_count = 0
    for asset in candidate_temp_assets:
        if asset.id in logo_asset_ids or (
            asset.content_package_id is not None and asset.content_package_id in protected_pkg_ids
        ):
            protected_asset_count += 1
        else:
            deletable_asset_ids.append(asset.id)

    expired_audit_ids = list(
        session.execute(select(AuditEvent.id).where(AuditEvent.retention_expires_at <= current))
        .scalars()
        .all()
    )

    deleted_sessions = 0
    deleted_sources = 0
    deleted_metrics = 0
    deleted_assets = 0
    deleted_audits = 0

    if not dry_run:
        if expired_session_ids:
            res = cast(
                CursorResult[Any],
                session.execute(delete(AuthSession).where(AuthSession.id.in_(expired_session_ids))),
            )
            deleted_sessions = int(res.rowcount or 0)
        if deletable_asset_ids:
            res = cast(
                CursorResult[Any],
                session.execute(delete(Asset).where(Asset.id.in_(deletable_asset_ids))),
            )
            deleted_assets = int(res.rowcount or 0)
        if expired_metrics_ids:
            res = cast(
                CursorResult[Any],
                session.execute(
                    delete(MetricsSnapshot).where(MetricsSnapshot.id.in_(expired_metrics_ids))
                ),
            )
            deleted_metrics = int(res.rowcount or 0)
        if expired_source_ids:
            # Only delete SourceItems not referenced by ClaimSource or TopicClusterSource
            # ForeignKey ondelete="RESTRICT" protects ClaimSource; filter cleanly first
            from media_os_api.db.models import ClaimSource, TopicClusterSource

            referenced_sources = set(
                session.execute(select(ClaimSource.source_item_id)).scalars().all()
            ) | set(session.execute(select(TopicClusterSource.source_item_id)).scalars().all())
            safe_source_ids = [sid for sid in expired_source_ids if sid not in referenced_sources]
            if safe_source_ids:
                res = cast(
                    CursorResult[Any],
                    session.execute(delete(SourceItem).where(SourceItem.id.in_(safe_source_ids))),
                )
                deleted_sources = int(res.rowcount or 0)
        if expired_audit_ids:
            res = cast(
                CursorResult[Any],
                session.execute(delete(AuditEvent).where(AuditEvent.id.in_(expired_audit_ids))),
            )
            deleted_audits = int(res.rowcount or 0)

    disk_report = (
        check_media_disk_usage(media_dir, warn_percent=warn_percent)
        if media_dir is not None
        else None
    )

    summary_metadata = {
        "dry_run": dry_run,
        "expired_auth_sessions": len(expired_session_ids),
        "expired_source_items": len(expired_source_ids),
        "expired_metrics_snapshots": len(expired_metrics_ids),
        "stale_youtube_authorizations": stale_yt_count,
        "expired_temp_assets": len(deletable_asset_ids),
        "protected_referenced_assets": protected_asset_count,
        "expired_audit_events": len(expired_audit_ids),
        "deleted_auth_sessions": deleted_sessions,
        "deleted_source_items": deleted_sources,
        "deleted_metrics_snapshots": deleted_metrics,
        "deleted_temp_assets": deleted_assets,
        "deleted_audit_events": deleted_audits,
        "disk_warning": disk_report.warning_triggered if disk_report else False,
    }
    session.add(
        AuditEvent(
            actor_id=actor_id,
            action="RETENTION_EVALUATION_DRY_RUN" if dry_run else "RETENTION_CLEANUP_EXECUTED",
            target_type="RetentionPolicy",
            target_id="global",
            request_id=request_id,
            outcome="SUCCESS",
            event_metadata=summary_metadata,
            retention_expires_at=current + timedelta(days=audit_retention_days),
        )
    )

    return RetentionReport(
        dry_run=dry_run,
        evaluated_at_utc=current.isoformat(),
        expired_auth_sessions=len(expired_session_ids),
        expired_source_items=len(expired_source_ids),
        expired_metrics_snapshots=len(expired_metrics_ids),
        stale_youtube_authorizations=stale_yt_count,
        expired_temp_assets=len(deletable_asset_ids),
        protected_referenced_assets=protected_asset_count,
        expired_audit_events=len(expired_audit_ids),
        deleted_auth_sessions=deleted_sessions,
        deleted_source_items=deleted_sources,
        deleted_metrics_snapshots=deleted_metrics,
        deleted_temp_assets=deleted_assets,
        deleted_audit_events=deleted_audits,
        disk_usage=disk_report,
    )
