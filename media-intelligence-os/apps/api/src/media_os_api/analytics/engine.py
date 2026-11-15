"""Pure views-analytics math: cumulative snapshots → increments → period totals and buckets.

Platforms report post metrics (Instagram `views`, Facebook reactions, ...) as a single *lifetime
cumulative* number. Media OS stores one timestamped observation per sync and derives what happened
between observations. Rules:

* Successive cumulative values are never summed. The increment between two observations is
  `later - earlier`, attributed to the interval between them.
* The first observation is a **baseline**: everything the post accrued before Media OS first saw it.
  It is attributed to `[published_at, first_observed_at]` when the publish time is known, otherwise
  to the instant of the first observation.
* A decrease (platform recount or counter reset) yields no negative views: the interval gets 0 and
  the lower value becomes the new reference. It is flagged.
* Duplicate observations at the same instant collapse to the last value.
* When an increment's interval straddles a period boundary it is prorated linearly by time and the
  result is flagged `approximate` — Media OS cannot know exactly when inside the interval the views
  happened, and it says so instead of inventing precision.

Nothing here touches the database, so every rule is unit-tested directly.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo

REPORTING_TZ = ZoneInfo("Asia/Kolkata")

RangeKey = Literal["7d", "30d", "60d", "all"]
RANGE_DAYS: dict[str, int] = {"7d": 7, "30d": 30, "60d": 60}


class IncrementKind(StrEnum):
    BASELINE = "baseline"  # lifetime value at first observation
    TRACKED = "tracked"  # growth observed between two syncs
    RESET = "reset"  # counter went down; 0 attributed, new reference taken


@dataclass(frozen=True)
class Observation:
    at: datetime
    value: float


@dataclass(frozen=True)
class Increment:
    start: datetime
    end: datetime
    delta: float
    kind: IncrementKind


@dataclass(frozen=True)
class Allocation:
    value: float
    approximate: bool


@dataclass(frozen=True)
class Window:
    start: datetime  # inclusive
    end: datetime  # exclusive


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("Analytics timestamps must be timezone-aware.")
    return dt.astimezone(UTC)


def normalize(observations: Iterable[Observation]) -> list[Observation]:
    """Sort by time and collapse duplicates at the same instant (last value wins)."""
    by_at: dict[datetime, float] = {}
    for obs in observations:
        by_at[_aware(obs.at)] = float(obs.value)
    return [Observation(at, by_at[at]) for at in sorted(by_at)]


def increments(
    observations: Iterable[Observation], *, published_at: datetime | None
) -> list[Increment]:
    series = normalize(observations)
    out: list[Increment] = []
    published = _aware(published_at) if published_at is not None else None
    prev: Observation | None = None
    for obs in series:
        if prev is None:
            start = published if published is not None and published < obs.at else obs.at
            if obs.value > 0:
                out.append(Increment(start, obs.at, obs.value, IncrementKind.BASELINE))
        else:
            delta = obs.value - prev.value
            if delta < 0:
                out.append(Increment(prev.at, obs.at, 0.0, IncrementKind.RESET))
            elif delta > 0:
                out.append(Increment(prev.at, obs.at, delta, IncrementKind.TRACKED))
        prev = obs
    return out


def allocate(inc: Increment, window: Window) -> Allocation:
    """Share of an increment that falls inside `[window.start, window.end)`."""
    if inc.delta == 0:
        return Allocation(0.0, False)
    if inc.start == inc.end:
        inside = window.start <= inc.end < window.end
        return Allocation(inc.delta if inside else 0.0, False)
    lo = max(inc.start, window.start)
    hi = min(inc.end, window.end)
    if hi <= lo:
        return Allocation(0.0, False)
    span = (inc.end - inc.start).total_seconds()
    fraction = (hi - lo).total_seconds() / span
    if fraction >= 1.0:
        return Allocation(inc.delta, False)
    return Allocation(inc.delta * fraction, True)


def total_in(incs: Sequence[Increment], window: Window) -> Allocation:
    value = 0.0
    approx = False
    for inc in incs:
        a = allocate(inc, window)
        value += a.value
        approx = approx or a.approximate
    return Allocation(value, approx)


def latest_value(observations: Iterable[Observation]) -> float | None:
    series = normalize(observations)
    return series[-1].value if series else None


# ---------------------------------------------------------------- reporting windows (Asia/Kolkata)


def local_midnight(day: date, tz: ZoneInfo = REPORTING_TZ) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def range_window(range_key: RangeKey, *, now: datetime, earliest: datetime | None) -> Window:
    """`Last N days` = local midnight N-1 days ago (inclusive) → now (exclusive).

    Today counts as one of the N days. `all` starts at the earliest known increment.
    """
    now_utc = _aware(now)
    if range_key == "all":
        start = _aware(earliest) if earliest is not None else now_utc
        return Window(min(start, now_utc), now_utc)
    days = RANGE_DAYS[range_key]
    today = now_utc.astimezone(REPORTING_TZ).date()
    return Window(local_midnight(today - timedelta(days=days - 1)), now_utc)


def previous_window(range_key: RangeKey, current: Window) -> Window | None:
    """The equally long window immediately before `current` (none for all time)."""
    if range_key == "all":
        return None
    days = RANGE_DAYS[range_key]
    end = current.start
    start_day = end.astimezone(REPORTING_TZ).date() - timedelta(days=days)
    return Window(local_midnight(start_day), end)


Granularity = Literal["day", "week", "month"]


def granularity_for(range_key: RangeKey, window: Window) -> Granularity:
    if range_key in ("7d", "30d", "60d"):
        return "day"
    span_days = (window.end - window.start).days
    if span_days <= 90:
        return "day"
    if span_days <= 540:
        return "week"
    return "month"


def buckets(window: Window, granularity: Granularity) -> list[tuple[date, Window]]:
    """Calendar buckets (Asia/Kolkata) covering the window; the last one ends at `window.end`."""
    first = window.start.astimezone(REPORTING_TZ).date()
    if granularity == "week":
        first = first - timedelta(days=first.weekday())
    elif granularity == "month":
        first = first.replace(day=1)
    out: list[tuple[date, Window]] = []
    cursor = first
    while True:
        start = local_midnight(cursor)
        if start >= window.end and out:
            break
        if granularity == "day":
            nxt = cursor + timedelta(days=1)
        elif granularity == "week":
            nxt = cursor + timedelta(days=7)
        else:
            nxt = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = min(local_midnight(nxt), window.end)
        out.append((cursor, Window(max(start, window.start), end)))
        cursor = nxt
        if local_midnight(cursor) >= window.end:
            break
    return out


def growth_pct(current: float, previous: float | None) -> float | None:
    """Period-over-period change; None when there is no comparable previous value."""
    if previous is None or previous <= 0:
        return None
    return round((current - previous) / previous * 100.0, 1)
