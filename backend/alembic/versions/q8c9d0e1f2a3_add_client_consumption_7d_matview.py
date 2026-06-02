"""add client_consumption_7d materialized view

Same shape and refresh pattern as `client_consumption_30d` (migration
p7b8c9d0e1f2) but bounded to the last 7 days. /clients/consumption?period=7d
hit a Parallel Seq Scan + 30 MB external sort and clocked ~13 s in prod
2026-06-02; reading the matview drops it to <100 ms.

Why a separate matview instead of summing days out of the 30d matview?
The 30d matview aggregates over the full window — we can't subtract the
older 23 days from it. A daily-granularity matview would solve both 7d
and 30d with one object, but it changes the delta-at-midnight semantics
slightly and is best left to a future refactor.

The CASE filter MUST match _MAX_PLAUSIBLE_DELTA_BYTES in clients.py
(8 * 1024^3 = 8 589 934 592) — divergence makes 7d differ from 24h.

Revision ID: q8c9d0e1f2a3
Revises: p7b8c9d0e1f2
Create Date: 2026-06-02 01:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "q8c9d0e1f2a3"
down_revision: str | None = "p7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep in sync with _COUNTER_METRICS in app/api/endpoints/clients.py.
_COUNTER_METRICS = (
    "peer_tx_bytes",
    "peer_rx_bytes",
    "radio_rx_bytes",
    "radio_tx_bytes",
)

# Must match _MAX_PLAUSIBLE_DELTA_BYTES in clients.py.
_MAX_DELTA_BYTES = 8 * 1024 ** 3


def upgrade() -> None:
    metric_list = ", ".join(f"'{m}'" for m in _COUNTER_METRICS)
    op.execute(
        f"""
        CREATE MATERIALIZED VIEW client_consumption_7d AS
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
              AND collected_at >= now() - interval '7 days'
            WINDOW w AS (
                PARTITION BY device_id, metric_name ORDER BY collected_at
            )
        ) deltas
        GROUP BY device_id, metric_name
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX ix_client_consumption_7d_pk
          ON client_consumption_7d (device_id, metric_name)
        """
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS client_consumption_7d")
