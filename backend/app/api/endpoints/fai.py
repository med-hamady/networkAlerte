"""FAI — interface d'intégration pour le système de paiement externe.

Le système de paiement nous transmet la **MAC** de l'équipement client (LR) et
nous demande de **bloquer** ou **débloquer** son accès internet. On retrouve le
LR par sa MAC (identité stable, insensible aux changements d'IP), puis on
réutilise exactement le même mécanisme que la page Accès / FAI du dashboard
(`client_block_service`) — SSH sur le LR, blocage persisté + ré-appliqué par le
job d'enforcement, survit au reboot du LR.

Ces routes sont protégées par `X-API-Key` (montées sous le router authentifié).
Identique en effet à `POST /devices/{id}/block-client`, mais indexé par MAC pour
le système tiers, qui ne connaît pas nos `id` internes.
"""

from __future__ import annotations

import datetime
import unicodedata
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.device import Lr
from app.services import client_block_service, fai_audit

router = APIRouter()

# Origine affichée dans le journal (colonne « Origine » de /fai-journal).
#
# Le système de paiement n'est pas un bloc unique : ses différents scripts appellent
# la même route, et savoir LEQUEL a coupé un client est ce qu'on veut lire quand on
# enquête sur une coupure. Le seul signal qu'ils nous transmettent est le `reason`,
# dont chaque script a sa formule fixe — on l'utilise donc comme signature.
#
# La correspondance se fait sur le PRÉFIXE : le motif se termine par une partie
# variable (« ... client <info> »), une égalité stricte ne matcherait jamais.
# Comparaison insensible à la casse et aux accents (« Impaye » / « Impayé »).
# Ajouter un script appelant = ajouter une ligne ici.
_REASON_SOURCES: tuple[tuple[str, str], ...] = (
    ("impaye - blocage auto - client", "Block_all.php"),
)

# Origine par défaut quand le `reason` ne correspond à aucune signature connue.
_DEFAULT_SOURCE = "payment"


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def _source_from_reason(reason: str | None) -> str:
    """Déduit l'origine à journaliser à partir du motif envoyé par l'appelant."""
    if not reason:
        return _DEFAULT_SOURCE
    normalized = _strip_accents(reason).strip().lower()
    for prefix, source in _REASON_SOURCES:
        if normalized.startswith(prefix):
            return source
    return _DEFAULT_SOURCE


class FaiBlockRequest(BaseModel):
    mac: str
    reason: str | None = None
    # "full" = coupure totale (port LAN fermé). "whatsapp_only" = filtre iptables
    # laissant DNS + WhatsApp/Meta joignables. Omis → défaut serveur.
    mode: Literal["full", "whatsapp_only"] | None = None


class FaiUnblockRequest(BaseModel):
    mac: str


class FaiBlockResult(BaseModel):
    ok: bool
    message: str
    mac: str | None
    name: str
    client_blocked: bool
    block_mode: str
    client_block_enforced_at: datetime.datetime | None
    # True quand l'ordre n'a pas pu être appliqué mais sera rejoué automatiquement
    # (LR éteint / radio coupée). False + ok=False ⇒ voir `unenforceable_reason`.
    retry_scheduled: bool
    # Renseigné quand le LR REFUSE la connexion SSH (mot de passe, host key) :
    # aucune nouvelle tentative automatique, une intervention technique est requise.
    unenforceable_reason: str | None
    # Par quel mécanisme le client est effectivement coupé :
    #   "lr"     → coupure appliquée sur son équipement (mécanisme nominal)
    #   "router" → repli : règle drop sur le routeur de cœur, parce que le LR ne
    #              répondait pas ou refusait la connexion
    #   null     → pas coupé
    enforced_by: Literal["lr", "router"] | None
    # Une règle de blocage est-elle en place sur le routeur pour ce client ?
    router_blocked: bool


def _result(lr: Lr, ok: bool, message: str) -> FaiBlockResult:
    """Snapshot the LR's block state — same payload for block / unblock / status."""
    blocked_reason = lr.block_unenforceable_reason
    if not lr.client_blocked:
        enforced_by = None
    elif lr.client_block_enforced_at is not None and blocked_reason is None:
        enforced_by = "lr"
    elif lr.router_blocked:
        enforced_by = "router"
    else:
        enforced_by = None  # ordre pris, pas encore appliqué
    return FaiBlockResult(
        ok=ok,
        message=message,
        mac=lr.mac_address,
        name=lr.name,
        client_blocked=lr.client_blocked,
        block_mode=lr.block_mode,
        client_block_enforced_at=lr.client_block_enforced_at,
        # Un ordre non appliqué reste en file tant que l'échec est transitoire.
        retry_scheduled=(not ok) and blocked_reason is None,
        unenforceable_reason=blocked_reason,
        enforced_by=enforced_by,
        router_blocked=lr.router_blocked,
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


@router.post("/block", response_model=FaiBlockResult)
async def fai_block(
    body: FaiBlockRequest,
    db: AsyncSession = Depends(get_db),
) -> FaiBlockResult:
    """Bloque l'accès internet d'un client à partir de la MAC de son LR.

    Modes : `full` (coupure totale du port LAN) ou `whatsapp_only` (filtre
    iptables laissant DNS + WhatsApp). Le blocage est persisté et ré-appliqué
    automatiquement — il survit à un reboot du LR.

    - 400 : MAC mal formée.
    - 404 : aucun LR pour cette MAC.
    - 409 : LR en mode bridge (le blocage ne peut pas fonctionner).
    """
    lr = await _lookup_lr(db, body.mac)
    if lr.topology_mode == "bridge":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Le LR '{lr.name}' est en mode bridge — le blocage ne peut pas "
                f"fonctionner (L2-transparent, iptables et dnsmasq contournés). "
                f"Reconfigurer le LR en mode routeur via airOS, puis réessayer."
            ),
        )
    ok, message = await client_block_service.block_client(
        db, lr, body.reason, body.mode
    )
    # Un refus d'identité n'est pas un échec de blocage : c'est une action NON
    # tentée parce que l'adresse de la fiche pointe sur un autre abonné. Le
    # journal le dit tel quel, sinon l'opérateur cherche une panne inexistante.
    fai_audit.log_action(
        "IDENT_KO" if client_block_service.is_identity_refusal(message) else "BLOCK",
        ok=ok, mac=lr.mac_address, name=lr.name,
        mode=lr.block_mode, source=_source_from_reason(body.reason), message=message,
    )
    return _result(lr, ok, message)


@router.post("/unblock", response_model=FaiBlockResult)
async def fai_unblock(
    body: FaiUnblockRequest,
    db: AsyncSession = Depends(get_db),
) -> FaiBlockResult:
    """Débloque l'accès internet d'un client à partir de la MAC de son LR.

    - 400 : MAC mal formée.
    - 404 : aucun LR pour cette MAC.
    """
    lr = await _lookup_lr(db, body.mac)
    ok, message = await client_block_service.unblock_client(db, lr)
    # Un refus d'identité n'est pas un échec de blocage : c'est une action NON
    # tentée parce que l'adresse de la fiche pointe sur un autre abonné. Le
    # journal le dit tel quel, sinon l'opérateur cherche une panne inexistante.
    fai_audit.log_action(
        "IDENT_KO" if client_block_service.is_identity_refusal(message) else "UNBLOCK",
        ok=ok, mac=lr.mac_address, name=lr.name,
        mode=lr.block_mode, message=message,
    )
    return _result(lr, ok, message)


@router.get("/status", response_model=FaiBlockResult)
async def fai_status(
    mac: str = Query(
        ...,
        description="MAC du LR client (formats acceptés : aa:bb:cc:dd:ee:ff, "
        "aa-bb-..., aabb.ccdd.eeff, aabbccddeeff)",
    ),
    db: AsyncSession = Depends(get_db),
) -> FaiBlockResult:
    """État de blocage actuel d'un client (lecture seule), par MAC de son LR.

    Permet au système de paiement de vérifier l'état réel en base avant/après une
    action. Ne touche pas au LR.

    - 400 : MAC mal formée.
    - 404 : aucun LR pour cette MAC.
    """
    lr = await _lookup_lr(db, mac)
    if not lr.client_blocked:
        return _result(lr, ok=True, message="Accès actif.")
    where = " (coupé sur le routeur)" if lr.router_blocked else ""
    return _result(lr, ok=True, message=f"Accès bloqué{where}.")
