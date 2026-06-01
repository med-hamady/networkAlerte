"""Standalone scheduler runner — production-only process.

In production the API runs with multiple uvicorn workers. If each worker
booted its own APScheduler instance, every job would fire N times (duplicate
SSH, duplicate alerts). This runner isolates the scheduler in its own process:

  - backend container : SCHEDULER_ENABLED=false, uvicorn --workers N (API only)
  - scheduler container : runs this module (jobs only, no HTTP server)

In dev the single backend container keeps running the scheduler in-process
(SCHEDULER_ENABLED=true by default) — no change.
"""
import asyncio
import logging
import signal

from app.core.logging import setup_logging
from app.services.snmp_service import close_snmp_engine
from app.tasks.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


async def _run() -> None:
    setup_logging()
    logger.info("Standalone scheduler runner starting...")
    start_scheduler()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # add_signal_handler unsupported on Windows; this runner only
            # ships in the Linux production image so the fallback is just
            # belt-and-braces for local sanity testing.
            pass

    await stop_event.wait()
    logger.info("Scheduler runner received shutdown signal")
    stop_scheduler()
    close_snmp_engine()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
