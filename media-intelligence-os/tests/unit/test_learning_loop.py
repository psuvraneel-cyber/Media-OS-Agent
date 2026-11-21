"""Unit: the learning loop's maths, steering and weekly report (N5)."""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from media_os_api.learning import core
from media_os_api.learning.weekly import weekly_text

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 1, 13, 30, tzinfo=UTC)


def _outcome(i: int, views: float, **features: str) -> core.Outcome:
    return core.Outcome(
        key=f"s{i}",
        package_id=f"p{i}",
        platform="INSTAGRAM",
        published_at=T0 + timedelta(days=i),
        title=f"Post {i}",
        features={"format": "CAROUSEL", **features},
        views=views,
        mature=True,
    )


# --------------------------------------------------------------------------- measuring


def test_value_at_interpolates_and_waits_for_data_after_the_mark() -> None:
    at = T0 + core.MATURE_AFTER
    series = [(T0, 0.0), (T0 + timedelta(hours=48), 40.0), (T0 + timedelta(hours=96), 80.0)]
    assert core.value_at(series, at) == pytest.approx(60.0)  # halfway between 48 h and 96 h
    assert core.value_at(series[:2], at) is None  # nothing observed after 72 h yet
    assert core.value_at([(at, 7.0)], at) == 7.0


def test_measure_uses_the_same_age_for_every_post() -> None:
    series = {"views": [(T0 + timedelta(hours=24), 30.0), (T0 + timedelta(hours=120), 130.0)]}
    young, mature = core.measure(series, T0, T0 + timedelta(hours=50))
    assert young == {} and mature is False
    values, mature = core.measure(series, T0, T0 + timedelta(days=10))
    assert mature and values["views"] == pytest.approx(30 + 100 * (48 / 96))


def test_engagement_weights_saves_and_shares_more_than_likes() -> None:
    assert core.engagement_of({"likes": 10, "saves": 2, "shares": 1}) == 10 + 6 + 3
    assert core.engagement_of({"views": 5}) is None


def test_lift_compares_with_the_previous_posts_median() -> None:
    posts = [_outcome(i, v) for i, v in enumerate([10, 10, 10, 10, 40])]
    core.assign_lifts(posts)
    assert posts[-1].baseline == 10
    assert posts[-1].lift == pytest.approx(41 / 11)
    assert posts[3].lift == pytest.approx(1.0)
    # A young post gets no lift.
    young = _outcome(9, 999)
    young.mature = False
    core.assign_lifts([*posts, young])
    assert young.lift is None


# --------------------------------------------------------------------------- groups


def test_groups_give_no_verdict_below_the_minimum() -> None:
    posts = [_outcome(i, 10, hook_type="QUESTION") for i in range(5)]
    for p in posts:
        p.lift = 3.0
    [g] = core.group_stats(posts, "hook_type", min_posts=30)
    assert g.verdict == "NOT_ENOUGH_DATA" and g.n == 5 and g.needed == 25
    assert g.median_lift == 3.0


def test_groups_give_verdicts_with_enough_posts() -> None:
    rng = random.Random(1)  # noqa: S311 - test data
    posts = []
    for i in range(40):
        good = _outcome(i, 10, hook_type="QUESTION")
        good.lift = 2 ** rng.uniform(0.8, 1.2)
        bad = _outcome(100 + i, 10, hook_type="MISTAKE")
        bad.lift = 2 ** rng.uniform(-1.2, -0.8)
        mixed = _outcome(200 + i, 10, hook_type="CURIOSITY")
        mixed.lift = 2 ** rng.uniform(-1, 1)
        posts += [good, bad, mixed]
    by = {g.value: g for g in core.group_stats(posts, "hook_type", min_posts=30)}
    assert by["QUESTION"].verdict == "BETTER"
    assert by["MISTAKE"].verdict == "WORSE"
    assert by["CURIOSITY"].verdict == "NO_CLEAR_DIFFERENCE"
    assert core.proven_winners({"hook_type": list(by.values())}) == {"hook_type": ["QUESTION"]}


# --------------------------------------------------------------------------- model


def test_model_shrinks_thin_evidence_towards_usual() -> None:
    one = core.fit([({"hook_type": "QUESTION"}, 3.0)], ["hook_type"])
    # One post at 8× lifts the prediction, but nowhere near 8×.
    assert 1.0 < one.predict_lift({"hook_type": "QUESTION"}) < 8.0
    many = core.fit([({"hook_type": "QUESTION"}, 3.0)] * 40, ["hook_type"])
    assert many.predict_lift({"hook_type": "QUESTION"}) > one.predict_lift(
        {"hook_type": "QUESTION"}
    )
    assert many.predict_lift({"hook_type": "NEVER_SEEN"}) == pytest.approx(2**many.mean)


def test_model_recovers_real_effects_and_beats_a_plain_guess() -> None:
    rng = random.Random(7)  # noqa: S311 - test data
    effect = {"REEL": 2.0, "CAROUSEL": -0.5}
    posts = []
    for i in range(60):
        fmt = "REEL" if i % 3 == 0 else "CAROUSEL"
        o = _outcome(i, 10, format=fmt, hook_type=rng.choice(["QUESTION", "MISTAKE"]))
        o.lift = 2 ** (effect[fmt] + rng.gauss(0, 0.3))
        o.v0_score = rng.uniform(60, 90)  # unrelated to the outcome
        posts.append(o)
    ev = core.leave_one_out(posts, ["format", "hook_type"], min_posts=30)
    assert ev.active and ev.mae_model is not None and ev.mae_baseline is not None
    assert ev.mae_model < ev.mae_baseline
    # Leave-one-out with two formats orders the clusters right; within a cluster it's noise.
    assert ev.rank_corr is not None and ev.rank_corr > 0.3
    assert ev.v0_rank_corr is not None and abs(ev.v0_rank_corr) < 0.4
    assert all(o.predicted_lift is not None for o in posts)


def test_model_is_not_trusted_with_too_few_posts() -> None:
    posts = [_outcome(i, 10) for i in range(5)]
    for i, p in enumerate(posts):
        p.lift = 2.0 if i % 2 else 0.5
    assert core.leave_one_out(posts, ["format"], min_posts=30).active is False


def test_spearman() -> None:
    assert core.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert core.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert core.spearman([1, 1, 1], [1, 2, 3]) is None


def test_winning_hooks_need_enough_posts_and_a_real_lift() -> None:
    posts = [_outcome(i, 10) for i in range(6)]
    for i, p in enumerate(posts):
        p.lift = [3.0, 1.1, 0.5, 2.0, 1.0, 0.9][i]
    picked = core.winning_hooks(posts)
    assert [p.title for p in picked] == ["Post 0", "Post 3"]
    assert core.winning_hooks(posts[:3]) == []  # too few measured posts


# --------------------------------------------------------------------------- steering


class _Brand:
    content_pillars = ["wedding_season", "how_it_works"]


def test_steering_fills_only_blank_fields_with_proven_winners() -> None:
    from media_os_api.content.autopilot import steer_fields

    winners = {"pillar": ["how_it_works"], "hook_type": ["QUESTION"], "language": ["XX"]}
    out = steer_fields(_Brand(), {"hook": "MISTAKE"}, winners, explore=False, seed=0)  # type: ignore[arg-type]
    assert out == {"pillar": "how_it_works"}  # hook chosen by the owner; bad language ignored
    assert steer_fields(_Brand(), {}, winners, explore=True, seed=0) == {}  # type: ignore[arg-type]
    assert steer_fields(_Brand(), {}, {}, explore=False, seed=0) == {}  # type: ignore[arg-type]


def test_about_a_quarter_of_jobs_explore() -> None:
    from media_os_api.content.autopilot import explores

    seed = random.Random(3).getrandbits(128)  # noqa: S311 - test data
    ids = [uuid.UUID(int=seed + i) for i in range(2000)]
    share = sum(explores(i) for i in ids) / len(ids)
    assert 0.2 < share < 0.3
    assert explores(ids[0]) == explores(ids[0])  # stable for retries


# --------------------------------------------------------------------------- weekly report


def _report(**over: Any) -> dict[str, Any]:
    now = datetime(2026, 11, 23, 3, 30, tzinfo=UTC)
    base: dict[str, Any] = {
        "settings": {"min_posts": 30},
        "counts": {"published": 3, "measured": 2},
        "model": {
            "n": 2,
            "active": False,
            "mae_model": None,
            "rank_corr": None,
            "v0_rank_corr": None,
        },
        "groups": {
            "format": [
                {
                    "value": "REEL",
                    "n": 2,
                    "median_lift": 9.0,
                    "verdict": "NOT_ENOUGH_DATA",
                    "needed": 28,
                },
                {
                    "value": "CAROUSEL",
                    "n": 3,
                    "median_lift": 0.5,
                    "verdict": "NOT_ENOUGH_DATA",
                    "needed": 27,
                },
            ]
        },
        "best_times": {"recommended": [], "by_hour": []},
        "posts": [
            {
                "schedule_id": "a",
                "title": "Promo reel",
                "published_at": (now - timedelta(days=4)).isoformat(),
                "audience_72h": 228,
                "actual_lift": 22.4,
                "features": {"format": "REEL", "weekday": "Tue", "hour": "18:00"},
            },
            {
                "schedule_id": "b",
                "title": "Card in minutes",
                "published_at": (now - timedelta(days=5)).isoformat(),
                "audience_72h": 3,
                "actual_lift": 0.4,
                "features": {"format": "CAROUSEL", "language": "EN", "pillar": "how_it_works"},
            },
        ],
    }
    base.update(over)
    return base


def test_weekly_report_says_early_signals_are_not_proven() -> None:
    now = datetime(2026, 11, 23, 3, 30, tzinfo=UTC)
    text = weekly_text(_report(), brand_name="Nimontron", now=now)
    assert "Best: Promo reel · 22.4× usual" in text
    assert "Weakest: Card in minutes · 0.4× usual" in text
    assert "English" in text and "How it works" in text
    assert "No firm conclusions yet" in text and "Early signals" in text
    assert "Do more of" not in text and "Stop" not in text


def test_weekly_report_states_conclusions_with_enough_posts() -> None:
    now = datetime(2026, 11, 23, 3, 30, tzinfo=UTC)
    groups = {
        "format": [
            {"value": "REEL", "n": 31, "median_lift": 6.0, "verdict": "BETTER", "needed": 0},
            {"value": "CAROUSEL", "n": 40, "median_lift": 0.4, "verdict": "WORSE", "needed": 0},
        ]
    }
    text = weekly_text(_report(groups=groups), brand_name="Nimontron", now=now)
    assert "Do more of" in text and "Format: Reel (6.0×, 31 posts)" in text
    assert "Stop or change" in text and "Format: Carousel (0.4×, 40 posts)" in text


# --------------------------------------------------------------------------- Facebook


def test_audience_uses_the_best_number_the_platform_reports() -> None:
    assert core.audience_of({"views": 9, "unique_viewers": 4}) == (9, "views")
    assert core.audience_of({"unique_viewers": 4, "reactions": 1}) == (4, "unique_viewers")
    assert core.audience_of({"plays": 2}) == (2, "plays")
    assert core.audience_of({"reactions": 1}) == (None, None)


def test_weekly_report_adds_a_facebook_line() -> None:
    from media_os_api.learning.weekly import facebook_line

    now = datetime(2026, 11, 23, 3, 30, tzinfo=UTC)
    old = (now - timedelta(days=5)).isoformat()
    base: dict[str, Any] = {"settings": {"mature_after_hours": 72}, "generated_at": now.isoformat()}
    fb: dict[str, Any] = {
        **base,
        "counts": {"published": 2},
        "posts": [
            {
                "title": "Card in minutes",
                "audience_72h": 3,
                "audience_metric": "unique_viewers",
                "actual_lift": 0.5,
                "published_at": old,
            },
            {
                "title": "Promo reel",
                "audience_72h": 11,
                "audience_metric": "views",
                "actual_lift": 2.0,
                "published_at": old,
            },
        ],
    }
    line = facebook_line(fb)
    # Reach and views are different numbers: never added up, each named for what it is.
    assert "2 post(s) measured" in line and "14" not in line
    assert "Promo reel (11 views, 2.0× the Page's usual)" in line
    assert facebook_line({**base, "counts": {"published": 0}, "posts": []}) == ""
    unmeasured: dict[str, Any] = {
        **base,
        "counts": {"published": 1},
        "posts": [{"title": "x", "audience_72h": None, "actual_lift": None, "published_at": old}],
    }
    assert "hasn't reported audience numbers" in facebook_line(unmeasured)
    young = {**unmeasured, "posts": [{**unmeasured["posts"][0], "published_at": now.isoformat()}]}
    assert "no post has reached 3 days old" in facebook_line(young)
    text = weekly_text(_report(), brand_name="Nimontron", now=now, facebook=fb)
    assert "📘 <b>Facebook</b>" in text


def test_each_account_and_metric_has_its_own_baseline() -> None:
    page_a = [_outcome(i, 100) for i in range(4)]
    page_b = [_outcome(10 + i, 2) for i in range(4)]
    for o in page_a:
        o.channel = "page-a"
    for o in page_b:
        o.channel = "page-b"
    video = _outcome(20, 50)
    video.channel, video.audience_metric = "page-a", "plays"
    core.assign_lifts([*page_a, *page_b, video])
    assert page_b[-1].baseline == 2  # not dragged up by page A's 100s
    assert page_a[-1].baseline == 100
    assert video.baseline == 50  # its own metric: only itself so far
