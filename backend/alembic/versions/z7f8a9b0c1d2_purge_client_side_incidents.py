"""purge client-side incidents (infrastructure-only /incidents page)

Policy 2026-06-09: the /incidents page surfaces INFRASTRUCTURE incidents only.
Client-side incidents — anything raised on a subscriber radio (a device in the
`lrs` table, rule_category == "lr") — are no longer created (see
incident_service.is_suppressed_incident) and must be purged from history.

Two exceptions, matching the runtime guard exactly:
  - lr_bridge_mode_misconfig is KEPT even on an LR (the operator must act on it —
    a bridge-mode LR silently breaks the client-block feature).
  - cpe_disconnected is PURGED even though it is raised on an infra Rocket: it
    signals a subscriber CPE vanished (client-side churn), not our outage.

This deletes the matching incidents and their audit-trail `alerts` rows. The
alerts FK references incidents with ON DELETE CASCADE, so deleting the incidents
is enough; we delete the alert rows explicitly first to stay correct on any DB
where the FK predates the cascade.

downgrade is a no-op — deleted rows cannot be reconstructed.

Revision ID: z7f8a9b0c1d2
Revises: y6e7f8a9b0c1
Create Date: 2026-06-09 12:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "z7f8a9b0c1d2"
down_revision: str | None = "y6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Incidents to purge: cpe_disconnected (any device), OR any incident on an LR
# device except lr_bridge_mode_misconfig. The NULL guard catches LR incidents
# whose alert_type is NULL (alert_type <> '...' is NULL → not deleted otherwise).
_PURGE_PREDICATE = """
    alert_type = 'cpe_disconnected'
    OR (
        device_id IN (SELECT id FROM lrs)
        AND (alert_type IS NULL OR alert_type <> 'lr_bridge_mode_misconfig')
    )
"""


def upgrade() -> None:
    # Remove notification audit rows attached to the purged incidents first…
    op.execute(
        f"""
        DELETE FROM alerts
        WHERE incident_id IN (
            SELECT id FROM incidents WHERE {_PURGE_PREDICATE}
        )
        """
    )
    # …then the incidents themselves.
    op.execute(f"DELETE FROM incidents WHERE {_PURGE_PREDICATE}")


def downgrade() -> None:
    # Irreversible: the purged client-side incidents cannot be reconstructed.
    pass
