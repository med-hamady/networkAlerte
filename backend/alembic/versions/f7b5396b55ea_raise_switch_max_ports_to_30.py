"""raise uisp_switches.max_ports to 30 (SNMP scan window, not a port count)

`max_ports` is the SNMP scan WINDOW: `collect_switch_port_metrics` reads ifIndex
1..max_ports, and anything beyond is simply never measured — a port outside it is
invisible even when it goes DOWN. The default was 16, which left real ports
unmeasured on the fleet:
  - `UISP-S-Pro` units are 24 RJ45 + 4 SFP+, i.e. ifIndex 1..28
  - PK1 carries `F60 PK1-CT2` on port 18, outside the window until the mapping job
    widened it automatically
  - the fibre SFP of CT1/ARF1 sits at ifIndex 25 and had to be exposed by hand in
    an earlier migration (`z8a9b0c1d2e3`) for `fiber_link_down` to see anything

30 covers every port of every model in the fleet, SFP+ cages included, with a
little headroom. Chosen over 28 so a slightly larger model needs no migration, and
well under the form's ceiling of 64.

⚠️ This widens COLLECTION only. It cannot create alerts on its own: the port rules
evaluate `switch_port_service.watched_ports` (ports proven to carry a supervised
device) plus the manual `rocket_port_index` — never "every scanned port". Metrics
for the extra indexes are collapsed latest-only in `device_metrics`, so there is no
storage growth per cycle either.

Raise only, never lower: a switch an operator set above 30 keeps its value.

Revision ID: f7b5396b55ea
Revises: ee5f6a7b8c9d
Create Date: 2026-07-30 11:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "f7b5396b55ea"
down_revision: str | None = "ee5f6a7b8c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE uisp_switches SET max_ports = 30 WHERE max_ports < 30")


def downgrade() -> None:
    # No way back: the previous per-switch values are not recorded, and 16 was a
    # default rather than a measured value. Left as-is on purpose — narrowing the
    # window again would only blind the monitoring.
    pass
