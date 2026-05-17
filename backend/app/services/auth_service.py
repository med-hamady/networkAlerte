"""
Authentication service — password hashing + server-side sessions.

Two halves:
  1. Passwords — bcrypt via passlib. Hash on create / change, verify on login.
     The work factor is embedded in the hash, so a future cost increase only
     requires re-hashing on next login (handled out of scope of this MVP).
  2. Sessions — server-side state, opaque cookie token. The raw token is
     ONLY in the cookie + the response body of /auth/login; the database
     stores its SHA-256 (hex). A DB leak therefore cannot mint a valid cookie.

Cookies are HttpOnly + Secure + SameSite=Lax. SameSite=Lax (rather than
Strict) is the pragmatic choice — cookies are still sent on top-level
navigations from a bookmark or the address bar, which Strict would break.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import secrets

from passlib.context import CryptContext
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth_session import AuthSession
from app.models.user import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Cookie that carries the session token from the browser. Renaming this would
# log everyone out — do not change without coordinating with the frontend.
SESSION_COOKIE_NAME = "supervisor_session"

# 12 h absolute session lifetime — covers a workday. Each authenticated
# request refreshes last_seen_at but does NOT extend expiry; an active user
# is asked to re-login once per day. Adjust to taste.
SESSION_TTL = datetime.timedelta(hours=12)

# bcrypt — `bcrypt__rounds=12` ≈ 250 ms per hash on modern hardware, the
# typical sweet spot. passlib stores the cost in the hash so changing this
# value only affects new hashes; old hashes still verify correctly.
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Hash a plain password with bcrypt. Never store the plain value."""
    if not plain:
        raise ValueError("password must not be empty")
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time bcrypt verification."""
    if not plain or not hashed:
        return False
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:  # noqa: BLE001 — malformed hash, treat as mismatch
        return False


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def _hash_token(raw: str) -> str:
    """Hex SHA-256 of the raw cookie token — what we persist."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    """32 random bytes → URL-safe base64 (~43 chars). Cryptographically strong."""
    return secrets.token_urlsafe(32)


async def create_session(
    db: AsyncSession,
    user: User,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
    ttl: datetime.timedelta = SESSION_TTL,
) -> tuple[str, AuthSession]:
    """Open a new session for `user` and return (raw_token, row).

    The raw token is what the caller must send to the browser as a cookie;
    the DB only stores its SHA-256. Updates the user's `last_login_at`.
    """
    now = datetime.datetime.now(datetime.UTC)
    raw_token = _generate_token()
    session = AuthSession(
        user_id=user.id,
        token_hash=_hash_token(raw_token),
        expires_at=now + ttl,
        last_seen_at=now,
        ip_address=ip_address,
        user_agent=(user_agent or "")[:255] or None,
    )
    db.add(session)
    user.last_login_at = now
    await db.flush()
    return raw_token, session


async def get_user_from_token(db: AsyncSession, raw_token: str | None) -> User | None:
    """Resolve a raw cookie token → live User, or None.

    Returns None when the token is missing / unknown / expired / linked to a
    disabled user. Refreshes `last_seen_at` on a successful lookup so the
    UI / audit trail can show recent activity per session.
    """
    if not raw_token:
        return None
    token_hash = _hash_token(raw_token)
    res = await db.execute(
        select(AuthSession, User)
        .join(User, User.id == AuthSession.user_id)
        .where(AuthSession.token_hash == token_hash),
    )
    row = res.first()
    if row is None:
        return None
    session, user = row
    now = datetime.datetime.now(datetime.UTC)
    if session.expires_at <= now:
        return None
    if not user.enabled:
        return None
    session.last_seen_at = now
    await db.flush()
    return user


async def revoke_session(db: AsyncSession, raw_token: str | None) -> None:
    """Delete the session matching `raw_token` (logout). Silent on miss."""
    if not raw_token:
        return
    token_hash = _hash_token(raw_token)
    await db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash))


async def revoke_all_sessions_for_user(db: AsyncSession, user_id: int) -> None:
    """Kick every active session of `user_id` (e.g. after a password change)."""
    await db.execute(delete(AuthSession).where(AuthSession.user_id == user_id))


async def cleanup_expired_sessions(db: AsyncSession) -> int:
    """Delete expired rows. Returns the number deleted. Safe to call often."""
    now = datetime.datetime.now(datetime.UTC)
    res = await db.execute(
        delete(AuthSession).where(AuthSession.expires_at <= now),
    )
    return res.rowcount or 0


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

async def get_user_by_username(db: AsyncSession, username: str) -> User | None:
    """Case-insensitive lookup. Username is normalised on insert too."""
    if not username:
        return None
    res = await db.execute(
        select(User).where(User.username == username.strip().lower()),
    )
    return res.scalar_one_or_none()
