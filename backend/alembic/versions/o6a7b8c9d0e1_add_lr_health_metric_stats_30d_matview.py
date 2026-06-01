"""add lr_health_metric_stats_30d materialized view

The /lr-health/bad-installations endpoint aggregates 30 days of
device_metrics for ~70 LRs × 5 tracked metrics. With ~16 M rows / 3.2 GB
in prod 2026-06-01, the planner picks a Parallel Seq Scan over the full
table (no index can serve a 1.3 M-row range scan more efficiently),
clocking ~4 s per call. This materialized view pre-computes the
aggregate and is refreshed every 15 min by a scheduler job, dropping the
endpoint cost from ~4 s to <100 ms.

The view's WHERE clause uses now() so the sliding 30-day window is
re-evaluated at each REFRESH — the view definition stays static while
the data window moves forward in time.

The UNIQUE index on (device_id, metric_name) is required by
REFRESH MATERIALIZED VIEW CONCURRENTLY (which never blocks readers).

Revision ID: o6a7b8c9d0e1
Revises: n5f6a7b8c9d0
Create Date: 2026-06-02 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "o6a7b8c9d0e1"
down_revision: str | None = "n5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep this list in sync with lr_health_service._TRACKED_METRICS.
# A mismatch silently breaks the page (LRs invisible / wrong verdicts).
_TRACKED_METRICS = (
    "signal_dbm",
    "link_potential_pct",
    "total_capacity_mbps",
    "local_rx_rate_idx",
    "remote_rx_rate_idx",
)


def upgrade() -> None:
    metric_list = ", ".join(f"'{m}'" for m in _TRACKED_METRICS)
    # CREATE MATERIALIZED VIEW populates it by default (no WITH NO DATA),
    # so the view is immediately queryable after this migration runs.
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW lr_health_metric_stats_30d AS
        SELECT
            dm.device_id,
            dm.metric_name,
            avg(dm.metric_value) AS mean,
            count(dm.id)          AS samples
        FROM device_metrics dm
        JOIN lrs ON lrs.id = dm.device_id
        WHERE dm.metric_name IN ({metric_list})
          AND dm.collected_at >= now() - interval '30 days'
        GROUP BY dm.device_id, dm.metric_name
        """
    )
    # UNIQUE index — mandatory prerequisite for REFRESH ... CONCURRENTLY.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_lr_health_metric_stats_30d_pk
          ON lr_health_metric_stats_30d (device_id, metric_name)
        """
    )


def downgrade() -> None:
    # DROP MATERIALIZED VIEW removes the unique index automatically.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS lr_health_metric_stats_30d")
