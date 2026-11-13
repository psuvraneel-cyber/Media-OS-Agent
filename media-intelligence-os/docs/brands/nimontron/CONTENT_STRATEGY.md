# Nimontron — Content Strategy v1 (roadmap N0)

**Status:** Draft, 2026-11-14. Built only on `BRAND_BRIEF.md` approved claims. Items marked
**[owner]** wait for the owner's answers (Play link, handles, tone).
**Tag vocabulary:** `apps/api/src/media_os_api/content/taxonomy.py` (every post carries a `PostTags` set).

---

## 1. Goal and success signals

Drive installs of Nimontron from Bengali families planning ceremonies, starting with the wedding
season (from **18 Nov 2026**). Early success signals, before enough data exists for N5 learning:
saves and shares per post (people keep and forward card ideas), profile visits, link clicks to the
Play listing. Install attribution comes later through the Play Console.

## 2. Content pillars

Pillar codes are stored in the brand profile's `content_pillars` and must match `PostTags.pillar`.

| Code | Pillar | Share of posts | What it shows | Approved claims used |
|---|---|---|---|---|
| `wedding_season` | Wedding season | ~40% | Hindu and Muslim wedding cards, gaye holud, bou-bhat/walima, the wedding pack | cards for both families; one-tap other-family card; wedding pack ₹99 (India) |
| `how_it_works` | How it works | ~25% | The three steps; English names turned into Bengali; WhatsApp share; map QR; PDF | three steps; names in English → Bengali; one-tap WhatsApp; map QR; gallery/PDF |
| `wording_help` | Correct Bengali wording | ~15% | Sadhu vs cholit, honorifics, the panjika date, the parts every card needs | wording in the familiar language of printed cards; honorifics added; panjika date help |
| `design_showcase` | Designs | ~15% | Free and premium designs (রাজকীয়, বেনারসি, শাঁখা-পলা, জামদানি, মোগল টালি, নকশিকাঁথা, গাঁদার মালা) | premium designs list; free classic designs; couple-photo design |
| `other_occasions` | Every occasion | ~5–10% | Annaprashan/aqiqah, birthday, puja, iftar, griha pravesh, biodata; shraddha/milad (free) | occasions list; shraddha, milad and memorial cards entirely free |

**Never featured** until the wording review signs them off: Christian and Buddhist cards (the
taxonomy rejects these occasions). Never attribute the default wedding verse to Tagore.

## 3. Channels, formats and cadence

| Channel | Formats | Cadence (start) | Notes |
|---|---|---|---|
| Instagram + Facebook (one page, cross-posted) | Carousel (5–8 slides); Reel (15–45 s) | ≤ 1 post/day; Reels 1–2/week | Carousels first (N2–N3); Reels from N4 |
| YouTube | Shorts (≤ 60 s); long-form (5–12 min) | Shorts 3–4/week; long-form every 1–2 weeks | Shorts from N4; long-form from N6, always owner-approved |

Hard caps enforced in the brand profile's `publishing_preferences` (N3 publishing must respect
them): Instagram 2/day, Facebook 2/day, YouTube Shorts 1/day, YouTube long-form 1/week. No
identical cross-account reposts; the same idea is re-cut per format.

**Language:** Bengali-first on-image text, with an English caption block for families abroad
(`Language.BN_EN`). A small share of English-only posts (`EN`) tests the abroad audience.

## 4. Hook library (structural patterns, not copy)

| `HookType` | Pattern | Example direction (wording generated later, claims from the brief only) |
|---|---|---|
| `QUESTION` | Ask about the reader's situation | "Wedding card wording giving you a headache?" |
| `PAIN_POINT` | Name a real frustration | spelling names in Bengali; which honorific goes where |
| `BEFORE_AFTER` | Raw details → finished card | names, date, venue on slide 1 → card on slide 2 |
| `SPEED_DEMO` | Show the three steps quickly | "three steps, one card" |
| `HOW_TO` | Teach one task | "how to make the groom's family card from the bride's" |
| `NUMBER_LIST` | N things | "5 lines every Bengali wedding card has" |
| `MISTAKE` | A common error, then the fix | honorific placement; mixing sadhu and cholit |
| `SEASONAL` | Tie to the calendar | wedding season, puja season |
| `SHOWCASE` | One design, close up | "this week's design: শাঁখা-পলা" |
| `CURIOSITY` | Hold the reveal to the end | final slide reveals the finished card |

Every post ends with one `CallToAction`: `INSTALL` (Play link confirmed), `TRY_FREE_DESIGNS`,
`SAVE_FOR_LATER`, `SHARE_WITH_FAMILY` or `COMMENT`.

## 5. Explore vs exploit

Until N5 has data, every post is `EXPLORE`: the first weeks rotate deliberately through hook
types, visual styles and posting slots so each gets a fair sample. After about 30 posts per format,
N5 shifts the mix to ~70–80% `EXPLOIT` (winning patterns) and ~20–30% `EXPLORE`.

## 6. First two weeks (from 18 Nov 2026) — weekly rhythm

The rotation covers every hook type and slot at least once. Specific auspicious wedding dates
(Agrahayan) should be checked against the panjika before any post names a date.

| Day | IG/FB | YouTube | Pillar · hook · slot |
|---|---|---|---|
| Mon | Carousel | Short | `wedding_season` · `SEASONAL` · EVENING |
| Tue | Carousel | — | `how_it_works` · `SPEED_DEMO` · AFTERNOON |
| Wed | Carousel | Short | `wording_help` · `MISTAKE` · EVENING |
| Thu | Reel | — | `design_showcase` · `SHOWCASE` · NIGHT |
| Fri | Carousel | Short | `wedding_season` · `BEFORE_AFTER` · EVENING |
| Sat | Carousel | Short | `how_it_works` · `HOW_TO` · MORNING |
| Sun | Carousel | — | `other_occasions` · `QUESTION` · EVENING |

Week 2 repeats the rhythm with the hooks rotated (`NUMBER_LIST`, `PAIN_POINT`, `CURIOSITY`, …) and
the slots shifted, so no pillar is tied to a single hook or time.

## 7. Open items

1. ~~Play Store link~~ confirmed: https://play.google.com/store/apps/details?id=com.nimontron.app (website link optional).
2. **[owner]** Account handles (Instagram/Facebook page, YouTube channel).
3. **[owner]** Tone: warm/familial, festive, practical "save time", or a mix by pillar.
4. **[owner]** Screen recordings for Reels/Shorts (N4).
5. Sample-data only: no real person's name ever appears on a showcased card.
