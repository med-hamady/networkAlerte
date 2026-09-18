"""
Section Administration — catalogue des droits, profils, comptes.

Trois familles de routes, chacune gardée par sa propre permission :

  - `GET /permissions`  → `admin.access`   — le catalogue, pour construire l'écran
  - `/profiles…`        → `admin.profiles` — créer / modifier / supprimer un profil
  - `/users…`           → `admin.users`    — créer un compte, l'affecter, le désactiver

⚠️ **Les trois permissions sont distinctes et c'est voulu** : `admin.access`
seule donne une vue en LECTURE de qui a le droit de quoi (utile à un
responsable) sans permettre d'y toucher. Les fondre en une donnerait le pouvoir
d'écriture à qui ne demandait qu'à regarder.

⚠️ **Aucune règle de sécurité n'est écrite ici** : les garde-fous
d'auto-verrouillage (dernier administrateur, profil système, profil encore
porté) vivent dans `profile_service`, et cet endpoint ne fait que traduire son
`ProfileError` en 409. Un second chemin d'écriture qui les réimplémenterait
finirait par diverger — et la divergence ne se verrait que le jour où plus
personne ne peut se connecter.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.permissions import (
    PERMISSION_GROUPS,
    normalize_permissions,
    unknown_permissions,
)
from app.db.session import get_db
from app.models.profile import Profile
from app.models.user import User
from app.schemas.access_control import (
    ManagedUserRead,
    PasswordReset,
    PermissionCatalog,
    PermissionGroupRead,
    PermissionRead,
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    UserCreate,
    UserUpdate,
)
from app.services import profile_service
from app.services.profile_service import ProfileError

logger = logging.getLogger(__name__)

router = APIRouter()

# Dépendances résolues UNE FOIS, au chargement du module. Deux raisons : ruff
# refuse un appel de fonction dans une valeur par défaut (B008), et les routes
# qui ont besoin de l'AUTEUR du geste (pour lui refuser de se verrouiller
# lui-même) doivent recevoir exactement la même dépendance que celle qui garde
# la route — sinon le contrôle et l'identité pourraient diverger.
_REQUIRE_ADMIN_ACCESS = Depends(require_permission("admin.access"))
_REQUIRE_ADMIN_PROFILES = Depends(require_permission("admin.profiles"))
_REQUIRE_ADMIN_USERS = Depends(require_permission("admin.users"))


# ---------------------------------------------------------------------------
# Traductions modèle → schéma
# ---------------------------------------------------------------------------

def _profile_out(profile: Profile, user_count: int) -> ProfileRead:
    """Vue d'un profil, avec ses droits EFFECTIFS.

    ⚠️ Pour le profil système on rend le catalogue entier et non sa colonne
    (vide) : sinon l'écran afficherait l'administrateur comme n'ayant aucun
    droit, ce qui est exactement le contraire de la vérité.
    """
    if profile.is_system:
        permissions = [perm.key for group in PERMISSION_GROUPS for perm in group.permissions]
    else:
        permissions = normalize_permissions(profile.permissions)
    return ProfileRead(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        permissions=permissions,
        is_system=profile.is_system,
        user_count=user_count,
    )


def _user_out(user: User) -> ManagedUserRead:
    return ManagedUserRead(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        enabled=user.enabled,
        last_login_at=user.last_login_at,
        profile_id=user.profile_id,
        profile_name=user.profile.name if user.profile else None,
        is_admin=profile_service.is_admin(user),
    )


def _reject_unknown(keys: list[str] | None) -> None:
    """422 sur une clé de permission qui n'existe pas au catalogue.

    Délibérément plus strict que `normalize_permissions`, qui ignore en silence.
    Le silence est juste pour une RELECTURE (une clé retirée du code ne doit pas
    faire échouer le chargement d'un profil) et faux pour une SAISIE : un
    `fai.blok` mal orthographié rendrait « profil enregistré » en n'ayant donné
    aucun droit, et le défaut se découvrirait le jour où l'agent ne peut pas
    travailler.
    """
    if keys is None:
        return
    unknown = unknown_permissions(keys)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Droits inconnus : {', '.join(unknown)}",
        )


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

@router.get(
    "/permissions",
    response_model=PermissionCatalog,
    dependencies=[_REQUIRE_ADMIN_ACCESS],
)
async def get_permission_catalog() -> PermissionCatalog:
    """Tout ce que le système sait faire, groupé comme la barre latérale.

    C'est la liste que l'administrateur coche pour définir un profil. Le
    frontend n'en garde AUCUNE copie en dur : ajouter une permission au
    catalogue backend la rend cochable sans toucher au dashboard.
    """
    return PermissionCatalog(
        groups=[
            PermissionGroupRead(
                key=group.key,
                label=group.label,
                description=group.description,
                permissions=[
                    PermissionRead(
                        key=perm.key,
                        label=perm.label,
                        description=perm.description,
                        kind=str(perm.kind),
                        route=perm.route,
                    )
                    for perm in group.permissions
                ],
            )
            for group in PERMISSION_GROUPS
        ],
    )


# ---------------------------------------------------------------------------
# Profils
# ---------------------------------------------------------------------------

@router.get(
    "/profiles",
    response_model=list[ProfileRead],
    dependencies=[_REQUIRE_ADMIN_ACCESS],
)
async def list_profiles(db: AsyncSession = Depends(get_db)) -> list[ProfileRead]:
    """Les profils existants, le profil système en tête."""
    profiles = await profile_service.list_profiles(db)
    out: list[ProfileRead] = []
    for profile in profiles:
        count = await profile_service.count_users_with_profile(db, profile.id)
        out.append(_profile_out(profile, count))
    return out


@router.post(
    "/profiles",
    response_model=ProfileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_REQUIRE_ADMIN_PROFILES],
)
async def create_profile(
    payload: ProfileCreate,
    db: AsyncSession = Depends(get_db),
) -> ProfileRead:
    """Créer un profil et cocher ses droits."""
    _reject_unknown(payload.permissions)
    try:
        profile = await profile_service.create_profile(
            db,
            name=payload.name,
            description=payload.description,
            permissions=payload.permissions,
        )
    except ProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    return _profile_out(profile, 0)


@router.put(
    "/profiles/{profile_id}",
    response_model=ProfileRead,
    dependencies=[_REQUIRE_ADMIN_PROFILES],
)
async def update_profile(
    profile_id: int,
    payload: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
) -> ProfileRead:
    """Renommer un profil ou modifier ses droits. Le profil système est refusé."""
    _reject_unknown(payload.permissions)
    profile = await profile_service.get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profil introuvable")
    try:
        await profile_service.update_profile(
            db,
            profile,
            name=payload.name,
            description=payload.description,
            permissions=payload.permissions,
        )
    except ProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    count = await profile_service.count_users_with_profile(db, profile.id)
    return _profile_out(profile, count)


@router.delete(
    "/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_REQUIRE_ADMIN_PROFILES],
)
async def delete_profile(profile_id: int, db: AsyncSession = Depends(get_db)) -> None:
    """Supprimer un profil — refusé s'il est système ou encore porté par un compte."""
    profile = await profile_service.get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profil introuvable")
    try:
        await profile_service.delete_profile(db, profile)
    except ProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()


# ---------------------------------------------------------------------------
# Comptes
# ---------------------------------------------------------------------------

@router.get(
    "/users",
    response_model=list[ManagedUserRead],
    dependencies=[_REQUIRE_ADMIN_ACCESS],
)
async def list_users(db: AsyncSession = Depends(get_db)) -> list[ManagedUserRead]:
    """Les comptes et le profil de chacun."""
    users = await profile_service.list_users(db)
    return [_user_out(user) for user in users]


@router.post(
    "/users",
    response_model=ManagedUserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[_REQUIRE_ADMIN_USERS],
)
async def create_user(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> ManagedUserRead:
    """Créer un compte (identifiant + mot de passe) et l'affecter à un profil."""
    try:
        user = await profile_service.create_user(
            db,
            username=payload.username,
            password=payload.password,
            profile_id=payload.profile_id,
            full_name=payload.full_name,
            enabled=payload.enabled,
        )
    except ProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@router.put(
    "/users/{user_id}",
    response_model=ManagedUserRead,
    dependencies=[_REQUIRE_ADMIN_USERS],
)
async def update_user(
    user_id: int,
    payload: UserUpdate,
    db: AsyncSession = Depends(get_db),
    actor: User | None = _REQUIRE_ADMIN_USERS,
) -> ManagedUserRead:
    """Changer le nom affiché, l'activation ou le profil d'un compte.

    `actor` est l'auteur du geste : le service s'en sert pour refuser à un
    administrateur de se retirer à lui-même l'administration.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable")
    try:
        await profile_service.update_user(
            db,
            user,
            actor=actor,
            full_name=payload.full_name,
            enabled=payload.enabled,
            profile_id=payload.profile_id,
        )
    except ProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@router.post(
    "/users/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_REQUIRE_ADMIN_USERS],
)
async def reset_password(
    user_id: int,
    payload: PasswordReset,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Réinitialiser le mot de passe d'un compte (toutes ses sessions tombent)."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable")
    await profile_service.set_user_password(db, user, payload.password)
    await db.commit()


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[_REQUIRE_ADMIN_USERS],
)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User | None = _REQUIRE_ADMIN_USERS,
) -> None:
    """Supprimer un compte — jamais le sien, jamais le dernier administrateur."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable")
    try:
        await profile_service.delete_user(db, user, actor=actor)
    except ProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
