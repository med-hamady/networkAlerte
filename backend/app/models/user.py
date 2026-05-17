"""
Application user — login + session-based auth.

One row per human operator of the supervisor. Created via the bootstrap
script `scripts/create_admin.py` for the initial admin, and later by an
admin UI (out of scope for the first iteration). Passwords are stored as
bcrypt hashes — the plain value never lives in the database.

Companion model: `AuthSession` (app/models/auth_session.py) holds the
server-side sessions opened on successful login.
"""

import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """Operator account for the dashboard."""

    __tablename__ = "users"

    # Login identifier — case-insensitive in practice (the service normalises
    # to lowercase before lookup). Kept short to avoid pathological inputs.
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    # Bcrypt hash (the work factor is embedded in the hash itself, no need to
    # store it separately). The plain password never lives in the DB.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # An account can be disabled without being deleted (keeps its audit trail
    # via the FK on auth_sessions, even if all sessions are revoked).
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username!r}, enabled={self.enabled})>"
