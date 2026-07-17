"""add latitude/longitude to devices (GPS coordinates from UISP)

No Ubiquiti radio in this fleet has a GPS receiver — a LiteBeam 5AC reports no
GPS hardware and leaves snmp.location empty, so the device itself can never tell
us where it is. The only source is the UISP controller, where the installer types
the position in at provisioning time: 1084/1299 devices carry one, and sites
carry none at all (0/1398), so this lands on `devices`, not on a site table.

Note `devices.location` is already taken — it holds the SITE NAME string, not
coordinates. Hence the explicit latitude/longitude columns.

Values are imported VERBATIM from UISP (operator decision, 2026-07-17): ~59 of
the 1084 sit outside Mauritania (installer-phone geoloc gone wrong — Riyadh,
Gaza, Trinidad, plus a few longitude sign flips). Anything reading these columns
must not assume the point is plausible.

Revision ID: w1g2h3i4j5k6
Revises: v0f1a2b3c4d5
Create Date: 2026-07-17 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "w1g2h3i4j5k6"
down_revision: str | None = "v0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("devices", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "longitude")
    op.drop_column("devices", "latitude")
