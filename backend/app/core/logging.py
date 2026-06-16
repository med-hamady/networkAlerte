import logging
import sys

from app.core.config import get_settings


def setup_logging() -> None:
    """Configure application logging."""
    settings = get_settings()

    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Reduce noise from third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.debug else logging.WARNING
    )
    # paramiko's transport thread logs full stacktraces at ERROR for every SSH
    # handshake that a device aborts ("Error reading SSH protocol banner",
    # Bad file descriptor, connection reset…). These are expected terrain
    # failures (LR with SSH disabled / not reachable on 22 / rate-limited) that
    # the lr_internet_probe job already handles and summarises itself, so we
    # silence paramiko's own noise to keep the scheduler log readable.
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)

    logger = logging.getLogger(__name__)
    logger.info("Logging configured — level=%s", settings.log_level)
