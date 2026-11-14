# N2 — Carousel pipeline → review queue

**Status:** built 2026-11-14 (owner-approved plan: migration 0006 + creative container).
Nothing in N2 publishes anything; approval only records the owner's decision (publishing is N3).

## Flow

1. **Brief** (`carousel/schema.py: CarouselBrief`): pillar, hook type, occasion, language mode
   (`BN`, `BN_EN`, `EN`), visual style, CTA, posting slot.
2. **Writer** (`carousel/writer.py`): the N1 router writes a `CarouselDraft` (5–8 slides,
   captions, hashtags, alt text) natively in the chosen language. Bengali and bilingual posts use
   the `BENGALI_COPY` route (Gemini first); English posts use `ENGLISH_COPY`.
3. **Brand kit** (`carousel/brand_kit.py`): the only facts (approved claim ids from the brand
   brief), visuals (asset ids, read in place) and palette the writer may use. Excluded assets:
   `raw/card-2.png`, `screenshots/3.png`, `screenshots/4.png` (misattributed Tagore verse).
4. **Text QA** (`carousel/qa.py`): structure, language mode, every content slide cites approved
   claims, banned phrases (rankings, install numbers, testimonials, Christian/Buddhist cards,
   iOS), Tagore mention (warning), India-only pricing, Play link on install CTAs, hashtags, length.
5. **Score v0** (`carousel/score.py`): rule rubric plus an optional AI review; always labelled
   "Heuristic v0 — rubric estimate, not a reach prediction".
6. **Save** (`carousel/service.py`): `ContentPackage` at `DRAFTED` with `post_tags`, `qa_report`,
   `score` (migration 0006) and an audit event; QA-passed drafts enqueue a render job.
7. **Render** (`carousel/render_job.py`, `carousel/render.py`, `carousel/templates.py`):
   `media-os-creative` renders 1080×1350 PNGs with headless Chromium, offline, fonts bundled
   (OFL, `infra/creative/fonts`), brand assets mounted read-only. Text auto-fits; anything that
   still overflows fails render QA. → `RENDERED` → `QA_PASSED`.
8. **Review** (`api/routes/content.py`, dashboard **Approvals**): slides, caption, QA checks and
   score; approve (QA-passed only) or reject with a note. Both are audited.

## Isolation

| Container | AI keys | Brand assets | Rendered media |
|---|---|---|---|
| worker | yes | — | — |
| creative | **no** | read-only | read/write |
| api | no | — | read-only |

## Operating

```
docker exec media-os-worker python -m media_os_api.carousel.admin draft \
  --pillar wedding_season --hook SEASONAL --occasion WEDDING_HINDU --language BN_EN
docker exec media-os-worker python -m media_os_api.carousel.admin generate ...   # saves + renders
```

Each carousel uses about two Gemini Flash requests (write + review). The free tier allows ~20 a day
on Flash; beyond that the router fails over to the next provider automatically.
