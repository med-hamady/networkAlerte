"""purge lr_down incidents

The `lr_down` alert type was removed entirely: a LR that stops answering ICMP
is a subscriber-side outage (client power cut / LR unplugged), not a fault of
our infrastructure, so it must never raise an incident. See device_ping_job.

This data migration deletes every historical incident (and its audit-trail
alert rows) carrying alert_type='lr_down'. Alert rows reference incidents with
ON DELETE CASCADE, so deleting the incidents is enough; we delete them
explicitly first anyway to stay correct on any DB where the FK predates the
cascade.

downgrade is a no-op — deleted rows cannot be restored.

Revision ID: r9d0e1f2a3b4
Revises: q8c9d0e1f2a3
Create Date: 2026-06-02 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "r9d0e1f2a3b4"
down_revision: str | None = "q8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove notification audit rows attached to lr_down incidents first…
    op.execute(
        """
        DELETE FROM alerts
        WHERE incident_id IN (
            SELECT id FROM incidents WHERE alert_type = 'lr_down'
        )
        """
    )
    # …then the incidents themselves.
    op.execute("DELETE FROM incidents WHERE alert_type = 'lr_down'")


def downgrade() -> None:
    # Irreversible: the purged lr_down incidents cannot be reconstructed.
    pass
