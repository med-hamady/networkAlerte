"""add client_modems table for customer-side modems behind an LR

Adds the `client_modems` joined-inheritance table for TP-Link / Huawei / ZTE
modems sitting in the client LAN behind an LR. They are not directly reachable
from the supervisor — interactive access is performed through an SSH jump via
the parent LR (services/jump_session.open_jump_channel).

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-05-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g8b9c0d1e2f3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_modems",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lr_id", sa.Integer(), nullable=True),
        sa.Column(
            "management_protocol",
            sa.String(length=10),
            nullable=False,
            server_default="ssh",
        ),
        sa.Column(
            "management_port",
            sa.Integer(),
            nullable=False,
            server_default="22",
        ),
        sa.Column("management_username", sa.String(length=100), nullable=True),
        sa.Column("management_password", sa.String(length=255), nullable=True),
        sa.Column("management_host_fingerprint", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lr_id"], ["lrs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_client_modems_lr_id", "client_modems", ["lr_id"])


def downgrade() -> None:
    op.drop_index("ix_client_modems_lr_id", table_name="client_modems")
    op.drop_table("client_modems")
