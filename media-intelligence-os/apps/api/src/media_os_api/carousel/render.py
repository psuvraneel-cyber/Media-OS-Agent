"""Headless-Chromium renderer (runs only in the creative container, where Playwright is installed).

Each slide is rendered from `templates.render_slide_html` at 1080×1350. Text boxes marked
`data-fit` are shrunk in steps until they fit; any box that still overflows is reported and fails
render QA. No network: every resource is a local file, and requests to anything else are aborted.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from media_os_api.carousel.brand_kit import BrandKit
from media_os_api.carousel.schema import Slide, SlideRole
from media_os_api.carousel.templates import (
    HEIGHT,
    WIDTH,
    DesignSlide,
    SlideContext,
    render_slide_html,
)
from media_os_api.content.taxonomy import Language

FIT_SCRIPT = """
async () => {
  await document.fonts.ready;
  const imgs = Array.from(document.images);
  const loaded = i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; });
  await Promise.all(imgs.map(loaded));
  // Bengali vowel signs overhang their line box vertically; that is not clipping. A box only
  // fails when text runs out sideways; the content area fails when it is taller than its frame.
  // 28px allowance: Bengali vowel signs (ি ু ৃ) overhang the last line box.
  const over = el => el.scrollHeight > el.clientHeight + 28 || el.scrollWidth > el.clientWidth + 2;
  const wide = el => el.scrollWidth > el.clientWidth + 4;
  const content = document.querySelector('.content');
  const boxes = Array.from(document.querySelectorAll('[data-fit]'));
  for (let step = 0; step < 8 && content && over(content); step++) {
    boxes.forEach(b => {
      const s = parseFloat(getComputedStyle(b).fontSize);
      b.style.fontSize = (s * 0.92) + 'px';
    });
  }
  const problems = [];
  if (content && over(content)) problems.push('content area overflows');
  boxes.forEach((b, i) => { if (wide(b)) problems.push('text box ' + (i + 1) + ' too wide'); });
  const broken = imgs.filter(i => !i.naturalWidth).length;
  if (broken) problems.push(broken + ' image(s) failed to load');
  return problems;
}
"""


def write_durably(path: Path, data: bytes) -> None:
    """Write a file so it survives the Docker VM stopping right afterwards.

    Without fsync the data can sit in the page cache; when Docker Desktop stopped uncleanly, the
    slides of a carousel that had already passed QA were left as 0-byte files. Write to a temp
    file, flush it to disk, rename it into place, then flush the folder so the rename sticks."""
    if not data:
        raise OSError(f"Refusing to write an empty file: {path.name}")
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    fsync_dir(path.parent)


def fsync_dir(folder: Path) -> None:
    """Flush a folder's entries (new files, renames, new subfolders) to disk."""
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:  # some filesystems don't allow opening a folder
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class RenderedSlide:
    index: int
    path: Path
    width: int
    height: int
    size: int
    sha256: str
    problems: list[str]


def media_root() -> Path:
    return Path(os.environ.get("MEDIA_OS_MEDIA_ROOT", "/data/media"))


def brand_asset_root(brand_slug: str) -> Path:
    return Path(os.environ.get("MEDIA_OS_BRAND_ASSETS_ROOT", "/brand-assets")) / brand_slug


def font_dir() -> Path:
    return Path(os.environ.get("MEDIA_OS_FONT_DIR", "/app/fonts"))


def _uri(path: Path) -> str:
    return path.resolve().as_uri()


def design_role(slide: Slide) -> str:
    if slide.role is SlideRole.HOOK:
        return "COVER"
    if slide.role is SlideRole.CTA:
        return "CTA"
    return "CONTENT"


def _design_for(
    slide: Slide,
    design: dict[str, Path] | None,
    settings: dict[str, dict[str, str]] | None,
) -> DesignSlide | None:
    if not design:
        return None
    role = design_role(slide)
    path = design.get(role)
    if path is None or not path.is_file():
        return None
    s = (settings or {}).get(role, {})
    return DesignSlide(
        background_uri=_uri(path), area=s.get("area", "middle"), tone=s.get("tone", "light")
    )


def render_carousel(
    kit: BrandKit,
    slides: list[Slide],
    language: Language,
    out_dir: Path,
    *,
    festive: bool = False,
    design: dict[str, Path] | None = None,
    design_settings: dict[str, dict[str, str]] | None = None,
) -> list[RenderedSlide]:
    """Render every slide to PNG. `design` maps COVER / CONTENT / CTA to the owner's template
    background files (with `design_settings` giving each one's text area and tone); without it
    the built-in design is used."""
    from playwright.sync_api import sync_playwright  # type: ignore[import-not-found,unused-ignore]

    assets = brand_asset_root(kit.brand_slug)
    icon = assets / "icon-1024.png"
    out_dir.mkdir(parents=True, exist_ok=True)
    # carousels/<id>/v<n>: make the new folders themselves durable, not just the files in them.
    for folder in (out_dir.parent.parent, out_dir.parent):
        fsync_dir(folder)
    roots = [assets, font_dir(), out_dir]
    roots += [p.parent for p in (design or {}).values()]
    allowed_roots = [str(p.resolve()) for p in roots]
    rendered: list[RenderedSlide] = []
    card_uris = tuple(
        _uri(assets / a.path)
        for a in kit.assets
        if a.kind == "CARD" and (assets / a.path).is_file()
    )
    content_numbers: dict[int, int] = {}
    n = 0
    for i, s in enumerate(slides, 1):
        if s.role not in (SlideRole.HOOK, SlideRole.CTA):
            n += 1
            content_numbers[i] = n
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--disable-gpu", "--no-first-run"])
        try:
            page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

            def guard(route: Any) -> None:
                url: str = route.request.url
                local = url.startswith("file://") and any(
                    unquote(url.removeprefix("file://")).startswith(r) for r in allowed_roots
                )
                if url.startswith("data:") or local or url == "about:blank":
                    route.continue_()
                else:
                    route.abort()

            page.route("**/*", guard)
            for i, slide in enumerate(slides, 1):
                visual = kit.asset(slide.visual_asset_id) if slide.visual_asset_id else None
                image = assets / visual.path if visual else None
                ctx = SlideContext(
                    index=i,
                    total=len(slides),
                    language=language,
                    image_uri=_uri(image) if image and image.is_file() else None,
                    image_kind=visual.kind if visual else None,
                    icon_uri=_uri(icon) if icon.is_file() else None,
                    font_base_uri=_uri(font_dir()) + "/",
                    festive=festive,
                    card_uris=card_uris,
                    content_number=content_numbers.get(i, 0),
                    design=_design_for(slide, design, design_settings),
                )
                html_path = out_dir / f"slide-{i:02d}.html"
                html_path.write_text(render_slide_html(kit, slide, ctx), encoding="utf-8")
                page.goto(_uri(html_path), wait_until="load")
                problems: list[str] = list(page.evaluate(FIT_SCRIPT))
                if visual and ctx.image_uri is None:
                    problems.append(f"visual '{visual.id}' not found in brand assets")
                png = out_dir / f"slide-{i:02d}.png"
                data = page.screenshot(type="png", full_page=False)
                write_durably(png, data)
                html_path.unlink(missing_ok=True)
                rendered.append(
                    RenderedSlide(
                        index=i,
                        path=png,
                        width=WIDTH,
                        height=HEIGHT,
                        size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        problems=problems,
                    )
                )
        finally:
            browser.close()
    return rendered
