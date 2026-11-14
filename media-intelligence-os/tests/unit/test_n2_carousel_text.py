"""N2 carousel writer prompt, text QA and virality score v0 (no network)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from media_os_api.ai.types import TaskClass
from media_os_api.carousel.brand_kit import NIMONTRON
from media_os_api.carousel.qa import QaReport, bengali_share, run_text_qa
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft
from media_os_api.carousel.score import AIReview, rule_scores, score_carousel
from media_os_api.carousel.writer import build_prompt, task_for
from media_os_api.content.taxonomy import HookType, Language, Occasion

pytestmark = pytest.mark.unit

PLAY = NIMONTRON.play_url


def brief(language: Language = Language.BN_EN, **kw: Any) -> CarouselBrief:
    return CarouselBrief(
        pillar="wedding_season", hook_type=HookType.SEASONAL, language=language, **kw
    )


def slide(
    role: str,
    bn: str | None,
    en: str | None,
    claims: list[str] | None = None,
    asset: str | None = None,
) -> dict[str, Any]:
    return {
        "role": role,
        "headline_bn": bn,
        "headline_en": en,
        "claim_ids": claims or [],
        "visual_asset_id": asset,
    }


def draft(language: Language = Language.BN_EN, **over: Any) -> CarouselDraft:
    bn = language is not Language.EN
    en = language is not Language.BN
    slides = [
        slide(
            "HOOK", "বিয়ের মরশুম এসে গেল" if bn else None, "Wedding season is here" if en else None
        ),
        slide(
            "POINT",
            "তিন ধাপে কার্ড" if bn else None,
            "A card in three steps" if en else None,
            ["speed"],
        ),
        slide(
            "PROOF",
            "ইংরেজিতে নাম, কার্ডে বাংলা" if bn else None,
            "Type names in English" if en else None,
            ["names_bn"],
            "screen_names_bn",
        ),
        slide(
            "POINT",
            "এক ট্যাপে হোয়াটসঅ্যাপ" if bn else None,
            "One tap to WhatsApp" if en else None,
            ["sharing"],
        ),
        slide(
            "CTA",
            "গুগল প্লে থেকে নিন" if bn else None,
            "Get it on Google Play" if en else None,
            ["on_play"],
        ),
    ]
    data: dict[str, Any] = {
        "slides": slides,
        "caption_bn": f"বিয়ের কার্ড সহজে। {PLAY}" if bn else None,
        "caption_en": f"Bengali wedding cards made easy. {PLAY}" if en else None,
        "hashtags": ["#Nimontron", "#BengaliWedding", "#বিয়েরকার্ড"],
        "alt_text_en": "A five-slide carousel about making Bengali wedding cards.",
    }
    data.update(over)
    return CarouselDraft.model_validate(data)


def status(report: QaReport, name: str) -> str:
    return next(c.status for c in report.checks if c.name == name)


@pytest.mark.parametrize("language", list(Language))
def test_clean_draft_passes_for_every_language(language: Language) -> None:
    report = run_text_qa(NIMONTRON, brief(language), draft(language))
    assert report.passed, report.failures


def test_language_mode_mismatch_fails() -> None:
    # A bilingual draft submitted as English-only has Bengali on the slides.
    report = run_text_qa(NIMONTRON, brief(Language.EN), draft(Language.BN_EN))
    assert status(report, "language_mode") == "FAIL"
    report = run_text_qa(NIMONTRON, brief(Language.BN_EN), draft(Language.BN))
    assert status(report, "language_mode") == "FAIL"


@pytest.mark.parametrize(
    "text",
    [
        "The best card app",
        "#1 for weddings",
        "10,000 downloads",
        "সেরা অ্যাপ",
        "Christian wedding cards",
        "Customers love it",
        "Now on iPhone",
    ],
)
def test_banned_phrases_fail(text: str) -> None:
    d = draft(caption_en=f"{text} {PLAY}")
    report = run_text_qa(NIMONTRON, brief(), d)
    assert status(report, "banned_phrases") == "FAIL"
    assert not report.passed


def test_tagore_mention_is_a_warning_not_a_failure() -> None:
    d = draft(caption_en=f"Premium lines include verses by Tagore. {PLAY}")
    report = run_text_qa(NIMONTRON, brief(), d)
    assert status(report, "needs_human_look") == "WARN"
    assert report.passed


def test_unknown_claim_uncited_slide_and_unknown_asset_fail() -> None:
    d = draft()
    d.slides[1].claim_ids = ["fastest_in_india"]
    d.slides[3].claim_ids = []
    d.slides[2].visual_asset_id = "card_rajkiyo_tagore"
    report = run_text_qa(NIMONTRON, brief(), d)
    assert status(report, "claims_approved") == "FAIL"
    assert status(report, "visual_assets") == "FAIL"


def test_excluded_tagore_assets_are_not_in_the_catalog() -> None:
    paths = {a.path for a in NIMONTRON.assets}
    assert not paths & set(NIMONTRON.excluded_assets)


def test_price_without_india_fails_and_install_cta_needs_play() -> None:
    d = draft(caption_en=f"Only ₹99 for the wedding pack. {PLAY}")
    assert status(run_text_qa(NIMONTRON, brief(), d), "pricing_region") == "FAIL"
    d2 = draft(caption_en="No link here", caption_bn="লিঙ্ক নেই")
    assert status(run_text_qa(NIMONTRON, brief(), d2), "call_to_action") == "FAIL"


def test_structure_requires_hook_first_and_cta_last() -> None:
    d = draft()
    d.slides[0].role, d.slides[-1].role = d.slides[-1].role, d.slides[0].role
    assert status(run_text_qa(NIMONTRON, brief(), d), "structure") == "FAIL"


def test_schema_limits_and_unapproved_occasion() -> None:
    with pytest.raises(ValidationError):
        draft(slides=draft().model_dump()["slides"][:3])
    with pytest.raises(ValidationError):
        brief(occasion=Occasion.WEDDING_CHRISTIAN)


def test_prompt_contains_rules_claims_assets_and_language_mode() -> None:
    system, prompt = build_prompt(NIMONTRON, brief(Language.EN))
    assert "Never attribute any verse to Tagore" in system
    assert "speed:" in prompt and "screen_names_bn" in prompt
    assert "LANGUAGE MODE EN" in prompt
    assert all(path not in prompt for path in NIMONTRON.excluded_assets)
    assert task_for(Language.EN) is TaskClass.ENGLISH_COPY
    assert task_for(Language.BN) is TaskClass.BENGALI_COPY


def test_score_is_labelled_heuristic_and_blends_review() -> None:
    d = draft()
    rules = rule_scores(brief(), d)
    assert set(rules) == {"hook_strength", "clarity", "visual_proof", "cta_strength"}
    no_ai = score_carousel(brief(), d, None)
    assert no_ai["label"].startswith("Heuristic v0") and not no_ai["ai_review_used"]
    review = AIReview(
        hook_strength=2,
        clarity=2,
        emotional_pull=2,
        cta_strength=2,
        one_improvement="Stronger hook.",
    )
    with_ai = score_carousel(brief(), d, review)
    assert with_ai["total"] < no_ai["total"] and with_ai["suggestion"] == "Stronger hook."
    assert 0 <= with_ai["total"] <= 100


def test_bengali_share() -> None:
    assert bengali_share("বিয়ের কার্ড") == 1.0
    assert bengali_share("card") == 0.0
