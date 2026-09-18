"""Profils d'accès : table `profiles` + `users.profile_id`

⚠️ **Le vrai risque de cette migration n'est pas le schéma, c'est de verrouiller
tout le monde dehors.** À partir de son application, chaque route exige une
permission, et un compte sans profil n'a AUCUN droit
(`profile_service.effective_permissions` — le sens sûr, choisi pour qu'un compte
orphelin ne devienne pas administrateur par accident). Les comptes existants,
créés avant l'arrivée des profils, se retrouveraient donc devant un dashboard
vide, sans aucun moyen de se réparer par l'interface : la section qui affecte un
profil demande elle-même `admin.users`.

D'où les deux gestes de données, indissociables de la création des colonnes :

  1. créer le profil SYSTÈME « Administrateur » (`is_system=true`) — celui qui
     détient tout par construction, sans liste de cases cochées ;
  2. y rattacher **tous** les comptes existants.

C'est la lecture fidèle de l'existant : jusqu'ici, tout compte capable de se
connecter avait le contrôle total. La migration ne retire donc rien à personne —
elle nomme ce qui était déjà vrai. Le cloisonnement commence au profil suivant,
créé à la main par l'administrateur.

Revision ID: e1f2a3b4c5d6
Revises: d9e8f7a6b5c4
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d9e8f7a6b5c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # Les clés de permission cochées. JSON et non table de jointure : la
        # liste des clés possibles est définie par le CODE (app/core/permissions),
        # pas par des données — cf. l'en-tête de app/models/profile.py.
        sa.Column("permissions", sa.JSON(), nullable=True),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_profiles_name", "profiles", ["name"], unique=True)

    # ⚠️ `ondelete="RESTRICT"` : supprimer un profil encore porté par un compte
    # est refusé par la base. Une cascade viderait les droits du compte en
    # silence — il resterait connecté, sans plus rien pouvoir faire et sans
    # savoir pourquoi. Le service refuse AVANT d'en arriver là, avec le nombre
    # de comptes concernés ; la contrainte n'est que le filet.
    op.add_column("users", sa.Column("profile_id", sa.Integer(), nullable=True))
    op.create_index("ix_users_profile_id", "users", ["profile_id"])
    op.create_foreign_key(
        "fk_users_profile_id_profiles", "users", "profiles",
        ["profile_id"], ["id"], ondelete="RESTRICT",
    )

    # --- Le profil système, et le rattachement de l'existant ----------------
    #
    # ⚠️ `permissions` reste VIDE volontairement : le profil système détient
    # tout par construction (`profile_service.effective_permissions` teste
    # `is_system` avant de lire la colonne). Y figer la liste des clés du jour
    # rendrait l'administrateur aveugle à toute permission ajoutée plus tard —
    # et la première oubliée pourrait être celle qui ouvre l'administration.
    #
    # `ON CONFLICT DO NOTHING` : la migration doit pouvoir être rejouée sur une
    # base où le profil existe déjà (restauration partielle, réexécution).
    op.execute(
        """
        INSERT INTO profiles (name, description, permissions, is_system,
                              created_at, updated_at)
        VALUES (
            'Administrateur',
            'Contrôle total du superviseur. Profil système : ses droits ne se '
            'cochent pas, il détient tout — y compris les fonctionnalités '
            'ajoutées plus tard.',
            '[]'::json,
            true,
            now(), now()
        )
        ON CONFLICT (name) DO NOTHING
        """,
    )

    # Tous les comptes existants étaient de fait administrateurs : c'est ce que
    # le système faisait avant cette migration. On le nomme, on ne le change pas.
    op.execute(
        """
        UPDATE users
           SET profile_id = (SELECT id FROM profiles WHERE is_system = true LIMIT 1)
         WHERE profile_id IS NULL
        """,
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_profile_id_profiles", "users", type_="foreignkey")
    op.drop_index("ix_users_profile_id", table_name="users")
    op.drop_column("users", "profile_id")
    op.drop_index("ix_profiles_name", table_name="profiles")
    op.drop_table("profiles")
