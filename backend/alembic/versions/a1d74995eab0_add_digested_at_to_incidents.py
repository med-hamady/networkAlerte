"""add_digested_at_to_incidents

Revision ID: a1d74995eab0
Revises: 6004c4e7391c
Create Date: 2026-04-28 00:09:00.424883

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1d74995eab0'
down_revision: Union[str, None] = '6004c4e7391c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'incidents',
        sa.Column('digested_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('incidents', 'digested_at')
