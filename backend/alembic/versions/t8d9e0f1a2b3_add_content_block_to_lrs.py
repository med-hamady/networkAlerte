"""add per-category content filter columns to lrs

The content-block feature lets the operator block a client's access to specific
services (TikTok, Facebook, Google, …) while leaving the rest of the internet
open — independent of the full/whatsapp_only block. ``blocked_categories`` is
the desired set of category keys (JSON list); NULL means the filter was never
used / fully cleared. ``content_block_enforced_at`` is the last time it was
successfully (re)asserted on the LR over SSH. Both default NULL — existing rows
keep no content filter.

Revision ID: t8d9e0f1a2b3
Revises: s7c8d9e0f1a2
Create Date: 2026-07-15 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "t8d9e0f1a2b3"
down_revision: str | None = "s7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column("blocked_categories", sa.JSON(), nullable=True),
    )
    op.add_column(
        "lrs",
        sa.Column(
            "content_block_enforced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("lrs", "content_block_enforced_at")
    op.drop_column("lrs", "blocked_categories")
