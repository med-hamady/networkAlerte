"""backfill location of auto-discovered LRs from their parent Rocket

One-shot data migration. Les LR auto-découvertes doivent partager la
localisation de leur Rocket parent (par ex. un Rocket sur le site "AT2"
doit avoir toutes ses LR auto-découvertes sur "AT2"). Cette migration
rattrape les LR déjà en base dont la `location` ne correspond pas (ou
est NULL) — les futures découvertes sont déjà gérées en temps réel par
discovery_service et device_service.

Seules les LR `auto_discovered = TRUE` ET rattachées à un Rocket sont
touchées :
  - les LR créées manuellement (auto_discovered=FALSE) ne sont jamais
    écrasées (on respecte la saisie opérateur)
  - les LR orphelines (rocket_id IS NULL) gardent leur location

Revision ID: j1b2c3d4e5f6
Revises: i0a1b2c3d4e5
Create Date: 2026-05-17 23:55:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'j1b2c3d4e5f6'
down_revision: str | None = 'i0a1b2c3d4e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Joined-table inheritance :
    #   - `devices` porte les colonnes partagées (dont `location`, `auto_discovered`)
    #   - `lrs.rocket_id` pointe vers `rockets.id` (= `devices.id` du Rocket)
    # On aligne `devices.location` de chaque LR auto-découverte sur
    # `devices.location` de son Rocket parent.
    op.execute(
        sa.text(
            """
            UPDATE devices AS lr_dev
            SET location = rocket_dev.location
            FROM lrs
            JOIN devices AS rocket_dev ON rocket_dev.id = lrs.rocket_id
            WHERE lr_dev.id = lrs.id
              AND lr_dev.auto_discovered = TRUE
              AND lrs.rocket_id IS NOT NULL
              AND lr_dev.location IS DISTINCT FROM rocket_dev.location
            """
        )
    )


def downgrade() -> None:
    # Pas de rollback fiable — on ne sait pas quelle valeur de location
    # chaque LR avait avant ce rattrapage. No-op volontaire.
    pass
