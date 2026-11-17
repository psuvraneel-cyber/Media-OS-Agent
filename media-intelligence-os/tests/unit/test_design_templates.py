"""Unit: carousel slides rendered on the owner's design-template backgrounds."""

from __future__ import annotations

from media_os_api.carousel.brand_kit import NIMONTRON
from media_os_api.carousel.schema import Slide, SlideRole
from media_os_api.carousel.templates import DesignSlide, SlideContext, render_slide_html
from media_os_api.content.taxonomy import Language


def _ctx(design: DesignSlide | None, index: int = 1) -> SlideContext:
    return SlideContext(
        index=index,
        total=5,
        language=Language.BN_EN,
        image_uri=None,
        image_kind=None,
        icon_uri=None,
        font_base_uri="file:///app/fonts/",
        design=design,
    )


def _slide(role: SlideRole) -> Slide:
    return Slide.model_validate(
        {
            "role": role,
            "headline_bn": "বিয়ের *কার্ড*",
            "headline_en": "Wedding <cards>",
            "claim_ids": [],
        }
    )


def test_design_slide_uses_the_background_area_and_tone() -> None:
    d = DesignSlide(
        background_uri="file:///data/uploads/b/templates/x.png", area="bottom", tone="dark"
    )
    html = render_slide_html(NIMONTRON, _slide(SlideRole.HOOK), _ctx(d))
    assert 'class="design"' in html and 'src="file:///data/uploads/b/templates/x.png"' in html
    assert "bottom:150px" in html  # text panel at the bottom
    assert "rgba(255,250,244,.80)" in html  # light panel behind dark text
    assert "Wedding &lt;cards&gt;" in html  # model text is escaped
    assert "<mark>কার্ড</mark>" in html and "Swipe" in html  # hook keeps the swipe cue


def test_closing_slide_keeps_the_play_badge_and_builtin_is_unchanged() -> None:
    d = DesignSlide(background_uri="file:///x.png", area="top", tone="light")
    cta = render_slide_html(NIMONTRON, _slide(SlideRole.CTA), _ctx(d, index=5))
    assert "Google Play" in cta and "top:110px" in cta and "rgba(12,10,14,.46)" in cta
    builtin = render_slide_html(NIMONTRON, _slide(SlideRole.HOOK), _ctx(None))
    assert 'class="design"' not in builtin and "mandala" in builtin
