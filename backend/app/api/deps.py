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


def _lr_verify_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated /fai/verify key (timing-safe)."""
    settings = get_settings()
    if not settings.lr_verify_api_key:
        return False  # no dedicated key — /fai/verify falls back to the /fai auth
    return hmac.compare_digest(x_api_key or "", settings.lr_verify_api_key)


def _uisp_assign_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated /uisp/assign key (timing-safe)."""
    settings = get_settings()
    if not settings.uisp_assign_api_key:
        return False  # no dedicated key — /uisp/assign falls back to normal auth
    return hmac.compare_digest(x_api_key or "", settings.uisp_assign_api_key)


def _content_block_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated /content-filter key (timing-safe)."""
    settings = get_settings()
    if not settings.content_block_api_key:
        return False  # no dedicated key — /content-filter falls back to normal auth
    return hmac.compare_digest(x_api_key or "", settings.content_block_api_key)


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


async def require_verify_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for GET /fai/verify: its own dedicated key, or the /fai auth.

    The verification consumer (a third party polling LR readiness, distinct from
    the payment system) holds `lr_verify_api_key`, which unlocks ONLY this route.
    Falling back to `require_fai_client` keeps the payment key, master api_key and
    operator sessions working too — so the dedicated key ADDS a scoped path, it
    never removes the existing ones.
    """
    if _lr_verify_api_key_matches(x_api_key):
        return None
    return await require_fai_client(request, x_api_key, db)


async def require_uisp_assign_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for POST /uisp/assign: its own dedicated key, or the normal auth.

    The payment system adopts newly installed CPEs by MAC; it holds
    `uisp_assign_api_key`, which unlocks ONLY this route. It deliberately does
    NOT fall back to `require_fai_client`: the block/unblock key answers a
    different question (couper un abonné) and must not become a way to write to
    the UISP controller. Falling back to `require_user_or_api_key` keeps the
    master key and operator sessions working — the dedicated key ADDS a scoped
    path, it never removes an existing one.

    ⚠️ Why this key exists at all: `/uisp/assign` is served on the .229 VIP,
    which fronts the WHOLE API (unlike the .233 listener, restricted to /fai).
    Letting a third party consume this route without a scoped key means handing
    over `api_key` — and with it `DELETE /devices/{id}` and `/uisp/sync`.
    """
    if _uisp_assign_api_key_matches(x_api_key):
        return None
    return await require_user_or_api_key(request, x_api_key, db)


async def require_content_block_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for the /content-filter routes: their own key, or the normal auth.

    The third party driving per-platform filtering holds `content_block_api_key`,
    which unlocks ONLY these routes. It deliberately does NOT fall back to
    `require_fai_client`: filtering TikTok on a subscriber and cutting his line
    entirely are two different powers, and the two callers are two different
    systems — sharing a key would make each able to do the other's job. Falling
    back to `require_user_or_api_key` keeps the master key and operator sessions
    working: the dedicated key ADDS a scoped path, it never removes one.
    """
    if _content_block_api_key_matches(x_api_key):
        return None
    return await require_user_or_api_key(request, x_api_key, db)
