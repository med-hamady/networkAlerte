"""set fiber_port_index on the three fibre-fed site switches (AT1/CT1/ARF1)

Data migration that designates the fibre uplink SFP port of the three sites that
reach the network over fibre, so the fiber_link_down alert starts watching them
without going through the edit form. Targeted by the switches' management IP
(unique — no risk of a wrong/duplicate match):

    AT1  (10.135.2.108) -> port 9
    CT1  (10.135.2.31)  -> port 25
    ARF1 (10.135.2.209) -> port 25

For CT1/ARF1 the fibre lands on ifIndex 25 (24-port switch + SFP uplinks). The
SNMP poll only walks ports 1..max_ports, so we also bump max_ports to at least 25
on those two — otherwise port_25_up is never collected and the alert would stay
silent.

An IP that no longer matches a switch row simply updates 0 rows (harmless) and
can still be set from the form. Idempotent.

Revision ID: z8a9b0c1d2e3
Revises: y7z8a9b0c1d2
Create Date: 2026-07-27 00:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "z8a9b0c1d2e3"
down_revision: str | None = "y7z8a9b0c1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (management IP, fibre SFP port index)
_FIBRE_SWITCHES = [
    ("10.135.2.108", 9),    # AT1
    ("10.135.2.31", 25),    # CT1
    ("10.135.2.209", 25),   # ARF1
]

_ALL_IPS = ", ".join(f"'{ip}'" for ip, _ in _FIBRE_SWITCHES)


def _switch_id(ip: str) -> str:
    """Subquery: id of the UISP switch at this management IP."""
    return (
        "SELECT id FROM devices "
        f"WHERE device_type = 'uisp_switch' AND ip_address = '{ip}'"
    )


def upgrade() -> None:
    for ip, port in _FIBRE_SWITCHES:
        if port <= 16:
            op.execute(
                f"UPDATE uisp_switches SET fiber_port_index = {port} "
                f"WHERE id IN ({_switch_id(ip)})"
            )
        else:
            # Ensure the SNMP poll scans far enough to see this port.
            op.execute(
                f"UPDATE uisp_switches "
                f"SET fiber_port_index = {port}, max_ports = GREATEST(max_ports, {port}) "
                f"WHERE id IN ({_switch_id(ip)})"
            )


def downgrade() -> None:
    # Clear only the fibre designation; leave the widened max_ports (harmless).
    op.execute(
        "UPDATE uisp_switches SET fiber_port_index = NULL "
        f"WHERE id IN (SELECT id FROM devices "
        f"WHERE device_type = 'uisp_switch' AND ip_address IN ({_ALL_IPS}))"
    )
