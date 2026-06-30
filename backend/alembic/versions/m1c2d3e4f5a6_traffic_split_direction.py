"""traffic_dest_stats: split bytes by direction (down/up)

Correctif + fonctionnalité débit. Le collecteur ne gardait que les flux dont la
DESTINATION est publique → il ne captait que le sens montant (upload). Or la
bande passante d'un lien est surtout descendante (download depuis les CDN). On
attribue désormais chaque flux à son extrémité PUBLIQUE (source en download,
destination en upload) et on stocke les octets par sens : down_bytes / up_bytes.

Les anciennes lignes (jour 0, mauvais sens + non résolues "Indéterminé") sont
purgées (TRUNCATE) : elles ne sont pas reconstructibles et fausseraient le débit.

Revision ID: m1c2d3e4f5a6
Revises: m0b1c2d3e4f5
Create Date: 2026-06-30 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m1c2d3e4f5a6"
down_revision: str | None = "m0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Day-0 throwaway data (wrong direction semantics + all "Indéterminé").
    op.execute("TRUNCATE TABLE traffic_dest_stats")
    op.drop_column("traffic_dest_stats", "bytes")
    op.drop_column("traffic_dest_stats", "packets")
    op.add_column(
        "traffic_dest_stats",
        sa.Column("down_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "traffic_dest_stats",
        sa.Column("up_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("traffic_dest_stats", "up_bytes")
    op.drop_column("traffic_dest_stats", "down_bytes")
    op.add_column(
        "traffic_dest_stats",
        sa.Column("packets", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "traffic_dest_stats",
        sa.Column("bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
