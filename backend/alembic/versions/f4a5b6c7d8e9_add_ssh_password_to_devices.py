"""add ssh_password to devices

Stores a per-device SSH password that takes priority over the global
LTU_LR_SSH_PASSWORD / LTU_API_PASSWORD env vars. Allows managing devices
with non-default credentials without touching the global .env.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-04-30 01:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'devices',
        sa.Column('ssh_password', sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('devices', 'ssh_password')
