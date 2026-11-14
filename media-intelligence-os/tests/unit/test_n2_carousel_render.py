"""N2 slide templates, render QA and routing (no browser needed)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from media_os_api.carousel.brand_kit import NIMONTRON
from media_os_api.carousel.render_job import RENDER_TASK, render_checks
from media_os_api.carousel.schema import Slide, SlideRole
from media_os_api.carousel.templates import SlideContext, render_slide_html
from media_os_api.content.taxonomy import Language
from media_os_api.jobs.routing import (
    CREATIVE_CELERY_TASK,
    CREATIVE_QUEUE,
    celery_task_for_queue,
    queue_for_task,
)

pytestmark = pytest.mark.unit


def ctx(language: Language, image: str | None = None, kind: str | None = None) -> SlideContext:
    return SlideContext(
        index=2,
        total=6,
        language=language,
        image_uri=image,
        image_kind=kind,
        icon_uri="file:///brand-assets/x/icon-1024.png",
        font_base_uri="file:///app/fonts/",
    )


SLIDE = Slide(
    role=SlideRole.POINT,
    headline_bn="তিন ধাপে কার্ড",
    headline_en="A card in <b>3</b> steps",
    body_bn="নাম লিখুন",
    body_en="Type the names",
    claim_ids=["speed"],
)


def test_language_modes_control_what_text_appears() -> None:
    bn = render_slide_html(NIMONTRON, SLIDE, ctx(Language.BN))
    en = render_slide_html(NIMONTRON, SLIDE, ctx(Language.EN))
    both = render_slide_html(NIMONTRON, SLIDE, ctx(Language.BN_EN))
    assert "তিন ধাপে কার্ড" in bn and "Type the names" not in bn
    assert "তিন ধাপে কার্ড" not in en and "Type the names" in en
    assert "তিন ধাপে কার্ড" in both and "steps" in both


def test_model_text_is_escaped_and_no_remote_urls() -> None:
    html = render_slide_html(NIMONTRON, SLIDE, ctx(Language.EN))
    assert "&lt;b&gt;3&lt;/b&gt;" in html and "<b>3</b>" not in html
    assert "http://" not in html and "https://" not in html


def test_bilingual_with_visual_drops_english_body_and_crops_screens() -> None:
    html = render_slide_html(
        NIMONTRON, SLIDE, ctx(Language.BN_EN, "file:///brand-assets/s.png", "SCREEN")
    )
    assert "Type the names" not in html and 'class="screen"' in html


def test_hook_shows_headlines_only_and_cta_has_play_pill() -> None:
    hook = SLIDE.model_copy(update={"role": SlideRole.HOOK})
    assert "নাম লিখুন" not in render_slide_html(NIMONTRON, hook, ctx(Language.BN_EN))
    cta = SLIDE.model_copy(update={"role": SlideRole.CTA})
    assert "Google Play" in render_slide_html(NIMONTRON, cta, ctx(Language.EN))
    assert "Google Play-এ পাবেন" in render_slide_html(NIMONTRON, cta, ctx(Language.BN))


@dataclass
class Fake:
    index: int
    width: int = 1080
    height: int = 1350
    size: int = 1000
    problems: list[str] | None = None
    path: Path = Path("x.png")


def test_render_checks() -> None:
    ok = render_checks([Fake(1, problems=[]), Fake(2, problems=[])], 2)
    assert all(c.status == "PASS" for c in ok)
    bad = render_checks([Fake(1, problems=["content area overflows"], size=9_000_000)], 2)
    failed = {c.name for c in bad if c.status == "FAIL"}
    assert failed == {"render_text_fits", "render_file_size", "render_slide_count"}


def test_render_jobs_route_to_the_creative_queue_only() -> None:
    assert queue_for_task(RENDER_TASK) == CREATIVE_QUEUE
    assert celery_task_for_queue(CREATIVE_QUEUE) == CREATIVE_CELERY_TASK
    assert queue_for_task("media_os.internal.ping") == "control"
