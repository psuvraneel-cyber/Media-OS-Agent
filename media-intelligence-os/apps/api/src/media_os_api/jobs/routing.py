"""Queue and Celery-task routing for durable jobs.

Research fetches are paced (arXiv ≥ 3.1 s, GitHub ≥ 6.5 s per request), so a single run can outlive
the default 90/120 s Celery limits and 120 s lease. They go to the `research` queue and run under
a dedicated Celery task with longer limits; everything else keeps the Phase 4 defaults.
"""

from __future__ import annotations

RESEARCH_FETCH_PREFIX = "media_os.research.fetch."

DEFAULT_QUEUE = "control"
RESEARCH_QUEUE = "research"

DEFAULT_CELERY_TASK = "media_os.execute_job_run"
RESEARCH_CELERY_TASK = "media_os.execute_research_job_run"

RESEARCH_LEASE_SECONDS = 900
RESEARCH_SOFT_TIME_LIMIT = 780
RESEARCH_TIME_LIMIT = 840

# N2: slide rendering runs only in the creative container (headless Chromium, no AI keys).
CREATIVE_PREFIX = "media_os.creative."
CREATIVE_QUEUE = "creative"
CREATIVE_CELERY_TASK = "media_os.execute_creative_job_run"
CREATIVE_LEASE_SECONDS = 600
CREATIVE_SOFT_TIME_LIMIT = 480
CREATIVE_TIME_LIMIT = 540


# N3 publishing waits for GitHub Pages builds (1-2 min, up to 7), so it shares the long-lease
# research queue/task (900 s lease) on the worker, which holds the publishing credentials.
PUBLISH_PREFIX = "media_os.publish."


def queue_for_task(task_name: str) -> str:
    if task_name.startswith((RESEARCH_FETCH_PREFIX, PUBLISH_PREFIX)):
        return RESEARCH_QUEUE
    if task_name.startswith(CREATIVE_PREFIX):
        return CREATIVE_QUEUE
    return DEFAULT_QUEUE


def celery_task_for_queue(queue_name: str) -> str:
    if queue_name == RESEARCH_QUEUE:
        return RESEARCH_CELERY_TASK
    if queue_name == CREATIVE_QUEUE:
        return CREATIVE_CELERY_TASK
    return DEFAULT_CELERY_TASK
