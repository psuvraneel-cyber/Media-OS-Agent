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
from media_os_api.carousel.novelty import RecentPost, avoid_block, closest_repeat
from media_os_api.carousel.qa import Check, QaReport, run_text_qa
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
        "- Bengali voice: everyday spoken চলিত Bengali, like a cousin helping with the "
        "wedding: the words families actually say. Avoid bookish or translated words (prefer "
        "'নামের বানান ভুল হবে না তো?' over 'নাম লেখা কি ভাবাচ্ছে?', 'সঙ্গে সঙ্গে' over "
        "'তৎক্ষণাৎ').\n"
        "- Kickers speak to the reader ('ধাপ ২', 'জানতেন?', 'STEP 2', 'DID YOU KNOW?'); "
        "never an internal label like PROOF, HOOK, POINT or CTA.\n"
        "- Never name a specific premium design (রাজকীয়, বেনারসি, জামদানি…) on a slide that "
        "shows a card render: none of the available card renders are those designs.\n"
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
    router: AIRouter,
    kit: BrandKit,
    brief: CarouselBrief,
    *,
    feedback: list[str] | None = None,
    avoid: str = "",
) -> tuple[CarouselDraft, str, list[AttemptRecord]]:
    """Returns the draft, the route that wrote it ('provider/model'), and all attempts.

    `feedback` carries the QA failures of a previous attempt so the rewrite can fix them;
    `avoid` lists recent posts whose subjects must not be repeated."""
    system, prompt = build_prompt(kit, brief)
    if avoid:
        prompt = avoid + prompt
    if feedback:
        prompt += (
            "\n\nYOUR PREVIOUS DRAFT WAS REJECTED BY QA FOR THESE REASONS. Write a new draft "
            "that fixes every one of them:\n" + "\n".join(f"- {f}" for f in feedback[:10])
        )
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


def write_with_qa(
    router: AIRouter,
    kit: BrandKit,
    brief: CarouselBrief,
    *,
    max_rewrites: int = 1,
    recent: list[RecentPost] | None = None,
    guidance: str = "",
) -> tuple[CarouselDraft, str, list[AttemptRecord], QaReport]:
    """Write, run text QA, and on failure rewrite once with the failures as feedback.

    With `recent` posts, the AI is told not to repeat their subjects, and a passing draft that
    still repeats one is rewritten once more; if it still repeats, the QA report carries a
    `novelty` warning naming the post (the owner decides in review).

    Returns the last draft with its QA report; a draft that still fails is returned as-is so the
    caller can save it as QA-failed (it never reaches the approval queue).

    `guidance` is put first in the prompt: the owner's recent review notes (see `feedback`)."""
    avoid = guidance + avoid_block(recent or [])
    draft, written_by, attempts = write_carousel(router, kit, brief, avoid=avoid)
    qa = run_text_qa(kit, brief, draft)
    for _ in range(max_rewrites):
        if qa.passed:
            break
        feedback = [f"{c.name}: {c.detail}" for c in qa.failures]
        draft, written_by, more = write_carousel(router, kit, brief, feedback=feedback, avoid=avoid)
        attempts = attempts + more
        qa = run_text_qa(kit, brief, draft)
    if recent is None:
        return draft, written_by, attempts, qa
    repeat = closest_repeat(draft, recent) if qa.passed else None
    if repeat is not None:
        feedback = [
            f"novelty: this repeats the subject of the recent post '{repeat.title}' "
            f"(both are about: {', '.join(repeat.shared)}). Write about a clearly different "
            "subject."
        ]
        retry, retry_by, more = write_carousel(router, kit, brief, feedback=feedback, avoid=avoid)
        attempts = attempts + more
        retry_qa = run_text_qa(kit, brief, retry)
        if retry_qa.passed:  # keep the rewrite only if it's still a valid draft
            draft, written_by, qa = retry, retry_by, retry_qa
            repeat = closest_repeat(draft, recent)
    qa.checks.append(
        Check(name="novelty", status="PASS")
        if repeat is None
        else Check(
            name="novelty",
            status="WARN",
            detail=f"Similar to a recent post: '{repeat.title}' "
            f"(both about {', '.join(repeat.shared)}).",
        )
    )
    return draft, written_by, attempts, qa
