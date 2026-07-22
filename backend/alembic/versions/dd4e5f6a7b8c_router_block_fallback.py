"""fai: repli du blocage client sur le routeur MikroTik

Deux colonnes sur `lrs` pour le filet de sécurité du blocage : quand la coupure
SSH sur le LR n'aboutit pas (LR éteint, SSH refusé, mot de passe rejeté), une
règle drop est posée sur le routeur de cœur, qui coupe sans dépendre de
l'équipement du client. Run du 2026-07-14 : 163 clients sur 222 étaient dans ce
cas, donc non coupés.

  - `router_blocked` : une règle est POSÉE sur le routeur pour ce client.
  - `router_blocked_at` : quand.

L'état DÉSIRÉ n'est délibérément pas stocké : il se dérive de l'existant
(`client_blocked` ET `client_block_enforced_at IS NULL`). Ces colonnes ne
mémorisent que ce qui est en place, pour n'appeler le routeur que sur transition
— sans elles, la réconciliation interrogerait le routeur pour chaque client
bloqué à chaque cycle de 120 s.

Backfill : aucun. Tous les LR partent à `false` / `NULL`, donc le premier cycle
du job pose les règles manquantes pour les clients déjà bloqués et non coupés.

Revision ID: dd4e5f6a7b8c
Revises: cc3d4e5f6a7b
"""

import sqlalchemy as sa

from alembic import op

revision = "dd4e5f6a7b8c"
down_revision = "cc3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lrs",
        sa.Column(
            "router_blocked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "lrs",
        sa.Column("router_blocked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lrs", "router_blocked_at")
    op.drop_column("lrs", "router_blocked")
