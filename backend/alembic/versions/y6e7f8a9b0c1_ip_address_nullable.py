"""ip_address nullable (MAC is the identity, IP is volatile/DHCP-churned)

Revision ID: y6e7f8a9b0c1
Revises: x5d6e7f8a9b0
Create Date: 2026-06-09 10:00:00.000000

Discovery identity is the MAC address (already UNIQUE + nullable). An IP belongs
to exactly one device at any instant (UNIQUE kept), but DHCP reassigns it between
CPE over time. Making ip_address NULLABLE lets reconcile release a stale binding
(set NULL) so the new owner can take the IP without violating UNIQUE(ip_address).
The stale device keeps its MAC identity and gets its real current IP back when
its own Rocket rediscovers it.
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'y6e7f8a9b0c1'
down_revision: str | None = 'x5d6e7f8a9b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        'devices', 'ip_address',
        existing_type=sa.String(length=45),
        nullable=True,
    )


def downgrade() -> None:
    # One-way in practice: will fail if any device has a NULL ip_address by then.
    op.alter_column(
        'devices', 'ip_address',
        existing_type=sa.String(length=45),
        nullable=False,
    )
