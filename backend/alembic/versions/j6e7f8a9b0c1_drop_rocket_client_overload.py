"""suppression de l'alerte rocket_client_overload — ménage de ses traces en base

La règle « Rocket saturé » tournait encore à chaque poll, mais son incident
était supprimé depuis le 2026-06-25 (migration `l9a0b1c2d3e4`) : elle ne
produisait plus rien que ses compteurs d'anti-flap. La saturation vit sur
/capacity et dans le rapport PDF quotidien.

Ne reste donc à nettoyer que les lignes `alert_states` de ce type (plus aucun
code ne les lit ni ne les écrit), plus d'éventuels incidents résiduels par
sécurité. Downgrade : rien à restaurer, un compteur d'anti-flap se reconstruit.
"""

from alembic import op

revision = "j6e7f8a9b0c1"
down_revision = "i5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM alert_states WHERE alert_type = 'rocket_client_overload'")
    op.execute("DELETE FROM incidents WHERE alert_type = 'rocket_client_overload'")


def downgrade() -> None:
    pass
