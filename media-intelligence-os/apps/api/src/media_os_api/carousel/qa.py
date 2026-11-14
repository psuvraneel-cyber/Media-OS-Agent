"""Automated text QA for carousel drafts (the rendered-image checks are added by the renderer).

Every check is deterministic. A FAIL keeps the carousel out of the approval queue; a WARN is shown
to the owner in the queue for a human look.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from media_os_api.carousel.brand_kit import BrandKit
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft, SlideRole, plain
from media_os_api.content.taxonomy import CallToAction, Language

QA_VERSION = "text-v2"
CARD_PILLARS = {"wedding_season", "design_showcase", "other_occasions"}
_BENGALI = re.compile(r"[ঀ-৿]")
_LATIN = re.compile(r"[A-Za-z]")
_HASHTAG = re.compile(r"^#[^\s#]{2,40}$")


class Check(BaseModel):
    name: str
    status: str  # PASS | WARN | FAIL
    detail: str = ""


class QaReport(BaseModel):
    version: str = QA_VERSION
    passed: bool
    checks: list[Check] = Field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == "FAIL"]

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def bengali_share(text: str) -> float:
    bn, lat = len(_BENGALI.findall(text)), len(_LATIN.findall(text))
    return bn / (bn + lat) if bn + lat else 0.0


def _check(name: str, problems: list[str], *, warn: bool = False) -> Check:
    if not problems:
        return Check(name=name, status="PASS")
    return Check(name=name, status="WARN" if warn else "FAIL", detail="; ".join(problems)[:500])


def _language(brief: CarouselBrief, draft: CarouselDraft) -> Check:
    problems: list[str] = []
    for i, s in enumerate(draft.slides, 1):
        bn = " ".join(t for t in (s.headline_bn, s.body_bn) if t)
        en = " ".join(t for t in (s.headline_en, s.body_en) if t)
        if brief.language is Language.BN:
            if not s.headline_bn or bengali_share(bn) < 0.8:
                problems.append(f"slide {i}: Bengali headline missing or not Bengali script")
            if en:
                problems.append(f"slide {i}: English text on a Bengali-only slide")
        elif brief.language is Language.EN:
            if not s.headline_en:
                problems.append(f"slide {i}: English headline missing")
            if bn or _BENGALI.search(en):
                problems.append(f"slide {i}: Bengali script on an English-only slide")
        else:
            if not (s.headline_bn and s.headline_en):
                problems.append(f"slide {i}: bilingual slide needs both headlines")
            elif bengali_share(s.headline_bn) < 0.8 or _BENGALI.search(s.headline_en):
                problems.append(f"slide {i}: headline languages mixed up")
    for i, s in enumerate(draft.slides, 1):
        labels = " ".join(t for t in (s.kicker, s.sticker) if t)
        if brief.language is Language.EN and _BENGALI.search(labels):
            problems.append(f"slide {i}: Bengali kicker/sticker on an English-only post")
        if brief.language is Language.BN and labels and bengali_share(labels) < 0.5:
            problems.append(f"slide {i}: English kicker/sticker on a Bengali-only post")
    if brief.language is Language.EN and draft.caption_bn:
        problems.append("English-only post has a Bengali caption")
    if brief.language is not Language.EN and not draft.caption_bn:
        problems.append("Bengali caption missing")
    if brief.language is not Language.BN and not draft.caption_en:
        problems.append("English caption missing")
    return _check("language_mode", problems)


def _structure(draft: CarouselDraft) -> Check:
    problems: list[str] = []
    roles = [s.role for s in draft.slides]
    if roles[0] is not SlideRole.HOOK:
        problems.append("first slide must be the HOOK")
    if roles[-1] is not SlideRole.CTA:
        problems.append("last slide must be the CTA")
    if roles.count(SlideRole.HOOK) > 1 or roles.count(SlideRole.CTA) > 1:
        problems.append("only one HOOK and one CTA slide")
    return _check("structure", problems)


def _claims(kit: BrandKit, draft: CarouselDraft) -> Check:
    problems: list[str] = []
    for i, s in enumerate(draft.slides, 1):
        unknown = [c for c in s.claim_ids if kit.claim(c) is None]
        if unknown:
            problems.append(f"slide {i}: unknown claim ids {unknown}")
        if s.role in (SlideRole.POINT, SlideRole.PROOF) and not s.claim_ids:
            problems.append(f"slide {i}: content slide cites no approved claim")
    return _check("claims_approved", problems)


def _banned(kit: BrandKit, draft: CarouselDraft) -> list[Check]:
    text = "\n".join(draft.all_text())
    fails: list[str] = []
    warns: list[str] = []
    for b in kit.banned:
        hit = b.pattern.search(text)
        if hit:
            (warns if b.severity == "WARN" else fails).append(f"{b.label}: '{hit.group(0)}'")
    return [_check("banned_phrases", fails), _check("needs_human_look", warns, warn=True)]


def _assets(kit: BrandKit, draft: CarouselDraft) -> Check:
    problems = [
        f"slide {i}: unknown visual '{s.visual_asset_id}'"
        for i, s in enumerate(draft.slides, 1)
        if s.visual_asset_id and kit.asset(s.visual_asset_id) is None
    ]
    return _check("visual_assets", problems)


def _pricing(draft: CarouselDraft) -> Check:
    text = "\n".join(draft.all_text())
    if re.search(r"₹|rupee|টাকা", text, re.IGNORECASE) and not re.search(
        r"india|ভারত", text, re.IGNORECASE
    ):
        return _check("pricing_region", ["price shown without saying it is the India price"])
    return _check("pricing_region", [])


def _cta(kit: BrandKit, brief: CarouselBrief, draft: CarouselDraft) -> Check:
    # The CTA template always draws the Google Play button; the caption must carry the link.
    problems: list[str] = []
    if brief.call_to_action is CallToAction.INSTALL:
        caption = f"{draft.caption_bn or ''} {draft.caption_en or ''}"
        if kit.play_url not in caption:
            problems.append("caption is missing the Play link")
    return _check("call_to_action", problems)


def _hashtags(draft: CarouselDraft) -> Check:
    bad = [h for h in draft.hashtags if not _HASHTAG.match(h)]
    dupes = len(draft.hashtags) != len({h.lower() for h in draft.hashtags})
    problems = ([f"malformed: {bad}"] if bad else []) + (["duplicates"] if dupes else [])
    return _check("hashtags", problems)


def _words(text: str | None) -> int:
    return len(plain(text or "").split())


def _slide_length(draft: CarouselDraft) -> list[Check]:
    long_bodies = [
        f"slide {i}: {max(_words(s.body_bn), _words(s.body_en))} words"
        for i, s in enumerate(draft.slides, 1)
        if max(_words(s.body_bn), _words(s.body_en)) > 18
    ]
    too_long = [x for x in long_bodies if int(x.split(": ")[1].split()[0]) > 28]
    hook = draft.slides[0]
    hook_words = max(_words(hook.headline_bn), _words(hook.headline_en))
    hook_issue = (
        [f"hook headline has {hook_words} words (aim for 9 or fewer)"] if hook_words > 10 else []
    )
    return [
        _check("readable_length", long_bodies, warn=not too_long),
        _check("hook_length", hook_issue, warn=True),
    ]


def _stickers(kit: BrandKit, draft: CarouselDraft) -> Check:
    problems: list[str] = []
    for i, s in enumerate(draft.slides, 1):
        if s.sticker and s.role is not SlideRole.HOOK:
            problems.append(f"slide {i}: stickers belong on the hook only")
        if s.sticker and not [c for c in s.claim_ids if kit.claim(c)]:
            problems.append(f"slide {i}: sticker is not backed by an approved claim")
    return _check("sticker_claim", problems)


def _card_visual(kit: BrandKit, brief: CarouselBrief, draft: CarouselDraft) -> Check:
    if brief.pillar not in CARD_PILLARS:
        return _check("shows_a_card", [])
    kinds = {
        a.kind for s in draft.slides if s.visual_asset_id and (a := kit.asset(s.visual_asset_id))
    }
    return _check(
        "shows_a_card", [] if "CARD" in kinds else ["no real card render shown"], warn=True
    )


def run_text_qa(kit: BrandKit, brief: CarouselBrief, draft: CarouselDraft) -> QaReport:
    checks = [
        _structure(draft),
        _language(brief, draft),
        _claims(kit, draft),
        *_banned(kit, draft),
        _assets(kit, draft),
        _pricing(draft),
        _cta(kit, brief, draft),
        _hashtags(draft),
        *_slide_length(draft),
        _stickers(kit, draft),
        _card_visual(kit, brief, draft),
    ]
    return QaReport(passed=not any(c.status == "FAIL" for c in checks), checks=checks)
