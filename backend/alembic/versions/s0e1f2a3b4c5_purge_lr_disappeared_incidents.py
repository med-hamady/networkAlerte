"""purge lr_disappeared incidents

The `lr_disappeared` alert type was removed entirely: a LR that stops appearing
in any Rocket peer-list is a subscriber-side outage (client power cut / LR
unplugged), not a fault of our infrastructure, so it must never raise an
incident — same rationale as the removed `lr_down`. The stale_lr_detection_job
that opened it has been deleted.

This data migration deletes every historical incident (and its audit-trail
alert rows) carrying alert_type='lr_disappeared'. Alert rows reference incidents
with ON DELETE CASCADE, so deleting the incidents is enough; we delete them
explicitly first anyway to stay correct on any DB where the FK predates the
cascade.

downgrade is a no-op — deleted rows cannot be restored.

Revision ID: s0e1f2a3b4c5
Revises: r9d0e1f2a3b4
Create Date: 2026-06-02 14:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "s0e1f2a3b4c5"
down_revision: str | None = "r9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove notification audit rows attached to lr_disappeared incidents first…
    op.execute(
        """
        DELETE FROM alerts
        WHERE incident_id IN (
            SELECT id FROM incidents WHERE alert_type = 'lr_disappeared'
        )
        """
    )
    # …then the incidents themselves.
    op.execute("DELETE FROM incidents WHERE alert_type = 'lr_disappeared'")


def downgrade() -> None:
    # Irreversible: the purged lr_disappeared incidents cannot be reconstructed.
    pass
