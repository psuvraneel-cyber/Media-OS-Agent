"""Design templates: the owner's own carousel backgrounds, and choosing a design per post.

A template set has three backgrounds (COVER for the hook slide, CONTENT for the middle slides,
CTA for the closing slide), each with a text area (top / middle / bottom) and a text tone
(light text for dark artwork, dark text for light artwork). Images are uploads (TEMPLATE_IMAGE
assets, 4:5, up to 8 MB).

When a carousel is generated, the chosen design is copied into its `source_manifest` (paths,
areas, tones), so later edits to the set never change a post that was already written.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.content import uploads as up
from media_os_api.db.models import Asset, BrandProfile, ContentPackage, DesignTemplate

ROLES = ("COVER", "CONTENT", "CTA")
AREAS = ("top", "middle", "bottom")
TONES = ("light", "dark")
TEMPLATE_IMAGE = "TEMPLATE_IMAGE"
BUILTIN = "builtin"
AUTO = "auto"
DEFAULT_SLIDE = {"area": "middle", "tone": "light"}
MAX_TEMPLATES = 20
RECENT = 12


class DesignError(ValueError):
    def __init__(self, message: str, *, code: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def slide_settings(t: DesignTemplate, role: str) -> dict[str, Any]:
    raw = dict((t.slides or {}).get(role) or {})
    return {
        "asset_id": raw.get("asset_id"),
        "area": raw.get("area") if raw.get("area") in AREAS else DEFAULT_SLIDE["area"],
        "tone": raw.get("tone") if raw.get("tone") in TONES else DEFAULT_SLIDE["tone"],
    }


def _assets(session: Session, t: DesignTemplate) -> dict[str, Asset]:
    ids = []
    for role in ROLES:
        aid = slide_settings(t, role)["asset_id"]
        if aid:
            try:
                ids.append(uuid.UUID(str(aid)))
            except ValueError:
                continue
    if not ids:
        return {}
    rows = session.execute(
        select(Asset).where(
            Asset.id.in_(ids), Asset.status == "ACTIVE", Asset.asset_type == TEMPLATE_IMAGE
        )
    ).scalars()
    return {str(a.id): a for a in rows}


def is_ready(session: Session, t: DesignTemplate) -> bool:
    found = _assets(session, t)
    return all(slide_settings(t, r)["asset_id"] in found for r in ROLES)


def view(session: Session, t: DesignTemplate) -> dict[str, Any]:
    found = _assets(session, t)
    slides = {}
    for role in ROLES:
        s = slide_settings(t, role)
        a = found.get(str(s["asset_id"])) if s["asset_id"] else None
        slides[role] = {
            "asset_id": str(a.id) if a else None,
            "width": a.width if a else None,
            "height": a.height if a else None,
            "area": s["area"],
            "tone": s["tone"],
        }
    return {
        "id": str(t.id),
        "name": t.name,
        "is_active": t.is_active,
        "auto_allowed": t.auto_allowed,
        "occasions": list(t.occasions or []),
        "slides": slides,
        "ready": all(slides[r]["asset_id"] for r in ROLES),
        "updated_at": t.updated_at.isoformat(),
    }


def list_templates(session: Session, brand_id: uuid.UUID) -> list[DesignTemplate]:
    return list(
        session.execute(
            select(DesignTemplate)
            .where(DesignTemplate.brand_profile_id == brand_id)
            .order_by(DesignTemplate.created_at)
        ).scalars()
    )


def get_template(session: Session, brand_id: uuid.UUID, template_id: uuid.UUID) -> DesignTemplate:
    t = session.get(DesignTemplate, template_id)
    if t is None or t.brand_profile_id != brand_id:
        raise DesignError("Template not found.", code="NOT_FOUND", status=404)
    return t


def create_template(session: Session, brand: BrandProfile, name: str) -> DesignTemplate:
    clean = name.strip()[:80]
    if not clean:
        raise DesignError("Give the template a name.", code="NO_NAME")
    if len(list_templates(session, brand.id)) >= MAX_TEMPLATES:
        raise DesignError(f"Up to {MAX_TEMPLATES} template sets per brand.", code="TOO_MANY")
    t = DesignTemplate(
        brand_profile_id=brand.id,
        name=clean,
        is_active=True,
        auto_allowed=True,
        occasions=[],
        slides={r: dict(DEFAULT_SLIDE) for r in ROLES},
    )
    session.add(t)
    session.flush()
    return t


def update_template(t: DesignTemplate, patch: dict[str, Any]) -> None:
    from media_os_api.content.taxonomy import UNAPPROVED_OCCASIONS, Occasion

    if patch.get("name") is not None:
        clean = str(patch["name"]).strip()[:80]
        if not clean:
            raise DesignError("Give the template a name.", code="NO_NAME")
        t.name = clean
    for key in ("is_active", "auto_allowed"):
        if patch.get(key) is not None:
            setattr(t, key, bool(patch[key]))
    if patch.get("occasions") is not None:
        allowed = {o.value for o in Occasion if o not in UNAPPROVED_OCCASIONS}
        occ = [str(o) for o in patch["occasions"]]
        bad = [o for o in occ if o not in allowed]
        if bad:
            raise DesignError(f"Unknown occasion: {', '.join(bad)}.", code="BAD_OCCASION")
        t.occasions = sorted(set(occ))
    if patch.get("slides") is not None:
        slides = {r: dict((t.slides or {}).get(r) or DEFAULT_SLIDE) for r in ROLES}
        for role, change in dict(patch["slides"]).items():
            if role not in ROLES or not isinstance(change, dict):
                raise DesignError(f"Unknown slide '{role}'.", code="BAD_SLIDE")
            if "area" in change:
                if change["area"] not in AREAS:
                    raise DesignError("Text area must be top, middle or bottom.", code="BAD_AREA")
                slides[role]["area"] = change["area"]
            if "tone" in change:
                if change["tone"] not in TONES:
                    raise DesignError("Text colour must be light or dark.", code="BAD_TONE")
                slides[role]["tone"] = change["tone"]
        t.slides = slides  # reassign so SQLAlchemy sees the JSON change


def set_image(
    session: Session,
    brand: BrandProfile,
    t: DesignTemplate,
    role: str,
    data: bytes,
    mime: str,
) -> Asset:
    if role not in ROLES:
        raise DesignError(f"Unknown slide '{role}'.", code="BAD_SLIDE", status=404)
    width, height = up.image_size(data)
    if not width or not height:
        raise DesignError("That image couldn't be opened.", code="NOT_AN_IMAGE")
    if width < 600:
        raise DesignError("Use an image at least 1080 px wide.", code="TOO_SMALL")
    if abs(width / height - 0.8) > 0.03:
        raise DesignError(
            f"The image is {width}×{height}. Carousel backgrounds must be 4:5, e.g. 1080×1350.",
            code="WRONG_SHAPE",
        )
    relative, dest = up.new_upload_path(brand, "templates", up.IMAGE_TYPES[mime])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    import hashlib

    asset = Asset(
        brand_profile_id=brand.id,
        content_package_id=None,
        asset_type=TEMPLATE_IMAGE,
        local_path=relative,
        mime_type=mime,
        file_size=len(data),
        checksum=f"sha256:{hashlib.sha256(data).hexdigest()}",
        width=width,
        height=height,
        status="ACTIVE",
    )
    session.add(asset)
    session.flush()
    old = slide_settings(t, role)["asset_id"]
    if old:
        prev = session.get(Asset, uuid.UUID(str(old)))
        if prev is not None and prev.asset_type == TEMPLATE_IMAGE:
            up.retire_asset(prev)
    slides = {r: dict((t.slides or {}).get(r) or DEFAULT_SLIDE) for r in ROLES}
    slides[role]["asset_id"] = str(asset.id)
    t.slides = slides
    return asset


def delete_template(session: Session, t: DesignTemplate) -> None:
    for a in _assets(session, t).values():
        up.retire_asset(a)
    session.delete(t)


# --------------------------------------------------------------------------- choosing


def snapshot(session: Session, t: DesignTemplate) -> dict[str, Any]:
    """What the renderer needs, copied into the post so later edits never change it."""
    found = _assets(session, t)
    slides = {}
    for role in ROLES:
        s = slide_settings(t, role)
        a = found.get(str(s["asset_id"]))
        if a is None:
            raise DesignError(f"'{t.name}' is missing its {role.lower()} image.", code="NOT_READY")
        slides[role] = {"local_path": a.local_path, "area": s["area"], "tone": s["tone"]}
    return {"kind": "design_template", "template_id": str(t.id), "name": t.name, "slides": slides}


def design_of(pkg: ContentPackage) -> dict[str, Any] | None:
    for item in pkg.source_manifest or []:
        if isinstance(item, dict) and item.get("kind") == "design_template":
            return item
    return None


def _recent_designs(session: Session, brand_id: uuid.UUID) -> list[str]:
    rows = session.execute(
        select(ContentPackage.source_manifest)
        .where(ContentPackage.brand_profile_id == brand_id, ContentPackage.format == "CAROUSEL")
        .order_by(ContentPackage.created_at.desc())
        .limit(RECENT)
    ).scalars()
    out = []
    for manifest in rows:
        found = BUILTIN
        for item in manifest or []:
            if isinstance(item, dict) and item.get("kind") == "design_template":
                found = str(item.get("template_id"))
        out.append(found)
    return out


def choose_design(
    session: Session,
    brand: BrandProfile,
    requested: str | None,
    occasion: str | None,
) -> dict[str, Any] | None:
    """The design for a new post: None is the built-in design, otherwise a template snapshot.

    `requested` is "builtin", a template id, or "auto"/None (Media OS chooses: the least recently
    used of the built-in design and every active, ready set that allows automatic use; sets
    tagged for the post's occasion are preferred over untagged ones).
    """
    if requested == BUILTIN:
        return None
    if requested and requested != AUTO:
        try:
            tid = uuid.UUID(str(requested))
        except ValueError as exc:
            raise DesignError("Unknown design.", code="BAD_DESIGN") from exc
        t = get_template(session, brand.id, tid)
        if not t.is_active:
            raise DesignError(f"'{t.name}' is switched off.", code="NOT_ACTIVE")
        return snapshot(session, t)
    usable = [
        t
        for t in list_templates(session, brand.id)
        if t.is_active and t.auto_allowed and is_ready(session, t)
    ]
    if not usable:
        return None
    tagged = [t for t in usable if occasion and occasion in (t.occasions or [])]
    untagged = [t for t in usable if not t.occasions]
    pool: list[DesignTemplate | None] = list(tagged or untagged)
    if not tagged:
        pool.append(None)  # the built-in design stays in the rotation unless a set fits better
    if not pool:
        return None
    recent = _recent_designs(session, brand.id)

    def key(t: DesignTemplate | None) -> tuple[int, int]:
        ident = BUILTIN if t is None else str(t.id)
        last = recent.index(ident) if ident in recent else len(recent) + 1
        return (-last, recent.count(ident))  # least recently used first

    best = sorted(pool, key=key)[0]
    return None if best is None else snapshot(session, best)
