from __future__ import annotations

from datetime import UTC, datetime, timedelta

from media_os_api.services.process_status import classify_queue, classify_scheduler

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_scheduler_running_when_it_queued_a_job_recently() -> None:
    assert classify_scheduler(NOW - timedelta(minutes=4), NOW).state == "running"
    assert classify_scheduler(NOW - timedelta(minutes=40), NOW).state == "down"
    assert classify_scheduler(None, NOW).state == "unknown"


def test_worker_running_down_and_stalled() -> None:
    def worker(finished: datetime | None, waiting: datetime | None = None) -> str:
        return classify_queue(
            "worker", last_finished=finished, oldest_waiting=waiting, now=NOW, on_demand=False
        ).state

    assert worker(NOW - timedelta(minutes=1)) == "running"
    assert worker(NOW - timedelta(hours=2)) == "down"
    # A backlog beats a recent success: something is stuck now.
    assert worker(NOW - timedelta(minutes=1), NOW - timedelta(minutes=30)) == "stalled"
    # A job that only just became due is normal.
    assert worker(NOW - timedelta(minutes=1), NOW - timedelta(minutes=2)) == "running"


def test_renderer_is_idle_without_work_and_stalled_with_a_backlog() -> None:
    def creative(finished: datetime | None, waiting: datetime | None = None) -> str:
        return classify_queue(
            "creative", last_finished=finished, oldest_waiting=waiting, now=NOW, on_demand=True
        ).state

    assert creative(None) == "idle"
    assert creative(NOW - timedelta(days=3)) == "idle"
    assert creative(NOW - timedelta(days=3), NOW - timedelta(minutes=15)) == "stalled"
