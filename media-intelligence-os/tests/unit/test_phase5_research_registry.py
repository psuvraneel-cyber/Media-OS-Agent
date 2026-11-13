"""Unit tests for the research live-fetch gate and backoff policy (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from media_os_api.db.models import ResearchSource
from media_os_api.jobs.errors import (
    PermanentTaskError,
    PolicyRefusalError,
    RetryableTaskError,
    validate_safe_task_payload,
)
from media_os_api.research.registry import (
    MAX_FAILURE_BACKOFF,
    assert_live_fetch_permitted,
    compute_failure_backoff,
    live_fetch_env_enabled,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _source(**overrides: object) -> ResearchSource:
    values: dict[str, object] = {
        "source_code": "HACKERNEWS",
        "display_name": "HN",
        "policy_mode": "EXPERIMENTAL",
        "live_fetch_approved": True,
        "approved_by": "owner",
        "approved_at": NOW,
        "backoff_until": None,
    }
    values.update(overrides)
    return ResearchSource(**values)


def test_all_switches_on_permits_fetch() -> None:
    assert_live_fetch_permitted(_source(), env_enabled=True, now=NOW)


@pytest.mark.parametrize(
    ("overrides", "env", "code"),
    [
        ({"source_code": "REDDIT", "policy_mode": "EXPERIMENTAL"}, True, "SOURCE_POLICY_BLOCKED"),
        ({"policy_mode": "BLOCKED"}, True, "SOURCE_POLICY_BLOCKED"),
        ({"policy_mode": "OFF"}, True, "SOURCE_DISABLED"),
        ({"live_fetch_approved": False}, True, "LIVE_FETCH_NOT_APPROVED"),
        ({}, False, "LIVE_FETCH_KILL_SWITCH"),
    ],
)
def test_each_switch_independently_refuses(
    overrides: dict[str, object], env: bool, code: str
) -> None:
    with pytest.raises(PolicyRefusalError) as excinfo:
        assert_live_fetch_permitted(_source(**overrides), env_enabled=env, now=NOW)
    assert excinfo.value.code == code
    assert isinstance(excinfo.value, PermanentTaskError)  # never retried


def test_active_backoff_is_retryable_not_permanent() -> None:
    with pytest.raises(RetryableTaskError) as excinfo:
        assert_live_fetch_permitted(
            _source(backoff_until=NOW + timedelta(minutes=5)), env_enabled=True, now=NOW
        )
    assert excinfo.value.code == "SOURCE_BACKOFF_ACTIVE"
    assert_live_fetch_permitted(
        _source(backoff_until=NOW - timedelta(seconds=1)), env_enabled=True, now=NOW
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("true", True), ("1", True), ("YES", True), ("false", False), ("", False), ("0", False)],
)
def test_kill_switch_parsing_defaults_off(value: str, expected: bool) -> None:
    assert live_fetch_env_enabled({"RESEARCH_LIVE_FETCH_ENABLED": value}) is expected
    assert live_fetch_env_enabled({}) is False


def test_failure_backoff_is_exponential_capped_and_honours_retry_after() -> None:
    assert compute_failure_backoff(1) == timedelta(seconds=60)
    assert compute_failure_backoff(2) == timedelta(seconds=120)
    assert compute_failure_backoff(4) == timedelta(seconds=480)
    assert compute_failure_backoff(30) == MAX_FAILURE_BACKOFF
    assert compute_failure_backoff(1, timedelta(seconds=900)) == timedelta(seconds=900)
    assert compute_failure_backoff(1, timedelta(days=3)) == MAX_FAILURE_BACKOFF


def test_research_fetch_payload_passes_secret_guard() -> None:
    # Seeded schedule payload_template shape; '*_key' names would be rejected as credentials.
    assert validate_safe_task_payload({"source": "ARXIV", "trigger": "schedule"})
