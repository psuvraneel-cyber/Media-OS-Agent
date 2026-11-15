"""The exact caption text sent to the platforms, shared by QA and publishing.

QA and the publisher must measure the same string: QA fails a carousel whose final caption
(Bengali + English + hashtags) exceeds Instagram's limit, and the publisher refuses rather than
silently truncating — truncation could cut off the Play link that QA verified.
"""

from __future__ import annotations

from collections.abc import Iterable

CAPTION_MAX = 2200  # Instagram caption limit (Facebook allows more; the stricter one wins)


def full_caption(caption: str | None, hashtags: Iterable[object]) -> str:
    text = (caption or "").strip()
    tags = " ".join(t for t in hashtags if isinstance(t, str) and t)
    return f"{text}\n\n{tags}".strip() if tags else text
