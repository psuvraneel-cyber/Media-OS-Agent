"""Unit: generated carousels must not repeat a recent post's subject."""

from __future__ import annotations

from typing import Any

import pytest

from media_os_api.carousel import writer
from media_os_api.carousel.brand_kit import NIMONTRON
from media_os_api.carousel.novelty import RecentPost, avoid_block, closest_repeat, terms
from media_os_api.carousel.schema import CarouselBrief, CarouselDraft
from media_os_api.content.taxonomy import HookType, Language

PLAY = NIMONTRON.play_url

# Real Nimontron posts (titles and hooks) as of 17 Nov 2026.
EXISTING = {
    "Wedding season is here!": [
        "Wedding season is here! Ready with your invitation cards?",
        "বিয়ের মরশুম এসে গেছে! নিমন্ত্রণ কার্ড তৈরি তো?",
    ],
    "A card in a few minutes": ["Make a Bengali invitation card in a few minutes"],
    "The invitation-writing mistake": ["বিয়ের নিমন্ত্রণপত্র লেখার সময় এই ভুলটি করছেন না তো?"],
    "Card delays?": [
        "Wedding card *delays? Make your own* invitations.",
        "বিয়ের কার্ডে *দেরি? নিজেই বানান* নিমন্ত্রণ।",
    ],
    "Spell everyone's name": ["Did you spell *everyone's name* right?"],
    "শ্রী/শ্রীমতী mistake": ["বিয়ের কার্ডে *শ্রী/শ্রীমতী* নিয়ে ভুল হচ্ছে না তো?"],
}


def _recent() -> list[RecentPost]:
    return [RecentPost(title=k, terms=frozenset(terms(v))) for k, v in EXISTING.items()]


def _draft(hook_bn: str, hook_en: str) -> CarouselDraft:
    def s(role: str, bn: str, en: str, claims: list[str]) -> dict[str, Any]:
        return {"role": role, "headline_bn": bn, "headline_en": en, "claim_ids": claims}

    return CarouselDraft.model_validate(
        {
            "slides": [
                s("HOOK", hook_bn, hook_en, []),
                s("POINT", "তিন ধাপে কার্ড", "A card in three steps", ["speed"]),
                s("POINT", "এক ট্যাপে হোয়াটসঅ্যাপ", "One tap to WhatsApp", ["sharing"]),
                s("POINT", "ইংরেজিতে নাম লিখুন", "Type names in English", ["names_bn"]),
                s("CTA", "গুগল প্লে থেকে নিন", "Get it on Google Play", ["on_play"]),
            ],
            "caption_bn": f"বিয়ের কার্ড সহজে। {PLAY}",
            "caption_en": f"Bengali wedding cards made easy. {PLAY}",
            "hashtags": ["#Nimontron", "#BengaliWedding", "#বিয়েরকার্ড"],
            "alt_text_en": "A five-slide carousel about Bengali wedding cards.",
        }
    )


REPEAT = _draft("শ্রী না শ্রীমতী? *এই ভুলটা* অনেকেই করেন", "Sri or Srimati? *Many get this* wrong")
FRESH = _draft("গায়ে হলুদের *কার্ড* এক মিনিটে", "A *gaye holud* card in one minute")


def test_the_real_repeat_is_caught_and_nothing_else_is() -> None:
    hit = closest_repeat(REPEAT, _recent())
    assert hit is not None and hit.title == "শ্রী/শ্রীমতী mistake"
    assert set(hit.shared) == {"শ্রী", "শ্রীমতী"}
    assert closest_repeat(FRESH, _recent()) is None
    # None of the existing posts count as repeats of each other (generic words don't count).
    posts = _recent()
    for i, a in enumerate(posts):
        for b in posts[i + 1 :]:
            shared = a.terms & b.terms
            assert len(shared) < 2, (a.title, b.title, shared)


def test_prompt_lists_recent_subjects() -> None:
    block = avoid_block(_recent())
    assert "Do NOT repeat" in block and "শ্রী/শ্রীমতী mistake" in block
    assert avoid_block([]) == ""


def _brief() -> CarouselBrief:
    return CarouselBrief(pillar="wording_help", hook_type=HookType.MISTAKE, language=Language.BN_EN)


@pytest.mark.parametrize(
    ("second", "expect"),
    [(FRESH, "PASS"), (REPEAT, "WARN")],
)
def test_a_repeat_is_rewritten_once_then_flagged(
    monkeypatch: pytest.MonkeyPatch, second: CarouselDraft, expect: str
) -> None:
    drafts = [REPEAT, second]
    prompts: list[dict[str, Any]] = []

    def fake_write(router: Any, kit: Any, brief: Any, **kw: Any) -> Any:
        prompts.append(kw)
        return drafts[len(prompts) - 1], "fake/model", []

    monkeypatch.setattr(writer, "write_carousel", fake_write)
    draft, _, _, qa = writer.write_with_qa(None, NIMONTRON, _brief(), recent=_recent())  # type: ignore[arg-type]
    assert len(prompts) == 2  # one extra rewrite for the repeat, no more
    assert "শ্রী/শ্রীমতী mistake" in prompts[0]["avoid"]
    assert "repeats the subject" in prompts[1]["feedback"][0]
    novelty = next(c for c in qa.checks if c.name == "novelty")
    assert novelty.status == expect and qa.passed  # a warning never blocks review
    assert draft is second
    if expect == "WARN":
        assert "শ্রী/শ্রীমতী mistake" in novelty.detail


def test_without_recent_posts_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(writer, "write_carousel", lambda *a, **k: (REPEAT, "fake/model", []))
    _, _, _, qa = writer.write_with_qa(None, NIMONTRON, _brief())  # type: ignore[arg-type]
    assert not any(c.name == "novelty" for c in qa.checks)


def test_owner_notes_go_first_in_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    from media_os_api.carousel.feedback import OwnerNote, notes_block

    prompts: list[dict[str, Any]] = []

    def fake_write(router: Any, kit: Any, brief: Any, **kw: Any) -> Any:
        prompts.append(kw)
        return FRESH, "fake/model", []

    monkeypatch.setattr(writer, "write_carousel", fake_write)
    block = notes_block([OwnerNote(title="Old post", note="Too salesy")])
    writer.write_with_qa(None, NIMONTRON, _brief(), recent=_recent(), guidance=block)  # type: ignore[arg-type]
    avoid = prompts[0]["avoid"]
    assert avoid.startswith("THE OWNER REJECTED") and "On 'Old post': Too salesy" in avoid
    assert "শ্রী/শ্রীমতী mistake" in avoid  # recent posts are still listed
    assert notes_block([]) == ""


def _hooks_only() -> list[RecentPost]:
    from media_os_api.carousel.novelty import concepts

    return [
        RecentPost(title=k, terms=frozenset(terms(v)), hook_concepts=frozenset(concepts(v)))
        for k, v in EXISTING.items()
    ]


def test_same_subject_in_another_language_is_a_repeat() -> None:
    # English-only draft vs the Bengali-only post of the day before (18 and 19 Nov 2026).
    english = _draft("শ্রী না শ্রীমতী?", "Sri or Srimati? *Many get this* wrong").model_copy()
    english.slides[0].headline_bn = None
    only_bn = [p for p in _hooks_only() if p.title == "শ্রী/শ্রীমতী mistake"]
    hit = closest_repeat(english, only_bn)
    assert hit is not None and hit.shared == ("honorifics",)
    assert closest_repeat(FRESH, _hooks_only()) is None


def test_real_posts_have_distinct_hook_subjects() -> None:
    posts = [p for p in _hooks_only() if p.hook_concepts]
    for i, a in enumerate(posts):
        for b in posts[i + 1 :]:
            assert not (a.hook_concepts & b.hook_concepts), (a.title, b.title)
