"""Post-tagging taxonomy (roadmap N0).

Every generated post is tagged at creation time so the N5 learning loop can compare like with like
("question hooks on carousels" vs "number-list hooks on carousels"). Tags are a closed vocabulary:
free text cannot be aggregated. Brand-specific content pillars are validated against the brand's own
`content_pillars` list.

Storage: tags will be persisted on `content_packages` in N2 (proposed migration 0006 adds a
`post_tags JSONB` column); `TAXONOMY_VERSION` is stored with each tag set so later analysis can tell
vocabularies apart.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

TAXONOMY_VERSION = "1"


class Platform(StrEnum):
    INSTAGRAM = "INSTAGRAM"
    FACEBOOK = "FACEBOOK"
    YOUTUBE = "YOUTUBE"


class ContentFormat(StrEnum):
    CAROUSEL = "CAROUSEL"  # Instagram / Facebook multi-image post
    REEL = "REEL"  # Instagram / Facebook vertical video
    SHORT = "SHORT"  # YouTube Shorts (≤ 60 s vertical)
    LONG_VIDEO = "LONG_VIDEO"  # YouTube long-form (tutorials, walkthroughs)


FORMAT_PLATFORMS: dict[ContentFormat, frozenset[Platform]] = {
    ContentFormat.CAROUSEL: frozenset({Platform.INSTAGRAM, Platform.FACEBOOK}),
    ContentFormat.REEL: frozenset({Platform.INSTAGRAM, Platform.FACEBOOK}),
    ContentFormat.SHORT: frozenset({Platform.YOUTUBE}),
    ContentFormat.LONG_VIDEO: frozenset({Platform.YOUTUBE}),
}


class HookType(StrEnum):
    """Opening-line pattern. Structural categories only; wording comes from approved claims."""

    QUESTION = "QUESTION"  # "Still writing invitation cards by hand?"
    PAIN_POINT = "PAIN_POINT"  # names a frustration (wording, spelling, time)
    BEFORE_AFTER = "BEFORE_AFTER"  # plain details → finished card
    SPEED_DEMO = "SPEED_DEMO"  # "a card in under a minute"
    HOW_TO = "HOW_TO"  # "how to make a bou-bhat card"
    NUMBER_LIST = "NUMBER_LIST"  # "5 lines every wedding card needs"
    MISTAKE = "MISTAKE"  # "the honorific mistake on most cards"
    SEASONAL = "SEASONAL"  # "wedding season is here"
    SHOWCASE = "SHOWCASE"  # "this week's design: শাঁখা-পলা"
    CURIOSITY = "CURIOSITY"  # withholds the reveal to the last slide/second


class Occasion(StrEnum):
    """Maps to Nimontron's card families (brand brief §2). GENERAL for app-level posts."""

    GENERAL = "GENERAL"
    WEDDING_HINDU = "WEDDING_HINDU"
    WEDDING_MUSLIM = "WEDDING_MUSLIM"
    WEDDING_CHRISTIAN = "WEDDING_CHRISTIAN"
    WEDDING_BUDDHIST = "WEDDING_BUDDHIST"
    GAYE_HOLUD = "GAYE_HOLUD"
    BOUBHAT_WALIMA = "BOUBHAT_WALIMA"
    ANNAPRASHAN_AQIQAH = "ANNAPRASHAN_AQIQAH"
    BIRTHDAY = "BIRTHDAY"
    PUJA = "PUJA"
    IFTAR = "IFTAR"
    UPANAYAN_GRIHAPRAVESH = "UPANAYAN_GRIHAPRAVESH"
    SHRADDHA_MEMORIAL = "SHRADDHA_MEMORIAL"
    BIODATA = "BIODATA"


# Draft wording for these card families is not yet approved (brand brief §3): never feature them.
UNAPPROVED_OCCASIONS: frozenset[Occasion] = frozenset(
    {Occasion.WEDDING_CHRISTIAN, Occasion.WEDDING_BUDDHIST}
)


class Language(StrEnum):
    BN = "BN"  # Bengali only
    EN = "EN"  # English only (families abroad)
    BN_EN = "BN_EN"  # Bengali primary with English captions


class VisualStyle(StrEnum):
    CARD_SHOWCASE = "CARD_SHOWCASE"  # rendered card designs, full bleed
    APP_SCREEN_DEMO = "APP_SCREEN_DEMO"  # screen recording / screenshots of the app
    BEFORE_AFTER_SPLIT = "BEFORE_AFTER_SPLIT"
    BRAND_TYPOGRAPHY = "BRAND_TYPOGRAPHY"  # text on brand red/gold/cream
    DESIGN_GRID = "DESIGN_GRID"  # several designs side by side
    TALKING_POINTS = "TALKING_POINTS"  # captions over motion, no voice


class CallToAction(StrEnum):
    INSTALL = "INSTALL"  # "Get it on Google Play"
    TRY_FREE_DESIGNS = "TRY_FREE_DESIGNS"  # classic wording and basic designs cost nothing
    SAVE_FOR_LATER = "SAVE_FOR_LATER"
    SHARE_WITH_FAMILY = "SHARE_WITH_FAMILY"
    COMMENT = "COMMENT"
    NONE = "NONE"


class PostingSlot(StrEnum):
    """Local-time buckets (brand timezone) for comparing posting times."""

    MORNING = "MORNING"  # 06:00–11:59
    AFTERNOON = "AFTERNOON"  # 12:00–16:59
    EVENING = "EVENING"  # 17:00–20:59
    NIGHT = "NIGHT"  # 21:00–05:59


class Strategy(StrEnum):
    EXPLOIT = "EXPLOIT"  # follows a pattern that has performed well
    EXPLORE = "EXPLORE"  # deliberate test of something new (target 20–30% of posts)


def posting_slot(when: datetime, timezone_name: str) -> PostingSlot:
    """Bucket a scheduled time into a local posting slot."""
    if when.tzinfo is None:
        raise ValueError("posting_slot requires a timezone-aware datetime.")
    hour = when.astimezone(ZoneInfo(timezone_name)).hour
    if 6 <= hour < 12:
        return PostingSlot.MORNING
    if 12 <= hour < 17:
        return PostingSlot.AFTERNOON
    if 17 <= hour < 21:
        return PostingSlot.EVENING
    return PostingSlot.NIGHT


class PostTags(BaseModel):
    """The closed tag set recorded on every post."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    taxonomy_version: str = TAXONOMY_VERSION
    platform: Platform
    format: ContentFormat
    pillar: str = Field(min_length=1, max_length=64)
    hook_type: HookType
    occasion: Occasion
    language: Language
    visual_style: VisualStyle
    call_to_action: CallToAction
    posting_slot: PostingSlot
    strategy: Strategy
    slide_count: int | None = Field(default=None, ge=1, le=20)
    duration_seconds: int | None = Field(default=None, ge=1, le=3600)

    @model_validator(mode="after")
    def _consistent(self) -> PostTags:
        if self.platform not in FORMAT_PLATFORMS[self.format]:
            raise ValueError(f"{self.format} cannot be published on {self.platform}.")
        if self.occasion in UNAPPROVED_OCCASIONS:
            raise ValueError(f"{self.occasion} wording is not approved for marketing yet.")
        if self.format is ContentFormat.CAROUSEL:
            if self.slide_count is None or self.duration_seconds is not None:
                raise ValueError("Carousels need slide_count and no duration.")
        else:
            if self.duration_seconds is None or self.slide_count is not None:
                raise ValueError("Videos need duration_seconds and no slide_count.")
            if (
                self.format in (ContentFormat.SHORT, ContentFormat.REEL)
                and self.duration_seconds > 90
            ):
                raise ValueError("Shorts/Reels must be 90 seconds or less.")
        return self


def validate_for_brand(tags: PostTags, brand_pillars: list[str]) -> PostTags:
    """Reject a pillar that is not one of the brand's configured content pillars."""
    if tags.pillar not in brand_pillars:
        raise ValueError(f"Pillar '{tags.pillar}' is not configured for this brand.")
    return tags
