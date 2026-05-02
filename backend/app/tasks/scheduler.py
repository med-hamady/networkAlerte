import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.tasks.jobs import register_jobs

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    """Start the APScheduler and register all jobs."""
    register_jobs(scheduler)
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler, waiting for running jobs to finish."""
    if scheduler.running:
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped")
