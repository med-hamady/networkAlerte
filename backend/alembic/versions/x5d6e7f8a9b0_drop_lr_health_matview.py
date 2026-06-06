"""drop lr_health_metric_stats_30d materialized view

The 30-day report sections that consumed this view (« santé liens clients » +
« qualité radio ») were removed from /reports: the radio quality metrics
(signal_dbm, cinr_db, ccq_pct, link_potential_pct, total_capacity_mbps,
local/remote_rx_rate_idx) are no longer historised — persist_device_metrics now
collapses them to a single latest row — so a 30-day average has no data to
aggregate. The page « Liaisons clients » already runs on LIVE values
(get_live_link_health), so nothing reads this view anymore.

DROP MATERIALIZED VIEW is an instant catalog operation (no table scan), so —
unlike a bulk delete — it is safe on the startup migration path. The matview's
refresh job (lr_health_matview_refresh_job) was removed from the scheduler in
the same change.

Revision ID: x5d6e7f8a9b0
Revises: w4c5d6e7f8a9
Create Date: 2026-06-06 00:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "x5d6e7f8a9b0"
down_revision: str | None = "w4c5d6e7f8a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # IF EXISTS so the migration is idempotent on environments where the view
    # was never created. Dropping the view also drops its unique index.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS lr_health_metric_stats_30d")


def downgrade() -> None:
    # Recreate the view as it was in o6a7b8c9d0e1. It will be empty until the
    # (also-removed) refresh job is restored — kept only for chain reversibility.
    metric_list = (
        "'signal_dbm', 'link_potential_pct', 'total_capacity_mbps', "
        "'local_rx_rate_idx', 'remote_rx_rate_idx'"
    )
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
    op.execute(
        """
        CREATE UNIQUE INDEX ix_lr_health_metric_stats_30d_pk
          ON lr_health_metric_stats_30d (device_id, metric_name)
        """
    )
