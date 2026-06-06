"""device_metrics collected_at index for retention purge (no-op migration)

Intentional no-op, same rationale as u2a3b4c5d6e7 (switch collapse): the index
`ix_device_metrics_collected_at` is NOT created here. On the existing prod
device_metrics (~16 M rows) a non-concurrent CREATE INDEX locks writes, and a
CONCURRENTLY build inside the startup migration would block uvicorn past the
backend healthcheck start_period → unhealthy container, failed deploy.

Instead the index is created by `device_metrics_retention_job` in the scheduler
via `CREATE INDEX CONCURRENTLY IF NOT EXISTS` — off the startup/healthcheck
path, idempotent, and a cheap no-op once built. The index is declared on the
DeviceMetric model (`__table_args__`) so alembic autogenerate is aware of it and
won't emit a spurious drop_index on a DB where the job already created it.

The index makes the retention purge's `WHERE collected_at < cutoff` an index
range scan instead of a full seq scan of device_metrics on every run.

This migration is kept (not removed) so the revision chain stays linear; it
simply records the version bump.

Revision ID: w4c5d6e7f8a9
Revises: v3b4c5d6e7f8
Create Date: 2026-06-06 00:00:00.000000

"""
from collections.abc import Sequence

revision: str = "w4c5d6e7f8a9"
down_revision: str | None = "v3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No-op — index built CONCURRENTLY by device_metrics_retention_job (see docstring).
    pass


def downgrade() -> None:
    pass
