"""add content_block_mode to lrs (denylist vs allowlist)

The content filter gains a second direction. Until now it could only *block*
the selected services (allow everything else); operators also need the reverse
— block everything and allow only the selected services (e.g. "WhatsApp only").
Both use the same `blocked_categories` list, so the direction needs its own
column.

"denylist" (the existing behaviour) is the server default, so every existing row
keeps working exactly as before.

Revision ID: bb2c3d4e5f6a
Revises: aa1b2c3d4e5f
Create Date: 2026-07-21 16:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "bb2c3d4e5f6a"
down_revision: str | None = "aa1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column(
            "content_block_mode",
            sa.String(length=10),
            server_default="denylist",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("lrs", "content_block_mode")
