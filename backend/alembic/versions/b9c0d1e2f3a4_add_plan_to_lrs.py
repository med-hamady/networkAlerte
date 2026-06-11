"""add subscription plan (traffic-shaper rate caps) to lrs

The customer's plan (forfait) is provisioned on the LR itself as an airOS
traffic shaper — an egress rate cap per interface in /tmp/system.cfg — and is
not exposed by any device HTTP API. `lr_plan_service` reads it over SSH (the
same channel client-block uses) and caches the download/upload caps here so the
frontend can display "20/10 Mbps" without an SSH round-trip per view.

`plan_synced_at` records the last successful read (NULL = never). Both caps NULL
after a sync means the LR has no shaper configured. The commercial plan *name*
is not on the device (CRM-only) and is intentionally not stored here.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-06-11 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: str | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lrs", sa.Column("plan_download_mbps", sa.Float(), nullable=True))
    op.add_column("lrs", sa.Column("plan_upload_mbps", sa.Float(), nullable=True))
    op.add_column(
        "lrs",
        sa.Column("plan_synced_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lrs", "plan_synced_at")
    op.drop_column("lrs", "plan_upload_mbps")
    op.drop_column("lrs", "plan_download_mbps")
