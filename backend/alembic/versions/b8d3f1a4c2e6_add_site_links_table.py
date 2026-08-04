"""add site_links table (câblage inter-sites synchronisé depuis UISP)

Le maillage site→site n'existe que dans le contrôleur, et ses data-links
désignent leurs extrémités par l'id UISP de l'équipement — id que nous ne
stockons pas. Servir la page en interrogeant UISP à chaque affichage revenait à
télécharger ~1300 équipements, ~1400 sites et ~1300 liens par ouverture d'onglet
(et toutes les 2 min via le rafraîchissement automatique), pour une donnée qui ne
bouge que lorsque le terrain pose un backhaul.

Cette table porte le CÂBLAGE, synchronisé 1×/jour. La SANTÉ des liaisons n'y est
pas : elle reste relue en direct depuis devices/device_metrics.

Une ligne = UN LIEN PHYSIQUE (deux radios entre les mêmes sites = deux lignes) ;
le regroupement par paire de sites est fait à la lecture, pour que le décompte
des liens redondants reste exact.

Aucune donnée à reprendre : la table se remplit au premier passage du job
(qui tourne aussi 1× au démarrage du scheduler).

Revision ID: b8d3f1a4c2e6
Revises: f7b5396b55ea
Create Date: 2026-08-04

"""

import sqlalchemy as sa

from alembic import op

revision = "b8d3f1a4c2e6"
down_revision = "f7b5396b55ea"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "site_links",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        # Noms de sites VERBATIM (le parc porte « A2  ARF1 » avec un double
        # espace, et devices.site le stocke tel quel) ; ordonnés site_a <= site_b
        # à l'écriture pour qu'une liaison ne change pas d'identité selon le sens
        # de provisioning.
        sa.Column("site_a", sa.Text(), nullable=False),
        sa.Column("site_b", sa.Text(), nullable=False),
        sa.Column("link_type", sa.String(length=20), nullable=False),
        # État rapporté par UISP AU SYNC ("active"/"disconnected") — vieux de 24 h
        # au pire, il documente le provisioning et ne dit pas si la liaison est up
        # maintenant.
        sa.Column("state", sa.String(length=20), nullable=True),
        sa.Column("mac_a", sa.String(length=17), nullable=True),
        sa.Column("mac_b", sa.String(length=17), nullable=True),
        sa.Column("name_a", sa.String(length=255), nullable=False),
        sa.Column("name_b", sa.String(length=255), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_site_links_site_a", "site_links", ["site_a"])
    op.create_index("ix_site_links_site_b", "site_links", ["site_b"])


def downgrade() -> None:
    op.drop_index("ix_site_links_site_a", table_name="site_links")
    op.drop_index("ix_site_links_site_b", table_name="site_links")
    op.drop_table("site_links")
