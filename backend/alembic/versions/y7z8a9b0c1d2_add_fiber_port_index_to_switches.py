"""add fiber_port_index to uisp_switches (fibre uplink SFP port to watch)

Three sites reach the network over FIBRE that lands on an SFP port of the site
switch (AT1 port 9, CT1/ARF1 port 25). When the fibre breaks the SFP loses light
and the port goes DOWN — a distinct failure from the RJ45 Rocket-port monitoring
(rocket_port_index). The `port_N_up` SNMP metric for that port is already
collected by snmp_poll_job; this column just designates which port is the fibre
uplink so the job can raise the dedicated fiber_link_down alert.

NULL = the switch has no fibre uplink to watch. Set 9/25/25 on the three sites
from the switch edit form. Preserved by the UISP sync (only name/ip/location/mac
are updated there).

Revision ID: y7z8a9b0c1d2
Revises: x6a7b8c9d0e1
Create Date: 2026-07-27 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "y7z8a9b0c1d2"
down_revision: str | None = "x6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "uisp_switches",
        sa.Column("fiber_port_index", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("uisp_switches", "fiber_port_index")
