"""add lr_latency_samples (LR → Internet latency history, 5-min buckets)

Backs the latency chart of the device modal (GET /devices/{id}/latency-history).
The SSH probe already measures the RTT every 60 s, but device_metrics.lr_latency_ms
is a *collapse* metric (one latest row per LR) — there was no history to plot.

Rather than promoting lr_latency_ms to HISTORY_METRICS (~860k rows/day at ~600 LRs,
which would re-create the device_metrics bloat), the probe folds each reading into a
5-minute bucket via upsert: one row per (device_id, bucket_start), ~5.2M rows at the
30-day retention enforced by lr_latency_retention_job. min_ms/max_ms keep the extremes
so a short spike survives the averaging.

Empty table on creation: the chart only has data from this migration forward.

Revision ID: u9e0f1a2b3c4
Revises: t8d9e0f1a2b3
Create Date: 2026-07-16 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u9e0f1a2b3c4"
down_revision: str | None = "t8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lr_latency_samples",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("avg_ms", sa.Float(), nullable=False),
        sa.Column("min_ms", sa.Float(), nullable=False),
        sa.Column("max_ms", sa.Float(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # ON CONFLICT target of the probe's upsert, and the index serving the
        # chart's (device_id, time window) range scan.
        sa.UniqueConstraint(
            "device_id", "bucket_start", name="uq_lr_latency_device_bucket",
        ),
    )
    # Retention purges on bucket_start alone, across all devices — the unique
    # constraint above leads with device_id so it can't serve that scan.
    op.create_index(
        "ix_lr_latency_samples_bucket", "lr_latency_samples", ["bucket_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_lr_latency_samples_bucket", table_name="lr_latency_samples")
    op.drop_table("lr_latency_samples")
