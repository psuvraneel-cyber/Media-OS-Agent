"""Carousel writer: brief + brand kit → validated `CarouselDraft` via the free-tier AI router."""

from __future__ import annotations

import json

from media_os_api.ai.router import AIRouter
from media_os_api.ai.types import AttemptRecord, GenerationRequest, TaskClass
from media_os_api.carousel.brand_kit import BrandKit
from media_os_api.carousel.schema import (
    LANGUAGE_RULES,
    MAX_SLIDES,
    MIN_SLIDES,
    CarouselBrief,
    CarouselDraft,
)
from media_os_api.content.taxonomy import Language

HOOK_GUIDE = {
    "QUESTION": "Open with a question about the reader's own situation.",
    "PAIN_POINT": "Name one real frustration (wording, spelling names, honorifics, time).",
    "BEFORE_AFTER": "Raw details first, then the finished card.",
    "SPEED_DEMO": "Show the three steps quickly.",
    "HOW_TO": "Teach one concrete task step by step.",
    "NUMBER_LIST": "A numbered list of N useful things.",
    "MISTAKE": "A common mistake, then the fix.",
    "SEASONAL": "Tie it to the season or festival calendar.",
    "SHOWCASE": "One design or card, close up.",
    "CURIOSITY": "Hold the reveal until the last content slide.",
}

CTA_GUIDE = {
    "INSTALL": "Last slide: invite them to get the app on Google Play.",
    "TRY_FREE_DESIGNS": "Last slide: classic wording and basic designs are free to try.",
    "SAVE_FOR_LATER": "Last slide: ask them to save the post for their next ceremony.",
    "SHARE_WITH_FAMILY": "Last slide: ask them to share it with the family member planning.",
    "COMMENT": "Last slide: ask one easy question to answer in the comments.",
    "NONE": "Last slide: a warm sign-off with the app name.",
}


def task_for(language: Language) -> TaskClass:
    return TaskClass.ENGLISH_COPY if language is Language.EN else TaskClass.BENGALI_COPY


def build_prompt(kit: BrandKit, brief: CarouselBrief) -> tuple[str, str]:
    claims = "\n".join(f"- {c.id}: {c.text}" for c in kit.claims)
    assets = "\n".join(
        f"- {a.id} ({a.kind}, {a.occasion.value}): {a.description}" for a in kit.assets
    )
    system = (
        f"You write Instagram/Facebook carousels for {kit.name_en} ({kit.name_bn}), an Android "
        "app that makes Bengali ceremony invitation cards and marriage biodata.\n"
        f"Tone: {kit.tone}\n"
        "HARD RULES:\n"
        "1. Use ONLY facts from the approved claims list. Every POINT and PROOF slide lists the "
        "claim ids it relies on in claim_ids. Never invent features, numbers, prices or reviews.\n"
        "2. Never say best, #1, top, leading, সেরা, or quote download/install/user counts.\n"
        "3. Never mention Christian or Buddhist cards. Never attribute any verse to Tagore.\n"
        "4. Never use a real person's name. Never mention iOS/iPhone.\n"
        "5. Prices (₹) only with 'in India'; otherwise 'price on Google Play'.\n"
        "6. Short lines: headlines up to ~8 words, bodies up to ~25 words. Mobile-first.\n"
        "Reply with ONE JSON object only."
    )
    prompt = (
        f"APPROVED CLAIMS (id: fact):\n{claims}\n\n"
        f"AVAILABLE VISUALS (use only these ids in visual_asset_id, or null for text-only):\n"
        f"{assets}\n\n"
        f"BRIEF:\n- pillar: {brief.pillar}\n- hook: {brief.hook_type.value} — "
        f"{HOOK_GUIDE[brief.hook_type.value]}\n- occasion: {brief.occasion.value}\n"
        f"- visual style: {brief.visual_style.value}\n- call to action: "
        f"{CTA_GUIDE[brief.call_to_action.value]}\n"
        f"- angle: {brief.angle or 'your choice within the pillar'}\n\n"
        f"{LANGUAGE_RULES[brief.language]}\n\n"
        f"STRUCTURE: {MIN_SLIDES}-{MAX_SLIDES} slides. Slide 1 role HOOK (stop the scroll). "
        "Middle slides POINT or PROOF (PROOF slides should use a visual). Last slide role CTA. "
        f"Play link for captions: {kit.play_url}\n"
        "hashtags: 5-10, each starting with #, no spaces, mix English and Bengali tags, e.g. "
        f"{' '.join(kit.hashtags_seed)}.\n"
        "alt_text_en: one English sentence describing the carousel for screen readers.\n\n"
        "JSON SHAPE:\n"
        + json.dumps(
            {
                "slides": [
                    {
                        "role": "HOOK|POINT|PROOF|CTA",
                        "headline_bn": "string or null",
                        "body_bn": "string or null",
                        "headline_en": "string or null",
                        "body_en": "string or null",
                        "visual_asset_id": "asset id or null",
                        "claim_ids": ["claim id"],
                    }
                ],
                "caption_bn": "string or null",
                "caption_en": "string or null",
                "hashtags": ["#tag"],
                "alt_text_en": "string",
            },
            ensure_ascii=False,
        )
    )
    return system, prompt


def write_carousel(
    router: AIRouter, kit: BrandKit, brief: CarouselBrief
) -> tuple[CarouselDraft, str, list[AttemptRecord]]:
    """Returns the draft, the route that wrote it ('provider/model'), and all attempts."""
    system, prompt = build_prompt(kit, brief)
    result = router.generate(
        GenerationRequest(
            task=task_for(brief.language),
            system=system,
            prompt=prompt,
            output_schema=CarouselDraft,
            max_output_tokens=4096,
            temperature=0.8,
            operation="carousel_write",
        )
    )
    if not isinstance(result.data, CarouselDraft):  # router validated against the schema
        raise TypeError("Router returned no CarouselDraft.")
    return result.data, f"{result.provider}/{result.model}", result.attempts
