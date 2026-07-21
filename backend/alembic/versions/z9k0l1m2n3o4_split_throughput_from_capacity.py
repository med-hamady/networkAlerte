"""split throughput from capacity: rename metric keys, drop ideal capacity

Le débit affiché sur la fiche équipement était lu dans ``capacity.*``, c.-à-d.
la CAPACITÉ du lien. « Débit DL » et « Capacité DL » sortaient donc de la même
source et affichaient la même valeur, alors que le dashboard Ubiquiti montre
deux séries distinctes (140 Mb/s de capacité pour 94 kb/s de trafic réel).

Cette migration aligne les données existantes sur le nouveau nommage :

  tx_rate_mbps  → dl_capacity_mbps    (c'ÉTAIT la capacité descendante)
  rx_rate_mbps  → ul_capacity_mbps    (c'ÉTAIT la capacité montante)

Le renommage préserve l'historique des courbes : ces relevés restent
sémantiquement justes, seul leur nom était trompeur. Les nouvelles clés
``dl_throughput_mbps`` / ``ul_throughput_mbps`` démarrent sans historique — il
n'existe aucune mesure de débit réel à rétro-remplir, et fabriquer une série à
partir des capacités reproduirait exactement le bug corrigé.

Sont supprimés :
  - ``tx_ideal_mbps`` / ``rx_ideal_mbps`` : la capacité idéale n'est plus
    affichée ni collectée (décision produit : montrer la capacité réelle).
  - les incidents ``capacity_low`` / ``capacity_ul_low`` : leurs règles
    reposaient sur le ratio réel/idéal et ont été supprimées avec lui.
  - la règle ``throughput_anomaly`` : ses incidents, ses compteurs anti-flap et
    ses baselines EMA (``_throughput_ema``). La règle est **entièrement
    supprimée** — elle ne surveillait pas ce que son nom indiquait (sur un
    Rocket elle lisait le taux de modulation PHY du SNMP airMAX) et plus aucun
    consommateur ne subsiste.

Revision ID: z9k0l1m2n3o4
Revises: y3i4j5k6l7m8
Create Date: 2026-07-20
"""

import sqlalchemy as sa

from alembic import op

revision = "z9k0l1m2n3o4"
down_revision = "y3i4j5k6l7m8"
branch_labels = None
depends_on = None


# (ancien nom, nouveau nom)
_RENAMES = (
    ("tx_rate_mbps", "dl_capacity_mbps"),
    ("rx_rate_mbps", "ul_capacity_mbps"),
)

# Métriques dont plus aucun consommateur n'existe.
_DROPPED_METRICS = ("tx_ideal_mbps", "rx_ideal_mbps")

_DROPPED_ALERT_TYPES = ("capacity_low", "capacity_ul_low", "throughput_anomaly")

# Baselines EMA de throughput_anomaly, stockées dans alert_states sous ce
# pseudo alert_type (et non sous le nom de l'alerte, d'où la purge séparée).
# La règle étant supprimée, plus rien ne les lit ni ne les nettoie.
_EMA_STATE_KEY = "_throughput_ema"


def upgrade() -> None:
    conn = op.get_bind()

    # device_metrics est en "collapse" (1 ligne par (device_id, metric_name)) :
    # un UPDATE du nom entrerait en collision avec une ligne déjà écrite sous le
    # nouveau nom par un scheduler qui tourne pendant la migration. On supprime
    # d'abord la cible éventuelle, puis on renomme.
    for old, new in _RENAMES:
        conn.execute(
            sa.text(
                "DELETE FROM device_metrics WHERE metric_name = :new "
                "AND device_id IN ("
                "  SELECT device_id FROM device_metrics WHERE metric_name = :old"
                ")"
            ),
            {"new": new, "old": old},
        )
        conn.execute(
            sa.text("UPDATE device_metrics SET metric_name = :new WHERE metric_name = :old"),
            {"new": new, "old": old},
        )
        # lr_metric_samples porte l'historique des courbes : ici on renomme sans
        # perte, l'unicité est (device_id, metric_name, bucket_start) et la
        # nouvelle clé n'a encore aucune ligne.
        conn.execute(
            sa.text("UPDATE lr_metric_samples SET metric_name = :new WHERE metric_name = :old"),
            {"new": new, "old": old},
        )

    for name in _DROPPED_METRICS:
        conn.execute(
            sa.text("DELETE FROM device_metrics WHERE metric_name = :name"), {"name": name}
        )
        conn.execute(
            sa.text("DELETE FROM lr_metric_samples WHERE metric_name = :name"), {"name": name}
        )

    for alert_type in _DROPPED_ALERT_TYPES:
        conn.execute(
            sa.text("DELETE FROM incidents WHERE alert_type = :t"), {"t": alert_type}
        )
        conn.execute(
            sa.text("DELETE FROM alert_states WHERE alert_type = :t"), {"t": alert_type}
        )

    conn.execute(
        sa.text("DELETE FROM alert_states WHERE alert_type = :t"), {"t": _EMA_STATE_KEY}
    )


def downgrade() -> None:
    """Renommage inverse uniquement.

    Les capacités idéales et les incidents capacity_low ne sont pas restaurés :
    la donnée est détruite, pas déplacée.
    """
    conn = op.get_bind()
    for old, new in _RENAMES:
        conn.execute(
            sa.text("UPDATE device_metrics SET metric_name = :old WHERE metric_name = :new"),
            {"old": old, "new": new},
        )
        conn.execute(
            sa.text("UPDATE lr_metric_samples SET metric_name = :old WHERE metric_name = :new"),
            {"old": old, "new": new},
        )
