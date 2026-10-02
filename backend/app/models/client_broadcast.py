import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Cycle de vie d'un envoi. `running` = le job vide sa file ; `done` = plus rien
# en attente ; `cancelled` = arrêté par un opérateur (le reste est marqué
# `cancelled`, jamais envoyé).
BROADCAST_STATUSES = ("running", "done", "cancelled")
# Destinataire : `pending` → `sent` | `failed`, ou `cancelled` si l'envoi est
# arrêté avant son tour. « Relancer les échecs » repasse les `failed` en
# `pending`, et rien d'autre.
RECIPIENT_STATUSES = ("pending", "sent", "failed", "cancelled")


class ClientBroadcast(Base):
    """Un message WhatsApp envoyé aux abonnés d'une ou plusieurs catégories.

    ⚠️ Les destinataires sont FIGÉS à la création (`ClientBroadcastRecipient`),
    pas recalculés à chaque passage du job : « relancer les échecs » doit viser
    les mêmes personnes qu'au premier envoi, même si entre-temps un client a été
    bloqué ou débloqué. Et un envoi d'une heure ne doit pas voir sa liste bouger
    sous ses pieds.
    """

    __tablename__ = "client_broadcasts"

    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Catégories choisies, séparées par des virgules (« active,blocked »).
    audiences: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # Auteur : NULL sur un appel par clé API, qui ne porte aucune identité.
    created_by: Mapped[str | None] = mapped_column(String(150))
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))


class ClientBroadcastRecipient(Base):
    """Un numéro visé par un envoi, et ce qu'il en est advenu."""

    __tablename__ = "client_broadcast_recipients"
    __table_args__ = (
        # Un numéro ne reçoit qu'UN message par envoi, même s'il porte plusieurs
        # LR (client à plusieurs services).
        UniqueConstraint("broadcast_id", "phone", name="uq_client_broadcast_recipient_phone"),
    )

    broadcast_id: Mapped[int] = mapped_column(
        ForeignKey("client_broadcasts.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # Numéro local à 8 chiffres ; l'indicatif 222 est ajouté à l'envoi.
    phone: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nom du LR au moment de l'envoi, COPIÉ : le sync UISP supprime les stations
    # déprovisionnées, l'historique doit rester lisible après.
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
