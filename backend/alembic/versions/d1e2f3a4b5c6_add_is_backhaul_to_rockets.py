"""add is_backhaul to rockets (P2P inter-site backhaul flag)

Some airMAX radios are NOT base stations serving subscribers — they form a
point-to-point link between two sites, exactly like an AF60. They still speak
airOS (so they stay rockets, polled by airos_api_poll_job), but they must be:
excluded from the client-capacity page + the rocket_client_overload rule,
supervised on link capacity (p2p_link_substandard) and surfaced in the
inter-site P2P section like an AF60.

The flag is operator-set (from a provided list) and preserved by the UISP sync
(which only updates name/ip/location/mac). Default false keeps every existing
Rocket an AP.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-06-18 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rockets",
        sa.Column(
            "is_backhaul",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )

    # Backfill the known P2P inter-site airMAX links (operator-provided list,
    # 2026-06-18). Scoped to airMAX Rockets matched by their current IP. On a
    # fresh/empty DB (CI) this matches nothing — harmless. New backhauls are
    # toggled from the UI afterwards (and the UISP sync preserves the flag).
    op.execute(
        """
        UPDATE rockets SET is_backhaul = true
        WHERE radio_tech = 'airmax'
          AND id IN (
              SELECT id FROM devices
              WHERE ip_address IN ('10.135.1.223', '10.135.15.4', '10.135.160.5')
          )
        """
    )


def downgrade() -> None:
    op.drop_column("rockets", "is_backhaul")
