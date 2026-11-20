"""The learning loop on real data: published posts → outcomes → what's working, a learned score,
best posting times, and the hints given to the writer.

Everything is computed from the database on demand (a brand has tens to hundreds of posts, so this
takes milliseconds); nothing is stored except the prediction saved on each new draft.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from media_os_api.analytics.service import _load_posts, _post_title
from media_os_api.content.taxonomy import posting_slot
from media_os_api.db.models import BrandProfile, ContentPackage
from media_os_api.learning import core

# Tags the model and the per-tag tables use. "hour" is only used for best times (too fine-grained
# to learn effects from).
MODEL_DIMENSIONS = (
    "format",
    "pillar",
    "hook_type",
    "language",
    "occasion",
    "design",
    "slot",
    "weekday",
)
REPORT_DIMENSIONS = (*MODEL_DIMENSIONS, "visual_style", "call_to_action", "strategy", "hour")
PRIMARY_PLATFORM = "INSTAGRAM"  # Facebook reach is still close to zero
MODEL_VERSION = "learned-v1"
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def min_posts() -> int:
    """Posts a group needs before Media OS draws a conclusion (`LEARNING_MIN_POSTS`, default 30)."""
    try:
        return max(5, int(os.environ.get("LEARNING_MIN_POSTS", core.MIN_POSTS)))
    except ValueError:
        return core.MIN_POSTS


# --------------------------------------------------------------------------- features


def design_label(pkg: ContentPackage) -> str:
    from media_os_api.content.designs import design_of
    from media_os_api.content.uploads import is_owner_upload

    if is_owner_upload(pkg):
        return "Owner upload"
    design = design_of(pkg)
    return str(design.get("name") or "Template") if design else "Built-in"


def features_of(pkg: ContentPackage, when: datetime | None, tz: str) -> dict[str, str]:
    """A post's tags as learning features. Time features come from the actual post time."""
    from media_os_api.content.uploads import is_owner_upload

    tags = dict(pkg.post_tags or {})
    owner = is_owner_upload(pkg)
    out = {
        "format": pkg.format,
        "source": "Owner upload" if owner else "Media OS",
        "design": design_label(pkg),
    }
    for key in ("pillar", "hook_type", "language", "occasion", "visual_style", "call_to_action"):
        out[key] = str(tags.get(key) or ("OWNER_UPLOAD" if owner else "UNKNOWN"))
    out["strategy"] = str(tags.get("strategy") or ("OWNER_UPLOAD" if owner else "EXPLORE"))
    if when is not None:
        local = when.astimezone(ZoneInfo(tz))
        out["slot"] = posting_slot(when, tz).value
        out["hour"] = f"{local.hour:02d}:00"
        out["weekday"] = WEEKDAYS[local.weekday()]
    return out


# --------------------------------------------------------------------------- outcomes


def outcomes_for(
    db: Session, brand: BrandProfile, *, platform: str | None = PRIMARY_PLATFORM, now: datetime
) -> list[core.Outcome]:
    out: list[core.Outcome] = []
    for post in _load_posts(db, brand.id, platform=platform):
        series = {k: [(o.at, o.value) for o in obs] for k, obs in post.series.items()}
        values, mature = core.measure(series, post.published_at, now)
        score = (post.package.score or {}).get("total")
        out.append(
            core.Outcome(
                key=str(post.schedule.id),
                package_id=str(post.package.id),
                platform=post.schedule.platform,
                published_at=post.published_at,
                title=_post_title(post.package),
                features=features_of(post.package, post.published_at, brand.timezone),
                views=values.get("views"),
                reach=values.get("reach"),
                engagement=core.engagement_of(values),
                mature=mature and "views" in values,
                v0_score=float(score) if isinstance(score, int | float) else None,
            )
        )
    core.assign_lifts(out)
    return out


# --------------------------------------------------------------------------- report


def _group_json(g: core.GroupStat) -> dict[str, Any]:
    return {
        "value": g.value,
        "n": g.n,
        "median_lift": round(g.median_lift, 2) if g.median_lift is not None else None,
        "avg_views": round(g.avg_views) if g.avg_views is not None else None,
        "avg_engagement_rate": round(g.avg_engagement_rate, 4)
        if g.avg_engagement_rate is not None
        else None,
        "verdict": g.verdict,
        "needed": g.needed,
    }


def _post_json(o: core.Outcome) -> dict[str, Any]:
    return {
        "schedule_id": o.key,
        "package_id": o.package_id,
        "platform": o.platform,
        "title": o.title,
        "published_at": o.published_at.astimezone(UTC).isoformat(),
        "mature": o.mature,
        "views_72h": round(o.views) if o.views is not None else None,
        "reach_72h": round(o.reach) if o.reach is not None else None,
        "engagement_rate": round(o.engagement_rate, 4) if o.engagement_rate is not None else None,
        "baseline_views": round(o.baseline) if o.baseline is not None else None,
        "actual_lift": round(o.lift, 2) if o.lift is not None else None,
        "predicted_lift": round(o.predicted_lift, 2) if o.predicted_lift is not None else None,
        "v0_score": o.v0_score,
        "features": o.features,
    }


def learning_report(
    db: Session,
    brand: BrandProfile,
    *,
    now: datetime | None = None,
    platform: str = PRIMARY_PLATFORM,
) -> dict[str, Any]:
    """Everything the Analytics page and the weekly report show."""
    now = now or datetime.now(UTC)
    need = min_posts()
    outcomes = outcomes_for(db, brand, platform=platform, now=now)
    learnable = [o for o in outcomes if o.lift is not None]
    evaluation = core.leave_one_out(learnable, MODEL_DIMENSIONS, min_posts=need)
    groups = {dim: core.group_stats(learnable, dim, min_posts=need) for dim in REPORT_DIMENSIONS}
    winners = core.proven_winners({d: groups[d] for d in MODEL_DIMENSIONS})
    best_hours = [g for g in groups["hour"] if g.verdict == "BETTER"]
    hooks = core.winning_hooks(learnable)
    return {
        "brand": brand.slug,
        "platform": platform,
        "generated_at": now.isoformat(),
        "settings": {
            "min_posts": need,
            "mature_after_hours": int(core.MATURE_AFTER.total_seconds() // 3600),
            "baseline_posts": core.BASELINE_POSTS,
            "explore_share": EXPLORE_SHARE,
        },
        "counts": {
            "published": len(outcomes),
            "measured": len(learnable),
            "waiting_for_72h": sum(1 for o in outcomes if not o.mature),
        },
        "model": {
            "version": MODEL_VERSION,
            "active": evaluation.active,
            "n": evaluation.n,
            "mae_model": _r(evaluation.mae_model),
            "mae_baseline": _r(evaluation.mae_baseline),
            "rank_corr": _r(evaluation.rank_corr),
            "v0_rank_corr": _r(evaluation.v0_rank_corr),
        },
        "groups": {dim: [_group_json(g) for g in stats] for dim, stats in groups.items()},
        "winners": winners,
        "best_times": {
            "recommended": [g.value for g in best_hours],
            "by_hour": [_group_json(g) for g in groups["hour"]],
            "by_slot": [_group_json(g) for g in groups["slot"]],
            "by_weekday": [_group_json(g) for g in groups["weekday"]],
        },
        "winning_hooks": [
            {"title": o.title, "lift": round(o.lift or 0, 2), "package_id": o.package_id}
            for o in hooks
        ],
        "posts": [
            _post_json(o) for o in sorted(outcomes, key=lambda o: o.published_at, reverse=True)
        ],
    }


def _r(x: float | None, digits: int = 3) -> float | None:
    return round(x, digits) if x is not None else None


# --------------------------------------------------------------------------- for the writer

EXPLORE_SHARE = 0.25  # share of generated posts kept as experiments


def trained_model(
    db: Session, brand: BrandProfile, *, now: datetime | None = None
) -> tuple[core.Model, core.Evaluation, dict[str, list[core.GroupStat]], list[core.Outcome]]:
    now = now or datetime.now(UTC)
    need = min_posts()
    learnable = [o for o in outcomes_for(db, brand, now=now) if o.lift is not None]
    evaluation = core.leave_one_out(learnable, MODEL_DIMENSIONS, min_posts=need)
    model = core.fit([(o.features, o.log_lift or 0.0) for o in learnable], MODEL_DIMENSIONS)
    groups = {dim: core.group_stats(learnable, dim, min_posts=need) for dim in MODEL_DIMENSIONS}
    return model, evaluation, groups, learnable


def prediction_for(
    model: core.Model, evaluation: core.Evaluation, features: Mapping[str, str]
) -> dict[str, Any]:
    """Saved on each new draft as `score["learned"]`; compared with the actual lift later."""
    return {
        "version": MODEL_VERSION,
        "predicted_lift": round(model.predict_lift(features), 3),
        "active": evaluation.active,
        "n_train": model.n_train,
        "label": (
            "Learned from your posts: expected views vs your usual post"
            if evaluation.active
            else f"Not trusted yet: learned from {model.n_train} posts; needs "
            f"{min_posts()} and to beat a plain guess"
        ),
    }


def winning_hooks_block(learnable: list[core.Outcome]) -> str:
    hooks = core.winning_hooks(learnable)
    if not hooks:
        return ""
    lines = "\n".join(f"- '{o.title}' ({o.lift:.1f}× the usual views)" for o in hooks)
    return (
        "HOOKS THAT DID BEST FOR THIS ACCOUNT (learn what made them work: the angle, the "
        "specific moment, the wording style. Never copy or reuse them):\n"
        f"{lines}\n\n"
    )
