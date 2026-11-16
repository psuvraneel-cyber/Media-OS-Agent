"""Postiz-reading jobs must outlast a slow Postiz start instead of failing within seconds."""

from __future__ import annotations

from media_os_api.analytics.sync import SYNC_METRICS_TASK
from media_os_api.jobs.errors import compute_backoff_seconds
from media_os_api.jobs.routing import (
    DEFAULT_RETRY_POLICY,
    POSTIZ_READ_RETRY_POLICY,
    retry_policy_for_task,
)
from media_os_api.publish.service import SYNC_TASK


def test_postiz_read_jobs_get_the_patient_policy() -> None:
    assert retry_policy_for_task(SYNC_METRICS_TASK) == POSTIZ_READ_RETRY_POLICY
    assert retry_policy_for_task(SYNC_TASK) == POSTIZ_READ_RETRY_POLICY


def test_other_jobs_keep_the_default_policy() -> None:
    assert retry_policy_for_task("media_os.internal.ping") == DEFAULT_RETRY_POLICY
    assert retry_policy_for_task("media_os.publish.schedule_carousel") == DEFAULT_RETRY_POLICY


def test_postiz_read_retries_span_at_least_ten_minutes() -> None:
    policy = POSTIZ_READ_RETRY_POLICY
    # Retries happen after attempts 1 .. maximum_attempts-1; jitter only adds time.
    waits = [
        compute_backoff_seconds(n, base_seconds=policy.base_backoff_seconds, jitter_ratio=0.0)
        for n in range(1, policy.maximum_attempts)
    ]
    assert waits[0] == 30.0
    assert sum(waits) >= 600
