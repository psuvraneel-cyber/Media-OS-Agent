# Research Worker (`workers/research`)

**Status:** Implemented in Phase 5 (2026-11-13).

The research fetchers, runner and job handlers live in the API package so the worker and API share
one codebase: `apps/api/src/media_os_api/research/` (`fetchers/`, `http/`, `runner.py`, `jobs.py`,
`registry.py`, `admin.py`). Jobs run on the `media-os-worker` container's `research` queue under the
`media_os.execute_research_job_run` Celery task (concurrency 1).

Every fetch is gated by the source registry (`OFF` / `EXPERIMENTAL` / `ENABLED` / `BLOCKED`), the
owner's live-fetch approval and the `RESEARCH_LIVE_FETCH_ENABLED` kill switch. Reddit is `BLOCKED`.
See `docs/PHASE_5_IMPLEMENTATION.md`.
