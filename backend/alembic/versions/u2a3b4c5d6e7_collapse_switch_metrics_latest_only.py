"""collapse switch device_metrics to one latest row per metric_name

Switch metrics are display-only: the device modal reads the latest value per
port via /devices/{id}/metrics/latest, and no matview or report consumes switch
history. A UISP Switch emits ~130 metrics/cycle (8 counters × ~16 ports +
uptime) → ~190k device_metrics rows/day, which only bloated the table and its
index (one switch had reached ~1.6 M rows → /metrics/latest seq-scanned and
timed out, so the switch modal showed nothing).

Going forward, snmp_poll_job overwrites switch metrics in place (a single row
per (device_id, metric_name)). This migration does the one-time backlog
cleanup so the steady-state invariant holds immediately: keep only the most
recent row per (device_id, metric_name) for uisp_switch devices, delete the
rest.

Irreversible: the deleted historical samples cannot be reconstructed. downgrade
is a no-op.

Revision ID: u2a3b4c5d6e7
Revises: t1f2a3b4c5d6
Create Date: 2026-06-03 13:10:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "u2a3b4c5d6e7"
down_revision: str | None = "t1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep rn=1 (latest by collected_at) per (device_id, metric_name) for
    # switches; delete the older duplicates. Served by ix_device_metrics_lookup
    # (device_id, metric_name, collected_at) for the window ordering.
    op.execute(
        """
        DELETE FROM device_metrics dm
        USING (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY device_id, metric_name
                       ORDER BY collected_at DESC, id DESC
                   ) AS rn
            FROM device_metrics
            WHERE device_id IN (
                SELECT id FROM devices WHERE device_type = 'uisp_switch'
            )
        ) ranked
        WHERE dm.id = ranked.id
          AND ranked.rn > 1
        """
    )


def downgrade() -> None:
    # Irreversible — deleted switch history cannot be reconstructed.
    pass
