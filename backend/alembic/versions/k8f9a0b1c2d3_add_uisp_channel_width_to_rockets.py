"""add uisp_channel_width_mhz to rockets (capacity fallback from UISP)

The capacity page needs each Rocket's channel width to compute its client
ceiling. Until now that width came only from the live poll (LTU API
channelWidth.tx / airOS chanbw) persisted in device_metrics. A Rocket that was
unreachable at poll time (or has no airOS creds) had no width → no computable
ceiling → it showed up as "indéterminé" and was excluded from the totals.

The UISP controller already reports every AP's width (overview.channelWidth), so
the daily sync now mirrors it here. The capacity service uses it as a FALLBACK
when the live width is missing — eliminating the "indéterminé" case without
hiding a real gap. The live width still wins when present.

Revision ID: k8f9a0b1c2d3
Revises: c6d7e8f9a0b1
Create Date: 2026-06-23 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k8f9a0b1c2d3"
down_revision: str | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rockets",
        sa.Column("uisp_channel_width_mhz", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rockets", "uisp_channel_width_mhz")
