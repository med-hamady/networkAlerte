"""add ssh_host_fingerprint to devices

Stores the pinned SSH host key fingerprint per device so the supervisor can
detect MITM on the LAN. Filled on first successful connect (TOFU pattern).

Revision ID: d2e3f4a5b6c7
Revises: c1f2a3b4d5e6
Create Date: 2026-04-29 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, None] = 'c1f2a3b4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'devices',
        sa.Column('ssh_host_fingerprint', sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('devices', 'ssh_host_fingerprint')
