"""Weekly insight report on Telegram (Mondays 09:00 IST): what worked, what to do more of, what
to stop, and how good the predictions were.

It only states a conclusion ("do more of", "stop") for groups that passed the minimum-posts bar.
Below it, the strongest early signals are listed and clearly labelled as such, with how many more
posts each needs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.db.models import BrandProfile, JobRun
from media_os_api.notify.messages import e, short

WEEKLY_TASK = "media_os.learning.weekly_report"

LABELS = {
    "format": "Format",
    "pillar": "Topic",
    "hook_type": "Hook",
    "language": "Language",
    "occasion": "Occasion",
    "design": "Design",
    "slot": "Time of day",
    "weekday": "Day",
    "hour": "Hour",
}


LANGUAGE_NAMES = {"EN": "English", "BN": "Bengali", "BN_EN": "Bilingual"}


def _nice(value: str) -> str:
    if value in LANGUAGE_NAMES:
        return LANGUAGE_NAMES[value]
    if value.isupper() or "_" in value:
        return value.replace("_", " ").capitalize()
    return value


def _x(lift: float | None) -> str:
    return f"{lift:.1f}×" if lift is not None else "–"


def weekly_text(report: dict[str, Any], *, brand_name: str, now: datetime) -> str:
    week_ago = now - timedelta(days=7)
    posts = report["posts"]
    this_week = [
        p
        for p in posts
        if datetime.fromisoformat(p["published_at"]) >= week_ago and p["views_72h"] is not None
    ]
    need = report["settings"]["min_posts"]
    lines = [f"📈 <b>Weekly insights: {e(brand_name)}</b>", ""]

    # This week's posts (measured at 72 h).
    if this_week:
        best = max(this_week, key=lambda p: p["actual_lift"] or 0)
        worst = min(this_week, key=lambda p: p["actual_lift"] or 0)
        total = sum(p["views_72h"] or 0 for p in this_week)
        lines.append(
            f"<b>This week</b>: {len(this_week)} post(s) measured, {total:,} views in their first "
            "3 days."
        )
        lines.append(f"🏆 Best: {e(short(best['title'], 60))} · {_x(best['actual_lift'])} usual")
        lines.append(f"   {e(_why(best))}")
        if worst["schedule_id"] != best["schedule_id"]:
            lines.append(
                f"🐢 Weakest: {e(short(worst['title'], 60))} · {_x(worst['actual_lift'])} usual"
            )
            lines.append(f"   {e(_why(worst))}")
    else:
        lines.append("<b>This week</b>: no post has reached 3 days old yet.")

    # Conclusions (enough data) and early signals (not enough yet).
    more, stop, early = [], [], []
    for dim, groups in report["groups"].items():
        if dim not in LABELS:
            continue
        for g in groups:
            label = f"{LABELS[dim]}: {_nice(g['value'])}"
            if g["verdict"] == "BETTER":
                more.append(f"{label} ({_x(g['median_lift'])}, {g['n']} posts)")
            elif g["verdict"] == "WORSE":
                stop.append(f"{label} ({_x(g['median_lift'])}, {g['n']} posts)")
            elif g["verdict"] == "NOT_ENOUGH_DATA" and g["n"] >= 2 and g["median_lift"]:
                early.append((g["median_lift"], label, g["n"], g["needed"]))
    lines.append("")
    if more:
        lines.append("✅ <b>Do more of</b>")
        lines += [f"• {e(m)}" for m in more[:5]]
    if stop:
        lines.append("🛑 <b>Stop or change</b>")
        lines += [f"• {e(s)}" for s in stop[:5]]
    if not more and not stop:
        lines.append(
            f"No firm conclusions yet: a group needs {need} measured posts first "
            f"({report['counts']['measured']} measured so far)."
        )
    if early:
        early.sort(reverse=True)
        lines.append("👀 <b>Early signals</b> (not proven yet)")
        for lift, label, n, needed in early[:3]:
            lines.append(f"• {e(label)}: {_x(lift)} over {n} posts, {needed} more needed")
        if len(early) > 3:
            weakest = early[-1]
            lines.append(f"• Weakest so far: {e(weakest[1])}: {_x(weakest[0])} over {weakest[2]}")

    # Predictions.
    model = report["model"]
    lines.append("")
    if model["n"] >= 3 and model["mae_model"] is not None:
        verdict = (
            "trusted: it replaces the old score"
            if model["active"]
            else "not trusted yet (the old score is still shown)"
        )
        corr = model["rank_corr"]
        lines.append(
            f"🔮 <b>Predictions</b>: learned from {model['n']} posts, {verdict}. "
            + (f"Ranking accuracy {corr:+.2f} (1 = perfect). " if corr is not None else "")
            + (
                f"Old score v0: {model['v0_rank_corr']:+.2f}."
                if model["v0_rank_corr"] is not None
                else ""
            )
        )
    else:
        lines.append("🔮 <b>Predictions</b>: not enough measured posts to learn from yet.")

    times = report["best_times"]
    if times["recommended"]:
        lines.append(f"⏰ <b>Best times</b>: {e(', '.join(times['recommended']))}")
    else:
        top = sorted(
            (g for g in times["by_hour"] if g["n"] >= 2 and (g["median_lift"] or 0) > 1),
            key=lambda g: g["median_lift"],
            reverse=True,
        )
        if top:
            lines.append(
                f"⏰ Best time so far: {e(top[0]['value'])} ({_x(top[0]['median_lift'])}, "
                f"{top[0]['n']} posts; not proven yet)"
            )
    return "\n".join(lines)


def _why(post: dict[str, Any]) -> str:
    f = post["features"]
    bits = [_nice(f.get("format", ""))]
    for key in ("pillar", "hook_type", "language"):
        v = f.get(key)
        if v and v not in ("UNKNOWN", "OWNER_UPLOAD"):
            bits.append(_nice(v))
    if f.get("hour"):
        bits.append(f"{f.get('weekday', '')} {f['hour']}".strip())
    return " · ".join(b for b in bits if b)


def handle_weekly_report(
    session: Session, job: JobRun, *, now: datetime | None = None
) -> dict[str, Any]:
    from media_os_api.learning.service import learning_report
    from media_os_api.notify.service import queue_alert

    now = now or datetime.now(UTC)
    from zoneinfo import ZoneInfo

    on_demand = (job.payload or {}).get("trigger") == "manual"
    if not on_demand and now.astimezone(ZoneInfo("Asia/Kolkata")).weekday() != 0:
        return {"sent": [], "skipped": "not Monday"}  # scheduled daily; reports weekly
    sent = []
    brands = session.execute(select(BrandProfile).where(BrandProfile.is_active.is_(True))).scalars()
    for brand in brands:
        report = learning_report(session, brand, now=now)
        if not report["counts"]["published"]:
            continue  # nothing posted yet: nothing to say
        week = now.strftime("%G-W%V")
        queue_alert(
            session,
            key=f"weekly:{brand.slug}:{week}",
            text=weekly_text(report, brand_name=brand.name, now=now),
            severity="info",
        )
        sent.append(brand.slug)
    return {"sent": sent}
