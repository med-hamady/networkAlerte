"""add manual_alerts table (bandeau d'anomalies à acquitter à la main)

Trois anomalies — dégradation d'un backhaul F60, vitesse d'un port de switch
dégradée, équipement d'infra instable — sont désormais répétées dans un bandeau
en haut du dashboard, où elles ne disparaissent QUE sur un clic « Résoudre » de
l'opérateur.

⚠️ Pourquoi une table et pas une lecture de `incidents` : un incident
non-disponibilité est HARD-DELETE à sa résolution automatique (il n'y a pas de
vue /archive). Un bandeau bâti sur `incidents` verrait donc sa ligne s'évaporer
dès que le port renégocie à 1 Gb/s ou que l'équipement cesse de flapper —
c.-à-d. sans que personne ait cliqué, soit l'exact contraire de ce qui est
demandé. La ligne doit SURVIVRE à la résolution de l'incident qui l'a fait
naître, donc vivre ailleurs.

Le cycle de vie des incidents n'est PAS touché : les trois types continuent de
s'ouvrir, de se résoudre seuls au retour à la normale, d'être purgés et
notifiés exactement comme avant. Ce canal est parallèle.

Aucune donnée à reprendre : la table se remplit à la prochaine détection. Les
anomalies déjà ouvertes au déploiement n'y entrent pas (leur incident existe
déjà, donc `open_incident` ne les rouvrira pas) — elles apparaîtront à leur
prochaine récidive. Rien à rattraper : elles restent visibles sur /incidents.

Revision ID: d4a5b6c7e8f9
Revises: b8d3f1a4c2e6
Create Date: 2026-08-12

"""

import sqlalchemy as sa

from alembic import op

revision = "d4a5b6c7e8f9"
down_revision = "b8d3f1a4c2e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "manual_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        # Libellés COPIÉS de l'incident à la détection : l'incident d'origine
        # est purgé à sa résolution, son titre ne serait donc plus lisible.
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        # NULL = encore dans le bandeau.
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=150), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_manual_alerts_device_id", "manual_alerts", ["device_id"])
    # La seule lecture chaude est « les non acquittées », servie à chaque
    # chargement de page du dashboard : index PARTIEL, qui ne porte donc que les
    # quelques lignes en attente et non tout l'historique conservé.
    op.create_index(
        "ix_manual_alerts_pending",
        "manual_alerts",
        ["detected_at"],
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_manual_alerts_pending", table_name="manual_alerts")
    op.drop_index("ix_manual_alerts_device_id", table_name="manual_alerts")
    op.drop_table("manual_alerts")
