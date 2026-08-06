"""Journal des blocages/déblocages — lecture pour le dashboard.

Sépare volontairement deux choses que la page affiche côte à côte :

  - **l'historique** (`entries`) : ce qui s'est passé, lu du fichier d'audit
    (`fai_audit.read_entries`) — y compris les actions venues du système de
    paiement, avec leur résultat.
  - **l'état à traiter** (`attention`) : ce qui est encore en souffrance, lu de la
    **base** — les LR dont l'ordre n'a pas pu être appliqué. C'est la vraie valeur
    opérationnelle : un log dit « ça a raté à 11 h », la base dit « c'est TOUJOURS
    raté maintenant ». Deux catégories :
      * `unenforceable` → le LR refuse la connexion SSH (mot de passe, host key) :
        plus aucune tentative automatique, un technicien doit intervenir.
      * `pending` → l'ordre sera rejoué tout seul (LR éteint) ; rien à faire, mais
        un client anormalement longtemps en attente se voit ici.

Ce router est monté avec l'auth NORMALE (session dashboard / clé maître), PAS avec
`require_fai_client` : la clé du système de paiement ne doit pas pouvoir lire le
journal — elle n'a besoin que de bloquer/débloquer.
"""

from __future__ import annotations

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.device import Lr
from app.services import fai_audit

router = APIRouter()


class JournalEntry(BaseModel):
    timestamp: str
    action: str  # BLOCK | UNBLOCK | RETRY_OK | ABANDON | IDENT_KO
    ok: bool
    mac: str | None
    name: str
    mode: str
    source: str  # payment | enforce
    # L'AGENT à l'origine de l'action, quand l'appelant nous l'a transmis :
    # e-mail d'un opérateur, ou libellé automatique (« auto system », « auto
    # retry »). `null` = non transmis — soit un appelant qui ne l'envoie pas, soit
    # une action interne (renforcement, script), soit une ligne écrite avant que
    # le champ existe. L'UI ne distingue pas ces cas : dans les trois, on ne sait
    # pas qui a demandé.
    user: str | None = None
    message: str
    # Une transcription de la session SSH est-elle archivée pour cette action ?
    # Pilote l'affichage du bouton « Voir la preuve » — inutile de proposer
    # l'ouverture d'une preuve qui n'existe pas (actions antérieures à la
    # fonctionnalité, mode whatsapp_only, ordres purement routeur).
    has_evidence: bool = False


class JournalStats(BaseModel):
    total: int
    ok: int
    failed: int
    abandoned: int


class AttentionRow(BaseModel):
    id: int
    name: str
    mac: str | None
    ip_address: str | None
    site: str | None
    # "unenforceable" = le LR refuse la connexion → intervention technique.
    # "pending"       = ordre en file, rejoué automatiquement.
    kind: Literal["unenforceable", "pending"]
    # Ce que l'ordre veut faire : bloquer (client_blocked) ou débloquer.
    intent: Literal["block", "unblock"]
    reason: str | None
    since: datetime.datetime | None
    # Le repli a-t-il pris le relais ? Change radicalement la lecture de la ligne :
    # le client EST coupé (par le routeur), seule la coupure sur son équipement
    # reste à faire. Sans ce drapeau, l'opérateur croirait le client en ligne.
    router_blocked: bool


class JournalResponse(BaseModel):
    entries: list[JournalEntry]
    stats: JournalStats
    attention: list[AttentionRow]


@router.get("", response_model=JournalResponse)
async def get_journal(
    # Le journal est lu EN ENTIER côté service (compteurs et recherche portent sur
    # tout l'historique) ; `limit` ne borne donc que la TAILLE DE LA RÉPONSE, pas
    # ce qui est examiné. Le plafond reste haut pour que la page puisse rendre le
    # journal complet, et existe seulement pour qu'une réponse ne devienne jamais
    # illimitée quand le fichier aura grossi de plusieurs années.
    limit: int = Query(200, ge=1, le=50_000),
    action: str | None = Query(None, description="BLOCK | UNBLOCK | RETRY_OK | ABANDON | IDENT_KO"),
    status: str | None = Query(None, description="ok | failed | abandoned"),
    search: str | None = Query(
        None, description="Filtre sur la MAC, le nom du client ou l'agent"
    ),
    db: AsyncSession = Depends(get_db),
) -> JournalResponse:
    """Historique des actions de blocage + LR encore en souffrance."""
    # Ordres aujourd'hui SATISFAITS en base : leurs vieilles lignes « non appliqué »
    # sont rattrapées et ne doivent plus polluer l'onglet « Non appliqué ».
    #   coupé (SSH ou routeur) → un BLOCK raté est rattrapé
    #   plus bloqué du tout     → un UNBLOCK raté est rattrapé
    resolved = await db.execute(
        select(Lr.mac_address, Lr.client_blocked,
               Lr.client_block_enforced_at, Lr.router_blocked)
        .where(Lr.mac_address.is_not(None))
    )
    resolved_block_macs: set[str] = set()
    resolved_unblock_macs: set[str] = set()
    for mac, blocked, enforced_at, router_blocked in resolved.all():
        m = mac.lower()
        if blocked and (enforced_at is not None or router_blocked):
            resolved_block_macs.add(m)
        if not blocked:
            resolved_unblock_macs.add(m)

    entries, stats = fai_audit.read_entries(
        limit=limit, action=action, status=status, search=search,
        resolved_block_macs=resolved_block_macs,
        resolved_unblock_macs=resolved_unblock_macs,
    )

    result = await db.execute(
        select(Lr).where(
            or_(
                Lr.block_unenforceable_reason.is_not(None),
                Lr.unblock_pending.is_(True),
            )
        )
    )
    attention = [
        AttentionRow(
            id=lr.id,
            name=lr.name,
            mac=lr.mac_address,
            ip_address=lr.ip_address,
            site=lr.site,
            kind="unenforceable" if lr.block_unenforceable_reason else "pending",
            intent="block" if lr.client_blocked else "unblock",
            reason=lr.block_unenforceable_reason,
            since=lr.client_blocked_at,
            router_blocked=lr.router_blocked,
        )
        for lr in result.scalars().all()
    ]
    # Les cas bloquants d'abord, et parmi eux ceux que le routeur ne couvre PAS —
    # ce sont les seuls où un client reste réellement en ligne sans rien payer.
    attention.sort(key=lambda r: (r.router_blocked, r.kind != "unenforceable", r.name))

    return JournalResponse(
        entries=[JournalEntry(**e) for e in entries],
        stats=JournalStats(**stats),
        attention=attention,
    )


class EvidenceResponse(BaseModel):
    timestamp: str
    mac: str | None
    action: str
    # Transcription brute de la session SSH : commandes envoyées, sorties du LR
    # (stderr compris) et codes de sortie. Rendue telle quelle, en monospace.
    transcript: str


@router.get("/evidence", response_model=EvidenceResponse)
async def get_evidence(
    timestamp: str = Query(..., description="Horodatage exact de l'entrée (ex. 2026-08-04T14:22:07Z)"),
    action: str = Query(..., description="BLOCK | UNBLOCK | RETRY_OK | ABANDON | IDENT_KO"),
    mac: str | None = Query(None, description="MAC du LR de l'entrée"),
) -> EvidenceResponse:
    """Preuve d'exécution d'une action : ce qui est réellement passé sur le LR.

    La ligne de journal ne porte qu'une phrase que **nous** avons rédigée. Cette
    route rend ce que l'**équipement** a reçu et répondu — c'est la différence
    entre « on a demandé la coupure » et « le LR l'a appliquée ».

    404 si aucune preuve n'est archivée pour cette entrée : actions antérieures à
    la fonctionnalité, mode whatsapp_only (pas encore couvert), ou ordres qui
    n'ont jamais atteint le LR par un autre chemin. L'UI n'appelle cette route
    que sur les entrées marquées ``has_evidence``.

    ⚠️ Les trois paramètres composent un nom de fichier : ils sont normalisés par
    ``fai_audit._evidence_filename`` (allowlist + basename), sans quoi un
    ``mac=../..`` ferait servir un fichier arbitraire du conteneur.
    """
    transcript = fai_audit.read_evidence(timestamp, mac, action)
    if transcript is None:
        raise HTTPException(
            status_code=404,
            detail="Aucune preuve archivée pour cette entrée du journal.",
        )
    return EvidenceResponse(
        timestamp=timestamp, mac=mac, action=action, transcript=transcript
    )
