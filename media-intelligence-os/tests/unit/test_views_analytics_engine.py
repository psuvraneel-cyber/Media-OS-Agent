"""Unit: views delta engine, reporting windows, Postiz label parsing (no database)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from media_os_api.analytics.engine import (
    REPORTING_TZ,
    IncrementKind,
    Observation,
    Window,
    allocate,
    buckets,
    granularity_for,
    growth_pct,
    increments,
    latest_value,
    normalize,
    previous_window,
    range_window,
    total_in,
)
from media_os_api.analytics.metrics import metric_key
from media_os_api.analytics.sync import observation_hour, parse_metrics


def at(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, tzinfo=UTC)


# ------------------------------------------------------------------ cumulative → increments


def test_cumulative_snapshots_are_never_summed() -> None:
    # The spec's example: Mon 1,000 / Tue 1,150 / Wed 1,300 → lifetime 1,300, growth 300.
    obs = [Observation(at(9), 1000), Observation(at(10), 1150), Observation(at(11), 1300)]
    incs = increments(obs, published_at=None)
    tracked = [i for i in incs if i.kind is IncrementKind.TRACKED]
    assert sum(i.delta for i in tracked) == 300
    assert sum(i.delta for i in incs) == 1300  # baseline 1000 + 300, not 3450
    assert latest_value(obs) == 1300


def test_baseline_attributed_from_publish_time() -> None:
    incs = increments([Observation(at(10, 12), 500)], published_at=at(10, 6))
    assert len(incs) == 1
    assert incs[0].kind is IncrementKind.BASELINE
    assert (incs[0].start, incs[0].end, incs[0].delta) == (at(10, 6), at(10, 12), 500)


def test_baseline_without_publish_time_is_a_point() -> None:
    incs = increments([Observation(at(10), 42)], published_at=None)
    assert incs[0].start == incs[0].end == at(10)


def test_counter_reset_never_goes_negative() -> None:
    obs = [Observation(at(1), 900), Observation(at(2), 300), Observation(at(3), 450)]
    incs = increments(obs, published_at=None)
    assert [i.kind for i in incs] == [
        IncrementKind.BASELINE,
        IncrementKind.RESET,
        IncrementKind.TRACKED,
    ]
    assert all(i.delta >= 0 for i in incs)
    assert incs[2].delta == 150  # measured from the lowered reference


def test_duplicate_snapshots_collapse() -> None:
    obs = [Observation(at(1), 10), Observation(at(1), 12), Observation(at(2), 20)]
    assert normalize(obs) == [Observation(at(1), 12), Observation(at(2), 20)]
    assert sum(i.delta for i in increments(obs, published_at=None)) == 20


def test_unsorted_input_is_sorted() -> None:
    obs = [Observation(at(3), 30), Observation(at(1), 10), Observation(at(2), 20)]
    assert sum(i.delta for i in increments(obs, published_at=None)) == 30


def test_missing_snapshots_spread_over_the_gap() -> None:
    # Sync missed two days: the 300 views between day 1 and day 4 are spread across the gap.
    incs = increments([Observation(at(1), 100), Observation(at(4), 400)], published_at=None)
    day2 = total_in(incs, Window(at(2), at(3)))
    assert day2.value == pytest.approx(100)
    assert day2.approximate is True


def test_no_change_produces_no_increment() -> None:
    incs = increments([Observation(at(1), 5), Observation(at(2), 5)], published_at=None)
    assert [i.kind for i in incs] == [IncrementKind.BASELINE]


def test_zero_baseline_skipped() -> None:
    assert increments([Observation(at(1), 0)], published_at=None) == []


def test_naive_timestamps_rejected() -> None:
    with pytest.raises(ValueError):
        increments([Observation(datetime(2026, 11, 1), 1)], published_at=None)


# ------------------------------------------------------------------ allocation to windows


def test_allocation_inside_partial_and_outside() -> None:
    (inc,) = [
        i
        for i in increments([Observation(at(1), 0), Observation(at(3), 200)], published_at=None)
        if i.kind is IncrementKind.TRACKED
    ]
    full = allocate(inc, Window(at(1), at(5)))
    assert (full.value, full.approximate) == (200, False)
    half = allocate(inc, Window(at(2), at(5)))
    assert half.value == pytest.approx(100) and half.approximate
    assert allocate(inc, Window(at(4), at(5))).value == 0


def test_old_post_contributes_to_recent_window() -> None:
    # Published 90 days before; views received during the last week still count in "7d".
    published = at(10) - timedelta(days=90)
    obs = [Observation(at(1), 5000), Observation(at(10), 5600)]
    incs = increments(obs, published_at=published)
    last_week = total_in(incs, Window(at(3), at(10)))
    assert last_week.value == pytest.approx(600 * 7 / 9)
    assert total_in(incs, Window(published, at(11))).value == pytest.approx(5600)


def test_point_baseline_window_boundaries_inclusive_exclusive() -> None:
    incs = increments([Observation(at(5), 70)], published_at=None)
    assert total_in(incs, Window(at(5), at(6))).value == 70  # start inclusive
    assert total_in(incs, Window(at(4), at(5))).value == 0  # end exclusive


# ------------------------------------------------------------------ windows (Asia/Kolkata)


def test_range_window_last_7_days_uses_local_midnight() -> None:
    now = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)  # 15:30 IST, 15 Nov
    w = range_window("7d", now=now, earliest=None)
    local_start = w.start.astimezone(REPORTING_TZ)
    assert local_start.date() == date(2026, 11, 9)
    assert (local_start.hour, local_start.minute) == (0, 0)
    assert w.end == now


def test_range_window_near_utc_midnight_uses_ist_date() -> None:
    now = datetime(2026, 11, 14, 20, 0, tzinfo=UTC)  # already 15 Nov 01:30 IST
    w = range_window("7d", now=now, earliest=None)
    assert w.start.astimezone(REPORTING_TZ).date() == date(2026, 11, 9)


def test_previous_window_same_length_and_adjacent() -> None:
    now = datetime(2026, 11, 15, 10, tzinfo=UTC)
    cur = range_window("30d", now=now, earliest=None)
    prev = previous_window("30d", cur)
    assert prev is not None
    assert prev.end == cur.start
    assert (prev.end - prev.start) == timedelta(days=30)
    assert previous_window("all", cur) is None


def test_all_time_window_starts_at_earliest() -> None:
    now = at(15)
    w = range_window("all", now=now, earliest=at(1))
    assert (w.start, w.end) == (at(1), now)
    empty = range_window("all", now=now, earliest=None)
    assert empty.start == empty.end == now


def test_daily_buckets_cover_window_exactly() -> None:
    now = datetime(2026, 11, 15, 10, tzinfo=UTC)
    w = range_window("7d", now=now, earliest=None)
    bs = buckets(w, "day")
    assert len(bs) == 7
    assert bs[0][1].start == w.start and bs[-1][1].end == w.end
    for (_, a), (_, b) in zip(bs, bs[1:], strict=False):
        assert a.end == b.start


def test_buckets_sum_equals_window_total() -> None:
    now = datetime(2026, 11, 15, 10, tzinfo=UTC)
    w = range_window("30d", now=now, earliest=None)
    obs = [Observation(now - timedelta(days=d, hours=3), 1000 - d * 20) for d in range(40, -1, -1)]
    incs = increments(obs, published_at=now - timedelta(days=45))
    whole = total_in(incs, w).value
    parts = sum(total_in(incs, bw).value for _, bw in buckets(w, "day"))
    assert parts == pytest.approx(whole)


def test_granularity_choice() -> None:
    now = at(15)
    assert granularity_for("60d", range_window("60d", now=now, earliest=None)) == "day"
    long = Window(now - timedelta(days=400), now)
    assert granularity_for("all", long) == "week"
    assert granularity_for("all", Window(now - timedelta(days=900), now)) == "month"
    weeks = buckets(long, "week")
    assert all(d.weekday() == 0 for d, _ in weeks)
    months = buckets(Window(now - timedelta(days=900), now), "month")
    assert all(d.day == 1 for d, _ in months)


def test_growth_pct() -> None:
    assert growth_pct(120, 100) == 20.0
    assert growth_pct(50, 0) is None
    assert growth_pct(50, None) is None


# ------------------------------------------------------------------ Postiz parsing


def test_metric_keys_are_platform_specific() -> None:
    assert metric_key("INSTAGRAM", "Views") == "views"
    assert metric_key("FACEBOOK", "Impressions") == "unique_viewers"  # never "views"
    assert metric_key("FACEBOOK", "Views") == "views"  # video posts
    assert metric_key("FACEBOOK", "Clicks by Type") is None
    assert metric_key("INSTAGRAM", "Weird Metric!") == "other_weird_metric"


def test_parse_metrics_takes_last_point_and_drops_garbage() -> None:
    payload: list[dict[str, Any]] = [
        {"label": "Views", "data": [{"total": "10", "date": "x"}, {"total": "25", "date": "y"}]},
        {"label": "Likes", "data": [{"total": "nope"}]},
        {"label": "Comments", "data": [{"total": "-3"}]},
        {"label": "Saves", "data": []},
        {"label": "Reach", "data": [{"total": 7}]},
    ]
    assert parse_metrics("INSTAGRAM", payload) == {
        "views": (Decimal("25"), "Views"),
        "reach": (Decimal("7"), "Reach"),
    }


def test_observation_hour_is_idempotency_bucket() -> None:
    a = observation_hour(datetime(2026, 11, 15, 10, 5, 9, tzinfo=UTC))
    b = observation_hour(datetime(2026, 11, 15, 10, 59, 59, tzinfo=UTC))
    assert a == b == datetime(2026, 11, 15, 10, tzinfo=UTC)


# ------------------------------------------------------------------ caption length (bug 4)


def test_full_caption_matches_publisher_and_is_never_truncated() -> None:
    from media_os_api.content.caption import CAPTION_MAX, full_caption

    assert full_caption(" hi ", ["#a", "#b"]) == "hi\n\n#a #b"
    assert full_caption("hi", []) == "hi"
    long = "x" * (CAPTION_MAX + 50)
    assert len(full_caption(long, ["#a"])) > CAPTION_MAX  # measured, not cut


# ------------------------------------------------------------------ sync call budget


def test_check_interval_tiers_by_age_and_status() -> None:
    from media_os_api.analytics.sync import check_interval

    assert check_interval(timedelta(days=3), "OK") == timedelta(hours=5)
    assert check_interval(timedelta(days=45), "OK") == timedelta(hours=23)
    assert check_interval(timedelta(days=400), None) == timedelta(days=6, hours=20)
    # Back-off never shortens an interval, only lengthens it.
    assert check_interval(timedelta(days=3), "NO_DATA") == timedelta(hours=23)
    assert check_interval(timedelta(days=3), "MISSING") == timedelta(days=6, hours=20)
    assert check_interval(timedelta(days=400), "NO_DATA") == timedelta(days=6, hours=20)


def test_is_due_respects_last_check() -> None:
    from media_os_api.analytics.sync import is_due
    from media_os_api.db.models import PublishSchedule

    now = datetime(2026, 11, 15, 12, tzinfo=UTC)
    fresh = PublishSchedule(scheduled_at=now - timedelta(days=2), metrics_checked_at=None)
    assert is_due(fresh, now)
    fresh.metrics_checked_at = now - timedelta(hours=4)
    assert not is_due(fresh, now)
    fresh.metrics_checked_at = now - timedelta(hours=6)
    assert is_due(fresh, now)
    old = PublishSchedule(
        scheduled_at=now - timedelta(days=200), metrics_checked_at=now - timedelta(days=3)
    )
    assert not is_due(old, now)  # weekly tier
