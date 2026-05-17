"""
Authentication endpoints — login / logout / current user / change password.

Public:
  - POST /auth/login           login with username + password

Authenticated (require_user):
  - POST /auth/logout          revoke the current session
  - GET  /auth/me              info on the logged-in user
  - POST /auth/change-password change own password (revokes all sessions)

Login is the only public auth endpoint — everything else requires a valid
session cookie. The session token is set by login as an HttpOnly + Secure +
SameSite=Lax cookie; the response body never echoes the raw token.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    UserRead,
)
from app.services import auth_service
from app.services.auth_service import SESSION_COOKIE_NAME, SESSION_TTL

logger = logging.getLogger(__name__)

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    """Pick the most truthful client IP available behind the reverse proxy."""
    return (
        request.headers.get("x-real-ip")
        or (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or None)
        or (request.client.host if request.client else None)
    )


def _set_session_cookie(response: Response, raw_token: str) -> None:
    """Apply the standard secure cookie attributes used everywhere."""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=True,        # the dashboard is served over HTTPS in prod
        samesite="lax",     # blocks CSRF-on-navigate while keeping bookmarks usable
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Validate credentials and open a server-side session.

    Generic 401 on every failure — never disclose whether the username
    existed or the password was wrong (prevents account enumeration).
    """
    user = await auth_service.get_user_by_username(db, payload.username)
    if user is None or not user.enabled or not auth_service.verify_password(
        payload.password, user.password_hash,
    ):
        logger.warning(
            "Login failed for username=%r from ip=%s",
            payload.username, _client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides",
        )

    raw_token, _session = await auth_service.create_session(
        db, user,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    _set_session_cookie(response, raw_token)
    logger.info("Login OK — user=%s ip=%s", user.username, _client_ip(request))
    return LoginResponse(user=UserRead.model_validate(user))


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_user),
) -> None:
    """Revoke the current session and clear the cookie. 204 on success."""
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    await auth_service.revoke_session(db, raw_token)
    await db.commit()
    _clear_session_cookie(response)


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(require_user)) -> UserRead:
    """Return the logged-in user — used by the frontend to gate the UI."""
    return UserRead.model_validate(user)


@router.post("/change-password", status_code=204)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_user),
) -> None:
    """Change own password. Revokes every active session (including this one).

    The client is then expected to redirect to /login. We intentionally do
    NOT mint a fresh session here — re-authenticating proves the new
    password works.
    """
    if not auth_service.verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mot de passe actuel incorrect",
        )
    user.password_hash = auth_service.hash_password(payload.new_password)
    await auth_service.revoke_all_sessions_for_user(db, user.id)
    await db.commit()
    _clear_session_cookie(response)
    logger.info("Password changed for user=%s (all sessions revoked)", user.username)
