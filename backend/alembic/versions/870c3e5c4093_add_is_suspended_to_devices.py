"""add_is_suspended_to_devices

Revision ID: 870c3e5c4093
Revises: 892dc24d2e92
Create Date: 2026-04-24 11:18:40.875378

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '870c3e5c4093'
down_revision: Union[str, None] = '892dc24d2e92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('devices', sa.Column('is_suspended', sa.Boolean(), server_default='false', nullable=False))
    op.alter_column('devices', 'last_seen',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True)


def downgrade() -> None:
    op.alter_column('devices', 'last_seen',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True)
    op.drop_column('devices', 'is_suspended')
