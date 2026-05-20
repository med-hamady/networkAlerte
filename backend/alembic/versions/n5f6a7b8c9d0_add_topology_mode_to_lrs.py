"""add topology_mode to lrs (router/bridge detection)

The client-block feature (full and whatsapp_only) is implemented on the LR
itself and only works when the LR is in router mode — it shuts the LAN port
or filters L3 traffic, both of which require the LR to be in the IP path of
the client. In bridge mode the LR is L2-transparent: iptables FORWARD and
the local dnsmasq are NOT in the client's path, so the block silently fails
to actually cut anything.

This column records the most recent detection so the API can refuse a block
on a bridged LR with a clear error, and the UI can surface a misconfig badge
to ask the operator to put the LR back in router mode. ``unknown`` means
detection hasn't run yet (no SSH credentials, or first poll pending).

Revision ID: n5f6a7b8c9d0
Revises: m4e5f6a7b8c9
Create Date: 2026-05-20 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "n5f6a7b8c9d0"
down_revision: str | None = "m4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column(
            "topology_mode",
            sa.String(length=10),
            server_default="unknown",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("lrs", "topology_mode")
