"""split devices into joined-table inheritance

Refactor: `devices` now holds only the columns common to every monitored device
(identity, status, SNMP, discovery metadata). The type-specific columns move
into four child tables joined by FK on devices.id:

- rockets       : LTU/airMAX base stations (radio_tech, HTTP API credentials)
- lrs           : subscriber radios with model_variant + rocket_id parent FK
- uisp_powers   : battery PDUs (api_username/password/port)
- uisp_switches : managed switches (max_ports, port_min_speed_mbps, ...)

Per project decision (2026-05-13), the existing rows are dropped and the seven
manual devices are re-created after deploy — the LR rows would have been
re-discovered automatically anyway, and no historical metrics depend on the
current device IDs.

The old `parent_id` self-FK on `devices` is removed: only LRs ever needed a
parent, and that relationship is now `lrs.rocket_id → rockets.id`.

Revision ID: e5f6a7b8c9d0
Revises: b7c8d9e0f1a2
Create Date: 2026-05-13 06:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Wipe all device rows. CASCADE on the FKs in device_metrics / incidents /
    # alerts / power_status_logs / alert_states removes the dependent rows.
    op.execute("TRUNCATE TABLE devices RESTART IDENTITY CASCADE;")

    # Drop columns that now live on a child table. CASCADE so PostgreSQL also
    # removes any constraint (FK, default, etc.) attached to the column —
    # belt-and-suspenders for the self-FK on parent_id whose name is not pinned.
    op.execute("ALTER TABLE devices DROP COLUMN parent_id CASCADE;")
    op.execute("ALTER TABLE devices DROP COLUMN model CASCADE;")
    op.execute("ALTER TABLE devices DROP COLUMN ssh_username CASCADE;")
    op.execute("ALTER TABLE devices DROP COLUMN ssh_password CASCADE;")
    op.execute("ALTER TABLE devices DROP COLUMN ssh_port CASCADE;")
    op.execute("ALTER TABLE devices DROP COLUMN ssh_host_fingerprint CASCADE;")
    op.execute("ALTER TABLE devices DROP COLUMN uisp_power_username CASCADE;")
    op.execute("ALTER TABLE devices DROP COLUMN uisp_power_password CASCADE;")
    op.execute("ALTER TABLE devices DROP COLUMN uisp_power_port CASCADE;")

    # rockets ────────────────────────────────────────────────────────────────
    op.create_table(
        "rockets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("radio_tech", sa.String(length=20), nullable=False),
        sa.Column("ssh_username", sa.String(length=100), nullable=True),
        sa.Column("ssh_password", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="443"),
        sa.Column("ssh_host_fingerprint", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # lrs ────────────────────────────────────────────────────────────────────
    op.create_table(
        "lrs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_variant", sa.String(length=30), nullable=False),
        sa.Column("rocket_id", sa.Integer(), nullable=True),
        sa.Column("ssh_username", sa.String(length=100), nullable=True),
        sa.Column("ssh_password", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("ssh_host_fingerprint", sa.String(length=255), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rocket_id"], ["rockets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lrs_rocket_id", "lrs", ["rocket_id"])

    # uisp_powers ────────────────────────────────────────────────────────────
    op.create_table(
        "uisp_powers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("api_username", sa.String(length=100), nullable=True),
        sa.Column("api_password", sa.String(length=255), nullable=True),
        sa.Column("api_port", sa.Integer(), nullable=False, server_default="443"),
        sa.ForeignKeyConstraint(["id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # uisp_switches ──────────────────────────────────────────────────────────
    op.create_table(
        "uisp_switches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("max_ports", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("rocket_port_index", sa.Integer(), nullable=True),
        sa.Column(
            "port_min_speed_mbps",
            sa.Float(),
            nullable=False,
            server_default="1000",
        ),
        sa.ForeignKeyConstraint(["id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("uisp_switches")
    op.drop_table("uisp_powers")
    op.drop_index("ix_lrs_rocket_id", table_name="lrs")
    op.drop_table("lrs")
    op.drop_table("rockets")

    op.add_column("devices", sa.Column("uisp_power_port", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("uisp_power_password", sa.String(length=255), nullable=True))
    op.add_column("devices", sa.Column("uisp_power_username", sa.String(length=100), nullable=True))
    op.add_column("devices", sa.Column("ssh_host_fingerprint", sa.String(length=255), nullable=True))
    op.add_column("devices", sa.Column("ssh_port", sa.Integer(), server_default="22", nullable=False))
    op.add_column("devices", sa.Column("ssh_password", sa.String(length=255), nullable=True))
    op.add_column("devices", sa.Column("ssh_username", sa.String(length=100), nullable=True))
    op.add_column("devices", sa.Column("model", sa.String(length=100), nullable=True))
    op.add_column("devices", sa.Column("parent_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "devices_parent_id_fkey", "devices", "devices",
        ["parent_id"], ["id"], ondelete="SET NULL",
    )
