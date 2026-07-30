"""add uplink_switch_id / uplink_switch_port to devices (auto-detected wiring)

Port-level switch monitoring used to watch exactly ONE port per switch, the one
an operator typed into `uisp_switches.rocket_port_index` — a column no code ever
filled, so it is NULL everywhere and the `switch_port_down` /
`switch_port_speed_low` rules never fired on any switch.

Filling that single column would not have been enough either: a site carries up
to SITE_INFRA_MAX (14) infra devices behind one switch, so one column can only
ever designate one of them.

These columns record, per device, which switch port it is physically plugged
into — discovered from the switch's MAC forwarding table (BRIDGE-MIB) by
switch_port_service, never typed by hand. Both NULL = wiring unknown, that port
is simply not watched (no noise on unused or third-party ports).

`uplink_switch_port` is an IF-MIB ifIndex, i.e. the same numbering as the
`port_N_up` / `port_N_speed_mbps` metrics already collected by snmp_poll_job.

ON DELETE SET NULL: removing a switch must not cascade-delete the Rockets
plugged into it — it just makes their wiring unknown again until the next
detection pass.

Revision ID: ee5f6a7b8c9d
Revises: c9d0e1f2a3b4
Create Date: 2026-07-29 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ee5f6a7b8c9d"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("uplink_switch_id", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("uplink_switch_port", sa.Integer(), nullable=True))
    op.add_column(
        "devices",
        sa.Column("uplink_detected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_devices_uplink_switch_id",
        "devices", "devices",
        ["uplink_switch_id"], ["id"],
        ondelete="SET NULL",
    )
    # The switch-port evaluation loads "every device plugged into THIS switch"
    # once per switch per SNMP cycle.
    op.create_index(
        "ix_devices_uplink_switch_id", "devices", ["uplink_switch_id"],
        postgresql_where=sa.text("uplink_switch_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_devices_uplink_switch_id", table_name="devices")
    op.drop_constraint("fk_devices_uplink_switch_id", "devices", type_="foreignkey")
    op.drop_column("devices", "uplink_detected_at")
    op.drop_column("devices", "uplink_switch_port")
    op.drop_column("devices", "uplink_switch_id")
