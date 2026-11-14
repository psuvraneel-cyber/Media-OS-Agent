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
    assert "Type the names" not in html and 'class="phone"' in html


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


def test_v2_design_elements() -> None:
    hook = Slide(
        role=SlideRole.HOOK,
        headline_bn="কার্ডে *ভুল বানান*?",
        headline_en="Wrong *spelling*?",
        sticker="৩ ধাপে কার্ড",
        claim_ids=["speed"],
    )
    html = render_slide_html(
        NIMONTRON,
        hook,
        SlideContext(
            index=1,
            total=6,
            language=Language.BN_EN,
            image_uri="file:///a/c.png",
            image_kind="CARD",
            icon_uri=None,
            font_base_uri="file:///app/fonts/",
            festive=True,
        ),
    )
    assert "<mark>ভুল বানান</mark>" in html and "<mark>spelling</mark>" in html
    assert 'class="garland"' in html and 'class="sticker"' in html
    assert 'class="hero card"' in html and 'class="swipe"' in html
    calm = render_slide_html(NIMONTRON, hook, ctx(Language.BN))  # not festive: no garland
    assert 'class="garland"' not in calm and "সোয়াইপ" in calm


def test_ghost_numerals_use_bengali_digits_in_bengali_mode() -> None:
    c = SlideContext(
        index=3,
        total=6,
        language=Language.BN,
        image_uri=None,
        image_kind=None,
        icon_uri=None,
        font_base_uri="file:///f/",
        content_number=2,
    )
    assert '<div class="ghost">০২</div>' in render_slide_html(NIMONTRON, SLIDE, c)


def test_emphasis_cannot_inject_html() -> None:
    evil = SLIDE.model_copy(update={"headline_en": "*<script>x</script>*"})
    html = render_slide_html(NIMONTRON, evil, ctx(Language.EN))
    assert "<script>" not in html and "<mark>&lt;script&gt;" in html


def test_festive_flag_never_for_memorial() -> None:
    from media_os_api.carousel.render_job import is_festive

    assert is_festive({"occasion": "WEDDING_HINDU"}) and is_festive({"hook_type": "SEASONAL"})
    assert not is_festive({"occasion": "SHRADDHA_MEMORIAL", "hook_type": "SEASONAL"})


def test_numbered_kicker_hides_the_ghost_numeral() -> None:
    c = SlideContext(
        index=3,
        total=6,
        language=Language.EN,
        image_uri=None,
        image_kind=None,
        icon_uri=None,
        font_base_uri="file:///f/",
        content_number=2,
    )
    step = SLIDE.model_copy(update={"kicker": "STEP 3"})
    assert 'class="ghost"' not in render_slide_html(NIMONTRON, step, c)
    tip = SLIDE.model_copy(update={"kicker": "PRO TIP"})
    assert 'class="ghost"' in render_slide_html(NIMONTRON, tip, c)
