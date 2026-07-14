"""fai: unblock enforcement + abandon on structural SSH failure

Deux colonnes sur `lrs`, l'une pour chaque moitié du contrat rendu symétrique :

  - `unblock_pending` : un déblocage a été enregistré mais n'a pas pu être appliqué
    (LR éteint). Le job de renforcement le rejoue jusqu'au succès. Sans ça, effacer
    `client_blocked` sortait le LR de la boucle de blocage et PLUS RIEN ne remontait
    son port → un client qui a payé restait coupé indéfiniment.
  - `block_unenforceable_reason` : le LR répond mais REFUSE la connexion SSH (mot de
    passe, host key). Réessayer toutes les 120 s n'y changera rien → on sort le LR de
    la boucle et on le signale (journal FAI) pour intervention technique.

Backfill : aucun. Les LR existants partent à `false` / `NULL` — soit exactement le
comportement d'avant (blocages rejoués, aucun abandon).

Revision ID: s7c8d9e0f1a2
Revises: r6b7c8d9e0f1
"""

import sqlalchemy as sa

from alembic import op

revision = "s7c8d9e0f1a2"
down_revision = "r6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column(
            "unblock_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "lrs",
        sa.Column("block_unenforceable_reason", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lrs", "block_unenforceable_reason")
    op.drop_column("lrs", "unblock_pending")
