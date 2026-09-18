"""Schémas Pydantic de la section Administration (profils et comptes)."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Catalogue des permissions
# ---------------------------------------------------------------------------

class PermissionRead(BaseModel):
    """Une case à cocher du formulaire de profil."""

    key: str
    label: str
    description: str
    kind: str          # "page" (une interface), "data" (un bloc d'info), "action" (un geste)
    route: str | None = None


class PermissionGroupRead(BaseModel):
    """Un bloc du formulaire — reprend les sections de la barre latérale."""

    key: str
    label: str
    description: str
    permissions: list[PermissionRead]


class PermissionCatalog(BaseModel):
    """Tout ce que le système sait faire, tel que l'admin le voit.

    Le frontend construit l'écran ENTIÈREMENT à partir de cette réponse : il ne
    contient aucune liste de fonctionnalités en dur. Ajouter une permission au
    catalogue backend la rend donc cochable sans toucher au dashboard.
    """

    groups: list[PermissionGroupRead]


# ---------------------------------------------------------------------------
# Profils
# ---------------------------------------------------------------------------

class ProfileRead(BaseModel):
    """Un profil tel qu'il est listé et édité."""

    id: int
    name: str
    description: str | None = None
    # Les clés EFFECTIVES : pour le profil système, c'est le catalogue entier,
    # pas sa colonne (qui est vide). Sans ça l'écran d'édition afficherait
    # l'administrateur comme n'ayant aucun droit.
    permissions: list[str]
    is_system: bool
    user_count: int


class ProfileCreate(BaseModel):
    """Body de POST /access-control/profiles."""

    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    """Body de PUT /access-control/profiles/{id} — tout est facultatif."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    permissions: list[str] | None = None


# ---------------------------------------------------------------------------
# Comptes
# ---------------------------------------------------------------------------

class ManagedUserRead(BaseModel):
    """Un compte tel qu'il apparaît dans la table d'administration."""

    id: int
    username: str
    full_name: str | None = None
    enabled: bool
    last_login_at: datetime.datetime | None = None
    profile_id: int | None = None
    profile_name: str | None = None
    is_admin: bool


class UserCreate(BaseModel):
    """Body de POST /access-control/users.

    ⚠️ `profile_id` est OBLIGATOIRE : un compte sans profil n'a aucun droit, il
    se connecterait donc sur un écran vide sans que personne comprenne pourquoi.
    """

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=200)
    profile_id: int
    full_name: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class UserUpdate(BaseModel):
    """Body de PUT /access-control/users/{id}.

    Le `username` n'y figure pas : c'est la clé qui relie ce compte à ses traces
    (journaux applicatifs, agent d'une action FAI). Le changer réécrirait le
    passé à moitié.
    """

    full_name: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None
    profile_id: int | None = None


class PasswordReset(BaseModel):
    """Body de POST /access-control/users/{id}/password (geste d'administrateur)."""

    password: str = Field(..., min_length=8, max_length=200)
