"""
FastAPI dependencies shared across endpoints.

Two flavours of authentication coexist:

  - **X-API-Key header** (`verify_api_key`) — for direct calls to the backend
    bypassing the dashboard (admin scripts, integrations). The key is a long
    shared secret read from settings.
  - **Session cookie** (`require_user`) — for the browser. Created by
    /auth/login, persisted server-side in `auth_sessions`. Carries a user
    identity (useful for audit), can be revoked, expires automatically.

Most routes accept EITHER (`require_user_or_api_key`), so the same code
path serves both the dashboard and admin scripts without duplication. The
auth router itself uses `require_user` directly because the API key is
not enough to identify whose password to change.
"""

import hmac
import logging

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service
from app.services.auth_service import SESSION_COOKIE_NAME

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


def _api_key_matches(x_api_key: str | None) -> bool:
    """True if the supplied header equals the configured API key (timing-safe)."""
    settings = get_settings()
    if not settings.api_key:
        return False  # auth-disabled mode falls through to require_user
    return hmac.compare_digest(x_api_key or "", settings.api_key)


def _fai_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated payment-system key (timing-safe)."""
    settings = get_settings()
    if not settings.fai_api_key:
        return False  # no dedicated key configured — /fai falls back to normal auth
    return hmac.compare_digest(x_api_key or "", settings.fai_api_key)


async def require_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Return the user owning the current session cookie, or raise 401."""
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    user = await auth_service.get_user_from_token(db, raw)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    return user


async def require_user_or_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Accept either a valid X-API-Key header or a valid session cookie.

    Returns the User on cookie auth, None on API key auth (no user identity).
    Raises 401 if neither path is valid.
    """
    if _api_key_matches(x_api_key):
        return None
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    user = await auth_service.get_user_from_token(db, raw)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    return user


async def require_fai_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for the /fai routes: the dedicated payment key, or the normal auth.

    The payment system holds `fai_api_key`, which unlocks nothing but these three
    routes — so handing it to a third party (and rotating it) never touches the
    dashboard or the admin scripts. Operators keep reaching /fai through their
    session cookie or the master `api_key`, which is what the dashboard's own
    block/unblock buttons use.
    """
    if _fai_api_key_matches(x_api_key):
        return None
    return await require_user_or_api_key(request, x_api_key, db)
