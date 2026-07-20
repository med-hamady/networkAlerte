"""add site_locations — one geographic position per physical site (mast)

The client map needs the sites, not just the clients: an operator wants to see
each mast and the link from every client to the mast it is served by.

A site is one mast carrying several sector Rockets (up to 16 on A2 PK1). They
share the structure, so ONE position per site is the right model — confirmed by
the data: across the 17 sites, the sectors' UISP coordinates spread by only
4 to 29 m, the footprint of the mast itself. Drawing one marker per sector would
stack 88 markers on 17 real places.

Seeded from the UISP controller (median per site — a median ignores a single
mis-provisioned sector, a mean would drag the whole site off). Unlike the client
LRs, this cannot be read from the device: the Rockets' SSH port is 443, which is
the airOS HTTPS API, not SSH — every attempt fails on "Error reading SSH
protocol banner".

Values are seeded, not frozen: the table is editable so a new site does not
require a deployment (operational config belongs in the DB).

Revision ID: y3i4j5k6l7m8
Revises: x2h3i4j5k6l7
Create Date: 2026-07-17 17:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "y3i4j5k6l7m8"
down_revision: str | None = "x2h3i4j5k6l7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Site name → (latitude, longitude), read from UISP 2026-07-17.
# ⚠️ "A2  ARF1" carries a DOUBLE space — it must match `devices.site` verbatim,
# so do not "clean" it: the join is on the exact string.
_SEED: list[tuple[str, float, float]] = [
    ("A2  ARF1", 18.055949, -15.974862),
    ("A2 AT1",   18.140022, -15.919665),
    ("A2 AT2",   18.141725, -15.940889),
    ("A2 CT1",   18.100136, -16.013656),
    ("A2 CT2",   18.078650, -16.020833),
    ("A2 DN1",   18.105613, -15.918395),
    ("A2 HQ",    18.114964, -15.991145),
    ("A2 KS1",   18.113357, -15.948003),
    ("A2 NR1",   18.122249, -16.002244),
    ("A2 PK1",   18.027638, -15.975445),
    ("A2 PK2",   17.985237, -15.966903),
    ("A2 SK1",   18.077928, -16.000256),
    ("A2 SM1",   18.073013, -15.962078),
    ("A2 SNDE",  18.119191, -15.974632),
    ("A2 TJN1",  18.069624, -15.914833),
    ("A2 TS1",   18.075388, -15.950619),
    ("A2 VEL1",  18.056224, -15.938294),
]


def upgrade() -> None:
    table = op.create_table(
        "site_locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("site", sa.String(255), nullable=False, unique=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="uisp"),
    )
    op.create_index("ix_site_locations_site", "site_locations", ["site"])
    op.bulk_insert(
        table,
        [
            {"site": s, "latitude": la, "longitude": lo, "source": "uisp"}
            for s, la, lo in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_site_locations_site", table_name="site_locations")
    op.drop_table("site_locations")
