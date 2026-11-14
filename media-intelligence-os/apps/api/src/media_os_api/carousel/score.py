"""Virality score v0 — an explicitly labelled heuristic, not a prediction.

Until N5 has the account's own performance history, the score is a rubric: deterministic checks on
the draft plus an optional AI review. It ranks drafts against each other and flags weak hooks; it
must never be presented as expected reach.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from media_os_api.ai.errors import AIUnavailable, AllProvidersExhausted
from media_os_api.ai.router import AIRouter
from media_os_api.ai.types import GenerationRequest, TaskClass
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft, SlideRole
from media_os_api.content.taxonomy import HookType

SCORE_VERSION = "heuristic-v0"
LABEL = "Heuristic v0 — rubric estimate, not a reach prediction"


class AIReview(BaseModel):
    hook_strength: int = Field(ge=0, le=10)
    clarity: int = Field(ge=0, le=10)
    emotional_pull: int = Field(ge=0, le=10)
    cta_strength: int = Field(ge=0, le=10)
    one_improvement: str = Field(max_length=300)


def _hook_text(draft: CarouselDraft) -> str:
    first = draft.slides[0]
    return first.headline_en or first.headline_bn or ""


def rule_scores(brief: CarouselBrief, draft: CarouselDraft) -> dict[str, int]:
    hook = _hook_text(draft)
    hook_pts = 10 if len(hook) <= 60 else 6 if len(hook) <= 80 else 3
    if brief.hook_type is HookType.QUESTION and "?" not in hook:
        hook_pts -= 3
    if brief.hook_type is HookType.NUMBER_LIST and not re.search(r"[\d০-৯]", hook):
        hook_pts -= 3
    bodies = [len((s.body_en or s.body_bn or "").split()) for s in draft.slides]
    avg = sum(bodies) / len(bodies)
    clarity = 10 if avg <= 18 else 7 if avg <= 25 else 4
    visuals = sum(1 for s in draft.slides if s.visual_asset_id)
    visual = 10 if 1 <= visuals <= 3 else 6 if visuals > 3 else 3
    cta = 10 if draft.slides[-1].role is SlideRole.CTA else 2
    return {
        "hook_strength": max(hook_pts, 0),
        "clarity": clarity,
        "visual_proof": visual,
        "cta_strength": cta,
    }


def ai_review(router: AIRouter, draft: CarouselDraft) -> AIReview | None:
    try:
        result = router.generate(
            GenerationRequest(
                task=TaskClass.REVIEW,
                system=(
                    "You are a strict social media editor for a Bengali invitation-card app. "
                    "Score the carousel 0-10 on each field. Be critical; 7+ means genuinely "
                    "scroll-stopping. Reply with one JSON object only."
                ),
                prompt=(
                    "Fields: hook_strength, clarity, emotional_pull, cta_strength (integers), "
                    "one_improvement (one sentence).\nCAROUSEL:\n"
                    + draft.model_dump_json(exclude={"alt_text_en", "hashtags"})
                ),
                output_schema=AIReview,
                max_output_tokens=1024,
                temperature=0.2,
                operation="carousel_review",
            )
        )
    except (AIUnavailable, AllProvidersExhausted):  # optional review; rule scores still stand
        return None
    return result.data if isinstance(result.data, AIReview) else None


def score_carousel(
    brief: CarouselBrief, draft: CarouselDraft, review: AIReview | None
) -> dict[str, Any]:
    rules = rule_scores(brief, draft)
    parts = dict(rules)
    if review is not None:
        parts["hook_strength"] = round((rules["hook_strength"] + review.hook_strength) / 2)
        parts["clarity"] = round((rules["clarity"] + review.clarity) / 2)
        parts["cta_strength"] = round((rules["cta_strength"] + review.cta_strength) / 2)
        parts["emotional_pull"] = review.emotional_pull
    weights = {
        "hook_strength": 0.35,
        "clarity": 0.2,
        "visual_proof": 0.15,
        "cta_strength": 0.15,
        "emotional_pull": 0.15,
    }
    used = {k: w for k, w in weights.items() if k in parts}
    total = sum(parts[k] * w for k, w in used.items()) / sum(used.values()) * 10
    return {
        "version": SCORE_VERSION,
        "label": LABEL,
        "total": round(total),
        "parts": parts,
        "ai_review_used": review is not None,
        "suggestion": review.one_improvement if review else None,
    }
