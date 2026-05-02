import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.services.snmp_service import close_snmp_engine
from app.tasks.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — startup and shutdown logic."""
    settings = get_settings()

    # Startup
    setup_logging()
    logger.info("Starting %s...", settings.app_name)

    if not settings.tls_verify_devices:
        logger.warning(
            "TLS verification disabled for device APIs (LTU/UISP Power). "
            "Set TLS_VERIFY_DEVICES=true once devices have valid certificates "
            "or fingerprint pinning is in place — currently MITM-vulnerable.",
        )

    if settings.scheduler_enabled:
        start_scheduler()

    logger.info("%s is ready", settings.app_name)

    yield

    # Shutdown
    logger.info("Shutting down %s...", settings.app_name)
    stop_scheduler()
    close_snmp_engine()


def create_app() -> FastAPI:
    """Application factory."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        description="Network supervision and automation system for UISP/Ubiquiti equipment",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router)

    return app


app = create_app()
