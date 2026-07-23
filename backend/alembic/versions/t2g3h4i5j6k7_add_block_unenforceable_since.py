"""fai: horodatage de l'abandon du blocage (self-heal lent)

Une colonne `block_unenforceable_since` sur `lrs` : quand l'abandon structurel
courant (mauvais mot de passe, clé d'hôte) a été posé. Le job de renforcement
s'en sert pour retenter un LR abandonné une fois toutes les
`client_block_abandon_retry_hours` (défaut 6 h), au lieu de le sauter pour
toujours. Objectif : un LR qui se répare seul — re-flashé (nouvelle clé d'hôte
ré-épinglée sur confirmation de la MAC dans `ssh_service._open_transport`), ou
corrigé hors bande — revient tout seul, sans désabandon manuel.

Backfill : aucun. Les LR déjà abandonnés partent à `NULL`, ce que
`_abandon_retry_due` lit comme « ré-essai dû tout de suite » → ils récupèrent
au cycle de renforcement suivant le déploiement, puis l'horodatage est posé à
chaque nouvel abandon.

Revision ID: t2g3h4i5j6k7
Revises: dd4e5f6a7b8c
"""

import sqlalchemy as sa

from alembic import op

revision = "t2g3h4i5j6k7"
down_revision = "dd4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column(
            "block_unenforceable_since", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("lrs", "block_unenforceable_since")
