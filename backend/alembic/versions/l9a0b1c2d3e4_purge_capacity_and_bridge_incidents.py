"""purge rocket_client_overload + lr_bridge_mode_misconfig incidents

Policy 2026-06-25: Rocket saturation (rocket_client_overload) is now owned by the
/capacity page and LR bridge-mode (lr_bridge_mode_misconfig) by the /access page.
Neither is created nor stored as an /incidents row anymore — both are added to
INFRA_DEVICE_SUPPRESSED_ALERT_TYPES (incident_service.is_suppressed_incident).
Existing rows of these two types must be purged from history.

The `alerts` audit table was already dropped (migration a8b9c0d1e2f3), so there is
nothing else to clean up — we only delete the incidents themselves.

downgrade is a no-op — deleted rows cannot be reconstructed.

Revision ID: l9a0b1c2d3e4
Revises: k8f9a0b1c2d3
Create Date: 2026-06-25 00:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "l9a0b1c2d3e4"
down_revision: str | None = "k8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM incidents
        WHERE alert_type IN ('rocket_client_overload', 'lr_bridge_mode_misconfig')
        """
    )


def downgrade() -> None:
    # Irreversible: the purged incidents cannot be reconstructed.
    pass
