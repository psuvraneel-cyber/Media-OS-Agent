# N5: the learning loop

**Status:** built 2026-11-21. Media OS measures every published post, works out what's working,
learns a score from the account's own history, steers new drafts towards proven winners while
keeping experiments, and reports weekly on Telegram. Nothing is ever posted without approval.

Code: `apps/api/src/media_os_api/learning/` (`core.py` is the maths, pure and unit-tested;
`service.py` reads the database; `weekly.py` is the Telegram report). API:
`GET /api/v1/analytics/learning?brand=<ref>`. Dashboard: **Analytics → What's working**.

## 1. One fair number per post

- **Same age for every post.** Platforms report lifetime cumulative numbers, so each post is
  measured at **72 hours** old: the value at that moment, interpolated between the syncs around it.
  A younger post is shown as "measured once it's 72 hours old" and isn't learned from yet.
- **Lift.** A post's 72-hour views divided by the account's usual post: the median 72-hour views of
  the previous 20 measured posts on the same platform (the whole history while there are fewer
  than 3). 2× is twice the usual views. This keeps account growth from looking like success.
  The model learns `log2(lift)`, so 2× and 0.5× are equally far from usual.
- **Engagement** (shown, not yet learned from): likes + 2×comments + 3×saves + 3×shares, and the
  same divided by reach.
- **Instagram first.** Facebook reach is still near zero; the API takes `platform=FACEBOOK` too.

## 2. Per-tag results

Grouped by format, topic, hook, language, occasion, design, time of day (slot), weekday, hour,
visual style, call to action and strategy. Time tags come from the actual post time, not the
brief.

- Each group shows posts, median lift, average 72-hour views and engagement rate.
- **A verdict needs `LEARNING_MIN_POSTS` measured posts (default 30).** Below that the group reads
  "Not enough data yet (n/30)" and is never called a winner or a loser.
- With enough posts: **Works better** when the whole ~95 % interval of its mean log-lift is above
  usual, **Works worse** when it's below, otherwise **No clear difference**.

## 3. The learned score (replaces score v0 once trusted)

- Additive model on log-lift: an overall level plus one effect per tag value (format, topic, hook,
  language, occasion, design, time of day, weekday), each shrunk towards "usual" by 8 pseudo-posts
  (empirical Bayes) and fitted by backfitting. With little data everything stays near 1×, so it
  can't chase one lucky post. No extra libraries.
- **Tested honestly:** leave-one-out (each past post predicted by a model that never saw it).
  Reported: mean absolute error against a plain "usual" guess, and ranking accuracy (Spearman),
  next to the same ranking accuracy for the old heuristic score v0.
- **Trusted ("In use")** only with at least 30 measured posts *and* a smaller error than the plain
  guess. Until then it's labelled "Not trusted yet" and the old score keeps its "heuristic" label.
- **Predicted vs actual for every post** on the Analytics page (leave-one-out for past posts).
  Each new draft also saves its prediction in `score.learned`, to compare once it's posted.

## 4. Steering generation

- Generated posts are tagged `strategy`: **EXPLOIT** (built from proven winners) or **EXPLORE**
  (an experiment). About **25 %** of generation jobs explore (stable per job, so retries agree).
- An exploit job fills the topic, hook, language and occasion the owner left blank with values that
  have a **Works better** verdict. With no proven winner yet nothing is steered, and the usual
  rotation decides (so today every post is EXPLORE). Owner-chosen fields are never overridden.
- **Winning hooks as examples:** once 5+ posts are measured, the hooks of the best posts (at least
  1.25× usual) go into the writer's prompt as style examples, with the instruction never to copy
  them.

## 5. Best posting times

From the account's own posts by local hour (and time of day, weekday). An hour is **proven** only
with 30+ posts and a "Works better" verdict; until then the page shows early numbers. Times are
never changed automatically: set posting times in Studio → Autopilot.

## 6. Weekly Telegram report

`learning.weekly_report` (migration 0017) runs daily at 09:00 IST and sends on **Mondays**:

- the week's measured posts, best and weakest with their tags;
- **Do more of** / **Stop or change**: only groups with a verdict;
- **Early signals**, labelled not proven, with how many more posts each needs;
- prediction status and accuracy (vs score v0), and the best time so far.

Run it on demand with a job payload `{"trigger": "manual"}`.

## Settings

| Setting | Default | Where |
|---|---|---|
| Posts per group before a verdict | 30 | env `LEARNING_MIN_POSTS` (min 5) |
| Age a post is measured at | 72 h | `core.MATURE_AFTER` |
| Posts in the "usual" baseline | 20 | `core.BASELINE_POSTS` |
| Share of experiments | 25 % | `service.EXPLORE_SHARE` |
| Shrinkage | 8 pseudo-posts | `core.SHRINK` |
