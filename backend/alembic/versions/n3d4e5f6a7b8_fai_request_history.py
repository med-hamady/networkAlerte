"""Persist request execution and observed connectivity, without guessing old results."""

import sqlalchemy as sa

from alembic import op

revision = "n3d4e5f6a7b8"
down_revision = "m2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("devices", sa.Column("connectivity_observed_at", sa.DateTime(timezone=True)))
    op.add_column("devices", sa.Column("offline_detected_at", sa.DateTime(timezone=True)))
    op.add_column("devices", sa.Column("online_detected_at", sa.DateTime(timezone=True)))
    op.create_table(
        "fai_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("request_key", sa.String(36), nullable=False, unique=True),
        sa.Column("mac", sa.String(17)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("site", sa.Text()),
        sa.Column("source", sa.String(120), nullable=False),
        sa.Column("agent", sa.String(120)),
        sa.Column("mode", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_reason", sa.Text()),
        sa.Column("actions", sa.JSON(), nullable=False),
    )
    op.create_index("ix_fai_requests_device_active", "fai_requests", ["device_id", "active"])
    op.create_table(
        "fai_request_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("request_id", sa.Integer(), sa.ForeignKey("fai_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target", sa.Text()),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("result", sa.String(30)),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_index("ix_fai_request_events_request_time", "fai_request_events", ["request_id", "timestamp"])


def downgrade():
    op.drop_table("fai_request_events")
    op.drop_table("fai_requests")
    for column in ("online_detected_at", "offline_detected_at", "connectivity_observed_at"):
        op.drop_column("devices", column)
