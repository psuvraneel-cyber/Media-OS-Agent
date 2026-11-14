"""Carousel brief (what to make) and draft (what the writer returns), plus language rules."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from media_os_api.content.taxonomy import (
    UNAPPROVED_OCCASIONS,
    CallToAction,
    ContentFormat,
    HookType,
    Language,
    Occasion,
    Platform,
    PostingSlot,
    PostTags,
    Strategy,
    VisualStyle,
)

MIN_SLIDES, MAX_SLIDES = 5, 8


class CarouselBrief(BaseModel):
    """One slot from the content calendar."""

    model_config = ConfigDict(extra="forbid")

    pillar: str = Field(min_length=1, max_length=64)
    hook_type: HookType
    occasion: Occasion = Occasion.GENERAL
    language: Language
    visual_style: VisualStyle = VisualStyle.BRAND_TYPOGRAPHY
    call_to_action: CallToAction = CallToAction.INSTALL
    posting_slot: PostingSlot = PostingSlot.EVENING
    strategy: Strategy = Strategy.EXPLORE
    angle: str | None = Field(default=None, max_length=300)

    @field_validator("occasion")
    @classmethod
    def _approved_occasion(cls, value: Occasion) -> Occasion:
        if value in UNAPPROVED_OCCASIONS:
            raise ValueError(f"{value} wording is not approved for marketing yet.")
        return value

    def post_tags(self, slide_count: int) -> PostTags:
        return PostTags(
            platform=Platform.INSTAGRAM,
            format=ContentFormat.CAROUSEL,
            pillar=self.pillar,
            hook_type=self.hook_type,
            occasion=self.occasion,
            language=self.language,
            visual_style=self.visual_style,
            call_to_action=self.call_to_action,
            posting_slot=self.posting_slot,
            strategy=self.strategy,
            slide_count=slide_count,
        )


class SlideRole(StrEnum):
    HOOK = "HOOK"
    POINT = "POINT"
    PROOF = "PROOF"  # shows the product (asset) doing what a claim says
    CTA = "CTA"


EMPHASIS = re.compile(r"\*([^*]{1,40})\*")


def plain(text: str) -> str:
    """Text without *emphasis* markers (for QA and length checks)."""
    return EMPHASIS.sub(lambda m: m.group(1), text).replace("*", "")


class Slide(BaseModel):
    role: SlideRole
    kicker: str | None = Field(default=None, max_length=28)  # tiny label above the headline
    headline_bn: str | None = Field(default=None, max_length=96)
    body_bn: str | None = Field(default=None, max_length=170)
    headline_en: str | None = Field(default=None, max_length=96)
    body_en: str | None = Field(default=None, max_length=170)
    sticker: str | None = Field(default=None, max_length=26)  # HOOK badge, must be claim-backed
    visual_asset_id: str | None = Field(default=None, max_length=64)
    claim_ids: list[str] = Field(default_factory=list, max_length=4)

    def texts(self) -> list[str]:
        raw = (
            self.kicker,
            self.headline_bn,
            self.body_bn,
            self.headline_en,
            self.body_en,
            self.sticker,
        )
        return [plain(t) for t in raw if t]


class CarouselDraft(BaseModel):
    slides: list[Slide] = Field(min_length=MIN_SLIDES, max_length=MAX_SLIDES)
    caption_bn: str | None = Field(default=None, max_length=1500)
    caption_en: str | None = Field(default=None, max_length=1500)
    hashtags: list[str] = Field(min_length=3, max_length=12)
    alt_text_en: str = Field(min_length=10, max_length=600)

    def all_text(self) -> list[str]:
        out = [t for s in self.slides for t in s.texts()]
        out += [t for t in (self.caption_bn, self.caption_en, self.alt_text_en) if t]
        return out + self.hashtags


LANGUAGE_RULES: dict[Language, str] = {
    Language.BN: (
        "LANGUAGE MODE BN (Bengali only). Every slide uses headline_bn (and body_bn where needed) "
        "in natural, warm Bengali script; leave headline_en and body_en null. caption_bn is the "
        "main caption; caption_en is ONE short English line for non-Bengali-reading relatives."
    ),
    Language.BN_EN: (
        "LANGUAGE MODE BN_EN (bilingual). Every slide has headline_bn AND headline_en that say "
        "the same thing (write each natively, do not translate word-for-word); bodies optional "
        "but if present give both. caption_bn first, then caption_en."
    ),
    Language.EN: (
        "LANGUAGE MODE EN (English only, for Bengali families abroad and relatives who do not "
        "read Bengali). Every slide uses headline_en (and body_en where needed); leave "
        "headline_bn and body_bn null; no Bengali script on slides except inside card images. "
        "Make clear the app creates cards in Bengali. caption_en only; caption_bn null."
    ),
}
