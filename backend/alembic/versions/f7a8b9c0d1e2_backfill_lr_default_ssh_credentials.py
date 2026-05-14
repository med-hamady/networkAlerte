"""backfill default SSH credentials on all existing LRs

One-shot data migration. Toutes les LR côté client A2 partagent les mêmes
credentials SSH (ubnt / A2HQ@87654321). Cette migration les applique aux LR
déjà en base qui n'ont pas encore de credentials — les futures LR
auto-découvertes les reçoivent directement via discovery_service.

Seules les lignes où ssh_username ET ssh_password sont NULL sont touchées,
pour ne pas écraser un éventuel override opérateur déjà saisi via l'UI.

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-05-15 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'f7a8b9c0d1e2'
down_revision: str | None = 'e5f6a7b8c9d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE lrs
            SET ssh_username = :username,
                ssh_password = :password,
                ssh_port = COALESCE(ssh_port, 22)
            WHERE ssh_username IS NULL
              AND ssh_password IS NULL
            """
        ).bindparams(username="ubnt", password="A2HQ@87654321")
    )


def downgrade() -> None:
    # Pas de rollback fiable — on ne sait pas distinguer les credentials posés
    # par cette migration de ceux saisis manuellement après. No-op volontaire.
    pass
