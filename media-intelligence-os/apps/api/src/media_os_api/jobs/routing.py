"""Queue and Celery-task routing for durable jobs.

Research fetches are paced (arXiv ≥ 3.1 s, GitHub ≥ 6.5 s per request), so a single run can outlive
the default 90/120 s Celery limits and 120 s lease. They go to the `research` queue and run under
a dedicated Celery task with longer limits; everything else keeps the Phase 4 defaults.
"""

from __future__ import annotations

from dataclasses import dataclass

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
# Views analytics sync calls Postiz once per published post; same worker queue and long lease.
ANALYTICS_PREFIX = "media_os.analytics."


def queue_for_task(task_name: str) -> str:
    if task_name.startswith((RESEARCH_FETCH_PREFIX, PUBLISH_PREFIX, ANALYTICS_PREFIX)):
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


@dataclass(frozen=True)
class RetryPolicy:
    maximum_attempts: int
    base_backoff_seconds: float


DEFAULT_RETRY_POLICY = RetryPolicy(maximum_attempts=3, base_backoff_seconds=2.0)
# Jobs that only read from Postiz. Postiz starts slower than the Media OS stack (it was still
# booting when a 6-hourly sync fired and the 2 s / 4 s retries gave up in 12 s), so they back off
# 30 s, 60 s, 120 s, 240 s, 300 s: about 12 minutes of tolerance before FAILED_FINAL.
POSTIZ_READ_RETRY_POLICY = RetryPolicy(maximum_attempts=6, base_backoff_seconds=30.0)
POSTIZ_READ_TASKS = frozenset({"media_os.publish.sync_status"})


def retry_policy_for_task(task_name: str) -> RetryPolicy:
    if task_name.startswith(ANALYTICS_PREFIX) or task_name in POSTIZ_READ_TASKS:
        return POSTIZ_READ_RETRY_POLICY
    return DEFAULT_RETRY_POLICY
