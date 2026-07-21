"""dégèle le statut des devices sans IP (faux « EN LIGNE » perpétuel)

Un LR qui perd son IP au churn DHCP (``discovery_service._release_ip_if_held``
la nulle pour la rendre au CPE qui l'a prise) sort du sweep de ping : celui-ci
filtre ``ip_address IS NOT NULL`` — il n'y a rien à pinguer. Conséquence non
voulue : PLUS RIEN n'écrit son ``status``, qui reste figé sur la dernière valeur
connue. Un LR qui avait son IP libérée alors qu'il était ``up`` s'affichait donc
« EN LIGNE » indéfiniment, avec une ``last_seen`` qui vieillissait sans fin
(constaté sur un LiteBeam 5AC vu « en ligne » avec une dernière vue à 17 h).

Le code ne remet plus que les NOUVEAUX cas à ``unknown``. Cette migration
rattrape les lignes déjà figées en base, qu'aucun job ne repassera jamais.

⚠️ L'UPDATE porte sur ``devices``, la table PARENTE : passer par une sous-classe
(``lrs``) sur une colonne du parent est le piège du UPDATE joined-table.

Revision ID: aa1b2c3d4e5f
Revises: z9k0l1m2n3o4
Create Date: 2026-07-21
"""

import sqlalchemy as sa

from alembic import op

revision = "aa1b2c3d4e5f"
down_revision = "z9k0l1m2n3o4"
branch_labels = None
depends_on = None


# Sentinelle du compteur d'échecs de ping (cf. alert_constants.PING_FAILURE_STATE_KEY).
# Recopiée en dur : une migration doit rester lisible à sa date, sans dépendre
# de la valeur courante d'une constante applicative.
_PING_FAILURE_STATE_KEY = "_ping_failures"


def upgrade() -> None:
    conn = op.get_bind()

    # Le volume est celui du parc (~10³ lignes au plus), pas celui des métriques :
    # un seul UPDATE borné, sans batch — rien à voir avec les purges de
    # device_metrics qui, elles, doivent rester hors des migrations de démarrage.
    res = conn.execute(
        sa.text(
            "UPDATE devices SET status = 'unknown' "
            "WHERE ip_address IS NULL AND status <> 'unknown'"
        )
    )
    unfrozen = res.rowcount or 0

    # Leur compteur anti-flap est obsolète : il a cessé d'être incrémenté au
    # moment où le device est sorti du sweep. Le laisser ferait basculer
    # « down » dès le premier paquet perdu après redécouverte.
    conn.execute(
        sa.text(
            "DELETE FROM alert_states WHERE alert_type = :k AND device_id IN ("
            "  SELECT id FROM devices WHERE ip_address IS NULL"
            ")"
        ),
        {"k": _PING_FAILURE_STATE_KEY},
    )

    # ASCII pur : la sortie de migration part sur un stdout dont l'encodage n'est
    # pas garanti (console Windows en cp1252 -> UnicodeEncodeError, migration en
    # echec sur un caractere decoratif).
    print(f"[aa1b2c3d4e5f] {unfrozen} device(s) sans IP degele(s): status='unknown'")


def downgrade() -> None:
    # Irréversible par nature : le statut d'avant était précisément la valeur
    # périmée qu'on corrige, et elle n'est conservée nulle part.
    pass
