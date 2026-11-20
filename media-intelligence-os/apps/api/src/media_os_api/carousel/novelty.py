"""Keep generated carousels from repeating a recent post's subject.

Two layers:
1. Before writing, the AI gets the subjects of the brand's recent posts and is told to choose a
   clearly different one (`avoid_block`).
2. After writing, the draft's distinctive words (headlines, ignoring generic words like wedding,
   card, mistake) are compared with each recent post. Sharing two or more distinctive words, and
   at least half of the smaller set, counts as a repeat: the writer rewrites once, and a draft
   that still repeats is flagged in the QA report (a warning, so the owner sees why).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from media_os_api.carousel.schema import CarouselDraft
from media_os_api.db.models import ContentPackage

RECENT_POSTS = 20  # compared against
PROMPT_POSTS = 12  # listed in the prompt
MIN_SHARED = 2
MIN_OVERLAP = 0.5

_WORD = re.compile(r"[a-z0-9]+|[ঀ-৿]+")
# Light Bengali stemming: ভুলটা → ভুল, কার্ডে → কার্ড, বিয়ের → বিয়ে.
_BN_SUFFIXES = ("গুলো", "গুলি", "টা", "টি", "দের", "েরা", "ের", "ে", "র", "য়")

# Words every Nimontron post uses, plus common function words: never evidence of a repeat.
_GENERIC = {
    # English
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "did",
    "for",
    "from",
    "get",
    "got",
    "has",
    "have",
    "how",
    "in",
    "is",
    "it",
    "its",
    "just",
    "make",
    "makes",
    "many",
    "more",
    "most",
    "my",
    "no",
    "not",
    "now",
    "of",
    "on",
    "or",
    "our",
    "right",
    "so",
    "that",
    "the",
    "their",
    "them",
    "these",
    "this",
    "to",
    "too",
    "up",
    "us",
    "was",
    "what",
    "when",
    "who",
    "why",
    "will",
    "with",
    "you",
    "your",
    "yours",
    "here",
    "own",
    "ready",
    "few",
    "minute",
    "minutes",
    "app",
    "nimontron",
    "card",
    "cards",
    "invitation",
    "invitations",
    "invite",
    "wedding",
    "weddings",
    "bengali",
    "design",
    "designs",
    "mistake",
    "mistakes",
    "wrong",
    "free",
    "easy",
    "step",
    "steps",
    "phone",
    "tap",
    "share",
    "whatsapp",
    "play",
    "google",
    # Bengali (stemmed forms)
    "এই",
    "এটা",
    "ওই",
    "কি",
    "কী",
    "না",
    "তো",
    "আর",
    "ও",
    "যে",
    "এক",
    "করুন",
    "করেন",
    "করছেন",
    "করবেন",
    "হয়",
    "হবে",
    "হচ্ছে",
    "নিয়ে",
    "জন্য",
    "দিয়ে",
    "থেকে",
    "অনেকেই",
    "অনেক",
    "আপনা",
    "আপনি",
    "নিজেই",
    "নিজে",
    "সব",
    "এখন",
    "বানান",
    "বানাতে",
    "বিয়ে",
    "কার্ড",
    "নিমন্ত্রণ",
    "নিমন্ত্রণপত্র",
    "নিমন্ত্রণপত্",
    "অ্যাপ",
    "ভুল",
    "ডিজাইন",
    "সহজে",
    "মিনিটে",
    "ধাপে",
    "তৈরি",
    "বিয়",
}


def _stem(word: str) -> str:
    if word.isascii():
        return word
    for suffix in _BN_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 2:
            return word[: -len(suffix)]
    return word


# Subjects named the same way in English and Bengali. Word overlap can't see that an English
# "Sri or Srimati?" and a Bengali "শ্রী/শ্রীমতী নিয়ে ভুল" are the same post, so the hook of each
# post is also reduced to these language-free subjects. Keys are lower-cased, stemmed words.
_CONCEPT_WORDS: dict[str, tuple[str, ...]] = {
    "honorifics": (
        "sri",
        "shri",
        "sree",
        "srimati",
        "shrimati",
        "honorific",
        "honorifics",
        "janab",
        "janaba",
        "শ্রী",
        "শ্রীমতী",
        "জনাব",
        "জনাবা",
        "সম্বোধন",
    ),
    "name spelling": ("name", "names", "spell", "spelt", "spelled", "spelling", "নাম"),
    "printer delays": ("delay", "delays", "late", "printer", "press", "দেরি", "প্রেস", "ছাপা"),
    "card wording": (
        "wording",
        "sadhu",
        "cholit",
        "verse",
        "verses",
        "সাধু",
        "চলিত",
        "ভাষা",
        "লেখা",
        "কবিতা",
    ),
    "bengali date": ("panjika", "tithi", "পঞ্জিকা", "তিথি", "তারিখ"),
    "map qr": ("qr", "map", "venue", "directions", "ম্যাপ", "কিউআর", "ঠিকানা"),
    "privacy": ("privacy", "private", "login", "offline", "account", "লগইন", "অফলাইন"),
    "pricing": ("price", "prices", "premium", "₹", "rupees", "দাম", "প্রিমিয়াম"),
    "other family": ("groom", "bride", "বরপক্ষ", "কনেপক্ষ", "বর", "কনে"),
    "wedding season": ("season", "seasonal", "মরশুম", "মরসুম"),
    "gaye holud": ("holud", "haldi", "হলুদ"),
    "bou-bhat / walima": ("boubhat", "walima", "reception", "বৌভাত", "বউভাত", "ওয়ালিমা"),
    "annaprashan": ("annaprashan", "mukhebhat", "aqiqah", "অন্নপ্রাশন", "মুখেভাত", "আকিকা"),
    "birthday": ("birthday", "জন্মদিন"),
    "upanayan / griha pravesh": ("upanayan", "griha", "pravesh", "পৈতে", "উপনয়ন", "গৃহপ্রবেশ"),
    "biodata": ("biodata", "বায়োডাটা", "পাত্র", "পাত্রী"),
    "shraddha / memorial": ("shraddha", "memorial", "milad", "শ্রাদ্ধ", "মিলাদ"),
    "puja": ("puja", "pujo", "durga", "lakshmi", "kali", "saraswati", "পূজা", "পুজো"),
}
_CONCEPTS = {word: concept for concept, words in _CONCEPT_WORDS.items() for word in words}


def concepts(texts: list[str]) -> set[str]:
    """Language-free subjects named in some text (see `_CONCEPT_WORDS`)."""
    out: set[str] = set()
    for text in texts:
        for raw in _WORD.findall((text or "").replace("*", " ").lower()):
            hit = _CONCEPTS.get(raw) or _CONCEPTS.get(_stem(raw))
            if hit:
                out.add(hit)
        if "₹" in (text or ""):
            out.add("pricing")
    return out


def terms(texts: list[str]) -> set[str]:
    """Distinctive words across some headlines (lower-cased, stemmed, generic words dropped)."""
    out: set[str] = set()
    for text in texts:
        for raw in _WORD.findall((text or "").replace("*", " ").lower()):
            word = _stem(raw)
            if len(word) >= 2 and word not in _GENERIC and raw not in _GENERIC:
                out.add(word)
    return out


def draft_terms(draft: CarouselDraft) -> set[str]:
    texts: list[str] = []
    for s in draft.slides:
        texts += [s.headline_bn or "", s.headline_en or ""]
    return terms(texts)


def hook_concepts(draft: CarouselDraft) -> set[str]:
    first = draft.slides[0]
    return concepts([first.headline_bn or "", first.headline_en or ""])


@dataclass(frozen=True)
class RecentPost:
    title: str
    terms: frozenset[str]
    hook_concepts: frozenset[str] = frozenset()  # the subject its hook names, in any language


@dataclass(frozen=True)
class Repeat:
    title: str
    shared: tuple[str, ...]
    overlap: float


def recent_posts(
    session: Session, brand_id: uuid.UUID, limit: int = RECENT_POSTS
) -> list[RecentPost]:
    rows = session.execute(
        select(ContentPackage)
        .where(
            ContentPackage.brand_profile_id == brand_id,
            ContentPackage.format == "CAROUSEL",
            ContentPackage.status != "REJECTED",
        )
        .order_by(ContentPackage.created_at.desc())
        .limit(limit)
    ).scalars()
    out = []
    for pkg in rows:
        texts = [pkg.title or "", pkg.hook or ""]
        hook_texts = list(texts)
        for i, s in enumerate(pkg.slide_data or []):
            if isinstance(s, dict):
                pair = [str(s.get("headline_bn") or ""), str(s.get("headline_en") or "")]
                texts += pair
                if i == 0:
                    hook_texts += pair
        title = (pkg.title or pkg.hook or "Untitled").replace("*", "").strip()
        out.append(
            RecentPost(
                title=title[:120],
                terms=frozenset(terms(texts)),
                hook_concepts=frozenset(concepts(hook_texts)),
            )
        )
    return out


def closest_repeat(draft: CarouselDraft, recent: list[RecentPost]) -> Repeat | None:
    """The recent post this draft repeats most, if any.

    Two ways to repeat: the hooks name the same subject (in any language), or the headlines share
    at least two distinctive words making up half the smaller set."""
    mine = draft_terms(draft)
    my_hook = hook_concepts(draft)
    best: Repeat | None = None
    for post in recent:
        same_subject = my_hook & post.hook_concepts
        if same_subject:
            return Repeat(title=post.title, shared=tuple(sorted(same_subject)), overlap=1.0)
    for post in recent:
        shared = mine & post.terms
        if len(shared) < MIN_SHARED:
            continue
        overlap = len(shared) / max(1, min(len(mine), len(post.terms)))
        if overlap >= MIN_OVERLAP and (best is None or overlap > best.overlap):
            best = Repeat(title=post.title, shared=tuple(sorted(shared)), overlap=overlap)
    return best


def avoid_block(recent: list[RecentPost]) -> str:
    if not recent:
        return ""
    lines = "\n".join(f"- {p.title}" for p in recent[:PROMPT_POSTS])
    return (
        "RECENT POSTS (already published or scheduled). Do NOT repeat any of their subjects, "
        "examples or angles; choose a clearly different subject within the brief:\n"
        f"{lines}\n\n"
    )
