"""add block_mode to lrs (full shutdown vs whatsapp-only)

A client block has two flavours:

  - ``full``          : shut the LR's LAN port — total internet cut.
  - ``whatsapp_only`` : an iptables allowlist on the LR (DNS + Meta/WhatsApp
                        ranges RETURN, everything else DROP) so the client can
                        still reach WhatsApp (e.g. to contact support / pay)
                        while the rest of the internet is cut.

``block_mode`` records which flavour is active so the enforcement job re-asserts
the right mechanism after an LR reboot. Defaults to ``full`` — existing blocked
LRs keep their port-shutdown behaviour unchanged.

Revision ID: l3d4e5f6a7b8
Revises: k2c3d4e5f6a7
Create Date: 2026-05-19 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "l3d4e5f6a7b8"
down_revision: str | None = "k2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column(
            "block_mode",
            sa.String(length=20),
            server_default="full",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("lrs", "block_mode")
