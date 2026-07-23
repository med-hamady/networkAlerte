"""diag SSH par LR : ssh_status / ssh_error / ssh_checked_at

Trois colonnes sur `lrs` renseignées par `lr_internet_probe_job` (qui ouvre déjà
une session SSH sur chaque LR up à chaque cycle — la capture est gratuite). Elles
distinguent « le LR REFUSE le SSH » (mot de passe invalide, SSH désactivé, clé
d'hôte incompatible → action technique requise) de « le LR est simplement hors
ligne » (déjà couvert par device_ping_job). Alimente la page « Diagnostics
d'accès », qui ne remonte que les refus sur des LR encore up.

Backfill : aucun. `ssh_status` part à NULL (jamais sondé) et se remplit au
premier tour de sonde après le déploiement.

Revision ID: u3h4i5j6k7l8
Revises: t2g3h4i5j6k7
"""

import sqlalchemy as sa

from alembic import op

revision = "u3h4i5j6k7l8"
down_revision = "t2g3h4i5j6k7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lrs", sa.Column("ssh_status", sa.String(length=20), nullable=True))
    op.add_column("lrs", sa.Column("ssh_error", sa.String(length=255), nullable=True))
    op.add_column(
        "lrs",
        sa.Column("ssh_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("lrs", "ssh_checked_at")
    op.drop_column("lrs", "ssh_error")
    op.drop_column("lrs", "ssh_status")
