"""drop alerts and notification_channels tables

Policy 2026-06-09: the /notifications page (notification audit trail) and the
notification-channels / alert-policies management surfaces are removed
entirely. Notifications are still SENT (env-based SMTP via notification_service),
but no audit row is persisted and channels are no longer stored in DB — the
env config (SMTP_ENABLED + NOTIFICATION_EMAILS) is the single source.

This drops both tables:
  - alerts             : the notification audit trail (read by /notifications).
  - notification_channels : DB-stored email channels (CRUD removed).

`alerts.channel_id` FK -> notification_channels and `alerts.incident_id` FK ->
incidents, so alerts is dropped first.

downgrade recreates both empty tables (the purged rows cannot be reconstructed).

Revision ID: a8b9c0d1e2f3
Revises: z7f8a9b0c1d2
Create Date: 2026-06-09 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "z7f8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("notification_channels")


def downgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("channel_type", sa.String(length=30), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "alerts",
        sa.Column("incident_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["channel_id"], ["notification_channels.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
