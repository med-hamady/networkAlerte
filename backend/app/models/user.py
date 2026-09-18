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

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.profile import Profile


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

    # Le profil qui porte les droits de ce compte. UN SEUL profil par compte :
    # « untel est Agent » se lit d'un coup d'oeil, ce qu'un cumul de profils
    # rendrait impossible. Un besoin particulier se traite en créant un profil
    # de plus, pas en empilant.
    #
    # ⚠️ `ondelete="RESTRICT"` : un profil encore porté par un compte ne peut
    # pas etre supprime. Une cascade viderait les droits du compte en silence
    # (il resterait connecte, sans plus rien pouvoir faire et sans savoir
    # pourquoi) ; un SET NULL ferait la meme chose en moins visible encore.
    # Le service refuse la suppression AVANT d'en arriver la, avec le nombre de
    # comptes concernes — la contrainte n'est que le filet.
    #
    # Nullable pour une seule raison : un compte cree avant l'arrivee des
    # profils. La migration les rattache tous a l'Administrateur, et
    # `effective_permissions` traite un profil absent comme AUCUN droit —
    # jamais comme tous.
    profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=True, index=True,
    )

    # `lazy="joined"` et pas `select` : le profil est relu a CHAQUE requete
    # authentifiee (c'est lui qui autorise ou refuse), donc le charger dans la
    # meme requete que la session evite un aller-retour par appel d'API.
    profile: Mapped[Profile | None] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username!r}, enabled={self.enabled})>"
