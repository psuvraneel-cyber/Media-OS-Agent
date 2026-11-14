"""Carousel writer: brief + brand kit → validated `CarouselDraft` via the free-tier AI router.

Prompt v2 is built to stop the scroll: a hook playbook per hook type (specific, relatable
moments, curiosity gaps, ≤ 9 words), *emphasis* on the key words, kicker labels, a claim-backed
sticker on the hook, a real card render on wedding/design posts, and ≤ 15-word bodies.
"""

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

# Structural patterns with example *directions* (the model writes its own words from the claims).
HOOK_GUIDE = {
    "QUESTION": (
        "A sharp question about a specific moment the reader has lived. e.g. 'মেজোমামার নামের "
        "বানান কি ঠিক লিখেছেন?' / 'Is your *uncle's name* spelt right on the card?'"
    ),
    "PAIN_POINT": (
        "Name one sting precisely (wrong spelling of names, which honorific goes where, the "
        "printer's delay, mixing sadhu and cholit). e.g. 'কার্ডে *ভুল বানান*? আর নয়।'"
    ),
    "BEFORE_AFTER": (
        "Contrast: plain details → finished card. e.g. 'শুধু নাম, তারিখ, ঠিকানা → *পুরো কার্ড*'"
    ),
    "SPEED_DEMO": "Make speed visceral. e.g. '*৩ ধাপে* তৈরি বিয়ের কার্ড' / 'A card in *3 steps*'",
    "HOW_TO": "Promise one concrete skill. e.g. 'বরপক্ষের কার্ড বানান *এক ট্যাপে*'",
    "NUMBER_LIST": "A numbered promise. e.g. 'বিয়ের কার্ডে যে *৫টি লাইন* থাকতেই হবে'",
    "MISTAKE": (
        "Call out a common mistake with mild suspense. e.g. '৯০% কার্ডে এই *একটা ভুল*' is NOT "
        "allowed (invented number) — instead: 'শ্রী না শ্রীমতী? *এই ভুলটা* অনেকেই করেন'"
    ),
    "SEASONAL": ("Tie to the moment with urgency and warmth. e.g. 'বিয়ের মরশুম শুরু, *কার্ড তৈরি তো?*'"),
    "SHOWCASE": "One design as the hero. e.g. 'এই সপ্তাহের ডিজাইন: *জামদানি*'",
    "CURIOSITY": (
        "Open a loop, pay it off on the last content slide. e.g. 'কার্ডের লেখা কে লিখল? "
        "*উত্তর শেষ স্লাইডে*'"
    ),
}

CTA_GUIDE = {
    "INSTALL": "Last slide: invite them to get the app on Google Play.",
    "TRY_FREE_DESIGNS": "Last slide: classic wording and basic designs are free to try.",
    "SAVE_FOR_LATER": "Last slide: ask them to save the post for their next ceremony.",
    "SHARE_WITH_FAMILY": "Last slide: ask them to share it with the family member planning.",
    "COMMENT": "Last slide: ask one easy question to answer in the comments.",
    "NONE": "Last slide: a warm sign-off with the app name.",
}

CARD_PILLARS = {"wedding_season", "design_showcase", "other_occasions"}


def task_for(language: Language) -> TaskClass:
    return TaskClass.ENGLISH_COPY if language is Language.EN else TaskClass.BENGALI_COPY


def build_prompt(kit: BrandKit, brief: CarouselBrief) -> tuple[str, str]:
    claims = "\n".join(f"- {c.id}: {c.text}" for c in kit.claims)
    assets = "\n".join(
        f"- {a.id} ({a.kind}, {a.occasion.value}): {a.description}" for a in kit.assets
    )
    system = (
        f"You are a top social-media creative writing Instagram/Facebook carousels for "
        f"{kit.name_en} ({kit.name_bn}), an Android app that makes Bengali ceremony invitation "
        "cards and marriage biodata. Your only goal: make Bengali families STOP scrolling, swipe "
        "to the end, and install the app.\n"
        f"Tone: {kit.tone}\n"
        "HARD RULES (breaking any = rejected):\n"
        "1. Use ONLY facts from the approved claims list. Every POINT and PROOF slide lists the "
        "claim ids it relies on in claim_ids. Never invent features, numbers, percentages, "
        "prices, statistics or reviews.\n"
        "2. Never say best, #1, top, leading, সেরা, or quote download/install/user counts.\n"
        "3. Never mention Christian or Buddhist cards. Never attribute any verse to Tagore.\n"
        "4. Bengali text must be in Bengali script only — never Devanagari (Hindi script). "
        "Never use a real person's name (relations like মেজোমামা, পিসিমা, aunt are fine). "
        "Never mention iOS/iPhone.\n"
        "5. Prices (₹) only with 'in India'; otherwise 'price on Google Play'.\n"
        "Reply with ONE JSON object only."
    )
    card_rule = (
        "- This pillar shows cards: use a CARD visual on the HOOK or on at least one PROOF slide.\n"
        if brief.pillar in CARD_PILLARS
        else ""
    )
    prompt = (
        f"APPROVED CLAIMS (id: fact):\n{claims}\n\n"
        f"AVAILABLE VISUALS (use only these ids in visual_asset_id, or null):\n{assets}\n\n"
        f"BRIEF:\n- pillar: {brief.pillar}\n- hook: {brief.hook_type.value} — "
        f"{HOOK_GUIDE[brief.hook_type.value]}\n- occasion: {brief.occasion.value}\n"
        f"- visual style: {brief.visual_style.value}\n- call to action: "
        f"{CTA_GUIDE[brief.call_to_action.value]}\n"
        f"- angle: {brief.angle or 'your choice within the pillar'}\n\n"
        f"{LANGUAGE_RULES[brief.language]}\n\n"
        "SCROLL-STOPPING CRAFT:\n"
        "- HOOK headline: at most 9 words. Silently draft 5 hooks, keep the one a busy parent "
        "planning a wedding could not scroll past. Specific beats generic: name a real moment, "
        "a relative, a worry, a result. Never open with 'Introducing' or the app name.\n"
        "- Wrap the 1-3 most important words of EVERY headline in *asterisks* (they are drawn "
        "as a gold highlight). Never more than one highlighted phrase per headline.\n"
        "- kicker: a 1-3 word label above content headlines, e.g. 'ধাপ ১', 'ভুল #১', 'বোনাস', "
        "'STEP 1', 'PRO TIP'. Null on HOOK and CTA.\n"
        "- sticker (HOOK only, optional): 2-4 words restating ONE approved claim, e.g. "
        "'৩ ধাপে কার্ড', 'লগইন লাগে না', 'Free classic designs'. If used, that claim's id must "
        "be in the HOOK's claim_ids. Null on other slides.\n"
        "- The HOOK should have a visual (a CARD for wedding/occasion posts, a SCREEN for "
        "how-to posts).\n"
        f"{card_rule}"
        "- Bodies: at most 15 words, one idea per slide, conversational. No filler.\n"
        "- Each slide must make the reader want the next one (build, then pay off).\n\n"
        f"STRUCTURE: {MIN_SLIDES}-{MAX_SLIDES} slides (6 is ideal). Slide 1 role HOOK. Middle "
        "slides POINT or PROOF (PROOF slides use a visual). Last slide role CTA. "
        f"Play link for captions: {kit.play_url}\n"
        "Captions: first line is a second hook; short paragraphs; end with the Play link.\n"
        "hashtags: 6-10, each starting with #, no spaces, mix English and Bengali, e.g. "
        f"{' '.join(kit.hashtags_seed)}.\n"
        "alt_text_en: one English sentence describing the carousel for screen readers.\n\n"
        "JSON SHAPE:\n"
        + json.dumps(
            {
                "slides": [
                    {
                        "role": "HOOK|POINT|PROOF|CTA",
                        "kicker": "string or null",
                        "headline_bn": "string with *emphasis* or null",
                        "body_bn": "string or null",
                        "headline_en": "string with *emphasis* or null",
                        "body_en": "string or null",
                        "sticker": "string or null (HOOK only)",
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
            max_output_tokens=8192,  # thinking models spend part of this
            temperature=0.9,
            operation="carousel_write",
        )
    )
    if not isinstance(result.data, CarouselDraft):  # router validated against the schema
        raise TypeError("Router returned no CarouselDraft.")
    return result.data, f"{result.provider}/{result.model}", result.attempts
