"""backfill lan_interface='eth0.1' for existing LTU LRs

Field verification on real devices (2026-05-19) proved the client LAN port
differs by family:

  - LTU LR (model_variant in ltu_lr / ltu_instant / ltu_lite): the customer
    terminates on the VLAN sub-interface ``eth0.1`` (→ br1, 172.16.0.0/24).
    The physical ``eth0`` carries the management bridge (eth0.2 → br0), so
    shutting eth0 would lock the supervisor out — the dynamic guard refuses
    it, but a block then simply fails until lan_interface is corrected.
  - airMAX LiteBeam (litebeam_5ac / litebeam_m5): the customer terminates on
    plain ``eth0``, so the migration-default of ``eth0`` is already correct.

Discovery now sets the right default at creation. This migration corrects
already-discovered LRs whose lan_interface still holds the original default
``eth0`` — operator-customised values (anything else) are preserved.

Revision ID: m4e5f6a7b8c9
Revises: l3d4e5f6a7b8
Create Date: 2026-05-19 00:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "m4e5f6a7b8c9"
down_revision: str | None = "l3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Only flip rows still at the original default — never clobber an operator
    # who already set something else after a site-specific verification.
    op.execute(
        """
        UPDATE lrs
        SET lan_interface = 'eth0.1'
        WHERE lan_interface = 'eth0'
          AND model_variant IN ('ltu_lr', 'ltu_instant', 'ltu_lite')
        """
    )


def downgrade() -> None:
    # Symmetric reversal — revert only the rows we likely touched.
    op.execute(
        """
        UPDATE lrs
        SET lan_interface = 'eth0'
        WHERE lan_interface = 'eth0.1'
          AND model_variant IN ('ltu_lr', 'ltu_instant', 'ltu_lite')
        """
    )
