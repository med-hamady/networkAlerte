"""Consommation client totalisée par journée

Crée `client_consumption_daily` : une ligne par (équipement, compteur, jour UTC)
portant les octets consommés ce jour-là.

Pourquoi : la consommation se CALCULE par différences successives sur les
compteurs cumulés de `device_metrics`, ce qui obligeait à conserver tous les
relevés indéfiniment (58,4 M lignes / 5,9 Go au 2026-09-24, seule table du
projet sans aucune rétention). Une journée écoulée ne change plus : on fait la
soustraction une fois, la nuit suivante, et on garde le total.

Cette migration ne fait que créer la table — elle ne remplit rien et ne
supprime rien. Le remplissage de l'historique se fait par
`scripts/backfill_consumption_daily.py`, et la rétention sur `device_metrics`
reste désactivée tant que les totaux n'ont pas été vérifiés.

Revision ID: h4c5d6e7f8a9
Revises: g3b4c5d6e7f8
"""

import sqlalchemy as sa
from alembic import op

revision = "h4c5d6e7f8a9"
down_revision = "g3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_consumption_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("metric_name", sa.String(length=100), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        # BigInteger : un client à 100 Mb/s dépasse le milliard d'octets en
        # 2 minutes, et on totalise une journée entière.
        sa.Column("bytes", sa.BigInteger(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("samples", sa.Integer(), nullable=False,
                  server_default=sa.text("0")),
        sa.Column("first_sample_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "device_id", "metric_name", "day",
            name="uq_client_consumption_daily_device_metric_day",
        ),
    )
    # Les lectures portent sur une plage de JOURS, tous équipements confondus ;
    # la contrainte unique commence par device_id et ne peut pas les servir.
    op.create_index(
        "ix_client_consumption_daily_day", "client_consumption_daily", ["day"],
    )

    # Même réglage d'autovacuum que les autres tables à fort renouvellement :
    # le défaut (20 % de la table morte avant nettoyage) a laissé
    # `lr_metric_samples` et `traffic_dest_stats` gonfler pendant des mois
    # (57 Go de base dont 17 de vide, constaté le 2026-09-24). Ici le
    # renouvellement vient des upserts de rattrapage et de la future rétention.
    op.execute(
        "ALTER TABLE client_consumption_daily SET ("
        "autovacuum_vacuum_scale_factor=0.0, "
        "autovacuum_vacuum_threshold=5000, "
        "autovacuum_vacuum_cost_delay=0)"
    )


def downgrade() -> None:
    op.drop_index("ix_client_consumption_daily_day",
                  table_name="client_consumption_daily")
    op.drop_table("client_consumption_daily")
