"""Automated text QA for carousel drafts (the rendered-image checks are added by the renderer).

Every check is deterministic. A FAIL keeps the carousel out of the approval queue; a WARN is shown
to the owner in the queue for a human look.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from media_os_api.carousel.brand_kit import BrandKit
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft, Slide, SlideRole
from media_os_api.content.taxonomy import CallToAction, Language

QA_VERSION = "text-v1"
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
    problems: list[str] = []
    if brief.call_to_action is CallToAction.INSTALL:
        last = " ".join(draft.slides[-1].texts()).lower()
        caption = f"{draft.caption_bn or ''} {draft.caption_en or ''}"
        if not re.search(r"google play|play store|প্লে স্টোর|গুগল প্লে", last):
            problems.append("CTA slide does not mention Google Play")
        if kit.play_url not in caption:
            problems.append("caption is missing the Play link")
    return _check("call_to_action", problems)


def _hashtags(draft: CarouselDraft) -> Check:
    bad = [h for h in draft.hashtags if not _HASHTAG.match(h)]
    dupes = len(draft.hashtags) != len({h.lower() for h in draft.hashtags})
    problems = ([f"malformed: {bad}"] if bad else []) + (["duplicates"] if dupes else [])
    return _check("hashtags", problems)


def _slide_length(draft: CarouselDraft) -> Check:
    def words(s: Slide) -> int:
        return max(len((s.body_bn or "").split()), len((s.body_en or "").split()))

    long_ones = [f"slide {i}" for i, s in enumerate(draft.slides, 1) if words(s) > 32]
    return _check("readable_length", long_ones, warn=True)


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
        _slide_length(draft),
    ]
    return QaReport(passed=not any(c.status == "FAIL" for c in checks), checks=checks)
