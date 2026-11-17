"""Design templates API: the owner's own carousel backgrounds.

* `GET    /api/v1/content/templates?brand=` — every template set, with readiness.
* `POST   /api/v1/content/templates?brand=` (CSRF) — create a set `{name}`.
* `PATCH  /api/v1/content/templates/{id}?brand=` (CSRF) — name, on/off, automatic use, occasions,
  and per slide (COVER / CONTENT / CTA) the text area and tone.
* `PUT    /api/v1/content/templates/{id}/images/{role}?brand=` (CSRF, raw PNG/JPEG/WebP, 4:5,
  ≤8 MB) — set one background.
* `DELETE /api/v1/content/templates/{id}?brand=` (CSRF) — delete the set and its images. Posts
  already written keep the design they were made with.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from media_os_api.api.routes.content import _brand_id, _db
from media_os_api.api.routes.studio import _read_small, _too_big
from media_os_api.auth import SessionPayload, require_csrf, require_session
from media_os_api.content import designs as ds
from media_os_api.content import uploads as up
from media_os_api.db.models import BrandProfile
from media_os_api.services.audit_service import record_audit_event

router = APIRouter(tags=["designs"])
BrandParam = Annotated[str, Query(min_length=1, max_length=128)]


def _fail(exc: ds.DesignError) -> HTTPException:
    return HTTPException(exc.status, {"code": exc.code, "message": str(exc)})


def _brand(db: Session, ref: str) -> BrandProfile:
    brand = db.get(BrandProfile, _brand_id(db, ref))
    if brand is None:  # pragma: no cover - _brand_id already 404s
        raise HTTPException(status.HTTP_404_NOT_FOUND, {"code": "BRAND_NOT_FOUND"})
    return brand


def _audit(db: Session, session: SessionPayload, action: str, target: str) -> None:
    record_audit_event(
        db,
        actor_id=f"owner:{session.subject}",
        action=action,
        target_type="design_template",
        target_id=target,
        outcome="SUCCESS",
    )


@router.get("/content/templates")
def list_templates(
    request: Request,
    _session: Annotated[SessionPayload, Depends(require_session)],
    brand: BrandParam,
) -> dict[str, Any]:
    with _db(request) as db:
        profile = _brand(db, brand)
        return {"items": [ds.view(db, t) for t in ds.list_templates(db, profile.id)]}


class TemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/content/templates", status_code=201)
def create_template(
    payload: TemplateCreate,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> dict[str, Any]:
    with _db(request) as db:
        profile = _brand(db, brand)
        try:
            t = ds.create_template(db, profile, payload.name)
        except ds.DesignError as exc:
            raise _fail(exc) from exc
        _audit(db, session, "DESIGN_TEMPLATE_CREATED", str(t.id))
        return ds.view(db, t)


class SlidePatch(BaseModel):
    area: Literal["top", "middle", "bottom"] | None = None
    tone: Literal["light", "dark"] | None = None


class TemplatePatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    is_active: bool | None = None
    auto_allowed: bool | None = None
    occasions: list[str] | None = Field(default=None, max_length=20)
    slides: dict[Literal["COVER", "CONTENT", "CTA"], SlidePatch] | None = None


@router.patch("/content/templates/{template_id}")
def patch_template(
    template_id: uuid.UUID,
    payload: TemplatePatch,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> dict[str, Any]:
    with _db(request) as db:
        profile = _brand(db, brand)
        try:
            t = ds.get_template(db, profile.id, template_id)
            patch = payload.model_dump(exclude_none=True)
            if payload.slides is not None:
                patch["slides"] = {
                    role: s.model_dump(exclude_none=True) for role, s in payload.slides.items()
                }
            ds.update_template(t, patch)
        except ds.DesignError as exc:
            raise _fail(exc) from exc
        db.flush()
        _audit(db, session, "DESIGN_TEMPLATE_CHANGED", str(t.id))
        return ds.view(db, t)


@router.put("/content/templates/{template_id}/images/{role}")
async def set_template_image(
    template_id: uuid.UUID,
    role: Literal["COVER", "CONTENT", "CTA"],
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> dict[str, Any]:
    _too_big(request, up.MAX_POST_IMAGE_BYTES)
    data = await _read_small(request, up.MAX_POST_IMAGE_BYTES)
    mime = up.sniff_image(data[:64])
    if mime is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"code": "NOT_AN_IMAGE", "message": "Use a PNG, JPEG or WebP image."},
        )
    with _db(request) as db:
        profile = _brand(db, brand)
        try:
            t = ds.get_template(db, profile.id, template_id)
            ds.set_image(db, profile, t, role, data, mime)
        except ds.DesignError as exc:
            raise _fail(exc) from exc
        db.flush()
        _audit(db, session, "DESIGN_TEMPLATE_IMAGE_SET", str(t.id))
        return ds.view(db, t)


@router.delete("/content/templates/{template_id}", status_code=204)
def delete_template(
    template_id: uuid.UUID,
    request: Request,
    session: Annotated[SessionPayload, Depends(require_csrf)],
    brand: BrandParam,
) -> None:
    with _db(request) as db:
        profile = _brand(db, brand)
        try:
            t = ds.get_template(db, profile.id, template_id)
        except ds.DesignError as exc:
            raise _fail(exc) from exc
        ds.delete_template(db, t)
        _audit(db, session, "DESIGN_TEMPLATE_DELETED", str(template_id))
