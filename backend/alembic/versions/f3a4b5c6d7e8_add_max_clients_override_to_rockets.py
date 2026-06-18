"""add max_clients_override to rockets (manual client-capacity ceiling)

Until now the rocket_client_overload ceiling was 100% computed by the per-family
+ channel-width formula (_rocket_overload_threshold). Operators need to pin that
ceiling by hand for some APs — e.g. when the channel width can't be auto-detected
(no airOS creds) or when field experience disagrees with the formula.

This adds a nullable per-Rocket override. NULL = keep the auto formula; a value
REPLACES it entirely. Editable from the Capacity page. Preserved by the UISP
sync (it only updates name/ip/location/mac).

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-18 14:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rockets",
        sa.Column("max_clients_override", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rockets", "max_clients_override")
