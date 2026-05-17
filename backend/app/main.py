import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.db.session import async_session_factory
from app.models.audit_log import AuditLog
from app.services.snmp_service import close_snmp_engine
from app.tasks.scheduler import start_scheduler, stop_scheduler

# HTTP verbs that mutate state — these are the only ones recorded by the audit
# middleware. Reads are excluded both for volume and signal: dashboards poll
# constantly and a flood of GETs would drown the real forensic trail.
_AUDITED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

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
        # Disable trailing-slash redirects: every collection route is declared
        # without a trailing slash (e.g. /devices, /devices/{id}). A mismatched
        # URL now returns 404 directly instead of a silent 307 — fixes the
        # classic FastAPI gotcha where curl POST/PUT bodies are dropped after
        # the redirect.
        redirect_slashes=False,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def audit_log_middleware(request: Request, call_next):  # noqa: ANN202
        """Persist a row in `audit_log` for every state-changing API call.

        Runs AFTER the response so we capture the final status_code. Strictly
        best-effort: an audit failure must never break the response (a broken
        audit path can't be allowed to take the API down). Filtering on
        /api/v1/ + write methods keeps the table from being flooded by reads.
        """
        response = await call_next(request)
        s = get_settings()
        if (
            s.audit_log_enabled
            and request.method in _AUDITED_METHODS
            and request.url.path.startswith("/api/v1/")
        ):
            try:
                # Behind nginx: prefer X-Real-IP, then X-Forwarded-For (first
                # hop), then the immediate peer. IPv6 fits in 45 chars.
                client_ip = (
                    request.headers.get("x-real-ip")
                    or (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or None)
                    or (request.client.host if request.client else None)
                )
                user_agent = (request.headers.get("user-agent") or "")[:255] or None
                async with async_session_factory() as session:
                    session.add(AuditLog(
                        method=request.method,
                        path=request.url.path[:500],
                        client_ip=client_ip,
                        status_code=response.status_code,
                        user_agent=user_agent,
                    ))
                    await session.commit()
            except Exception:
                logger.exception("audit_log insert failed (non-fatal)")
        return response

    register_exception_handlers(app)
    app.include_router(api_router)

    return app


app = create_app()
