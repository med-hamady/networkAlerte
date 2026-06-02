"""add client_consumption_30d materialized view

The /clients/consumption endpoint sums positive deltas of byte counters
over a sliding window. Before this commit the deltas were computed in
Python after streaming millions of samples from device_metrics (30d view
clocked ~36 s in prod 2026-06-02 → user-visible "Chargement…" stall).

The companion refactor in app/api/endpoints/clients.py moves the delta
math to SQL via LAG() + CASE. For the 30-day window — the most common
view — this matview pre-computes the same aggregate and is refreshed
every 15 min by `client_consumption_matview_refresh_job`, dropping 30d
latency from ~36 s to <100 ms.

24h/7d run the live SQL query (sub-2 s, acceptable). Lifetime also runs
live so it stays correct once retention >30 d is enabled (the matview's
30 d cutoff would silently truncate the lifetime total).

The CASE filter MUST match _MAX_PLAUSIBLE_DELTA_BYTES in
clients.py (8 * 1024^3 = 8 589 934 592). A divergence makes the 30d
view diverge from 24h/7d.

Revision ID: p7b8c9d0e1f2
Revises: o6a7b8c9d0e1
Create Date: 2026-06-02 00:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "p7b8c9d0e1f2"
down_revision: str | None = "o6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep this list in sync with _COUNTER_METRICS in app/api/endpoints/clients.py.
_COUNTER_METRICS = (
    "peer_tx_bytes",
    "peer_rx_bytes",
    "radio_rx_bytes",
    "radio_tx_bytes",
)

# Counter-glitch threshold — must match _MAX_PLAUSIBLE_DELTA_BYTES in clients.py.
# 8 GB per 60 s cycle = ~1 Gbps headroom, fine for current radio capacity.
_MAX_DELTA_BYTES = 8 * 1024 ** 3


def upgrade() -> None:
    metric_list = ", ".join(f"'{m}'" for m in _COUNTER_METRICS)
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW client_consumption_30d AS
        SELECT
            device_id,
            metric_name,
            SUM(CASE WHEN d IS NOT NULL AND d >= 0 AND d <= {_MAX_DELTA_BYTES}
                     THEN d ELSE 0 END) AS bytes,
            COUNT(*)          AS samples,
            MIN(collected_at) AS first_sample_at
        FROM (
            SELECT
                device_id,
                metric_name,
                collected_at,
                metric_value - LAG(metric_value) OVER w AS d
            FROM device_metrics
            WHERE metric_name IN ({metric_list})
              AND collected_at >= now() - interval '30 days'
            WINDOW w AS (
                PARTITION BY device_id, metric_name ORDER BY collected_at
            )
        ) deltas
        GROUP BY device_id, metric_name
        """
    )
    # UNIQUE index — prerequisite for REFRESH MATERIALIZED VIEW CONCURRENTLY.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_client_consumption_30d_pk
          ON client_consumption_30d (device_id, metric_name)
        """
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS client_consumption_30d")
