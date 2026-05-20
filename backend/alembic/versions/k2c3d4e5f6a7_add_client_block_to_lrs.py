"""add client-block columns to lrs

Adds the per-LR client internet block state. Cutting a client = SSH into the
LR and shutting its LAN-facing port (`lan_interface`, default eth0); SSH
survives because it transits the radio link, not the LAN port. `client_blocked`
is the intent, `client_block_enforced_at` the last time the shutdown was
actually (re)applied on the device — an enforcement job re-asserts the cut so
it survives an LR reboot. Supersedes the removed no-op `devices.is_suspended`.

Revision ID: k2c3d4e5f6a7
Revises: j1b2c3d4e5f6
Create Date: 2026-05-18 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k2c3d4e5f6a7"
down_revision: str | None = "j1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column(
            "client_blocked",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "lrs",
        sa.Column("client_blocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "lrs",
        sa.Column("client_blocked_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "lrs",
        sa.Column(
            "lan_interface",
            sa.String(length=20),
            server_default="eth0",
            nullable=False,
        ),
    )
    op.add_column(
        "lrs",
        sa.Column(
            "client_block_enforced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("lrs", "client_block_enforced_at")
    op.drop_column("lrs", "lan_interface")
    op.drop_column("lrs", "client_blocked_reason")
    op.drop_column("lrs", "client_blocked_at")
    op.drop_column("lrs", "client_blocked")
