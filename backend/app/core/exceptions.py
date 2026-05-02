import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application exception."""

    def __init__(self, message: str, status_code: int = 500, detail: str | None = None):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class DeviceNotFoundError(AppError):
    """Raised when a device is not found."""

    def __init__(self, device_id: int):
        super().__init__(
            message=f"Device with id {device_id} not found",
            status_code=404,
        )


class DeviceUnreachableError(AppError):
    """Raised when a device cannot be reached."""

    def __init__(self, ip_address: str):
        super().__init__(
            message=f"Device at {ip_address} is unreachable",
            status_code=503,
        )


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on the FastAPI app."""

    @app.exception_handler(AppError)
    async def app_exception_handler(_request: Request, exc: AppError) -> JSONResponse:
        logger.warning("AppError: %s", exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.message,
                "detail": exc.detail,
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        # Log with full traceback so bugs are always visible in logs.
        # We intentionally do NOT re-raise: Starlette would return a plain 500 text/html
        # response which breaks JSON clients. The 500 JSON below is the right contract.
        logger.exception(
            "Unhandled exception on %s %s — %s: %s",
            _request.method,
            _request.url.path,
            type(exc).__name__,
            exc,
        )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "type": type(exc).__name__},
        )
