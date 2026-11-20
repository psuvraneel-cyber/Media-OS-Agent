"""Durable job `media_os.creative.render_carousel` (creative queue, creative container only).

Payload: {"content_package_id": "<uuid>"} — ids only, never content.
DRAFTED → render slides → Asset rows → render QA merged into the QA report → RENDERED, then
QA_PASSED when every text and render check passes (the package then appears in the approval queue).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from media_os_api.carousel.brand_kit import kit_for
from media_os_api.carousel.qa import Check
from media_os_api.carousel.schema import Slide
from media_os_api.content.taxonomy import Language
from media_os_api.db.models import Asset, BrandProfile, ContentPackage, JobRun
from media_os_api.jobs.errors import PermanentTaskError
from media_os_api.workflow.state_machine import ContentLifecycleStatus, transition_content_status

RENDER_TASK = "media_os.creative.render_carousel"
ACTOR = "worker:creative"
INSTAGRAM_MAX_BYTES = 8 * 1024 * 1024
SLIDE_ASSET_TYPE = "CAROUSEL_SLIDE"


FESTIVE_OCCASIONS = {"WEDDING_HINDU", "WEDDING_MUSLIM", "GAYE_HOLUD", "BOUBHAT_WALIMA", "PUJA"}


def is_festive(tags: dict[str, Any]) -> bool:
    """Marigold garland on the hook: celebrations only, never shraddha/memorial."""
    if tags.get("occasion") == "SHRADDHA_MEMORIAL":
        return False
    return (
        tags.get("occasion") in FESTIVE_OCCASIONS
        or tags.get("hook_type") == "SEASONAL"
        or tags.get("pillar") == "wedding_season"
    )


def relative_slide_dir(pkg: ContentPackage) -> Path:
    return Path("carousels") / str(pkg.id) / f"v{pkg.version}"


def render_checks(rendered: list[Any], expected: int) -> list[Check]:
    problems = [f"slide {r.index}: {p}" for r in rendered for p in r.problems]
    size = [f"slide {r.index}: {r.size} bytes" for r in rendered if r.size > INSTAGRAM_MAX_BYTES]
    dims = [f"slide {r.index}" for r in rendered if (r.width, r.height) != (1080, 1350)]
    count = [] if len(rendered) == expected else [f"{len(rendered)} of {expected} slides rendered"]

    def chk(name: str, items: list[str]) -> Check:
        return Check(name=name, status="FAIL" if items else "PASS", detail="; ".join(items)[:500])

    return [
        chk("render_text_fits", problems),
        chk("render_dimensions", dims),
        chk("render_file_size", size),
        chk("render_slide_count", count),
    ]


def design_inputs(
    pkg: ContentPackage,
) -> tuple[dict[str, Path] | None, dict[str, dict[str, str]] | None, list[str]]:
    """The owner's template backgrounds saved with this post: files, area/tone per slide, and the
    slides whose image is missing (those fall back to the built-in design)."""
    from media_os_api.content.designs import design_of
    from media_os_api.content.uploads import resolve_asset_path

    design = design_of(pkg)
    if not design:
        return None, None, []
    files: dict[str, Path] = {}
    settings: dict[str, dict[str, str]] = {}
    missing: list[str] = []
    for role, s in dict(design.get("slides") or {}).items():
        path = resolve_asset_path(str(s.get("local_path", "")))
        if path is None or not path.is_file():
            missing.append(role)
            continue
        files[role] = path
        settings[role] = {"area": str(s.get("area", "middle")), "tone": str(s.get("tone", "light"))}
    return files, settings, missing


def handle_render_carousel(session: Session, job: JobRun) -> dict[str, Any]:
    from media_os_api.carousel.render import media_root, render_carousel  # creative image only

    raw_id = (job.payload or {}).get("content_package_id")
    try:
        package_id = uuid.UUID(str(raw_id))
    except ValueError as exc:
        raise PermanentTaskError("Invalid content_package_id.", code="BAD_PAYLOAD") from exc
    pkg = session.get(ContentPackage, package_id)
    if pkg is None or pkg.format != "CAROUSEL":
        raise PermanentTaskError("Carousel package not found.", code="NOT_FOUND")
    if pkg.status != ContentLifecycleStatus.DRAFTED.value:
        return {"skipped": True, "status": pkg.status}  # idempotent replay
    brand = session.get(BrandProfile, pkg.brand_profile_id)
    if brand is None:
        raise PermanentTaskError("Brand not found.", code="NOT_FOUND")
    kit = kit_for(brand.slug)
    version = pkg.version
    slides = [Slide.model_validate(s) for s in pkg.slide_data]
    language = Language((pkg.post_tags or {}).get("language", Language.BN_EN.value))

    rel = relative_slide_dir(pkg)
    design_files, design_settings, design_missing = design_inputs(pkg)
    rendered = render_carousel(
        kit,
        slides,
        language,
        media_root() / rel,
        festive=is_festive(pkg.post_tags or {}),
        design=design_files,
        design_settings=design_settings,
    )

    # Rendering takes a while. If the owner edited the text (or asked for a re-render)
    # meanwhile, this render is stale: record nothing, and the job for the new version renders.
    session.refresh(pkg, with_for_update=True)
    if pkg.version != version or pkg.status != ContentLifecycleStatus.DRAFTED.value:
        # Nothing points at this version's files: remove them so they don't pile up.
        shutil.rmtree(media_root() / rel, ignore_errors=True)
        return {"skipped": True, "stale_version": version, "current_version": pkg.version}

    session.execute(
        update(Asset)
        .where(Asset.content_package_id == pkg.id, Asset.asset_type == SLIDE_ASSET_TYPE)
        .values(status="SUPERSEDED")
    )
    for r in rendered:
        session.add(
            Asset(
                brand_profile_id=pkg.brand_profile_id,
                content_package_id=pkg.id,
                asset_type=SLIDE_ASSET_TYPE,
                local_path=(rel / r.path.name).as_posix(),
                mime_type="image/png",
                file_size=r.size,
                checksum=r.sha256,
                width=r.width,
                height=r.height,
                status="ACTIVE",
            )
        )

    checks = render_checks(rendered, len(slides))
    if design_missing:
        checks.append(
            Check(
                name="render_design",
                status="WARN",
                detail="Template image missing for "
                + ", ".join(r.lower() for r in design_missing)
                + "; used the built-in design there.",
            )
        )
    report = dict(pkg.qa_report or {"checks": [], "passed": True})
    kept = [c for c in report.get("checks", []) if not str(c.get("name", "")).startswith("render_")]
    report["checks"] = kept + [c.model_dump(mode="json") for c in checks]
    report["passed"] = all(c.get("status") != "FAIL" for c in report["checks"])
    report["render_version"] = "render-v1"
    pkg.qa_report = report
    session.flush()

    transition_content_status(
        session,
        content_package_id=pkg.id,
        to_status=ContentLifecycleStatus.RENDERED,
        actor_id=ACTOR,
        reason=f"{len(rendered)} slides rendered",
        idempotency_key=f"render:{pkg.id}:v{pkg.version}",
        expected_brand_id=pkg.brand_profile_id,
    )
    if report["passed"]:
        transition_content_status(
            session,
            content_package_id=pkg.id,
            to_status=ContentLifecycleStatus.QA_PASSED,
            actor_id=ACTOR,
            reason="All text and render QA checks passed",
            idempotency_key=f"qa:{pkg.id}:v{pkg.version}",
            expected_brand_id=pkg.brand_profile_id,
        )
    final = session.execute(
        select(ContentPackage.status).where(ContentPackage.id == pkg.id)
    ).scalar_one()
    return {"slides": len(rendered), "qa_passed": report["passed"], "status": final}
