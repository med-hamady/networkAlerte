"""Keep request identification and waiting intervals across reloads."""

import sqlalchemy as sa

from alembic import op

revision = "o4e5f6a7b8c9"
down_revision = "n3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    # Old records have no reliable waiting intervals. Do not backdate a wait
    # from device outage or receipt time, or invent missing contact details.
    op.add_column("fai_requests", sa.Column("identification", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("fai_requests", sa.Column("waiting_periods", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade():
    op.drop_column("fai_requests", "waiting_periods")
    op.drop_column("fai_requests", "identification")
