"""add airfibers table for airFiber 60 (AF60-LR) backhaul links

Adds the `airfibers` joined-inheritance table for airFiber 60 LR point-to-point
60 GHz backhaul links. These are infrastructure devices added manually (like
uisp_switches / uisp_powers). They speak the same UDAPI as LTU devices, so the
`ssh_*` columns hold the local HTTP API credentials (same convention as rockets).

Revision ID: v3b4c5d6e7f8
Revises: u2a3b4c5d6e7
Create Date: 2026-06-05 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v3b4c5d6e7f8"
down_revision: str | None = "u2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "airfibers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ssh_username", sa.String(length=100), nullable=True),
        sa.Column("ssh_password", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("ssh_host_fingerprint", sa.String(length=255), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("airfibers")
