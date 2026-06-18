import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.tasks.jobs import register_jobs

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    """Start the APScheduler and register all jobs.

    The effective job set depends on ``settings.scheduler_group`` (all/fast/heavy),
    applied inside ``register_jobs`` — see that function for the split rationale.
    """
    from app.core.config import get_settings

    register_jobs(scheduler)
    scheduler.start()
    logger.info(
        "Scheduler started (group=%s) with %d jobs",
        get_settings().scheduler_group, len(scheduler.get_jobs()),
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler, waiting for running jobs to finish."""
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped")
