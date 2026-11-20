"""Autopilot: generate carousels on demand or daily, and fill posting slots with approved ones.

* `media_os.content.generate_carousel` (worker; holds the AI keys): writes one carousel with the
  existing writer + QA + score pipeline. A brief left blank is chosen automatically, rotating
  topics, hooks and languages away from recent posts. Passing drafts are rendered and land in the
  Approvals review queue; the owner gets a Telegram message.
* `media_os.content.daily_drafts` (daily 08:00 IST): queues each brand's daily drafts, then puts
  approved, unscheduled Media OS carousels into free posting slots when auto-scheduling is on.
* `auto_schedule()`: the next free slot at the brand's posting times, through the normal
  `request_schedule` checks (lead time, daily cap, 5-minute spacing, queued requests).

Approval is always required: nothing is scheduled until the owner approves it. Settings live in
`brand.publishing_preferences["autopilot"]`. Owner uploads (Studio) are never auto-scheduled.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from media_os_api.content.taxonomy import HookType, Language, Occasion
from media_os_api.db.models import BrandProfile, ContentPackage, JobRun
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.jobs.ledger import enqueue_durable_job
from media_os_api.jobs.routing import queue_for_task

logger = logging.getLogger("media_os_api.autopilot")

GENERATE_TASK = "media_os.content.generate_carousel"
DAILY_TASK = "media_os.content.daily_drafts"
ACTOR = "system:autopilot"
MAX_PER_REQUEST = 3
MAX_PER_DAY = 10  # generation requests per brand per IST day (protects free AI quotas)
MAX_DAILY_DRAFTS = 3
RECENT_WINDOW = 12  # avoid repeating a topic/hook combination from the last N carousels

DEFAULTS: dict[str, Any] = {
    "daily_drafts": 1,  # 0 = off
    "auto_schedule": False,
    "post_times": ["19:00"],  # local brand time, HH:MM
    "platforms": ["INSTAGRAM", "FACEBOOK"],
}

# Occasions that suit each topic. Unapproved occasions are never chosen.
_PILLAR_OCCASIONS: dict[str, list[Occasion]] = {
    "wedding_season": [Occasion.WEDDING_HINDU, Occasion.WEDDING_MUSLIM],
    "wording_help": [Occasion.WEDDING_HINDU, Occasion.WEDDING_MUSLIM, Occasion.GENERAL],
    "other_occasions": [
        Occasion.GAYE_HOLUD,
        Occasion.BOUBHAT_WALIMA,
        Occasion.ANNAPRASHAN_AQIQAH,
        Occasion.BIRTHDAY,
        Occasion.UPANAYAN_GRIHAPRAVESH,
    ],
}
# Bengali-first audience: bilingual twice as often as Bengali-only or English-only.
_LANGUAGES = [Language.BN_EN, Language.BN, Language.BN_EN, Language.EN]


class AutopilotError(ValueError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- settings


def has_generator(brand: BrandProfile) -> bool:
    from media_os_api.carousel.brand_kit import KITS

    return brand.slug in KITS


def settings_for(brand: BrandProfile) -> dict[str, Any]:
    from media_os_api.publish.service import daily_cap

    stored = dict((brand.publishing_preferences or {}).get("autopilot") or {})
    out = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    if not has_generator(brand):
        out["daily_drafts"] = 0
    # Posts per day per platform: the same limit every scheduling path enforces.
    out["daily_caps"] = {p: daily_cap(brand, p) for p in CAP_PLATFORMS}
    return out


CAP_PLATFORMS = ("INSTAGRAM", "FACEBOOK")
MIN_CAP, MAX_CAP = 1, 10


def validate_caps(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise AutopilotError("Posts per day must be set per platform.", code="BAD_DAILY_CAPS")
    out = {}
    for p in CAP_PLATFORMS:
        if p not in raw:
            continue
        n = raw[p]
        if not isinstance(n, int) or isinstance(n, bool) or not MIN_CAP <= n <= MAX_CAP:
            raise AutopilotError(
                f"Posts per day must be {MIN_CAP}–{MAX_CAP}.", code="BAD_DAILY_CAPS"
            )
        out[p] = n
    return out


def validate_settings(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULTS)
    drafts = raw.get("daily_drafts", out["daily_drafts"])
    if not isinstance(drafts, int) or not 0 <= drafts <= MAX_DAILY_DRAFTS:
        raise AutopilotError("Daily drafts must be 0–3.", code="BAD_DAILY_DRAFTS")
    out["daily_drafts"] = drafts
    out["auto_schedule"] = bool(raw.get("auto_schedule", out["auto_schedule"]))
    times = raw.get("post_times", out["post_times"])
    if not isinstance(times, list) or not 1 <= len(times) <= 3:
        raise AutopilotError("Choose 1–3 posting times.", code="BAD_POST_TIMES")
    parsed = sorted({_hhmm(t) for t in times})
    out["post_times"] = [t.strftime("%H:%M") for t in parsed]
    platforms = [
        p for p in raw.get("platforms", out["platforms"]) if p in ("INSTAGRAM", "FACEBOOK")
    ]
    if not platforms:
        raise AutopilotError("Choose Instagram and/or Facebook.", code="NO_PLATFORM")
    out["platforms"] = platforms
    return out


def _hhmm(value: Any) -> time:
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except (ValueError, TypeError) as exc:
        raise AutopilotError(f"'{value}' isn't a time like 19:00.", code="BAD_POST_TIMES") from exc


def save_settings(brand: BrandProfile, raw: dict[str, Any]) -> dict[str, Any]:
    clean = validate_settings(raw)
    caps = validate_caps(raw["daily_caps"]) if raw.get("daily_caps") is not None else {}
    prefs = dict(brand.publishing_preferences or {})
    prefs["autopilot"] = clean
    if caps:
        # Other platforms' caps (e.g. YouTube) are kept as they are.
        prefs["daily_caps"] = {**dict(prefs.get("daily_caps") or {}), **caps}
    brand.publishing_preferences = prefs  # reassign so SQLAlchemy sees the JSON change
    return settings_for(brand)


# --------------------------------------------------------------------------- topic choice


def _recent_combos(session: Session, brand_id: uuid.UUID) -> list[tuple[str, str, str]]:
    rows = session.execute(
        select(ContentPackage.post_tags)
        .where(ContentPackage.brand_profile_id == brand_id, ContentPackage.format == "CAROUSEL")
        .order_by(ContentPackage.created_at.desc())
        .limit(RECENT_WINDOW)
    ).scalars()
    out = []
    for tags in rows:
        t = tags or {}
        out.append((str(t.get("pillar")), str(t.get("hook_type")), str(t.get("language"))))
    return out


def pick_brief(
    session: Session,
    brand: BrandProfile,
    *,
    pillar: str | None = None,
    hook: str | None = None,
    language: str | None = None,
    occasion: str | None = None,
    seed: int = 0,
) -> Any:
    """A brief for the next carousel. Blank fields rotate away from recent posts."""
    from media_os_api.carousel.schema import CarouselBrief
    from media_os_api.content.taxonomy import UNAPPROVED_OCCASIONS

    pillars = [p for p in (brand.content_pillars or []) if isinstance(p, str)]
    if not pillars:
        raise AutopilotError("This brand has no content topics set.", code="NO_PILLARS")
    if pillar is not None and pillar not in pillars:
        raise AutopilotError(f"Unknown topic '{pillar}'.", code="BAD_PILLAR")
    recent = _recent_combos(session, brand.id)
    used_pillars = [c[0] for c in recent]
    used_pairs = {(c[0], c[1]) for c in recent}
    used_langs = [c[2] for c in recent]

    chosen_pillar = pillar or min(
        pillars, key=lambda p: (used_pillars.count(p), (pillars.index(p) + seed) % len(pillars))
    )
    hooks = list(HookType)
    if hook is not None:
        chosen_hook = HookType(hook)
    else:
        fresh = [h for h in hooks if (chosen_pillar, h.value) not in used_pairs]
        pool = fresh or hooks
        chosen_hook = pool[(len(recent) + seed) % len(pool)]
    if language is not None:
        chosen_lang = Language(language)
    else:
        chosen_lang = min(
            _LANGUAGES,
            key=lambda lang: (
                used_langs.count(lang.value) / _LANGUAGES.count(lang),
                _LANGUAGES.index(lang),
            ),
        )
    if occasion is not None:
        chosen_occ = Occasion(occasion)
    else:
        options = [
            o
            for o in _PILLAR_OCCASIONS.get(chosen_pillar, [Occasion.GENERAL])
            if o not in UNAPPROVED_OCCASIONS
        ] or [Occasion.GENERAL]
        chosen_occ = options[(len(recent) + seed) % len(options)]
    if chosen_hook is HookType.SEASONAL and chosen_occ is Occasion.GENERAL:
        chosen_hook = HookType.QUESTION  # a seasonal hook needs an occasion to be about
    return CarouselBrief(
        pillar=chosen_pillar,
        hook_type=chosen_hook,
        occasion=chosen_occ,
        language=chosen_lang,
    )


# --------------------------------------------------------------------------- requests


def ist_day(now: datetime, tz: str) -> date:
    return now.astimezone(ZoneInfo(tz)).date()


def requests_today(session: Session, brand: BrandProfile, now: datetime) -> int:
    start = datetime.combine(ist_day(now, brand.timezone), time(0), ZoneInfo(brand.timezone))
    return int(
        session.execute(
            select(func.count())
            .select_from(JobRun)
            .where(
                JobRun.task_name == GENERATE_TASK,
                JobRun.brand_profile_id == brand.id,
                JobRun.created_at >= start,
            )
        ).scalar_one()
    )


def request_generation(
    session: Session,
    brand: BrandProfile,
    *,
    count: int,
    brief: dict[str, Any],
    trigger: str,
    key_prefix: str | None = None,
    now: datetime | None = None,
) -> list[JobRun]:
    now = now or datetime.now(UTC)
    if not has_generator(brand):
        raise AutopilotError("Post generation isn't set up for this brand.", code="NO_GENERATOR")
    if not 1 <= count <= MAX_PER_REQUEST:
        raise AutopilotError("Generate 1–3 posts at a time.", code="BAD_COUNT")
    if requests_today(session, brand, now) + count > MAX_PER_DAY:
        raise AutopilotError(
            f"That's more than {MAX_PER_DAY} posts generated today (free AI limits). "
            "Try again tomorrow.",
            code="DAILY_LIMIT",
        )
    clean = {
        k: v for k, v in brief.items() if k in ("pillar", "hook", "language", "occasion") and v
    }
    design = str(brief.get("design") or "auto")
    # Validate overrides now, so the owner sees a mistake straight away.
    chosen = pick_brief(session, brand, **clean)
    from media_os_api.content.designs import DesignError, choose_design

    try:
        choose_design(session, brand, design, chosen.occasion.value)
    except DesignError as exc:
        raise AutopilotError(str(exc), code=exc.code) from exc
    stamp = now.strftime("%Y%m%dT%H%M%S")
    jobs = []
    for i in range(count):
        job, _ = enqueue_durable_job(
            session,
            task_name=GENERATE_TASK,
            task_type="CONTENT_GENERATION",
            payload={
                "brand": brand.slug,
                "brief": clean,
                "design": design,
                "trigger": trigger,
                "seed": i,
            },
            idempotency_key=f"{key_prefix or 'generate:' + brand.slug + ':' + stamp}:{i}",
            brand_profile_id=brand.id,
            queue_name=queue_for_task(GENERATE_TASK),
            maximum_attempts=2,
        )
        jobs.append(job)
    return jobs


# --------------------------------------------------------------------------- worker side


def handle_generate_carousel(session: Session, job: JobRun) -> dict[str, Any]:
    from media_os_api.carousel.admin import produce
    from media_os_api.notify.messages import e, short
    from media_os_api.notify.service import dashboard_url, queue_alert

    payload = dict(job.payload or {})
    brand = session.execute(
        select(BrandProfile).where(BrandProfile.slug == str(payload.get("brand", "")))
    ).scalar_one_or_none()
    if brand is None:
        raise PermanentTaskError("Brand not found.", code="NOT_FOUND")
    try:
        brief = pick_brief(
            session, brand, seed=int(payload.get("seed", 0)), **payload.get("brief", {})
        )
    except (AutopilotError, ValueError) as exc:
        raise PermanentTaskError(str(exc), code="BAD_BRIEF") from exc
    from media_os_api.content.designs import DesignError, choose_design

    try:
        design = choose_design(
            session, brand, str(payload.get("design") or "auto"), brief.occasion.value
        )
    except DesignError:
        design = None  # the chosen set was switched off or emptied since: use the built-in design
    # produce() saves the package in its own transaction (it records AI usage as it goes), on the
    # worker's own engine.
    from sqlalchemy import Engine

    from media_os_api.db.session import create_session_factory

    bind = session.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    from media_os_api.carousel.feedback import notes_block, owner_notes
    from media_os_api.carousel.novelty import recent_posts

    result = produce(
        brand.slug,
        brief,
        save=True,
        factory=create_session_factory(engine),
        design=design,
        recent=recent_posts(session, brand.id),  # don't repeat a recent post's subject
        guidance=notes_block(owner_notes(session, brand.id)),  # the owner's review notes
    )
    passed = bool(result.get("qa", {}).get("passed"))
    similar = next(
        (
            str(c.get("detail") or "")
            for c in result.get("qa", {}).get("checks", [])
            if c.get("name") == "novelty" and c.get("status") == "WARN"
        ),
        "",
    )
    title = ""
    slides = (result.get("draft") or {}).get("slides") or []
    if slides:
        first = slides[0]
        title = str(first.get("headline_en") or first.get("headline_bn") or "")
    pid = result.get("content_package_id", "")
    if passed:
        text = (
            f"✍️ <b>New post ready for review</b>\n{e(short(title.replace('*', ''), 80))}\n"
            f"Topic: {e(brief.pillar.replace('_', ' '))} · {e(brief.language.value)} · "
            f"Design: {e(design['name'] if design else 'Built-in')}\n"
            + (f"⚠️ {e(similar)}\n" if similar else "")
            + "The slides are rendering now. "
            + f'<a href="{e(dashboard_url())}/approvals">Review it</a>'
        )
    else:
        text = (
            f"✍️ <b>A new draft needs fixes</b>\n{e(short(title.replace('*', ''), 80))}\n"
            "It didn't pass the automatic checks, so it won't be posted. You can re-check it in "
            "Approvals or generate another."
        )
    queue_alert(session, key=f"draft:{pid or job.id}", text=text, severity="info", silent=True)
    return {
        "content_package_id": pid,
        "qa_passed": passed,
        "pillar": brief.pillar,
        "hook": brief.hook_type.value,
        "language": brief.language.value,
        "written_by": result.get("written_by"),
        "design": design["name"] if design else "Built-in",
    }


def handle_daily_drafts(
    session: Session, job: JobRun, *, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    queued: dict[str, int] = {}
    scheduled: dict[str, int] = {}
    brands = session.execute(select(BrandProfile).where(BrandProfile.is_active.is_(True))).scalars()
    for brand in brands:
        cfg = settings_for(brand)
        n = int(cfg["daily_drafts"])
        if n > 0:
            day = ist_day(now, brand.timezone).isoformat()
            try:
                jobs = request_generation(
                    session,
                    brand,
                    count=n,
                    brief={},
                    trigger="daily",
                    key_prefix=f"daily:{brand.slug}:{day}",
                    now=now,
                )
                queued[brand.slug] = len(jobs)
            except AutopilotError as exc:
                logger.info("daily drafts skipped for %s: %s", brand.slug, exc.code)
        if cfg["auto_schedule"]:
            scheduled[brand.slug] = schedule_waiting(session, brand, now=now)
    return {"queued": queued, "auto_scheduled": scheduled}


# --------------------------------------------------------------------------- auto-scheduling


def _is_generated(pkg: ContentPackage) -> bool:
    from media_os_api.content.uploads import is_owner_upload

    return pkg.format == "CAROUSEL" and not is_owner_upload(pkg)


def auto_schedule(
    session: Session, pkg: ContentPackage, brand: BrandProfile, *, now: datetime | None = None
) -> datetime | None:
    """Put an approved Media OS carousel into the next free posting slot. Returns the time, or
    None when it isn't eligible or no slot is free in the next 30 days."""
    from media_os_api.publish.service import (
        ScheduleRequestError,
        can_schedule,
        package_schedules,
        request_schedule,
    )

    now = now or datetime.now(UTC)
    cfg = settings_for(brand)
    if not cfg["auto_schedule"] or not _is_generated(pkg):
        return None
    if not can_schedule(pkg, package_schedules(session, pkg)):
        return None
    tz = ZoneInfo(brand.timezone)
    times = [_hhmm(t) for t in cfg["post_times"]]
    today = now.astimezone(tz).date()
    for offset in range(0, 30):
        day = today + timedelta(days=offset)
        for t in times:
            when = datetime.combine(day, t, tz)
            if when <= now:
                continue
            try:
                with session.begin_nested():
                    request_schedule(
                        session,
                        pkg=pkg,
                        brand=brand,
                        when=when,
                        platforms=list(cfg["platforms"]),
                        actor_id=ACTOR,
                        now=now,
                    )
                return when
            except ScheduleRequestError as exc:
                if exc.code in ("DAILY_CAP", "SLOT_TAKEN", "TOO_SOON", "SAME_TIME"):
                    continue
                logger.info("auto-schedule of %s stopped: %s", pkg.id, exc.code)
                return None
    return None


def schedule_waiting(session: Session, brand: BrandProfile, *, now: datetime | None = None) -> int:
    """Approved Media OS carousels that aren't scheduled yet, oldest first, into free slots."""
    from media_os_api.publish.service import SCHEDULE_TASK

    waiting = session.execute(
        select(ContentPackage)
        .where(
            ContentPackage.brand_profile_id == brand.id,
            ContentPackage.format == "CAROUSEL",
            ContentPackage.approval_status == "APPROVED",
            ContentPackage.status == "QA_PASSED",
        )
        .order_by(ContentPackage.created_at)
    ).scalars()
    done = 0
    for pkg in waiting:
        in_flight = session.execute(
            select(JobRun.id).where(
                JobRun.task_name == SCHEDULE_TASK,
                JobRun.status.in_(("PENDING", "DISPATCHED", "RUNNING", "FAILED_RETRYABLE")),
                JobRun.payload["content_package_id"].astext == str(pkg.id),
            )
        ).first()
        if in_flight is None and auto_schedule(session, pkg, brand, now=now) is not None:
            done += 1
    return done
