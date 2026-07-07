"""drop orphan ix_device_metrics_collected_at index

The standalone index on device_metrics(collected_at) only ever served the
device_metrics retention purge's `WHERE collected_at < cutoff` range scan. That
retention was removed entirely (byte-counter history is now kept indefinitely so
the /clients custom date range can look arbitrarily far back), leaving this index
orphaned: nothing reads it (consumption queries use the composite
(device_id, metric_name, collected_at) index), and it just adds write overhead on
every insert into a high-churn table.

Dropped CONCURRENTLY: on device_metrics (millions of rows, polled every 30–60 s)
a plain DROP INDEX takes an ACCESS EXCLUSIVE lock on the table, which would wait
behind — and then block — the constant inserts and the multi-second consumption
reads. DROP INDEX CONCURRENTLY takes only SHARE UPDATE EXCLUSIVE (non-blocking to
reads/writes) and is a fast catalog + unlink (no table scan, unlike a CONCURRENTLY
*build*), so it is safe on the startup migration path. CONCURRENTLY cannot run
inside a transaction → autocommit_block(). IF EXISTS makes it idempotent (the
index was created at runtime by the old scheduler job, so it may be absent on some
DBs).

Revision ID: q5a6b7c8d9e0
Revises: p4f5a6b7c8d9
Create Date: 2026-07-07 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "q5a6b7c8d9e0"
down_revision: str | None = "p4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CONCURRENTLY must run outside a transaction; autocommit_block commits the
    # migration's transaction, runs this in autocommit, then resumes.
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_device_metrics_collected_at")


def downgrade() -> None:
    # Recreate for chain reversibility. CONCURRENTLY + IF NOT EXISTS so the
    # rebuild never locks writes and is idempotent. Kept only for symmetry — the
    # index has no consumer now that retention is gone.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            "ix_device_metrics_collected_at ON device_metrics (collected_at)"
        )
