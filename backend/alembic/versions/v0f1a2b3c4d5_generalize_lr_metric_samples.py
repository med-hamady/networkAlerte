"""generalize lr_latency_samples -> lr_metric_samples (one curve per metric_name)

The latency chart proved the shape; the device modal now also charts link capacity
(total_capacity_mbps) and link rates (tx/rx_rate_mbps), and more metrics will follow.
Rather than a new table per metric, the history table gains a `metric_name` column.

Done as a RENAME + ALTER rather than create/drop so the latency history already
collected in production is preserved (it cannot be rebuilt — the source metrics are
collapse-only in device_metrics).

Revision ID: v0f1a2b3c4d5
Revises: u9e0f1a2b3c4
Create Date: 2026-07-16 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "v0f1a2b3c4d5"
down_revision: str | None = "u9e0f1a2b3c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.rename_table("lr_latency_samples", "lr_metric_samples")

    # Existing rows are all latency — the server_default backfills them in place,
    # then it is dropped so the column stays explicit for every future writer.
    op.add_column(
        "lr_metric_samples",
        sa.Column(
            "metric_name", sa.String(length=100),
            nullable=False, server_default="lr_latency_ms",
        ),
    )
    op.alter_column("lr_metric_samples", "metric_name", server_default=None)

    # Names lose the "_ms" unit now that the rows are not latency-only.
    op.alter_column("lr_metric_samples", "avg_ms", new_column_name="avg_value")
    op.alter_column("lr_metric_samples", "min_ms", new_column_name="min_value")
    op.alter_column("lr_metric_samples", "max_ms", new_column_name="max_value")

    # Uniqueness now includes the metric: one row per (device, metric, bucket).
    op.drop_constraint(
        "uq_lr_latency_device_bucket", "lr_metric_samples", type_="unique",
    )
    op.create_unique_constraint(
        "uq_lr_metric_device_name_bucket",
        "lr_metric_samples",
        ["device_id", "metric_name", "bucket_start"],
    )

    op.execute(
        "ALTER INDEX ix_lr_latency_samples_bucket "
        "RENAME TO ix_lr_metric_samples_bucket"
    )


def downgrade() -> None:
    # Only latency rows can live in the old shape — the rest would violate its
    # (device_id, bucket_start) uniqueness, so drop them first.
    op.execute("DELETE FROM lr_metric_samples WHERE metric_name <> 'lr_latency_ms'")
    op.execute(
        "ALTER INDEX ix_lr_metric_samples_bucket "
        "RENAME TO ix_lr_latency_samples_bucket"
    )
    op.drop_constraint(
        "uq_lr_metric_device_name_bucket", "lr_metric_samples", type_="unique",
    )
    op.create_unique_constraint(
        "uq_lr_latency_device_bucket",
        "lr_metric_samples",
        ["device_id", "bucket_start"],
    )
    op.alter_column("lr_metric_samples", "avg_value", new_column_name="avg_ms")
    op.alter_column("lr_metric_samples", "min_value", new_column_name="min_ms")
    op.alter_column("lr_metric_samples", "max_value", new_column_name="max_ms")
    op.drop_column("lr_metric_samples", "metric_name")
    op.rename_table("lr_metric_samples", "lr_latency_samples")
