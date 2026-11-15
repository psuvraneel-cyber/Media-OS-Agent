"""Metric vocabulary: how Postiz post-analytics labels map onto Media OS metric keys.

Verified against the running Postiz v2.25.3 provider code (read-only):

* Instagram `postAnalytics` → Graph `/{media}/insights` with
  `metric=views,reach,saved,likes,comments,shares` — one lifetime value per metric.
* Facebook photo/link posts → `post_total_media_view_unique` (labelled "Impressions" by Postiz,
  but it is the number of *unique viewers*), `post_reactions_by_type_total` (Reactions),
  `post_clicks`.
* Facebook video posts → `total_video_views` (Views), `fb_reels_total_plays` (Plays), ...

The same label can mean different things per platform, so mapping is keyed by (platform, label).
Only a platform's native **views** metric feeds the combined "platform-reported views" total; reach,
unique viewers, plays and impressions are never added to it.
"""

from __future__ import annotations

import re

VIEWS = "views"

# Interactions that make up "engagement" where the platform reports them.
ENGAGEMENT_KEYS = ("likes", "reactions", "comments", "shares", "saves")

_LABELS: dict[str, dict[str, str]] = {
    "INSTAGRAM": {
        "views": VIEWS,
        "reach": "reach",
        "likes": "likes",
        "comments": "comments",
        "shares": "shares",
        "saves": "saves",
    },
    "FACEBOOK": {
        "views": VIEWS,  # video posts: total_video_views
        "plays": "plays",
        "impressions": "unique_viewers",  # post_total_media_view_unique, NOT views
        "reactions": "reactions",
        "clicks": "clicks",
    },
    "YOUTUBE": {
        "views": VIEWS,
        "likes": "likes",
        "comments": "comments",
    },
}

SKIP_LABELS = {"clicks by type"}

METRIC_LABELS: dict[str, str] = {
    VIEWS: "Views",
    "reach": "Reach",
    "unique_viewers": "Unique viewers",
    "plays": "Plays",
    "likes": "Likes",
    "reactions": "Reactions",
    "comments": "Comments",
    "shares": "Shares",
    "saves": "Saves",
    "clicks": "Clicks",
}

VIEW_DEFINITIONS: dict[str, str] = {
    "INSTAGRAM": "Instagram 'views': times the post was displayed, counted by Meta (lifetime).",
    "FACEBOOK": (
        "Facebook image posts report unique viewers (post_total_media_view_unique), not views, so "
        "they are shown separately and not added to the combined total. Facebook video posts "
        "report total_video_views, which is included."
    ),
    "YOUTUBE": "YouTube 'views' as reported by the YouTube Data API (not connected yet).",
}


def metric_key(platform: str, label: str) -> str | None:
    """Media OS key for a Postiz label, or None when the label carries no usable metric."""
    norm = label.strip().lower()
    if not norm or norm in SKIP_LABELS:
        return None
    known = _LABELS.get(platform.upper(), {})
    if norm in known:
        return known[norm]
    slug = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")
    return f"other_{slug}"[:64] if slug else None
