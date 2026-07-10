"""APScheduler wiring for scheduled pipeline runs (HNTR-4).

This module turns the validated ``[scheduler]`` configuration into an
AsyncIOScheduler that calls the same through-scoring pipeline the manual
dashboard trigger uses (HNTR-51), with a ``scheduled`` trigger type so the
audit trail distinguishes the two. Overlap protection stays inside
``run_job_pipeline`` (the lock file); the scheduler itself never locks,
otherwise every scheduled run would self-skip.

Decided simplifications: missed runs wait for the next slot (``coalesce``
plus a short misfire grace, no catch-up on restart), and scheduler health
on the dashboard is just the next scheduled run time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session

from app.db.database import engine
from app.models.config import SchedulerConfig
from app.services.pipeline import run_job_pipeline


logger = logging.getLogger(__name__)

PIPELINE_JOB_ID_PREFIX = "job-pipeline"

# A misfire older than this waits for the next scheduled slot instead of
# firing late: a job scrape is not urgent, so no catch-up on restart.
MISFIRE_GRACE_SECONDS = 60


def build_pipeline_scheduler(
    scheduler_config: SchedulerConfig,
    run_pipeline: Callable[[], Awaitable[None]] | None = None,
    *,
    scheduler: AsyncIOScheduler | None = None,
) -> AsyncIOScheduler | None:
    """Register one cron job per configured run time; ``None`` when disabled.

    ``run_pipeline`` defaults to the real scheduled job body; tests inject
    a recording fake. ``scheduler`` is injectable so tests can inspect
    registered jobs on a never-started instance instead of waiting for
    wall time.
    """
    if not scheduler_config.enabled:
        return None

    job_body = run_pipeline if run_pipeline is not None else run_scheduled_pipeline
    resolved = scheduler or AsyncIOScheduler()
    timezone = ZoneInfo(scheduler_config.timezone)
    for run_at in scheduler_config.runs_at:
        hour, minute = run_at.split(":")
        resolved.add_job(
            job_body,
            trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone=timezone),
            id=f"{PIPELINE_JOB_ID_PREFIX}-{hour}{minute}",
            coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            max_instances=1,
            replace_existing=True,
        )
    resolved.add_listener(_log_missed_run, EVENT_JOB_MISSED)
    return resolved


def next_scheduled_run_time(
    scheduler: AsyncIOScheduler | None,
    now: datetime | None = None,
) -> datetime | None:
    """Return the earliest upcoming pipeline fire time, or ``None``.

    Computed from each job's trigger so it works on a not-yet-started
    scheduler and under a test-controlled clock. The scheduler only ever
    carries pipeline jobs (this module is its sole builder).
    """
    if scheduler is None:
        return None
    fire_times = []
    for job in scheduler.get_jobs():
        reference = now if now is not None else datetime.now(job.trigger.timezone)
        fire_time = job.trigger.get_next_fire_time(None, reference)
        if fire_time is not None:
            fire_times.append(fire_time)
    return min(fire_times, default=None)


def next_scheduled_run_time_for(state) -> datetime | None:
    """Next fire time from ``app.state``, shared by every route that
    renders the recent-runs panel so the lookups cannot drift apart."""
    return next_scheduled_run_time(getattr(state, "scheduler", None))


async def run_scheduled_pipeline() -> None:
    """Default scheduled job body: one pipeline run in its own session.

    The pipeline's stage work is largely synchronous (SQLite commits,
    keyword scoring), so it runs in a worker thread — the same reasoning
    that made the manual trigger routes sync — keeping the event loop
    responsive for dashboard and health requests during a run.
    """

    def run_blocking() -> None:
        with Session(engine) as session:
            asyncio.run(run_job_pipeline(session, trigger_type="scheduled"))

    await asyncio.to_thread(run_blocking)


def _log_missed_run(event) -> None:
    """Log fires the scheduler saw but could not start in time.

    This only catches a blocked-but-running app (event loop stalled past
    the misfire grace). Slots that pass while the app is down leave no
    trace at all: jobs live in memory, and no-catch-up-on-restart is the
    decided behavior.
    """
    logger.warning(
        "scheduled pipeline run %s missed its slot; waiting for the next one",
        event.job_id,
    )
