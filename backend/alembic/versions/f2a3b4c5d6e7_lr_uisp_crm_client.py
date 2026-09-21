"""lrs : rattachement au client CRM dans UISP (site + id/nom client)

Trois colonnes nullables, sans valeur par défaut : aucune réécriture de table,
aucune donnée à rattraper. Le sync UISP des stations les remplit, et il tourne
une fois au démarrage du scheduler : redémarrer `scheduler-heavy` au
déploiement, sinon les fiches afficheraient « non rattaché » jusqu'au lendemain.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lrs", sa.Column("uisp_site_name", sa.String(200), nullable=True))
    op.add_column("lrs", sa.Column("uisp_crm_client_id", sa.String(32), nullable=True))
    op.add_column("lrs", sa.Column("uisp_crm_client_name", sa.String(200), nullable=True))


def downgrade() -> None:
    op.drop_column("lrs", "uisp_crm_client_name")
    op.drop_column("lrs", "uisp_crm_client_id")
    op.drop_column("lrs", "uisp_site_name")
