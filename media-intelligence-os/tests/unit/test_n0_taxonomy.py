"""Unit tests for the post-tagging taxonomy (roadmap N0)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from media_os_api.content.taxonomy import (
    FORMAT_PLATFORMS,
    TAXONOMY_VERSION,
    ContentFormat,
    Platform,
    PostingSlot,
    PostTags,
    posting_slot,
    validate_for_brand,
)

pytestmark = pytest.mark.unit

BASE: dict[str, Any] = {
    "platform": "INSTAGRAM",
    "format": "CAROUSEL",
    "pillar": "wedding_season",
    "hook_type": "QUESTION",
    "occasion": "WEDDING_HINDU",
    "language": "BN_EN",
    "visual_style": "CARD_SHOWCASE",
    "call_to_action": "INSTALL",
    "posting_slot": "EVENING",
    "strategy": "EXPLOIT",
    "slide_count": 7,
}


def test_valid_carousel_and_short() -> None:
    tags = PostTags(**BASE)
    assert tags.taxonomy_version == TAXONOMY_VERSION
    short = PostTags(
        **{
            **BASE,
            "platform": "YOUTUBE",
            "format": "SHORT",
            "slide_count": None,
            "duration_seconds": 35,
        }
    )
    assert short.format is ContentFormat.SHORT


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"platform": "YOUTUBE"}, "cannot be published"),  # carousel on YouTube
        ({"format": "SHORT", "slide_count": None, "duration_seconds": 30}, "cannot be published"),
        ({"occasion": "WEDDING_CHRISTIAN"}, "not approved"),
        ({"occasion": "WEDDING_BUDDHIST"}, "not approved"),
        ({"slide_count": None}, "slide_count"),
        ({"duration_seconds": 30}, "no duration"),
        ({"format": "REEL", "slide_count": None, "duration_seconds": 120}, "90 seconds"),
        ({"format": "REEL"}, "duration_seconds"),
        ({"hook_type": "CLICKBAIT"}, "hook_type"),
        ({"unknown_tag": "x"}, "unknown_tag"),
        ({"pillar": ""}, "pillar"),
    ],
)
def test_invalid_tag_sets_are_rejected(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        PostTags(**{**BASE, **overrides})


def test_every_format_has_platforms() -> None:
    assert set(FORMAT_PLATFORMS) == set(ContentFormat)
    assert FORMAT_PLATFORMS[ContentFormat.LONG_VIDEO] == frozenset({Platform.YOUTUBE})


def test_pillar_must_belong_to_brand() -> None:
    tags = PostTags(**BASE)
    assert validate_for_brand(tags, ["wedding_season", "how_it_works"]) is tags
    with pytest.raises(ValueError, match="not configured"):
        validate_for_brand(tags, ["how_it_works"])


@pytest.mark.parametrize(
    ("utc_hour", "slot"),
    [
        (1, PostingSlot.MORNING),  # 06:30 IST
        (7, PostingSlot.AFTERNOON),  # 12:30 IST
        (12, PostingSlot.EVENING),  # 17:30 IST
        (16, PostingSlot.NIGHT),  # 21:30 IST
        (23, PostingSlot.NIGHT),  # 04:30 IST
    ],
)
def test_posting_slot_uses_brand_timezone(utc_hour: int, slot: PostingSlot) -> None:
    when = datetime(2026, 11, 18, utc_hour, 0, tzinfo=UTC)
    assert posting_slot(when, "Asia/Kolkata") is slot


def test_posting_slot_requires_aware_datetime() -> None:
    with pytest.raises(ValueError):
        posting_slot(datetime(2026, 11, 18, 10, 0), "Asia/Kolkata")
