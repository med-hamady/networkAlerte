"""add uisp_enrolled_at to lrs (dernier enrôlement UISP poussé par nous)

Un CPE ne parle au contrôleur UISP que s'il porte sa clé de connexion. Ceux qui
ne l'ont pas sont invisibles de l'inventaire — donc potentiellement non facturés
(liste « découverts par radio mais absents de UISP » de /access-diagnostics).
L'enrôlement se pousse désormais par SSH depuis le dashboard.

Cette colonne enregistre le dernier enrôlement RÉUSSI (contrôleur ayant adopté
l'équipement), et sépare trois états que `uisp_synced_at` seul confondait :
  NULL                             → jamais tenté
  renseignée, uisp_synced_at NULL  → adopté, en attente du sync quotidien
  renseignée et déjà ancienne      → adopté mais toujours hors roster : anomalie

Ce n'est PAS une preuve de présence dans UISP aujourd'hui : seul
`uisp_synced_at`, écrit par le sync des stations, l'atteste.

Revision ID: c9d0e1f2a3b4
Revises: z8a9b0c1d2e3
Create Date: 2026-07-28 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "z8a9b0c1d2e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column("uisp_enrolled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lrs", "uisp_enrolled_at")
