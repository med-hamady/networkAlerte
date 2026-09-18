"""
Profil d'accès — un jeu de droits, porté par zéro, un ou plusieurs utilisateurs.

Un profil ne contient QUE des clés du catalogue `app/core/permissions.py`. Les
libellés, les descriptions et le regroupement à l'écran vivent dans le code ;
la base ne garde que la liste cochée. C'est ce qui permet d'ajouter une
fonctionnalité cochable sans migration.

⚠️ **Les droits sont une colonne JSON, pas une table d'association.** Le choix
est délibéré : la liste des clés possibles est définie par le CODE, pas par des
données, donc une table de jointure n'apporterait ni intégrité référentielle
réelle (il faudrait la resynchroniser à chaque déploiement) ni requête utile
(on ne demande jamais « quels profils ont ce droit ? » à la base, et la réponse
tiendrait en une lecture de quelques dizaines de lignes). Elle coûterait en
revanche une écriture multi-lignes là où un profil s'enregistre aujourd'hui en
un seul UPDATE. Même raisonnement que `devices.policy_overrides`.

⚠️ **Le profil SYSTÈME (`is_system=True`) ne lit jamais cette colonne** : il
détient tout par construction (cf. `profile_service.effective_permissions`).
Voir l'avertissement en tête de `app/core/permissions.py`.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Profile(Base):
    """Un profil d'accès (« Agent », « Superviseur », « Administrateur »…)."""

    __tablename__ = "profiles"

    # Nom affiché ET identifiant fonctionnel — unique, c'est ce que l'admin voit
    # dans la liste déroulante au moment d'affecter un utilisateur.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Les clés de permission cochées. Toujours relue à travers
    # `permissions.normalize_permissions` : une clé retirée du catalogue depuis
    # la dernière écriture est ignorée plutôt que de faire échouer le chargement.
    permissions: Mapped[list | None] = mapped_column(JSON, nullable=True, default=list)

    # Profil VERROUILLÉ, posé par la migration et jamais par un humain :
    # ni renommable, ni supprimable, et ses droits ne se cochent pas (il a tout).
    # Sans ce drapeau, décocher « Administration » sur le profil de l'unique
    # admin verrouillerait TOUT LE MONDE dehors, sans recours par l'interface.
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    def __repr__(self) -> str:
        return f"<Profile(id={self.id}, name={self.name!r}, is_system={self.is_system})>"
