"""Brand kits: the only facts, assets and visual tokens carousel generation may use.

The Nimontron kit is transcribed from `docs/brands/nimontron/BRAND_BRIEF.md` (owner-approved store
listing). A claim not listed here must never appear in content; QA rejects unknown claim ids and
banned patterns. Assets are referenced by id and read in place (read-only mount of the owner's
`C:\\nimontron\\store`), never copied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from media_os_api.content.taxonomy import Occasion


@dataclass(frozen=True)
class Claim:
    id: str
    theme: str
    text: str  # English statement of the approved fact (writers may phrase it in any language)


@dataclass(frozen=True)
class VisualAsset:
    id: str
    path: str  # relative to the brand asset root
    kind: str  # CARD | SCREEN
    occasion: Occasion
    description: str


@dataclass(frozen=True)
class BannedPattern:
    label: str
    pattern: re.Pattern[str]
    severity: str = "FAIL"  # FAIL blocks; WARN needs a human look in the review queue


@dataclass(frozen=True)
class BrandKit:
    brand_slug: str
    name_bn: str
    name_en: str
    play_url: str
    tone: str
    palette: dict[str, str]
    claims: tuple[Claim, ...]
    assets: tuple[VisualAsset, ...]
    excluded_assets: dict[str, str]  # path -> reason (never used)
    banned: tuple[BannedPattern, ...]
    hashtags_seed: tuple[str, ...] = field(default_factory=tuple)

    def claim(self, claim_id: str) -> Claim | None:
        return next((c for c in self.claims if c.id == claim_id), None)

    def asset(self, asset_id: str) -> VisualAsset | None:
        return next((a for a in self.assets if a.id == asset_id), None)


def _p(regex: str) -> re.Pattern[str]:
    return re.compile(regex, re.IGNORECASE)


NIMONTRON = BrandKit(
    brand_slug="brand-app-promo",
    name_bn="নিমন্ত্রণ",
    name_en="Nimontron",
    play_url="https://play.google.com/store/apps/details?id=com.nimontron.app",
    tone=(
        "Warm and festive, like a trusted elder in the family helping with the card. Gentle and "
        "respectful for shraddha and memorial cards. Never pushy, never hype."
    ),
    palette={
        "red": "#a3161f",
        "red_deep": "#7c0f17",
        "gold": "#b9821a",
        "gold_soft": "#f6e3b4",
        "tint": "#fbf1ec",
        "ink": "#231b1b",
        "green": "#0f5a3a",
        "share": "#1f8a4c",
    },
    claims=(
        Claim("speed", "Speed", "A card is ready in a few minutes, in three steps."),
        Claim(
            "wording",
            "Wording",
            "The app writes the whole card in the familiar language of printed cards, "
            "sadhu or cholit.",
        ),
        Claim(
            "names_bn",
            "Names",
            "Type names in English and they appear in Bengali; the app shows exactly how.",
        ),
        Claim("honorifics", "Honorifics", "শ্রী/শ্রীমতী and জনাব/জনাবা are added automatically."),
        Claim("panjika", "Dates", "The app helps with the Bengali (panjika) date."),
        Claim("editing", "Editing", "You can change any line in your own words."),
        Claim(
            "sharing",
            "Sharing",
            "One-tap WhatsApp send with a short date/address note; save to gallery; print PDF.",
        ),
        Claim("map_qr", "Guests", "A map QR code on the card helps guests find the venue."),
        Claim("other_family", "Families", "Make the other family's card from yours in one tap."),
        Claim(
            "privacy",
            "Privacy",
            "No login or account; names, photos and addresses never leave the phone; "
            "works offline.",
        ),
        Claim(
            "occasions",
            "Occasions",
            "Cards for Hindu and Muslim weddings, gaye holud, bou-bhat/walima, annaprashan/"
            "mukhebhat, aqiqah, birthdays, puja invitations (Durga, Lakshmi, Kali, Saraswati), "
            "iftar, upanayan, griha pravesh, shraddha and niyombhanga, milad, memorial prayers, "
            "and marriage biodata in Bengali or English.",
        ),
        Claim(
            "free_tier",
            "Free tier",
            "Classic wording and basic designs cost nothing; shraddha, milad and memorial cards "
            "are entirely free.",
        ),
        Claim(
            "premium",
            "Premium",
            "Premium adds literary wording (seasonal lines and verses) and designs such as "
            "রাজকীয়, বেনারসি, শাঁখা-পলা, জামদানি, মোগল টালি, নকশিকাঁথা, গাঁদার মালা; a "
            "couple-photo design; HD images and print PDF without the app name.",
        ),
        Claim(
            "pricing_india",
            "Pricing (India only)",
            "In India: one premium card ₹49; wedding pack ₹99 for all cards of one couple. "
            "Outside India say 'price on Google Play'.",
        ),
        Claim("on_play", "Availability", "Available on Google Play (Android)."),
    ),
    assets=(
        VisualAsset(
            "card_muslim_wedding",
            "raw/card-1.png",
            "CARD",
            Occasion.WEDDING_MUSLIM,
            "Muslim wedding (শুভ বিবাহ) card, cream with green and gold nakshi border, app sample "
            "data.",
        ),
        VisualAsset(
            "card_annaprashan",
            "raw/card-3.png",
            "CARD",
            Occasion.ANNAPRASHAN_AQIQAH,
            "Annaprashan (শুভ অন্নপ্রাশন) card, night-blue with gold moon and stars, app sample data.",
        ),
        VisualAsset(
            "screen_occasion_picker",
            "screenshots/1.png",
            "SCREEN",
            Occasion.GENERAL,
            "App home: choose the ceremony (wedding, Muslim wedding, gaye holud, bou-bhat, "
            "annaprashan); headline 'beautiful Bengali invitation cards in minutes'.",
        ),
        VisualAsset(
            "screen_names_bn",
            "screenshots/2.png",
            "SCREEN",
            Occasion.WEDDING_HINDU,
            "Names typed in English (Aishi, Arka) shown as they will appear in Bengali.",
        ),
        VisualAsset(
            "screen_share_qr_pdf",
            "screenshots/5.png",
            "SCREEN",
            Occasion.WEDDING_HINDU,
            "Finished card with map QR code, HD image / PDF (A5, A4) buttons and one-tap WhatsApp "
            "send with the short date/address note.",
        ),
        VisualAsset(
            "screen_biodata",
            "screenshots/6.png",
            "SCREEN",
            Occasion.BIODATA,
            "Marriage biodata (পাত্রীর পরিচয়) in Bengali, app sample data.",
        ),
    ),
    excluded_assets={
        "raw/card-2.png": "Shows the default wedding verse credited to Tagore (not his).",
        "screenshots/3.png": "Shows the default wedding verse credited to Tagore (not his).",
        "screenshots/4.png": "Shows the default wedding verse credited to Tagore (not his).",
    },
    banned=(
        BannedPattern(
            "ranking", _p(r"\b(best|number\s*one|no\.?\s*1|top[- ]rated|leading)\b|#\s*1\b|সেরা")
        ),
        BannedPattern(
            "install_numbers",
            _p(r"\d[\d,.]*\s*\+?\s*(downloads|installs|users)|ডাউনলোড\s*[০-৯\d]|[০-৯\d]+\s*জন"),
        ),
        BannedPattern(
            "testimonial", _p(r"\b(testimonial|review(s|ed)?|customers? (say|said|love))\b")
        ),
        BannedPattern(
            "unapproved_occasion",
            _p(r"christian|buddhist|baptism|pabbajja|sanghadan|খ্রিস্টান|খ্রিষ্টান|বৌদ্ধ|ব্যাপটিজম"),
        ),
        BannedPattern("tagore_mention", _p(r"tagore|rabindranath|রবীন্দ্র|ঠাকুরের কবিতা"), "WARN"),
        BannedPattern("ios_claim", _p(r"\b(ios|iphone|app store)\b")),
    ),
    hashtags_seed=(
        "#Nimontron",
        "#BengaliWedding",
        "#বিয়েরকার্ড",
        "#নিমন্ত্রণ",
        "#WeddingCard",
        "#BengaliInvitation",
    ),
)

KITS: dict[str, BrandKit] = {NIMONTRON.brand_slug: NIMONTRON}


def kit_for(brand_slug: str) -> BrandKit:
    try:
        return KITS[brand_slug]
    except KeyError:
        raise ValueError(f"No carousel brand kit for '{brand_slug}'.") from None
