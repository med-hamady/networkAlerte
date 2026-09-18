"""
Profils d'accès et comptes utilisateurs — la logique métier des droits.

Deux responsabilités qui se répondent :

  1. **Résoudre** les droits effectifs d'un compte (`effective_permissions`,
     `has_permission`) — c'est ce que consulte chaque requête authentifiée.
  2. **Administrer** profils et comptes (le reste du module) — c'est ce que fait
     la section Administration du dashboard.

⚠️ **Les garde-fous d'auto-verrouillage vivent ICI, pas dans les endpoints.**
Un superviseur dont plus personne ne peut administrer les comptes se répare à la
main en base de données, sur le serveur de production. Les quatre règles :

  - le profil système ne peut être ni renommé, ni supprimé, ni modifié ;
  - le **dernier** compte administrateur actif ne peut être ni supprimé, ni
    désactivé, ni rétrogradé vers un autre profil ;
  - un profil encore porté par des comptes ne peut pas être supprimé ;
  - un administrateur ne peut pas se retirer à lui-même l'accès (les trois
    règles précédentes en découlent pour le cas courant, celle-ci couvre le cas
    où il reste d'autres admins mais qu'il se coupe l'herbe sous le pied par
    mégarde — voir `assert_not_self_locking`).

Un endpoint qui contournerait ce module casserait ces règles sans qu'aucun test
d'API ne le voie : c'est pourquoi elles sont vérifiées sur le SERVICE.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import PERMISSION_KEYS, normalize_permissions
from app.models.profile import Profile
from app.models.user import User
from app.services import auth_service

logger = logging.getLogger(__name__)


class ProfileError(Exception):
    """Refus métier — porté tel quel à l'appelant (409 côté API)."""


# ---------------------------------------------------------------------------
# Résolution des droits — le chemin chaud, lu à chaque requête authentifiée
# ---------------------------------------------------------------------------

def effective_permissions(user: User | None) -> frozenset[str]:
    """Les droits réellement détenus par ce compte.

    ⚠️ `None` rend l'ensemble VIDE, jamais l'ensemble complet. Un appelant sans
    identité d'utilisateur (authentification par clé API) est traité à part, en
    amont, par `deps.py` — il ne doit surtout pas ressortir d'ici avec des
    droits par défaut.

    ⚠️ Un compte **sans profil** n'a AUCUN droit, pas tous les droits. C'est le
    sens sûr : un compte créé de travers, ou orphelin d'un profil supprimé à la
    main en base, ne doit pas se retrouver administrateur.
    """
    if user is None or user.profile is None:
        return frozenset()
    # Le profil système détient tout PAR CONSTRUCTION, sans lire sa colonne :
    # une permission ajoutée au catalogue lui appartient immédiatement, sans
    # que personne n'ait à re-cocher une case (cf. app/core/permissions.py).
    if user.profile.is_system:
        return PERMISSION_KEYS
    return frozenset(normalize_permissions(user.profile.permissions))


def has_permission(user: User | None, *keys: str) -> bool:
    """Vrai si le compte détient AU MOINS UNE des clés demandées.

    Le OU est délibéré : plusieurs pages partagent une même lecture (la page
    « Demandes de coupure » et le « Journal des blocages » tirent tous deux de
    `GET /fai-journal`). Exiger toutes les clés fermerait la route à qui n'a
    reçu qu'une des deux pages, alors qu'il a le droit de la voir.
    """
    if not keys:
        return True
    granted = effective_permissions(user)
    return any(key in granted for key in keys)


def is_admin(user: User | None) -> bool:
    """Vrai si le compte porte le profil système (tous les droits)."""
    return user is not None and user.profile is not None and user.profile.is_system


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------

async def list_profiles(db: AsyncSession) -> list[Profile]:
    """Tous les profils, le profil système d'abord puis par nom."""
    result = await db.execute(
        select(Profile).order_by(Profile.is_system.desc(), Profile.name),
    )
    return list(result.scalars().all())


async def get_profile(db: AsyncSession, profile_id: int) -> Profile | None:
    return await db.get(Profile, profile_id)


async def get_admin_profile(db: AsyncSession) -> Profile | None:
    """Le profil système. Posé par la migration, jamais créé par l'interface."""
    result = await db.execute(select(Profile).where(Profile.is_system.is_(True)))
    return result.scalars().first()


async def list_users(db: AsyncSession) -> list[User]:
    """Tous les comptes, par nom d'utilisateur (le profil vient en jointure)."""
    result = await db.execute(select(User).order_by(User.username))
    return list(result.scalars().unique().all())


async def count_users_with_profile(db: AsyncSession, profile_id: int) -> int:
    result = await db.execute(
        select(func.count()).select_from(User).where(User.profile_id == profile_id),
    )
    return int(result.scalar_one())


async def _count_active_admins(db: AsyncSession, *, excluding_user_id: int | None = None) -> int:
    """Comptes ACTIFS portant le profil système, hors celui qu'on s'apprête à changer.

    « Actif » compte, parce qu'un compte désactivé n'ouvre aucune session : ne
    regarder que le profil laisserait désactiver le dernier administrateur
    utilisable tant qu'il reste un admin désactivé en base.
    """
    stmt = (
        select(func.count())
        .select_from(User)
        .join(Profile, User.profile_id == Profile.id)
        .where(Profile.is_system.is_(True), User.enabled.is_(True))
    )
    if excluding_user_id is not None:
        stmt = stmt.where(User.id != excluding_user_id)
    result = await db.execute(stmt)
    return int(result.scalar_one())


async def assert_last_admin_survives(
    db: AsyncSession,
    user: User,
    *,
    still_admin: bool,
    still_enabled: bool,
) -> None:
    """Refuser une modification qui laisserait le système sans administrateur.

    Appelée AVANT toute écriture qui retire à un compte son profil système, le
    désactive ou le supprime. `still_admin`/`still_enabled` décrivent l'état
    APRÈS la modification envisagée.
    """
    if not is_admin(user) or not user.enabled:
        return  # ce compte ne fait pas partie du quorum d'admins actifs
    if still_admin and still_enabled:
        return  # il le reste — rien à vérifier
    remaining = await _count_active_admins(db, excluding_user_id=user.id)
    if remaining == 0:
        raise ProfileError(
            "Impossible : ce compte est le dernier administrateur actif. "
            "Crée ou réactive un autre administrateur avant de le modifier.",
        )


def assert_not_self_locking(actor: User | None, target: User, *, still_admin: bool) -> None:
    """Empêcher un administrateur de se retirer à LUI-MÊME l'administration.

    Distinct de `assert_last_admin_survives` : ici le système garde d'autres
    administrateurs, mais l'auteur du geste perdrait dans la seconde l'écran
    depuis lequel il travaille — et, s'il est seul connecté ce jour-là, la
    réparation demande un accès au serveur. Le geste reste possible : il faut
    qu'un AUTRE administrateur le fasse, ce qui est aussi la trace de qui l'a
    décidé.
    """
    if actor is None or actor.id != target.id:
        return
    if is_admin(actor) and not still_admin:
        raise ProfileError(
            "Impossible de retirer ton propre accès administrateur. "
            "Demande à un autre administrateur de le faire.",
        )


# ---------------------------------------------------------------------------
# Écriture — profils
# ---------------------------------------------------------------------------

async def _assert_name_free(db: AsyncSession, name: str, *, exclude_id: int | None = None) -> None:
    stmt = select(Profile.id).where(func.lower(Profile.name) == name.lower())
    if exclude_id is not None:
        stmt = stmt.where(Profile.id != exclude_id)
    if (await db.execute(stmt)).scalars().first() is not None:
        raise ProfileError(f"Un profil nommé « {name} » existe déjà.")


async def create_profile(
    db: AsyncSession,
    *,
    name: str,
    description: str | None,
    permissions: list[str],
) -> Profile:
    """Créer un profil. Le nom est unique (insensible à la casse)."""
    name = name.strip()
    if not name:
        raise ProfileError("Le nom du profil est obligatoire.")
    await _assert_name_free(db, name)
    profile = Profile(
        name=name,
        description=(description or "").strip() or None,
        permissions=normalize_permissions(permissions),
        is_system=False,
    )
    db.add(profile)
    await db.flush()
    logger.info("Profil créé : %r (%d droits)", profile.name, len(profile.permissions or []))
    return profile


async def update_profile(
    db: AsyncSession,
    profile: Profile,
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: list[str] | None = None,
) -> Profile:
    """Modifier un profil. Le profil système est intouchable."""
    if profile.is_system:
        raise ProfileError(
            f"Le profil « {profile.name} » est un profil système : il détient "
            "tous les droits et ne peut pas être modifié.",
        )
    if name is not None:
        name = name.strip()
        if not name:
            raise ProfileError("Le nom du profil est obligatoire.")
        await _assert_name_free(db, name, exclude_id=profile.id)
        profile.name = name
    if description is not None:
        profile.description = description.strip() or None
    if permissions is not None:
        profile.permissions = normalize_permissions(permissions)
    await db.flush()
    logger.info("Profil modifié : %r (%d droits)", profile.name, len(profile.permissions or []))
    return profile


async def delete_profile(db: AsyncSession, profile: Profile) -> None:
    """Supprimer un profil — refusé s'il est système ou encore porté."""
    if profile.is_system:
        raise ProfileError(
            f"Le profil « {profile.name} » est un profil système : il ne peut "
            "pas être supprimé.",
        )
    used_by = await count_users_with_profile(db, profile.id)
    if used_by:
        raise ProfileError(
            f"« {profile.name} » est encore affecté à {used_by} compte(s). "
            "Affecte-les à un autre profil avant de le supprimer.",
        )
    await db.delete(profile)
    await db.flush()
    logger.info("Profil supprimé : %r", profile.name)


# ---------------------------------------------------------------------------
# Écriture — comptes
# ---------------------------------------------------------------------------

async def create_user(
    db: AsyncSession,
    *,
    username: str,
    password: str,
    profile_id: int,
    full_name: str | None = None,
    enabled: bool = True,
) -> User:
    """Créer un compte et l'affecter à un profil.

    ⚠️ Le profil est OBLIGATOIRE à la création : un compte sans profil n'a aucun
    droit (cf. `effective_permissions`), donc il se connecterait sur un écran
    vide sans que personne comprenne pourquoi.
    """
    username = (username or "").strip().lower()
    if not username:
        raise ProfileError("Le nom d'utilisateur est obligatoire.")
    if await auth_service.get_user_by_username(db, username) is not None:
        raise ProfileError(f"Le nom d'utilisateur « {username} » est déjà pris.")
    profile = await get_profile(db, profile_id)
    if profile is None:
        raise ProfileError("Profil introuvable.")

    user = User(
        username=username,
        password_hash=auth_service.hash_password(password),
        full_name=(full_name or "").strip() or None,
        enabled=enabled,
        profile_id=profile.id,
    )
    db.add(user)
    await db.flush()
    logger.info("Compte créé : %r (profil %r)", user.username, profile.name)
    return user


async def update_user(
    db: AsyncSession,
    user: User,
    *,
    actor: User | None,
    full_name: str | None = None,
    enabled: bool | None = None,
    profile_id: int | None = None,
) -> User:
    """Modifier un compte : nom affiché, activation, profil.

    Le nom d'utilisateur n'est volontairement PAS modifiable : c'est la clé qui
    relie ce compte à ses traces (journaux applicatifs, agent d'une action FAI).
    Le changer réécrirait le passé à moitié.
    """
    next_profile = user.profile
    if profile_id is not None and profile_id != user.profile_id:
        next_profile = await get_profile(db, profile_id)
        if next_profile is None:
            raise ProfileError("Profil introuvable.")

    still_admin = next_profile is not None and next_profile.is_system
    still_enabled = user.enabled if enabled is None else enabled

    assert_not_self_locking(actor, user, still_admin=still_admin)
    await assert_last_admin_survives(
        db, user, still_admin=still_admin, still_enabled=still_enabled,
    )

    if full_name is not None:
        user.full_name = full_name.strip() or None
    if enabled is not None and enabled != user.enabled:
        user.enabled = enabled
        if not enabled:
            # Un compte désactivé qui garde une session ouverte reste dans la
            # place jusqu'à l'expiration du cookie (12 h) : désactiver doit
            # mettre dehors MAINTENANT, sinon le geste ne veut rien dire.
            await auth_service.revoke_all_sessions_for_user(db, user.id)
    if next_profile is not None and next_profile.id != user.profile_id:
        user.profile_id = next_profile.id
        # Les droits sont relus à chaque requête, donc le changement s'applique
        # sans reconnexion — mais on révoque quand même : l'interface d'un
        # utilisateur déjà connecté a été construite sur les anciens droits, et
        # une page ouverte continuerait d'afficher des boutons qui répondent
        # désormais 403.
        await auth_service.revoke_all_sessions_for_user(db, user.id)

    await db.flush()
    logger.info(
        "Compte modifié : %r (profil=%s, actif=%s)",
        user.username,
        next_profile.name if next_profile else None,
        user.enabled,
    )
    return user


async def set_user_password(db: AsyncSession, user: User, password: str) -> None:
    """Réinitialiser le mot de passe d'un compte (geste d'administrateur).

    Toutes ses sessions sont révoquées : si le mot de passe est réinitialisé
    parce qu'il a fuité, laisser les sessions ouvertes annulerait le geste.
    """
    user.password_hash = auth_service.hash_password(password)
    await auth_service.revoke_all_sessions_for_user(db, user.id)
    await db.flush()
    logger.info("Mot de passe réinitialisé pour %r (sessions révoquées)", user.username)


async def delete_user(db: AsyncSession, user: User, *, actor: User | None) -> None:
    """Supprimer un compte — jamais le sien, jamais le dernier administrateur."""
    if actor is not None and actor.id == user.id:
        raise ProfileError("Impossible de supprimer ton propre compte.")
    await assert_last_admin_survives(db, user, still_admin=False, still_enabled=False)
    await db.delete(user)
    await db.flush()
    logger.info("Compte supprimé : %r", user.username)
