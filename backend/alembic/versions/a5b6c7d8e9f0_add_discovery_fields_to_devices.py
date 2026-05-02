"""add discovery fields to devices

Adds the columns required for auto-discovery of LR devices reported as peers
by parent Rocket APs:

  mac_address          : stable identifier (unique), survives IP changes
  hostname             : friendly name reported by the peer
  firmware_version     : firmware string from the peer
  auto_discovered      : True for devices created by the discovery service
  first_discovered_at  : first time the peer appeared in a Rocket's peer list
  last_discovered_at   : most recent appearance (used by stale-detection job)

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-05-02 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'a5b6c7d8e9f0'
down_revision: str | None = 'f4a5b6c7d8e9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'devices',
        sa.Column('mac_address', sa.String(length=17), nullable=True),
    )
    op.add_column(
        'devices',
        sa.Column('hostname', sa.String(length=255), nullable=True),
    )
    op.add_column(
        'devices',
        sa.Column('firmware_version', sa.String(length=100), nullable=True),
    )
    op.add_column(
        'devices',
        sa.Column(
            'auto_discovered',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        'devices',
        sa.Column('first_discovered_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'devices',
        sa.Column('last_discovered_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint(
        'uq_devices_mac_address', 'devices', ['mac_address'],
    )
    # Drop the server_default once the column is in place — application code
    # owns the default from now on.
    op.alter_column('devices', 'auto_discovered', server_default=None)


def downgrade() -> None:
    op.drop_constraint('uq_devices_mac_address', 'devices', type_='unique')
    op.drop_column('devices', 'last_discovered_at')
    op.drop_column('devices', 'first_discovered_at')
    op.drop_column('devices', 'auto_discovered')
    op.drop_column('devices', 'firmware_version')
    op.drop_column('devices', 'hostname')
    op.drop_column('devices', 'mac_address')
