"""Pydantic schemas for the authentication endpoints."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Body of POST /auth/login."""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)


class UserRead(BaseModel):
    """Public view of a User row — never includes the password hash.

    Porte aussi les DROITS effectifs du compte : c'est la réponse de
    `GET /auth/me`, donc la seule source dont le dashboard dispose pour savoir
    quelles entrées de menu afficher et quels boutons rendre. Elle est calculée
    côté serveur à chaque appel — le frontend ne déduit jamais un droit d'un nom
    de profil.

    ⚠️ Ce que porte cette réponse ne PROTÈGE rien : elle sert à ne pas montrer à
    quelqu'un des écrans qui lui répondraient 403. Le contrôle réel est sur
    chaque route (cf. `deps.require_permission`).
    """

    id: int
    username: str
    full_name: str | None = None
    enabled: bool
    last_login_at: datetime.datetime | None = None
    profile_id: int | None = None
    profile_name: str | None = None
    # Vrai pour le profil système, qui détient tout par construction. Le
    # frontend s'en sert pour les cas où « tous les droits » se dit mieux que
    # d'énumérer 30 cases.
    is_admin: bool = False
    permissions: list[str] = []

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    """Body returned by POST /auth/login. The session token is in the cookie."""

    user: UserRead


class ChangePasswordRequest(BaseModel):
    """Body of POST /auth/change-password — the logged-in user changes own pwd."""

    current_password: str = Field(..., min_length=1, max_length=200)
    new_password: str = Field(..., min_length=8, max_length=200)
