"""suppression de l'alerte cpe_disconnected — ménage de ses traces en base

« Rocket LTU sans aucun CPE connecté ». La règle tournait à chaque poll, mais
son incident était supprimé en toutes circonstances (churn côté abonné, pas une
panne de notre réseau) : elle ne produisait plus que ses compteurs d'anti-flap.
Un secteur réellement mort reste couvert par `radio_interface_down` et
`rocket_down`. Même démarche que `j6e7f8a9b0c1` (rocket_client_overload).

Downgrade : rien à restaurer, un compteur d'anti-flap se reconstruit.
"""

from alembic import op

revision = "k7f8a9b0c1d2"
down_revision = "j6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM alert_states WHERE alert_type = 'cpe_disconnected'")
    op.execute("DELETE FROM incidents WHERE alert_type = 'cpe_disconnected'")


def downgrade() -> None:
    pass
