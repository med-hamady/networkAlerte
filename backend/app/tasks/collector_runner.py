"""Standalone NetFlow collector runner — production-only process.

A UDP NetFlow listener is permanent, not interval-based, so it doesn't belong in
APScheduler. Like the scheduler, it runs in its own container:

  - netflow-collector container : RUN_MODE=collector, runs this module
    (UDP listener + periodic flush to traffic_dest_stats, no HTTP server).

Migrations are owned by the API container; this one waits for it to be healthy
via depends_on. Mirrors app/tasks/runner.py.
"""
import asyncio
import contextlib
import logging
import signal

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.services.netflow_service import run_collector

logger = logging.getLogger(__name__)


async def _run() -> None:
    setup_logging()
    logger.info("NetFlow collector runner starting...")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler is unsupported on Windows; this runner only ships in
        # the Linux production image so this is just for local sanity.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    if not get_settings().netflow_collector_enabled:
        # The container exists solely for the collector; when the feature is off
        # we idle (rather than exit and restart-loop) so flipping the env var and
        # restarting the container is all it takes to enable it.
        logger.warning(
            "NETFLOW_COLLECTOR_ENABLED is false — collector idle (set it to true "
            "and restart this container to start listening).",
        )
        await stop_event.wait()
    else:
        await run_collector(stop_event)
    logger.info("NetFlow collector runner stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
