"""Filtre de contenu par PLATEFORME — interface pour un système tiers.

L'appelant nous transmet la **MAC** de l'équipement client (LR) et le **nom
d'une plateforme** (``tiktok``, ``facebook``, …) et nous demande de la bloquer
ou de la rétablir. On retrouve le LR par sa MAC (identité stable, insensible aux
changements d'IP), puis on réutilise exactement le mécanisme de la page
« Filtre de contenu » du dashboard (``client_block_service.set_content_block``)
— empoisonnement DNS dnsmasq par SSH sur le LR, intention persistée et
ré-appliquée toutes les 120 s par le job de renforcement, survit au reboot du LR.

Deux différences avec la page, et elles sont volontaires :

  - **L'appel est CUMULATIF.** La page envoie l'ensemble complet des services
    cochés ; ici on envoie une plateforme et un verbe. Bloquer TikTok chez un
    client qui a déjà Facebook bloqué laisse Facebook bloqué. Un tiers qui
    n'envoie qu'une plateforme n'a pas à connaître — ni à réémettre — l'état
    complet du client, qu'un opérateur a pu modifier entre-temps depuis le
    dashboard.
  - **Une plateforme inconnue est REFUSÉE** (400), là où le chemin interne
    ignore silencieusement les clés inconnues. Cf. ``validate_platforms`` : un
    « titkok » mal orthographié doit échouer au premier appel, pas se découvrir
    sur un abonné toujours sur TikTok.

La clé ``adult`` (contenu 18+) est une **pseudo-plateforme** : même verbe pour
l'appelant, mais aucune liste de domaines derrière — elle bascule le résolveur
amont du LR vers un résolveur familial et vit dans ``lrs.block_adult_content``,
pas dans ``blocked_categories``. Le routage est fait par
``client_block_service.split_adult`` ; cf. **Filtre de contenu par plateforme**
dans CLAUDE.md pour les pièges que cette séparation évite.

Routes montées derrière `require_content_block_client` : clé dédiée
``CONTENT_BLOCK_API_KEY``, qui n'ouvre QUE ce router — ni /fai (couper l'accès
entier d'un abonné), ni /devices, ni le reste de l'API.
"""

from __future__ import annotations

import datetime
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.device import Lr
from app.services import client_block_service

logger = logging.getLogger(__name__)

router = APIRouter()

# L'AGENT à l'origine de l'action, tel que l'appelant nous le transmet — même
# rôle et mêmes contraintes que sur /fai (cf. `fai.AgentUser`) : purement
# traçable, jamais une condition de l'action, donc **facultatif**. Journalisé
# dans le log applicatif à côté de l'action, pas dans le journal FAI : celui-ci
# recense les COUPURES d'accès, et y mêler du filtrage de contenu changerait le
# sens de ses lignes (et le format d'un fichier déjà écrit).
AgentUser = Annotated[
    str | None,
    Field(
        description=(
            "Agent à l'origine de l'action (e-mail d'un opérateur ou libellé "
            "automatique). Facultatif, journalisé tel quel."
        ),
    ),
]

# Une plateforme, ou plusieurs d'un coup : l'appel nominal en porte une, mais un
# tiers qui vend un bouquet (« réseaux sociaux ») les enverrait une par une pour
# rien — et chaque appel supplémentaire est une session SSH sur la radio du
# client. Une chaîne seule reste acceptée telle quelle.
PlatformField = Annotated[
    str | list[str],
    Field(
        description=(
            "Clé de plateforme (« tiktok ») ou liste de clés. Valeurs acceptées : "
            "voir GET /content-filter/platforms."
        ),
    ),
]

_USER_MAX_LEN = 120


def _clean_user(user: str | None) -> str | None:
    """Normalise l'identifiant d'agent — cadrage, jamais de refus (cf. /fai)."""
    if not user:
        return None
    return " ".join(user.split())[:_USER_MAX_LEN] or None


class PlatformRequest(BaseModel):
    mac: str
    platform: PlatformField
    user: AgentUser = None


class ContentFilterPlatform(BaseModel):
    key: str
    label: str
    description: str
    domains: list[str]
    domain_count: int
    # Comment la clé agit sur l'équipement. « domains » = empoisonnement DNS des
    # domaines listés ; « upstream_resolver » = bascule du résolveur amont du LR
    # vers un résolveur familial qui maintient lui-même la catégorisation.
    #
    # ⚠️ Champ ajouté pour le 18+, dont la liste `domains` est VIDE par nature :
    # sans lui, un `domain_count: 0` se lit comme une clé inerte et l'appelant
    # conclut à une erreur de configuration de notre côté.
    mechanism: str


class ContentFilterResult(BaseModel):
    # ⚠️ Reflète l'APPLICATION sur l'équipement, pas la prise en compte de
    # l'ordre : un LR éteint rend ok=False avec retry_scheduled=true, et le job
    # de renforcement posera le filtre dès qu'il répondra. L'intention, elle,
    # est enregistrée dans tous les cas.
    ok: bool
    message: str
    mac: str | None
    name: str
    # Direction du filtre. « denylist » = tout sauf ces services ; « allowlist »
    # = rien sauf ces services. L'appelant n'a pas à la piloter, mais il doit
    # pouvoir la lire : elle change le sens de `categories`.
    mode: str
    # Ce qui est effectivement INACCESSIBLE au client, quelle que soit la
    # direction — la question que pose l'appelant.
    blocked_platforms: list[str]
    # L'ensemble tel qu'il est stocké : identique au précédent en « denylist »,
    # son complément en « allowlist ». Exposé pour lever l'ambiguïté, jamais
    # à interpréter sans `mode`.
    categories: list[str]
    content_block_enforced_at: datetime.datetime | None
    # True quand l'ordre n'a pas pu être appliqué mais sera rejoué
    # automatiquement (LR éteint, radio coupée).
    retry_scheduled: bool
    # Renseigné quand le LR REFUSE la connexion SSH (mot de passe, clé d'hôte) :
    # aucune nouvelle tentative automatique, intervention technique requise.
    unenforceable_reason: str | None


def _result(lr: Lr, ok: bool, message: str) -> ContentFilterResult:
    """Instantané du filtre de contenu — même charge utile pour les 3 routes."""
    reason = lr.block_unenforceable_reason
    categories = lr.blocked_categories or []
    return ContentFilterResult(
        ok=ok,
        message=message,
        mac=lr.mac_address,
        name=lr.name,
        mode=lr.content_block_mode,
        blocked_platforms=client_block_service.effective_blocked_platforms(
            categories, lr.content_block_mode, lr.block_adult_content,
        ),
        categories=categories,
        content_block_enforced_at=lr.content_block_enforced_at,
        # Un ordre non appliqué reste en file tant que l'échec est transitoire.
        retry_scheduled=(not ok) and reason is None,
        unenforceable_reason=reason,
    )


async def _lookup_lr(db: AsyncSession, mac: str) -> Lr:
    """Retrouve le LR par MAC ; 400 si MAC mal formée, 404 si introuvable."""
    try:
        lr = await client_block_service.find_lr_by_mac(db, mac)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if lr is None:
        raise HTTPException(status_code=404, detail=f"Aucun LR avec le MAC {mac!r}")
    return lr


def _platform_list(value: str | list[str]) -> list[str]:
    """Valide les clés reçues ; 400 sur une plateforme inconnue."""
    raw = [value] if isinstance(value, str) else list(value)
    try:
        return client_block_service.validate_platforms(raw)
    except client_block_service.UnknownPlatformError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _apply(
    db: AsyncSession, body: PlatformRequest, *, blocked: bool,
) -> ContentFilterResult:
    """Chemin d'écriture unique des deux routes — seul `blocked` les sépare."""
    platforms = _platform_list(body.platform)
    lr = await _lookup_lr(db, body.mac)
    if lr.topology_mode == "bridge":
        # Même refus que /fai/block, et pour une raison plus directe encore : en
        # bridge le LR est L2-transparent, son dnsmasq n'est pas dans le chemin
        # du client, donc AUCUN filtre DNS ne peut mordre. Poser l'intention
        # sans le dire laisserait croire l'abonné filtré.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Le LR '{lr.name}' est en mode bridge — le filtre de contenu ne "
                f"peut pas fonctionner (L2-transparent, dnsmasq contourné). "
                f"Reconfigurer le LR en mode routeur via airOS, puis réessayer."
            ),
        )
    try:
        ok, message = await client_block_service.set_platform_block(
            db, lr, platforms, blocked=blocked,
        )
    except client_block_service.ContentFilterConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logger.warning(
        "CONTENT FILTER API — %s %s sur LR '%s' (id=%d, %s) — agent=%s — ok=%s",
        "BLOCK" if blocked else "UNBLOCK", ",".join(platforms),
        lr.name, lr.id, lr.mac_address, _clean_user(body.user) or "-", ok,
    )
    return _result(lr, ok, message)


@router.get("/platforms", response_model=list[ContentFilterPlatform])
async def list_platforms() -> list[ContentFilterPlatform]:
    """Catalogue des plateformes filtrables — les seules valeurs acceptées.

    À lire plutôt qu'à coder en dur : les jeux de domaines sont ajustables par
    variable d'environnement, et une clé ajoutée ici devient utilisable sans
    changement côté appelant.
    """
    settings = get_settings()
    platforms = [
        ContentFilterPlatform(
            key=key,
            label=settings.content_block_label(key),
            description=settings.content_block_description(key),
            domains=domains,
            domain_count=len(domains),
            mechanism="domains",
        )
        for key, domains in settings.content_block_catalog().items()
    ]
    # Le 18+ ferme la liste : même verbe pour l'appelant, autre mécanisme sur
    # l'équipement — et aucune liste de domaines, par construction.
    adult = client_block_service.ADULT_PLATFORM
    platforms.append(
        ContentFilterPlatform(
            key=adult,
            label=settings.content_block_label(adult),
            description=settings.content_block_description(adult),
            domains=[],
            domain_count=0,
            mechanism="upstream_resolver",
        ),
    )
    return platforms


@router.post("/block", response_model=ContentFilterResult)
async def block_platform(
    body: PlatformRequest,
    db: AsyncSession = Depends(get_db),
) -> ContentFilterResult:
    """Rend une ou plusieurs plateformes INACCESSIBLES au client (par MAC).

    Cumulatif : les plateformes déjà filtrées chez ce client le restent. Le
    filtre est persisté et ré-appliqué automatiquement (survit au reboot du LR).
    Rejouable — appliquer deux fois le même ordre ré-affirme simplement le
    filtre sur l'équipement.

    - 400 : MAC mal formée, ou plateforme inconnue du catalogue.
    - 404 : aucun LR pour cette MAC.
    - 409 : LR en mode bridge, ou intention inexprimable sur un client en mode
      « autoriser uniquement » (bloquer sa dernière plateforme autorisée
      effacerait le filtre et lui rouvrirait tout l'internet).
    """
    return await _apply(db, body, blocked=True)


@router.post("/unblock", response_model=ContentFilterResult)
async def unblock_platform(
    body: PlatformRequest,
    db: AsyncSession = Depends(get_db),
) -> ContentFilterResult:
    """Rend une ou plusieurs plateformes de nouveau accessibles (par MAC).

    Cumulatif lui aussi : les autres plateformes filtrées chez ce client ne sont
    pas touchées. Aucun filtre actif = succès sans rien faire.

    - 400 : MAC mal formée, ou plateforme inconnue du catalogue.
    - 404 : aucun LR pour cette MAC.
    - 409 : LR en mode bridge.
    """
    return await _apply(db, body, blocked=False)


@router.get("/status", response_model=ContentFilterResult)
async def platform_status(
    mac: str = Query(
        ...,
        description="MAC du LR client (formats acceptés : aa:bb:cc:dd:ee:ff, "
        "aa-bb-..., aabb.ccdd.eeff, aabbccddeeff)",
    ),
    db: AsyncSession = Depends(get_db),
) -> ContentFilterResult:
    """Filtre de contenu actuel d'un client (lecture seule), par MAC de son LR.

    Lit l'état en base : l'intention posée et la date de sa dernière application
    réussie. Ne touche pas au LR.

    - 400 : MAC mal formée.
    - 404 : aucun LR pour cette MAC.
    """
    lr = await _lookup_lr(db, mac)
    blocked = client_block_service.effective_blocked_platforms(
        lr.blocked_categories, lr.content_block_mode, lr.block_adult_content,
    )
    message = (
        f"{len(blocked)} plateforme(s) bloquée(s)." if blocked
        else "Aucune plateforme bloquée."
    )
    return _result(lr, ok=True, message=message)
