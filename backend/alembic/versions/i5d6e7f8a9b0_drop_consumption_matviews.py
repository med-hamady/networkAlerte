"""Suppression des matviews de consommation 7 j / 30 j

Les fenêtres 7 j et 30 j de `/clients` sont servies depuis le RÉSUMÉ QUOTIDIEN
(`client_consumption_daily`, migration h4c5d6e7f8a9) : des totaux déjà
calculés, recousus avec la journée en cours par
`consumption_service._aggregate_via_daily`.

Pourquoi les supprimer plutôt que les laisser dormir :

1. **Elles empêchaient la rétention.** Chaque REFRESH relit la fenêtre entière
   de `device_metrics` — 30 jours pour l'une. Tant qu'elles existent, purger
   les relevés bruts au-delà de 7 jours ferait afficher à `/clients` une
   consommation de 30 jours calculée sur 7, c.-à-d. un chiffre FAUX sans la
   moindre erreur pour le dire.

2. **Leur REFRESH coûtait >19 min d'E/S** (mesuré le 2026-07-20 : planifié
   toutes les 15 min, il tournait EN PERMANENCE, faisait passer la phase 2 de
   la sonde LR à ~40 min/tour et rendait `ltu_api_poll` à 0/60 Rockets). Il
   avait été ramené à un cron quotidien ; il disparaît ici.

3. Une matview laissée en place se serait vidée petit à petit au fil de la
   rétention, en silence, et le premier qui aurait rebranché une lecture
   dessus aurait servi des totaux amputés.

⚠️ Aucune donnée n'est perdue : une matview ne contient que le résultat d'un
calcul, et ce calcul est désormais celui du résumé quotidien.

⚠️ `downgrade()` les recrée à l'identique (définitions reprises des migrations
p7b8c9d0e1f2 et q8c9d0e1f2a3) mais les rend VIDES : un `REFRESH` est
nécessaire, et il ne retrouvera que ce que la rétention a laissé dans
`device_metrics`.

Revision ID: i5d6e7f8a9b0
Revises: h4c5d6e7f8a9
"""

from alembic import op

revision = "i5d6e7f8a9b0"
down_revision = "h4c5d6e7f8a9"
branch_labels = None
depends_on = None


# Reprise littérale des deux définitions d'origine, pour que le downgrade
# restaure exactement ce qui existait et pas une approximation.
_MATVIEW_SQL = """
CREATE MATERIALIZED VIEW {name} AS
SELECT
    device_id,
    metric_name,
    SUM(CASE WHEN d IS NOT NULL AND d >= 0 AND d <= 8589934592
             THEN d ELSE 0 END) AS bytes,
    COUNT(*)          AS samples,
    MIN(collected_at) AS first_sample_at
FROM (
    SELECT
        device_id,
        metric_name,
        collected_at,
        metric_value - LAG(metric_value) OVER w AS d
    FROM device_metrics
    WHERE metric_name IN ('peer_tx_bytes', 'peer_rx_bytes',
                          'radio_rx_bytes', 'radio_tx_bytes')
      AND collected_at >= now() - interval '{window}'
    WINDOW w AS (PARTITION BY device_id, metric_name ORDER BY collected_at)
) deltas
GROUP BY device_id, metric_name
WITH NO DATA
"""


def upgrade() -> None:
    # IF EXISTS : une base montée après le 2026-09-25 ne les a jamais eues.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS client_consumption_30d")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS client_consumption_7d")


def downgrade() -> None:
    op.execute(_MATVIEW_SQL.format(name="client_consumption_30d", window="30 days"))
    op.execute(
        "CREATE UNIQUE INDEX ix_client_consumption_30d_pk "
        "ON client_consumption_30d (device_id, metric_name)"
    )
    op.execute(_MATVIEW_SQL.format(name="client_consumption_7d", window="7 days"))
    op.execute(
        "CREATE UNIQUE INDEX ix_client_consumption_7d_pk "
        "ON client_consumption_7d (device_id, metric_name)"
    )
