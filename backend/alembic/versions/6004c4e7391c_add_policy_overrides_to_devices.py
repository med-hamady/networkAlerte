"""add_policy_overrides_to_devices

Revision ID: 6004c4e7391c
Revises: b2c3d4e5f6a7
Create Date: 2026-04-28 00:06:08.462305

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6004c4e7391c'
down_revision: str | None = 'b2c3d4e5f6a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('policy_overrides', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('devices', 'policy_overrides')
