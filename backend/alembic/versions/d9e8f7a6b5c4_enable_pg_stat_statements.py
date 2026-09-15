"""active pg_stat_statements — classement des requetes couteuses

Diagnostic du 2026-09-11 (scripts/diag-perf.sh) : 27 echantillons sur 49 de
requetes actives attendaient un verrou, et la phase « base » de la sonde LR
durait autant que ses sessions SSH — mais rien ne permettait de dire QUELLES
requetes coutent : l'extension n'etait pas installee.

⚠️ L'extension ne mesure rien tant que sa bibliotheque n'est pas CHARGEE au
demarrage du serveur : `shared_preload_libraries=pg_stat_statements`, pose dans
docker-compose.prod.yml. Creee sans elle (en dev par exemple), la vue existe
mais toute lecture echoue — ce qui ne casse rien d'autre.

⚠️ Creation CONDITIONNELLE, jamais bloquante : cette migration tourne au
demarrage du backend. Une extension absente de l'image, ou un role non
superutilisateur (elle n'est pas « trusted »), ferait sinon echouer toute la
chaine de migrations — donc refuser de demarrer l'API pour un simple outil de
diagnostic. Dans ces deux cas on journalise et on continue.

Revision ID: d9e8f7a6b5c4
Revises: b1c2d3e4f5a6
Create Date: 2026-09-15 12:00:00.000000

"""
import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9e8f7a6b5c4"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_stat_statements'")
    ).scalar()
    if not available:
        logger.warning("pg_stat_statements indisponible sur ce serveur : extension non creee.")
        return

    role, is_superuser = bind.execute(
        sa.text("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")
    ).one()
    if not is_superuser:
        logger.warning(
            "Role %s non superutilisateur : pg_stat_statements non creee "
            "(CREATE EXTENSION pg_stat_statements; a passer a la main).",
            role,
        )
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS pg_stat_statements")
