"""
FastAPI dependencies shared across endpoints.
"""

import hmac
import logging

from fastapi import Header, HTTPException, status

from app.core.config import get_settings

logger = logging.getLogger(__name__)


async def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Reject requests missing or carrying a wrong X-API-Key header.

    Authentication is skipped entirely when Settings.api_key is empty so that
    local dev environments don't need to configure a key. Production startup
    refuses to boot when api_key is empty (see Settings._validate_production_secrets).
    """
    settings = get_settings()
    if not settings.api_key:
        return  # auth disabled (dev mode — refused at startup in production)
    # compare_digest requires str (not None) — treat absent header as empty string
    if not hmac.compare_digest(x_api_key or "", settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
