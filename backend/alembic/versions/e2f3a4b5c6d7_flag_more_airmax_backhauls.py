"""flag more airMAX P2P backhauls (10.135.150.3, 10.135.90.3)

Suite de d1e2f3a4b5c6 : deux liens P2P airMAX supplémentaires identifiés par
l'opérateur (2026-06-18). Migration distincte car d1e2f3a4b5c6 est déjà
appliquée en prod (on ne peut pas la rejouer). Même backfill scopé aux Rockets
airMAX, matché par IP courante ; no-op sur une base où l'IP n'existe pas.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-18 13:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE rockets SET is_backhaul = true
        WHERE radio_tech = 'airmax'
          AND id IN (
              SELECT id FROM devices
              WHERE ip_address IN ('10.135.150.3', '10.135.90.3')
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE rockets SET is_backhaul = false
        WHERE radio_tech = 'airmax'
          AND id IN (
              SELECT id FROM devices
              WHERE ip_address IN ('10.135.150.3', '10.135.90.3')
          )
        """
    )
