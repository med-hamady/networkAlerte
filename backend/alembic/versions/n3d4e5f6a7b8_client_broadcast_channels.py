"""message aux clients : canal d'envoi (WhatsApp et/ou SMS)

Chaque envoi porte ses canaux (`client_broadcasts.channels`) et chaque
destinataire son canal (`client_broadcast_recipients.channel`) : un numéro a
une ligne par canal choisi. WhatsApp et SMS avancent ainsi à leur propre
rythme (un job par canal), et un échec SMS se relance sans renvoyer le
WhatsApp déjà reçu.

⚠️ Le canal SMS lui-même a été RETIRÉ de `main` (pas encore validé), mais
cette migration y est CONSERVÉE : elle a pu être appliquée en prod, et une
révision absente du code empêcherait le backend de démarrer. Sans le SMS elle
est inoffensive — toutes les lignes prennent le canal par défaut `whatsapp`.

L'unicité passe de (envoi, numéro) à (envoi, canal, numéro). Les lignes déjà
présentes étaient toutes WhatsApp : c'est la valeur par défaut posée ici.

Revision ID: n3d4e5f6a7b8
Revises: m2c3d4e5f6a7
Create Date: 2026-10-02
"""

import sqlalchemy as sa

from alembic import op

revision = "n3d4e5f6a7b8"
down_revision = "m2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "client_broadcasts",
        sa.Column("channels", sa.String(length=30), nullable=False, server_default="whatsapp"),
    )
    op.add_column(
        "client_broadcast_recipients",
        sa.Column("channel", sa.String(length=10), nullable=False, server_default="whatsapp"),
    )
    op.drop_constraint("uq_client_broadcast_recipient_phone",
                       "client_broadcast_recipients", type_="unique")
    op.create_unique_constraint(
        "uq_client_broadcast_recipient_channel_phone",
        "client_broadcast_recipients", ["broadcast_id", "channel", "phone"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_client_broadcast_recipient_channel_phone",
                       "client_broadcast_recipients", type_="unique")
    op.execute("DELETE FROM client_broadcast_recipients WHERE channel <> 'whatsapp'")
    op.create_unique_constraint(
        "uq_client_broadcast_recipient_phone",
        "client_broadcast_recipients", ["broadcast_id", "phone"],
    )
    op.drop_column("client_broadcast_recipients", "channel")
    op.drop_column("client_broadcasts", "channels")
