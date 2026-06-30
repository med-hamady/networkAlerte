"""add traffic_dest_stats (top client destinations by ASN/operator)

New NetFlow collector subsystem: the edge router (MikroTik) exports NetFlow to a
dedicated collector process which resolves each destination IP to its ASN and
flushes per-(time bucket, ASN) byte aggregates here. The /traffic page reads them
back to rank operators/CDNs (Facebook, Google/YouTube, Netflix…) — the signal used
to decide which cache (GGC/FNA/OCA) to request.

Aggregated table (one row per bucket+ASN), kept small; old buckets are purged by
traffic_stats_retention_job. Index on bucket_start serves the period roll-up and
the retention range scan.

Revision ID: m0b1c2d3e4f5
Revises: l9a0b1c2d3e4
Create Date: 2026-06-30 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m0b1c2d3e4f5"
down_revision: str | None = "l9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "traffic_dest_stats",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("asn", sa.Integer(), nullable=True),
        sa.Column("as_org", sa.String(length=160), nullable=True),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.Column("packets", sa.BigInteger(), nullable=False),
        sa.Column("flows", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_traffic_dest_stats_bucket", "traffic_dest_stats", ["bucket_start"],
    )


def downgrade() -> None:
    op.drop_index("ix_traffic_dest_stats_bucket", table_name="traffic_dest_stats")
    op.drop_table("traffic_dest_stats")
