# N3 — Publishing approved carousels to Instagram + Facebook

**Status:** built 2026-11-15. Every post needs the owner's **approval** and an explicit
**Schedule** click; nothing is published automatically.

## Flow

1. Dashboard → Approvals → **Approved — ready to post**: pick a time and Instagram/Facebook,
   press **Schedule**. The API checks: approved + QA passed, 20 min – 30 days ahead, daily caps
   (`publishing_preferences.daily_caps`, default 2 per platform per local day).
2. Worker job `media_os.publish.schedule_carousel` (long-lease queue):
   - mirrors Postiz's connected channels into `channel_accounts`;
   - converts slides PNG → JPEG (Instagram accepts JPEG only);
   - commits all slides in **one commit** to the owner's public repo
     `psuvraneel-cyber/nimontron-media` (GitHub Pages) and waits until every URL serves;
   - creates one Postiz *schedule* post per channel (Postiz public API) and records
     `publish_schedules` rows; the package moves QA_PASSED → POSTIZ_DRAFT → USER_APPROVED →
     SCHEDULED. Re-runs never create duplicate posts.
3. Postiz publishes at that time through the owner's Meta app (development mode).
4. `media_os.publish.sync_status` (every 15 min once enabled) reads post states back from Postiz:
   PUBLISHED (with the post link), FAILED, or CANCELLED (deleted in Postiz).

To cancel a scheduled post, delete it in Postiz (http://localhost:4007); the next sync marks it
CANCELLED.

## Configuration (worker only; never in API/web/creative)

| Variable | Where it comes from |
|---|---|
| `POSTIZ_API_KEY` | Postiz → Settings → Public API (same key as `postiz-runtime/.api.env`) |
| `NIMONTRON_MEDIA_GITHUB_TOKEN` | Fine-grained PAT, only `nimontron-media`, Contents read/write |
| `NIMONTRON_MEDIA_GITHUB_REPO` | `psuvraneel-cyber/nimontron-media` |
| `NIMONTRON_MEDIA_BASE_URL` | `https://psuvraneel-cyber.github.io/nimontron-media` |

Postiz needs `FACEBOOK_APP_ID` / `FACEBOOK_APP_SECRET` in `postiz-runtime/.env` (owner's Meta app
"Nimontron Publisher", development mode).

## Schema

Migration `0007_facebook_platform` allows `FACEBOOK` on `channel_accounts` and
`publish_schedules` (replacing two double-prefixed constraint names) and seeds the disabled
schedule `publish.sync_status`.
