"""add ptp_litebeam device type + migrate existing P2P LiteBeams

Les LiteBeam airMAX en mode point-à-point (UISP overview.wirelessMode ap-ptp /
sta-ptp) ne sont ni des Rockets (station de base) ni des LR (abonnés) : nouveau
type dédié `ptp_litebeam` (table `ptp_litebeams`). Cette migration :
  1. crée la table ptp_litebeams ;
  2. migre l'existant — les Rockets `is_backhaul=true` ET les devices dont la MAC
     est dans la liste des bouts PTP relevés via l'API UISP (12 au 2026-06-23) —
     en déplaçant la ligne de sous-type (rockets/lrs) vers ptp_litebeams ;
  3. supprime la colonne `is_backhaul` de rockets (remplacée par le type dédié).

Revision ID: b5c6d7e8f9a0
Revises: j7e8f9a0b1c2
Create Date: 2026-06-23 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "j7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Bouts PTP LiteBeam relevés via l'API UISP (type=airMax, wirelessMode ap-ptp/sta-ptp).
_PTP_MACS = (
    "6c:63:f8:b6:bd:fa", "6c:63:f8:cc:d4:8c", "60:22:32:cc:b0:67",
    "60:22:32:d8:4d:11", "1c:6a:1b:b6:5e:c7", "f4:e2:c6:88:8b:27",
    "6c:63:f8:b8:d3:1b", "1c:6a:1b:bc:00:2d", "60:22:32:d6:9a:e7",
    "6c:63:f8:b6:94:d1", "1c:6a:1b:bc:1f:24", "1c:6a:1b:b6:36:f8",
)


def upgrade() -> None:
    op.create_table(
        "ptp_litebeams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ssh_username", sa.String(length=100), nullable=True),
        sa.Column("ssh_password", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("ssh_host_fingerprint", sa.String(length=255), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── Migrer l'existant vers le nouveau type ──────────────────────────────
    # Une instruction par op.execute (pattern asyncpg du projet). La TEMP TABLE
    # persiste sur la même connexion pour les exécutions suivantes de la migration.
    macs = ", ".join(f"'{m}'" for m in _PTP_MACS)
    op.execute(
        f"CREATE TEMP TABLE _ptp_ids AS "
        f"SELECT id FROM devices WHERE lower(mac_address) IN ({macs}) "
        f"UNION SELECT id FROM rockets WHERE is_backhaul = true"
    )
    op.execute(
        "INSERT INTO ptp_litebeams (id, ssh_username, ssh_password, ssh_port, ssh_host_fingerprint) "
        "SELECT r.id, r.ssh_username, r.ssh_password, COALESCE(r.ssh_port, 443), r.ssh_host_fingerprint "
        "FROM rockets r JOIN _ptp_ids p ON p.id = r.id"
    )
    op.execute(
        "INSERT INTO ptp_litebeams (id, ssh_username, ssh_password, ssh_port, ssh_host_fingerprint) "
        "SELECT l.id, l.ssh_username, l.ssh_password, COALESCE(l.ssh_port, 443), l.ssh_host_fingerprint "
        "FROM lrs l JOIN _ptp_ids p ON p.id = l.id"
    )
    op.execute("DELETE FROM rockets r USING _ptp_ids p WHERE p.id = r.id")
    op.execute("DELETE FROM lrs l USING _ptp_ids p WHERE p.id = l.id")
    op.execute("UPDATE devices d SET device_type = 'ptp_litebeam' FROM _ptp_ids p WHERE p.id = d.id")
    op.execute("DROP TABLE _ptp_ids")

    op.drop_column("rockets", "is_backhaul")


def downgrade() -> None:
    # Remet la colonne is_backhaul ; reconvertit les ptp_litebeams en Rockets
    # airMAX backhaul (perte du model_variant d'origine pour ceux qui étaient LR).
    op.add_column(
        "rockets",
        sa.Column("is_backhaul", sa.Boolean(), server_default="false", nullable=False),
    )
    op.execute(
        "INSERT INTO rockets (id, radio_tech, is_backhaul, ssh_username, ssh_password, ssh_port, ssh_host_fingerprint) "
        "SELECT id, 'airmax', true, ssh_username, ssh_password, COALESCE(ssh_port, 443), ssh_host_fingerprint "
        "FROM ptp_litebeams"
    )
    op.execute(
        "UPDATE devices d SET device_type = 'rocket' FROM ptp_litebeams p WHERE p.id = d.id"
    )
    op.drop_table("ptp_litebeams")
