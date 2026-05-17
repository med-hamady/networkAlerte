"""add audit_log table (write-only request audit trail)

Adds the `audit_log` table that records every state-changing HTTP request
(POST/PUT/PATCH/DELETE) routed by the FastAPI middleware. Forensic intent:
answer "who/what/when" after the fact, and feed the abnormal-volume detection
job. See app/models/audit_log.py and tasks/jobs.security_anomaly_detection_job.

Revision ID: h9c0d1e2f3a4
Revises: g8b9c0d1e2f3
Create Date: 2026-05-17 19:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h9c0d1e2f3a4"
down_revision: str | None = "g8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("client_ip", sa.String(length=45), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
    )
    op.create_index("ix_audit_log_created_at", "audit_log", ["created_at"])
    op.create_index(
        "ix_audit_log_client_ip_created_at",
        "audit_log",
        ["client_ip", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_client_ip_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_created_at", table_name="audit_log")
    op.drop_table("audit_log")
