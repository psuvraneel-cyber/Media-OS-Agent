"""Unit tests: the committed Nimontron brand profile spec is valid and matches the strategy."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from media_os_api.brand_onboarding import BrandOnboardingSpec

pytestmark = pytest.mark.unit

BRAND_DIR = Path(__file__).resolve().parents[2] / "docs" / "brands" / "nimontron"


def _spec() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((BRAND_DIR / "brand_profile.json").read_text("utf-8"))
    return data


def test_committed_spec_is_valid_and_safe() -> None:
    spec = BrandOnboardingSpec.model_validate(_spec())
    prefs = spec.publishing_preferences
    assert prefs["approval_required"] is True
    assert prefs["placeholder_unconfigured"] is True  # owner inputs still pending
    assert spec.call_to_action_preferences["play_store_url"] == (
        "https://play.google.com/store/apps/details?id=com.nimontron.app"
    )  # owner-confirmed, never guessed
    assert spec.visual_identity["palette"]["red"] == "#a3161f"


def test_pillars_match_strategy_doc() -> None:
    strategy = (BRAND_DIR / "CONTENT_STRATEGY.md").read_text("utf-8")
    table_codes = re.findall(r"^\| `([a-z_]+)` \|", strategy, flags=re.M)
    assert table_codes == _spec()["content_pillars"]


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"content_pillars": ["Wedding Season"]}, "snake_case"),
        ({"content_pillars": ["a_b", "a_b"]}, "unique"),
        ({"timezone": "Mars/Base"}, "Unknown timezone"),
        (
            {"call_to_action_preferences": {"play_store_url": "https://evil.example/app"}},
            "play_store_url",
        ),
        ({"publishing_preferences": {"approval_required": False}}, "approval_required"),
        (
            {
                "publishing_preferences": {
                    "approval_required": True,
                    "daily_caps": {"INSTAGRAM": 50},
                }
            },
            "daily_caps",
        ),
        ({"slug": "renamed"}, "slug"),
    ],
)
def test_invalid_specs_are_rejected(patch: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        BrandOnboardingSpec.model_validate({**_spec(), **patch})


def test_valid_play_url_is_accepted() -> None:
    data = _spec()
    data["call_to_action_preferences"] = {
        **data["call_to_action_preferences"],
        "play_store_url": "https://play.google.com/store/apps/details?id=com.nimontron.app",
    }
    assert BrandOnboardingSpec.model_validate(data).call_to_action_preferences["play_store_url"]
