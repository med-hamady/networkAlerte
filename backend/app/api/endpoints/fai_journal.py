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

from fastapi import APIRouter, Depends, Query
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
    message: str


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


class JournalResponse(BaseModel):
    entries: list[JournalEntry]
    stats: JournalStats
    attention: list[AttentionRow]


@router.get("", response_model=JournalResponse)
async def get_journal(
    limit: int = Query(200, ge=1, le=1000),
    action: str | None = Query(None, description="BLOCK | UNBLOCK | RETRY_OK | ABANDON | IDENT_KO"),
    status: str | None = Query(None, description="ok | failed | abandoned"),
    search: str | None = Query(None, description="Filtre sur la MAC ou le nom du client"),
    db: AsyncSession = Depends(get_db),
) -> JournalResponse:
    """Historique des actions de blocage + LR encore en souffrance."""
    entries, stats = fai_audit.read_entries(
        limit=limit, action=action, status=status, search=search,
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
        )
        for lr in result.scalars().all()
    ]
    # Les cas bloquants (intervention technique) d'abord.
    attention.sort(key=lambda r: (r.kind != "unenforceable", r.name))

    return JournalResponse(
        entries=[JournalEntry(**e) for e in entries],
        stats=JournalStats(**stats),
        attention=attention,
    )
