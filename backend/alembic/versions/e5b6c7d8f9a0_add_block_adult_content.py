"""add block_adult_content to lrs (per-client 18+ filter)

The content filter gains a per-client "block adult content (18+)" option: when
on, the client's LR forwards its DNS upstream to a family-safe resolver
(Cloudflare for Families) that maintains the adult-domain categorisation itself.
It is orthogonal to the per-category domain list, so it needs its own column.

Default false — existing rows keep no adult filtering.

Revision ID: e5b6c7d8f9a0
Revises: d4a5b6c7e8f9
Create Date: 2026-07-27 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b6c7d8f9a0"
down_revision: str | None = "d4a5b6c7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column(
            "block_adult_content",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("lrs", "block_adult_content")
