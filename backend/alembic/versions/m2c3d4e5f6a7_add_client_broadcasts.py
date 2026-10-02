"""add client_broadcasts + client_broadcast_recipients (message WhatsApp aux clients)

Page /broadcast : un opérateur choisit une ou plusieurs catégories d'abonnés
(actifs, bloqués, hors supervision), écrit un message, et le job
`client_broadcast_job` l'envoie sur WhatsApp à chacun, un par un.

Deux tables parce que l'envoi dure (~1 h pour 800 clients, un message toutes
les 4 s) : il doit survivre à un redémarrage, se suivre depuis la page, et
permettre de relancer les seuls échecs. La liste des destinataires est FIGÉE à
la création — relancer vise les mêmes personnes qu'au premier envoi.

Aucune donnée à reprendre.

Revision ID: m2c3d4e5f6a7
Revises: k7f8a9b0c1d2
Create Date: 2026-10-02
"""

import sqlalchemy as sa

from alembic import op

revision = "m2c3d4e5f6a7"
down_revision = "k7f8a9b0c1d2"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "client_broadcasts",
        *_timestamps(),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("audiences", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.String(length=150), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "client_broadcast_recipients",
        *_timestamps(),
        sa.Column("broadcast_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["broadcast_id"], ["client_broadcasts.id"], ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broadcast_id", "phone",
                            name="uq_client_broadcast_recipient_phone"),
    )
    op.create_index(
        "ix_client_broadcast_recipients_broadcast_id",
        "client_broadcast_recipients", ["broadcast_id"],
    )
    # La lecture chaude du job : « le prochain en attente ». Index PARTIEL, qui
    # ne porte que la file et pas tout l'historique des envois passés.
    op.create_index(
        "ix_client_broadcast_recipients_pending",
        "client_broadcast_recipients", ["broadcast_id", "id"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_client_broadcast_recipients_pending",
                  table_name="client_broadcast_recipients")
    op.drop_index("ix_client_broadcast_recipients_broadcast_id",
                  table_name="client_broadcast_recipients")
    op.drop_table("client_broadcast_recipients")
    op.drop_table("client_broadcasts")
