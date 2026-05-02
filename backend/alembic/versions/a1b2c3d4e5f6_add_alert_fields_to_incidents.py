"""add_alert_fields_to_incidents

Revision ID: a1b2c3d4e5f6
Revises: 1ead65fa554c
Create Date: 2026-04-27 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = '1ead65fa554c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('incidents', sa.Column('alert_type', sa.String(50), nullable=True))
    op.add_column('incidents', sa.Column('metric_name', sa.String(100), nullable=True))
    op.add_column('incidents', sa.Column('metric_value', sa.Float(), nullable=True))
    op.add_column('incidents', sa.Column('threshold_value', sa.Float(), nullable=True))
    op.add_column('incidents', sa.Column('probable_cause', sa.String(100), nullable=True))
    op.add_column('incidents', sa.Column('last_triggered_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_incidents_alert_type', 'incidents', ['alert_type'])


def downgrade() -> None:
    op.drop_index('ix_incidents_alert_type', table_name='incidents')
    op.drop_column('incidents', 'last_triggered_at')
    op.drop_column('incidents', 'probable_cause')
    op.drop_column('incidents', 'threshold_value')
    op.drop_column('incidents', 'metric_value')
    op.drop_column('incidents', 'metric_name')
    op.drop_column('incidents', 'alert_type')
