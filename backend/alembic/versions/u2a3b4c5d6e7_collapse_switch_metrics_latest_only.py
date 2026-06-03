"""collapse switch device_metrics to one latest row per metric_name (no-op)

Intentional no-op. An earlier version of this migration deleted the switch
metrics backlog (~1.6 M rows) here, but a bulk delete in the startup migration
blocked uvicorn from coming up within the backend healthcheck start_period
(90 s) → the container was marked unhealthy and the deploy failed (and the
delete ran inside one giant transaction holding locks on device_metrics).

The collapse now happens in `snmp_poll_job` instead: for a switch it does a
per-metric DELETE+INSERT, which keeps a single row per (device_id, metric_name)
in steady state AND absorbs the historical backlog on the first poll cycle —
one metric_name at a time, indexed on (device_id, metric_name), entirely inside
the scheduler so it never touches the backend startup path / healthcheck.

This migration is kept (not removed) so the revision chain stays linear for any
environment that already advanced past t1f2a3b4c5d6; it simply records the
version bump.

Revision ID: u2a3b4c5d6e7
Revises: t1f2a3b4c5d6
Create Date: 2026-06-03 13:10:00.000000

"""
from collections.abc import Sequence

revision: str = "u2a3b4c5d6e7"
down_revision: str | None = "t1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No-op — backlog collapse moved to snmp_poll_job (see module docstring).
    pass


def downgrade() -> None:
    pass
