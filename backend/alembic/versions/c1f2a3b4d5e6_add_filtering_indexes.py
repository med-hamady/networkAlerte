"""add filtering indexes on incidents and device_metrics

Performance: the dashboard and the various GET /incidents queries filter on
status / severity / device_id / detected_at; without these indexes every
listing degrades to a full table scan once incident volume grows.

device_metrics: the "latest metric" queries (jobs.py:_build_switch_context
and devices/{id}/metrics/latest) filter by (device_id, metric_name) and pick
MAX(collected_at). A composite index covers all three.

Revision ID: c1f2a3b4d5e6
Revises: a1d74995eab0
Create Date: 2026-04-29 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1f2a3b4d5e6'
down_revision: str | None = 'a1d74995eab0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index('ix_incidents_status', 'incidents', ['status'])
    op.create_index('ix_incidents_severity', 'incidents', ['severity'])
    op.create_index('ix_incidents_device_id', 'incidents', ['device_id'])
    op.create_index('ix_incidents_detected_at', 'incidents', ['detected_at'])
    op.create_index(
        'ix_device_metrics_lookup',
        'device_metrics',
        ['device_id', 'metric_name', 'collected_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_device_metrics_lookup', table_name='device_metrics')
    op.drop_index('ix_incidents_detected_at', table_name='incidents')
    op.drop_index('ix_incidents_device_id', table_name='incidents')
    op.drop_index('ix_incidents_severity', table_name='incidents')
    op.drop_index('ix_incidents_status', table_name='incidents')
