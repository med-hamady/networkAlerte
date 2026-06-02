"""drop incidents.probable_cause

The probable-cause correlation feature (alert_correlation.determine_probable_cause)
was removed entirely: it inferred a coarse root cause (e.g. rocket_down caused by
switch_down) and stored it on incidents.probable_cause. The column, the engine and
all UI/email surfaces are gone, so the column is dropped.

downgrade re-adds the (always-NULL) column; the historical values cannot be
reconstructed.

Revision ID: t1f2a3b4c5d6
Revises: s0e1f2a3b4c5
Create Date: 2026-06-02 18:30:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "t1f2a3b4c5d6"
down_revision: str | None = "s0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("incidents", "probable_cause")


def downgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("probable_cause", sa.String(length=100), nullable=True),
    )
