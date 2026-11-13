# Nimontron — Brand Brief for Media OS (`brand-app-promo`)

**Status:** v1, compiled 2026-11-14 from the owner's app folder `C:\nimontron` (read-only).
Every product fact below cites its source file. Nothing here is invented; anything not found in the
sources is listed under **Open questions** and must not be claimed in content until the owner answers.

Primary sources: `docs/STORE_LISTING.md` (owner-approved listing copy), `docs/HANDOFF.md`
(24 Oct 2026), `CHANGELOG.md` (v1.1.0), `docs/PLAY_CONSOLE.md`, `src/styles/app.css`, `store/`.

---

## 1. What the product is

- **Name:** নিমন্ত্রণ (Nimontron). Play listing names: "নিমন্ত্রণ: কার্ড ও বায়োডাটা" (bn),
  "Nimontron: Bengali Invitations" (en). App id `com.nimontron.app`. *(STORE_LISTING, HANDOFF)*
- **One-line:** Bengali ceremony invitation cards and marriage biodata, in correct, graceful Bengali,
  made in a few minutes, offline on the phone. *(README, STORE_LISTING)*
- **How it works:** enter names, date and place → the app writes the whole card → pick a design →
  send on WhatsApp, save to gallery, or get a print PDF. "তিন ধাপে তৈরি হয়ে যাবে" (ready in three
  steps). *(STORE_LISTING, screenshot 1)*

## 2. Approved claims (safe to use in content)

Taken from the owner-approved store listing:

| Theme | Claim |
|---|---|
| Speed | Cards in a few minutes; three steps |
| Wording | The app writes the whole card in the familiar language of printed cards, sadhu or cholit |
| Names | Type names in English, they appear in Bengali; the app shows exactly how |
| Honorifics | শ্রী / শ্রীমতী, জনাব / জনাবা added automatically |
| Dates | Help with the Bengali (panjika) date |
| Editing | Change any line in your own words |
| Sharing | One-tap WhatsApp send with a short date/address note; save to gallery; print PDF |
| Guests | Map QR code on the card |
| Families | Make the other family's card from yours in one tap |
| Privacy | No login or account; names, photos and addresses never leave the phone; works offline |
| Occasions | Hindu & Muslim weddings, Christian & Buddhist weddings, gaye holud, annaprashan/mukhebhat, aqiqah, baptism, birthdays, puja invitations (Durga, Lakshmi, Kali, Saraswati…), iftar, upanayan, pabbajja, griha pravesh, shraddha & niyombhanga, milad, memorial prayers, sanghadan, marriage biodata (Bengali or English) |
| Free tier | Classic wording and basic designs cost nothing; shraddha, milad and memorial cards are entirely free |
| Premium | Literary wording (Tagore's verses, seasonal lines); designs such as রাজকীয়, বেনারসি, শাঁখা-পলা, জামদানি, মোগল টালি, নকশিকাঁথা, গাঁদার মালা; couple-photo design; HD images and print PDF without the app name |
| Pricing | One premium card ₹49; wedding pack ₹99 (all cards of one couple). Other countries: Google's converted price — show "price on Google Play" outside India *(PLAY_CONSOLE, HANDOFF)* |

## 3. Claims to avoid

- **"Best", "#1", "top", or any ranking or download numbers.** None are backed up (the owner's own Play
  metadata policy note).
- **Do not attribute the default wedding verse to Tagore.** It was found not to be his (CHANGELOG
  1.1.0). Only say "Tagore's verses" for verses that really are his.
- **Christian and Buddhist card wording is still Draft** until reviewers sign off
  (`docs/WORDING_REVIEW.md`). Don't feature those cards in ads until it's approved.
- **No user testimonials or reviews** unless they are real and the owner provides them with consent.
- **Never show a real person's name on a sample card.** Use the app's built-in sample data only.
- **Install counts:** the owner confirmed 34 installs on 2026-11-14. That is internal context only; don't
  quote install numbers in content.

## 4. Audience and languages

- **Primary:** Bengali families planning ceremonies — West Bengal and Bangladesh. **Secondary:** Bengali
  families abroad (USA, UK, Canada, UAE, Australia), where the simulated buyers converted best.
  *(STORE_LISTING)*
- **Language:** Bengali-first content with English captions/variants for families abroad and
  non-Bengali-reading relatives.
- **Seasonality:** Bengali wedding season begins **18 Nov 2026** *(HANDOFF)*. Puja and Eid calendars
  drive the other card types.

## 5. Visual identity

| Token | Hex | Use |
|---|---|---|
| red | `#a3161f` | primary brand red |
| red-deep | `#7c0f17` | dark backgrounds |
| gold | `#b9821a` | accents, rules, borders |
| gold-soft | `#f6e3b4` | light accent fills |
| tint | `#fbf1ec` | warm cream background |
| ink | `#231b1b` | body text |
| green | `#0f5a3a` | Muslim-card accents |
| share | `#1f8a4c` | WhatsApp/share actions |

Source: `src/styles/app.css`. The look: deep red and gold, cream backgrounds, ornate traditional card
borders, bold Bengali display type. Fonts are bundled in `public/fonts` (licences in
`public/fonts/licenses`); they are used under their own licences only.

## 6. Asset library (referenced in place, not copied)

| Asset | Path |
|---|---|
| App icon 1024 / Play icon 512 | `C:\nimontron\store\icon-1024.png`, `store\play-icon-512.png` |
| Feature graphic 1024×500 | `C:\nimontron\store\feature-graphic.png` |
| Store screenshots 1080×1920 (6) | `C:\nimontron\store\screenshots\1.png … 6.png` |
| Raw card renders and app shots | `C:\nimontron\store\raw\card-*.png`, `shot-*.png` |
| Screenshot frame templates | `C:\nimontron\store\raw\frame-*.html`, `feature.html` |
| Design gallery (live) | `npm run dev` → `/dev/gallery` in the app repo |

Fresh card renders for content should come from the app's own renderer (sample data only), so designs
stay pixel-accurate. The app has a screenshot generator: `scripts/store-assets.mjs`.

## 7. Channels (owner's plan, 2026-11-14)

| Account | Formats |
|---|---|
| Instagram + Facebook (one brand, cross-posted) | Carousels, occasional Reels |
| YouTube | Shorts and long-form |

## 8. Open questions for the owner

1. ~~Production status~~ **Answered 2026-11-14: Nimontron is live in production on Google Play** (owner
   checked; 34 installs). "Get it on Google Play" calls to action are allowed.
2. Public Play Store link and a website/landing link to use in bios and captions.
3. Handles for the Instagram/Facebook page and the YouTube channel (to be created by the owner).
4. Tone preference: warm and familial, festive, or practical "save time" — or a mix by format.
5. Screen recordings of the app in use (needed for Reels/Shorts quality).
6. Any real user feedback the owner is willing to quote (with consent).

`.env` and other credential files in `C:\nimontron` were **not** opened.
