"""Autopilot API: generate posts on demand, and the brand's daily drafts / auto-scheduling.

* `GET  /api/v1/content/generate/options?brand=` — topics, hooks, languages, today's usage.
* `POST /api/v1/content/generate?brand=` (CSRF) — queue 1–3 carousels (blank = Media OS chooses).
* `GET  /api/v1/content/generate/recent?brand=` — the latest generation requests and outcomes.
* `GET  /api/v1/brands/{ref}/autopilot` · `PUT` (CSRF) — daily drafts, auto-schedule, post times.

Generated carousels go to the Approvals review queue. Nothing is scheduled without approval.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.api.routes.content import _brand_id, _db
from media_os_api.auth import SessionPayload, require_csrf, require_session
from media_os_api.content import autopilot as ap
from media_os_api.content.designs import is_ready, list_templates
from media_os_api.content.taxonomy import UNAPPROVED_OCCASIONS, HookType, Language, Occasion
from media_os_api.db.models import BrandProfile, JobRun, ScheduleDefinition
from media_os_api.services.audit_service import record_audit_event

router = APIRouter(tags=["autopilot"])
BrandParam = Annotated[str, Query(min_length=1, max_length=128)]

HOOK_LABELS = {
    "QUESTION": "Question",
    "PAIN_POINT": "Pain point",
    "BEFORE_AFTER": "Before / after",
    "SPEED_DEMO": "Speed demo",
    "HOW_TO": "How-to",
    "NUMBER_LIST": "Numbered list",
    "MISTAKE": "Common mistake",
    "SEASONAL": "Seasonal",
    "SHOWCASE": "Showcase",
    "CURIOSITY": "Curiosity",
}
LANGUAGE_LABELS = {"BN_EN": "Bengali + English", "BN": "Bengali", "EN": "English"}


def _brand(db: Session, ref: str) -> BrandProfile:
    brand = db.get(BrandProfile, _brand_id(db, ref))
    if brand is None:  # pragma: no cover - _brand_id already 404s
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "BRAND_NOT_FOUND"})
    return brand


def _fail(exc: ap.AutopilotError) -> HTTPException:
    code = status.HTTP_429_TOO_MANY_REQUESTS if exc.code == "DAILY_LIMIT" else 422
    return HTTPException(code, {"code": exc.code, "message": str(exc)})


@router.get("/content/generate/options")
def generate_options(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
) -> dict[str, Any]:
    with _db(request) as db:
        profile = _brand(db, brand)
        return {
            "can_generate": ap.has_generator(profile),
            "pillars": [
                {"value": p, "label": p.replace("_", " ").capitalize()}
                for p in profile.content_pillars or []
                if isinstance(p, str)
            ],
            "hooks": [
                {"value": h.value, "label": HOOK_LABELS.get(h.value, h.value)} for h in HookType
            ],
            "languages": [
                {"value": lang.value, "label": LANGUAGE_LABELS[lang.value]} for lang in Language
            ],
            "occasions": [
                {"value": o.value, "label": o.value.replace("_", " ").title()}
                for o in Occasion
                if o not in UNAPPROVED_OCCASIONS
            ],
            "generated_today": ap.requests_today(db, profile, datetime.now(UTC)),
            "daily_limit": ap.MAX_PER_DAY,
            "max_per_request": ap.MAX_PER_REQUEST,
            "designs": [
                {"value": "auto", "label": "Let Media OS choose"},
                {"value": "builtin", "label": "Built-in design"},
            ]
            + [
                {"value": str(t.id), "label": t.name}
                for t in list_templates(db, profile.id)
                if t.is_active and is_ready(db, t)
            ],
        }


class GenerateRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=ap.MAX_PER_REQUEST)
    pillar: str | None = Field(default=None, max_length=64)
    hook: str | None = Field(default=None, max_length=32)
    language: str | None = Field(default=None, max_length=8)
    occasion: str | None = Field(default=None, max_length=32)
    design: str | None = Field(default=None, max_length=64)  # "auto", "builtin" or a template id


@router.post("/content/generate", status_code=202)
def generate(
    payload: GenerateRequest,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> dict[str, Any]:
    with _db(request) as db:
        profile = _brand(db, brand)
        try:
            jobs = ap.request_generation(
                db,
                profile,
                count=payload.count,
                brief={
                    "pillar": payload.pillar,
                    "hook": payload.hook,
                    "language": payload.language,
                    "occasion": payload.occasion,
                    "design": payload.design,
                },
                trigger="owner",
            )
        except ap.AutopilotError as exc:
            raise _fail(exc) from exc
        except ValueError as exc:  # an unknown hook/language/occasion value
            raise HTTPException(
                422, {"code": "BAD_BRIEF", "message": f"Invalid choice: {exc}"}
            ) from exc
        record_audit_event(
            db,
            actor_id=f"owner:{session.subject}",
            action="GENERATION_REQUESTED",
            target_type="brand_profile",
            target_id=str(profile.id),
            outcome="SUCCESS",
            metadata={"count": payload.count},
        )
        return {"queued": len(jobs), "job_ids": [str(j.id) for j in jobs]}


@router.get("/content/generate/recent")
def generate_recent(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
    limit: Annotated[int, Query(ge=1, le=30)] = 10,
) -> dict[str, Any]:
    with _db(request) as db:
        profile = _brand(db, brand)
        rows = db.execute(
            select(JobRun)
            .where(JobRun.task_name == ap.GENERATE_TASK, JobRun.brand_profile_id == profile.id)
            .order_by(JobRun.created_at.desc())
            .limit(limit)
        ).scalars()
        items = []
        for j in rows:
            result = j.result_payload or {}
            items.append(
                {
                    "id": str(j.id),
                    "status": j.status,
                    "trigger": (j.payload or {}).get("trigger"),
                    "created_at": j.created_at.isoformat(),
                    "content_package_id": result.get("content_package_id"),
                    "qa_passed": result.get("qa_passed"),
                    "pillar": result.get("pillar"),
                    "error_code": j.last_error_code,
                }
            )
        return {"items": items}


class AutopilotSettings(BaseModel):
    daily_drafts: int = Field(ge=0, le=ap.MAX_DAILY_DRAFTS)
    auto_schedule: bool
    post_times: list[str] = Field(min_length=1, max_length=3)
    platforms: list[str] = Field(default=["INSTAGRAM", "FACEBOOK"], min_length=1, max_length=2)


def _settings_view(db: Session, profile: BrandProfile) -> dict[str, Any]:
    nxt = db.execute(
        select(ScheduleDefinition.next_run_at, ScheduleDefinition.is_enabled).where(
            ScheduleDefinition.name == "content.daily_drafts"
        )
    ).first()
    return {
        **ap.settings_for(profile),
        "can_generate": ap.has_generator(profile),
        "next_daily_run": nxt[0].isoformat() if nxt and nxt[0] and nxt[1] else None,
        "timezone": profile.timezone,
    }


@router.get("/brands/{brand_ref}/autopilot")
def get_autopilot(
    brand_ref: str,
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
) -> dict[str, Any]:
    with _db(request) as db:
        return _settings_view(db, _brand(db, brand_ref))


@router.put("/brands/{brand_ref}/autopilot")
def put_autopilot(
    brand_ref: str,
    payload: AutopilotSettings,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
) -> dict[str, Any]:
    with _db(request) as db:
        profile = _brand(db, brand_ref)
        try:
            ap.save_settings(profile, payload.model_dump())
        except ap.AutopilotError as exc:
            raise _fail(exc) from exc
        record_audit_event(
            db,
            actor_id=f"owner:{session.subject}",
            action="AUTOPILOT_SETTINGS_CHANGED",
            target_type="brand_profile",
            target_id=str(profile.id),
            outcome="SUCCESS",
            metadata=payload.model_dump(),
        )
        # Turning auto-scheduling on also places posts that were already approved and waiting.
        if payload.auto_schedule:
            ap.schedule_waiting(db, profile)
        db.flush()
        return _settings_view(db, profile)
