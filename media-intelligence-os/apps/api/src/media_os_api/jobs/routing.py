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


def queue_for_task(task_name: str) -> str:
    return RESEARCH_QUEUE if task_name.startswith(RESEARCH_FETCH_PREFIX) else DEFAULT_QUEUE


def celery_task_for_queue(queue_name: str) -> str:
    return RESEARCH_CELERY_TASK if queue_name == RESEARCH_QUEUE else DEFAULT_CELERY_TASK
