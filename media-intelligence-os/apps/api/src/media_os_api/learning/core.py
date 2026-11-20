"""The learning loop's maths: pure functions, no database, so every rule is unit-tested directly.

**Outcome of a post.** Platforms report lifetime cumulative numbers, so posts of different ages
aren't comparable. Each post is measured at the same age, `MATURE_AFTER` (72 h): the value at that
moment, interpolated between the syncs around it. A post younger than that isn't used yet.

**Lift.** Raw views grow with the account, so each post is compared with the account's typical
post just before it: the median 72-hour views of the previous `BASELINE_POSTS` mature posts on
the same platform (all earlier posts while there are fewer). `lift = views / baseline`, and the
model learns `log2(lift)`, so 2× and 0.5× are equally far from "usual".

**Groups.** Results per tag value (topic, hook, language, ...). A verdict is given only when the
group has at least `min_posts` posts (default 30); below that the numbers are shown as an early
signal, never as a conclusion.

**Model.** An additive model on log-lift: overall mean plus one effect per tag value, each
shrunk towards zero by `SHRINK` pseudo-posts (empirical Bayes), fitted by a few rounds of
backfitting. With little data every effect stays near zero, so predictions stay near "usual"
instead of chasing noise. It's judged by leave-one-out: each past post is predicted by a model
that never saw it.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

MATURE_AFTER = timedelta(hours=72)
BASELINE_POSTS = 20
MIN_POSTS = 30
SHRINK = 8.0
BACKFIT_ROUNDS = 12
# Interactions weighted by how much they signal intent: a save or share beats a like.
ENGAGEMENT_WEIGHTS = {"likes": 1.0, "reactions": 1.0, "comments": 2.0, "saves": 3.0, "shares": 3.0}


# --------------------------------------------------------------------------- outcome of a post


def value_at(series: Sequence[tuple[datetime, float]], at: datetime) -> float | None:
    """Cumulative value at `at`, interpolated between the observations around it.

    None when nothing was observed at or after `at` (the post isn't old enough yet, or syncs
    stopped before then). Before the first observation the value is scaled down linearly from 0
    at publish time, which `published_at` callers handle by passing it as a (t, 0) point."""
    points = sorted(series)
    if not points or points[-1][0] < at:
        return None
    before = [p for p in points if p[0] <= at]
    after = [p for p in points if p[0] >= at]
    hi = after[0]
    if not before:
        return hi[1]
    lo = before[-1]
    if hi[0] == lo[0]:
        return hi[1]
    span = (hi[0] - lo[0]).total_seconds()
    frac = (at - lo[0]).total_seconds() / span
    return lo[1] + (hi[1] - lo[1]) * frac


@dataclass
class Outcome:
    """One published post on one platform, measured at 72 hours."""

    key: str  # publish schedule id
    package_id: str
    platform: str
    published_at: datetime
    title: str
    features: dict[str, str]  # tag -> value, e.g. {"hook_type": "QUESTION"}
    views: float | None = None
    reach: float | None = None
    engagement: float | None = None
    mature: bool = False
    baseline: float | None = None
    lift: float | None = None
    predicted_lift: float | None = None  # leave-one-out, from posts before or around it
    v0_score: float | None = None

    @property
    def log_lift(self) -> float | None:
        return math.log2(self.lift) if self.lift and self.lift > 0 else None

    @property
    def engagement_rate(self) -> float | None:
        if self.engagement is None or not self.reach:
            return None
        return self.engagement / self.reach


def measure(
    series: Mapping[str, Sequence[tuple[datetime, float]]],
    published_at: datetime,
    now: datetime,
) -> tuple[dict[str, float], bool]:
    """Each metric's value at 72 h after publishing, and whether the post is old enough."""
    at = published_at + MATURE_AFTER
    if now < at:
        return {}, False
    out: dict[str, float] = {}
    for key, points in series.items():
        v = value_at([(published_at, 0.0), *points], at)
        if v is not None:
            out[key] = v
    return out, bool(out)


def engagement_of(values: Mapping[str, float]) -> float | None:
    present = [k for k in ENGAGEMENT_WEIGHTS if k in values]
    if not present:
        return None
    return sum(values[k] * ENGAGEMENT_WEIGHTS[k] for k in present)


def assign_lifts(outcomes: list[Outcome]) -> None:
    """Set each mature post's baseline and lift, per platform, oldest first."""
    by_platform: dict[str, list[Outcome]] = {}
    for o in outcomes:
        if o.mature and o.views is not None:
            by_platform.setdefault(o.platform, []).append(o)
    for posts in by_platform.values():
        posts.sort(key=lambda o: o.published_at)
        everything = [o.views for o in posts if o.views is not None]
        for i, o in enumerate(posts):
            earlier = [
                p.views for p in posts[max(0, i - BASELINE_POSTS) : i] if p.views is not None
            ]
            pool = earlier if len(earlier) >= 3 else everything  # the first few: whole history
            base = statistics.median(pool) if pool else None
            o.baseline = base
            # +1 keeps a 0-view post finite and stops tiny baselines from exploding the ratio.
            o.lift = ((o.views or 0.0) + 1.0) / (base + 1.0) if base is not None else None


# --------------------------------------------------------------------------- groups


@dataclass
class GroupStat:
    dimension: str
    value: str
    n: int
    median_lift: float | None
    mean_log_lift: float | None
    avg_views: float | None
    avg_engagement_rate: float | None
    verdict: str  # BETTER | WORSE | NO_CLEAR_DIFFERENCE | NOT_ENOUGH_DATA
    needed: int  # posts still needed for a verdict (0 when there's one)


def _mean(xs: Iterable[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return sum(vals) / len(vals) if vals else None


def group_stats(
    outcomes: Sequence[Outcome], dimension: str, *, min_posts: int = MIN_POSTS
) -> list[GroupStat]:
    """Per value of one tag: how its posts did against the account's usual."""
    groups: dict[str, list[Outcome]] = {}
    for o in outcomes:
        if o.lift is None:
            continue
        groups.setdefault(o.features.get(dimension, "UNKNOWN"), []).append(o)
    out = []
    for value, posts in groups.items():
        logs = [o.log_lift for o in posts if o.log_lift is not None]
        n = len(logs)
        mean = sum(logs) / n if n else None
        verdict = "NOT_ENOUGH_DATA"
        if n >= min_posts and mean is not None:
            sd = statistics.stdev(logs) if n > 1 else 0.0
            margin = 2 * sd / math.sqrt(n)  # ~95 % interval on the mean log-lift
            if mean - margin > 0:
                verdict = "BETTER"
            elif mean + margin < 0:
                verdict = "WORSE"
            else:
                verdict = "NO_CLEAR_DIFFERENCE"
        out.append(
            GroupStat(
                dimension=dimension,
                value=value,
                n=n,
                median_lift=statistics.median([o.lift for o in posts if o.lift is not None])
                if posts
                else None,
                mean_log_lift=mean,
                avg_views=_mean(o.views for o in posts),
                avg_engagement_rate=_mean(o.engagement_rate for o in posts),
                verdict=verdict,
                needed=max(0, min_posts - n),
            )
        )
    out.sort(key=lambda g: (-(g.mean_log_lift if g.mean_log_lift is not None else -99), -g.n))
    return out


# --------------------------------------------------------------------------- model


@dataclass
class Model:
    """mean + Σ effect[dimension][value], in log2-lift."""

    mean: float = 0.0
    effects: dict[str, dict[str, float]] = field(default_factory=dict)
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    n_train: int = 0

    def predict_log(self, features: Mapping[str, str]) -> float:
        total = self.mean
        for dim, values in self.effects.items():
            total += values.get(features.get(dim, "UNKNOWN"), 0.0)
        return total

    def predict_lift(self, features: Mapping[str, str]) -> float:
        return float(2 ** self.predict_log(features))


def fit(
    rows: Sequence[tuple[Mapping[str, str], float]],
    dimensions: Sequence[str],
    *,
    shrink: float = SHRINK,
    rounds: int = BACKFIT_ROUNDS,
) -> Model:
    """Fit the shrunken additive model to (features, log_lift) rows."""
    model = Model(n_train=len(rows))
    if not rows:
        return model
    # Lift is relative to the account's usual post, so "usual" (log-lift 0) is the prior for
    # the overall level too: a handful of lucky posts can't lift every prediction.
    model.mean = sum(y for _, y in rows) / (len(rows) + shrink)
    for dim in dimensions:
        model.effects[dim] = {}
        model.counts[dim] = {}
        for feats, _ in rows:
            v = feats.get(dim, "UNKNOWN")
            model.counts[dim][v] = model.counts[dim].get(v, 0) + 1
    for _ in range(rounds):
        for dim in dimensions:
            sums: dict[str, float] = {}
            for feats, y in rows:
                v = feats.get(dim, "UNKNOWN")
                others = model.predict_log(feats) - model.effects[dim].get(v, 0.0)
                sums[v] = sums.get(v, 0.0) + (y - others)
            model.effects[dim] = {v: s / (model.counts[dim][v] + shrink) for v, s in sums.items()}
    return model


@dataclass
class Evaluation:
    n: int
    mae_model: float | None  # mean absolute error in log2-lift, leave-one-out
    mae_baseline: float | None  # always predicting "usual" (log-lift 0)
    rank_corr: float | None  # Spearman: does it order posts correctly?
    v0_rank_corr: float | None  # same for the old heuristic score
    active: bool  # trusted enough to replace score v0


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None

    def ranks(vals: Sequence[float]) -> list[float]:
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else None


def leave_one_out(
    outcomes: list[Outcome], dimensions: Sequence[str], *, min_posts: int = MIN_POSTS
) -> Evaluation:
    """Predict each post with a model fitted on all the others; store it on the outcome."""
    rows: list[tuple[Outcome, float]] = [
        (o, ly) for o in outcomes if (ly := o.log_lift) is not None
    ]
    errs: list[float] = []
    base_errs: list[float] = []
    preds: list[float] = []
    actual: list[float] = []
    v0s: list[float] = []
    v0_actual: list[float] = []
    for i, (o, y) in enumerate(rows):
        train = [(p.features, ly) for j, (p, ly) in enumerate(rows) if j != i]
        m = fit(train, dimensions)
        p = m.predict_log(o.features)
        o.predicted_lift = 2**p
        errs.append(abs(p - y))
        base_errs.append(abs(y))
        preds.append(p)
        actual.append(y)
        if o.v0_score is not None:
            v0s.append(o.v0_score)
            v0_actual.append(y)
    n = len(rows)
    mae = sum(errs) / n if n else None
    mae_base = sum(base_errs) / n if n else None
    return Evaluation(
        n=n,
        mae_model=mae,
        mae_baseline=mae_base,
        rank_corr=spearman(preds, actual),
        v0_rank_corr=spearman(v0s, v0_actual),
        active=bool(n >= min_posts and mae is not None and mae_base is not None and mae < mae_base),
    )


# --------------------------------------------------------------------------- steering


def proven_winners(
    groups: Mapping[str, Sequence[GroupStat]],
) -> dict[str, list[str]]:
    """Per dimension, the values with a BETTER verdict (enough posts and clearly above usual)."""
    return {
        dim: [g.value for g in stats if g.verdict == "BETTER"]
        for dim, stats in groups.items()
        if any(g.verdict == "BETTER" for g in stats)
    }


def winning_hooks(
    outcomes: Sequence[Outcome], *, limit: int = 5, min_mature: int = 5, min_lift: float = 1.25
) -> list[Outcome]:
    """The best-performing mature posts' hooks, as style examples for the writer."""
    mature = [o for o in outcomes if o.lift is not None]
    if len(mature) < min_mature:
        return []
    best = sorted(mature, key=lambda o: o.lift or 0.0, reverse=True)
    picked: list[Outcome] = []
    seen: set[str] = set()
    for o in best:
        if (o.lift or 0) < min_lift or not o.title or o.package_id in seen:
            continue
        seen.add(o.package_id)
        picked.append(o)
        if len(picked) >= limit:
            break
    return picked
