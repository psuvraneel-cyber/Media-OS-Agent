"""Unit: parsing Postiz account analytics and the report's whole-account section."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from media_os_api.analytics.account import account_metric_key, window_totals
from media_os_api.notify import messages as msg

IG = [
    {
        "label": "Reach",
        "data": [{"total": 3, "date": "2026-11-14"}, {"total": 4, "date": "2026-11-15"}],
    },
    {"label": "Likes", "data": [{"total": 12, "date": "2026-11-16"}]},
    {"label": "Views", "data": [{"total": 250, "date": "2026-11-16"}]},
    {"label": "Saves", "data": [{"total": "oops", "date": "2026-11-16"}]},
    {"label": "", "data": [{"total": 9}]},
]
FB = [
    {"label": "Media views", "data": [{"total": 4}, {"total": 6}]},
    {"label": "Page Impressions", "data": [{"total": 1}, {"total": 1}]},
    {"label": "Posts Engagement", "data": [{"total": 2}, {"total": 2}]},
    {"label": "Page followers", "data": [{"total": 0}, {"total": 1}]},
]


def test_labels_map_to_stable_keys() -> None:
    assert account_metric_key("INSTAGRAM", "Views") == "views"
    assert account_metric_key("INSTAGRAM", "Follower Count") == "new_followers"
    assert account_metric_key("FACEBOOK", "Media views") == "views"
    assert account_metric_key("FACEBOOK", "Page Impressions") == "unique_viewers_daily_sum"
    assert account_metric_key("FACEBOOK", "Something New") == "other_something_new"
    assert account_metric_key("FACEBOOK", "  ") is None


def test_window_totals_sum_daily_values_and_skip_bad_ones() -> None:
    ig = window_totals("INSTAGRAM", IG)
    assert ig["reach_daily_sum"][0] == Decimal(7)
    assert ig["likes"][0] == Decimal(12) and ig["views"][0] == Decimal(250)
    assert "saves" not in ig  # non-numeric value dropped, never guessed
    fb = window_totals("FACEBOOK", FB)
    assert fb["views"][0] == Decimal(10) and fb["engagements"][0] == Decimal(4)
    assert fb["new_followers"][0] == Decimal(1)


def test_report_shows_whole_account_section() -> None:
    channels: list[dict[str, Any]] = [
        {
            "platform": "INSTAGRAM",
            "display": [
                {"key": "views", "label": "Views", "value": 1250.0},
                {"key": "likes", "label": "Likes", "value": 12.0},
                {"key": "new_followers", "label": "New followers", "value": None},
            ],
        },
        {
            "platform": "FACEBOOK",
            "display": [{"key": "views", "label": "Media views", "value": 10.0}],
        },
    ]
    lines = "\n".join(msg.account_block(channels))
    assert "Whole account · last 7 days" in lines and "incl. ones posted directly" in lines
    assert "Views 1,250 · Likes 12" in lines and "New followers" not in lines
    assert "Media views 10" in lines and "2 days behind" in lines
    assert msg.account_block([]) == []
