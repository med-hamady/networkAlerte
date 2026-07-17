"""add latitude/longitude to devices (coordinates provisioned ON the device)

Read over SSH from the LR's own /tmp/system.cfg (`system.latitude` /
`system.longitude`) by lr_plan_service, which already greps that file for the
traffic-shaper plan — same session, same grep, no extra SSH round-trip.

NOT a GPS fix. Field-checked 2026-07-17 on an LTU-LR: ubnt-gps-reader does run
against /dev/ttyAMA0, but the fix is void (gps_info = ",,V,,...", 0 satellites,
mca-status gpsFixed=0, live lat/lon 0.000000). The stored value is whatever the
operator provisioned.

Coverage is per-device, not per-family: LTU (afltu), airMAX AC (WA) and LiteBeam
M5 (XW) all carry the key when the installer filled it in — confirmed on a
LiteBeam 5AC holding system.latitude=18.135, on an LTU-Lite holding none, and on
an M5 whose key is present but EMPTY. NULL therefore means "never provisioned on
that unit", never "that model can't".

Do not substitute `mca-status`: it mirrors the config on airMAX but reports the
live (always unfixed, 0.000000) GPS on LTU.

Deliberately NOT sourced from UISP (operator decision 2026-07-17): the two
sources disagree — median ~2.6 km apart, up to 9.4 km on a 15-device sample —
and the device was chosen as the single source of truth. An earlier UISP-based
import was implemented and reverted (594f770 / 6629268).

Note `devices.location` was already taken: it holds the SITE NAME, not
coordinates. Hence the explicit columns.

Revision ID: x2h3i4j5k6l7
Revises: v0f1a2b3c4d5
Create Date: 2026-07-17 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "x2h3i4j5k6l7"
down_revision: str | None = "v0f1a2b3c4d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("devices", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "longitude")
    op.drop_column("devices", "latitude")
