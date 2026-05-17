"""
Active login sessions — one row per browser session opened via /auth/login.

The session is identified by an opaque token (32 random bytes, base64-url
encoded) stored **hashed** here — the raw value is sent to the browser as
the `session` cookie and is never persisted in clear text. A leak of the
table cannot be used to forge a session.

Lifecycle:
  - login        → row inserted, cookie set on the response
  - each request → looked up by token_hash, last_seen_at refreshed
  - expires_at   → past = invalid (the request is treated as anonymous)
  - logout       → row deleted, cookie cleared on the response
  - user disabled or deleted → ON DELETE CASCADE wipes the sessions
"""

import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AuthSession(Base):
    """A live browser login session."""

    __tablename__ = "auth_sessions"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    # SHA-256 of the raw cookie token (hex). Indexed because every authenticated
    # request looks the row up by this column.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # Forensic context — the IP and User-Agent at session creation time. Kept
    # narrow on purpose: not used for security decisions (a user's IP can
    # change legitimately), only for the audit trail and the operator UI.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped["User"] = relationship()  # noqa: F821

    __table_args__ = (
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuthSession(id={self.id}, user_id={self.user_id}, "
            f"expires_at={self.expires_at.isoformat() if self.expires_at else None})>"
        )
