"""add uisp_power credentials to devices

Per-device UISP Power API credentials (username, password, port) that
override the global UISP_POWER_* env vars when set. Allows managing
multiple UISP Power units with distinct passwords.

Revision ID: b7c8d9e0f1a2
Revises: a5b6c7d8e9f0
Create Date: 2026-05-06 13:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'b7c8d9e0f1a2'
down_revision: str | None = 'a5b6c7d8e9f0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'devices',
        sa.Column('uisp_power_username', sa.String(length=100), nullable=True),
    )
    op.add_column(
        'devices',
        sa.Column('uisp_power_password', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'devices',
        sa.Column('uisp_power_port', sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('devices', 'uisp_power_port')
    op.drop_column('devices', 'uisp_power_password')
    op.drop_column('devices', 'uisp_power_username')
