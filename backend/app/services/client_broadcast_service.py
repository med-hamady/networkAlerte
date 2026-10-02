"""Message WhatsApp aux clients — choix des destinataires, file d'envoi, relance.

Page /broadcast. Un opérateur choisit une ou plusieurs catégories d'abonnés,
écrit un message ; `create_broadcast` fige la liste des numéros, puis
`client_broadcast_job` la vide un message à la fois (`process_queue`).

⚠️ **Les catégories sont celles de /access, avec la même règle** :
  - `active`             = non bloqué ET pas hors supervision (la tuile « Accès actif ») ;
  - `blocked`            = `client_blocked` ;
  - `out_of_supervision` = `schemas.device.is_out_of_supervision`, importée et
    jamais recopiée — sinon « actifs » ici et « Accès actif » là-bas finiraient
    par ne plus compter les mêmes clients.
Plusieurs catégories = leur UNION. Les trois ensemble = tout le parc.

⚠️ **Aucune colonne téléphone en base** : le numéro vit dans le NOM du LR
(« 44910449- Habib Khoumeini », cf. `core/permissions.py`). On ne retient qu'un
mobile mauritanien à 8 chiffres commençant par 2, 3 ou 4 ; un nom sans numéro
valide est RENDU dans l'aperçu (`without_phone`), jamais deviné.

⚠️ **Un message à la fois, espacé** (`client_broadcast_delay_seconds`) :
WhatsApp bannit un numéro qui écrit en rafale à des centaines de destinataires.
C'est la ressource à protéger — le numéro de l'entreprise porte aussi les
alertes du réseau.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.client_broadcast import ClientBroadcast, ClientBroadcastRecipient
from app.models.device import Lr
from app.schemas.device import is_out_of_supervision
from app.services import whatsapp_service

logger = logging.getLogger(__name__)

AUDIENCES = ("active", "blocked", "out_of_supervision")
COUNTRY_CODE = "222"
MAX_MESSAGE_LENGTH = 4000

# Numéro en tête du nom d'abord (la convention « <tel>- <nom> »), sinon n'importe
# où — jamais au milieu d'une suite de chiffres plus longue (un numéro de contrat).
_LEADING_RE = re.compile(r"^\s*(?:\+?222)?([234]\d{7})(?!\d)")
_ANYWHERE_RE = re.compile(r"(?<!\d)(?:\+?222)?([234]\d{7})(?!\d)")


class BroadcastError(Exception):
    """Refus métier — rendu en 409 par l'endpoint, avec ce message."""


def extract_phone(name: str | None) -> str | None:
    """Numéro local à 8 chiffres lu dans le nom du LR, ou None."""
    if not name:
        return None
    m = _LEADING_RE.match(name) or _ANYWHERE_RE.search(name)
    return m.group(1) if m else None


def normalize_audiences(audiences: Iterable[str]) -> list[str]:
    """Catégories valides, dans l'ordre du catalogue, sans doublon."""
    wanted = set(audiences)
    unknown = wanted - set(AUDIENCES)
    if unknown:
        raise BroadcastError(f"Catégorie inconnue : {', '.join(sorted(unknown))}")
    if not wanted:
        raise BroadcastError("Choisir au moins une catégorie de clients")
    return [a for a in AUDIENCES if a in wanted]


def lr_categories(client_blocked: bool, ip_address: str | None,
                  uisp_last_seen: datetime.datetime | None) -> set[str]:
    """Les catégories de /access auxquelles ce LR appartient."""
    oos = is_out_of_supervision(ip_address, uisp_last_seen)
    cats: set[str] = set()
    if client_blocked:
        cats.add("blocked")
    if oos:
        cats.add("out_of_supervision")
    if not client_blocked and not oos:
        cats.add("active")
    return cats


@dataclass
class AudiencePreview:
    lr_count: int = 0
    # (numéro, nom) — un seul par numéro, le premier LR rencontré (ordre par id).
    recipients: list[tuple[str, str]] = field(default_factory=list)
    without_phone: list[str] = field(default_factory=list)
    duplicate_count: int = 0


async def select_audience(session: AsyncSession, audiences: list[str]) -> AudiencePreview:
    wanted = set(audiences)
    rows = (
        await session.execute(
            select(Lr.id, Lr.name, Lr.client_blocked, Lr.ip_address, Lr.uisp_last_seen)
            .order_by(Lr.id)
        )
    ).all()

    preview = AudiencePreview()
    seen: set[str] = set()
    for row in rows:
        if not lr_categories(row.client_blocked, row.ip_address, row.uisp_last_seen) & wanted:
            continue
        preview.lr_count += 1
        phone = extract_phone(row.name)
        if phone is None:
            preview.without_phone.append(row.name or f"#{row.id}")
            continue
        if phone in seen:
            preview.duplicate_count += 1
            continue
        seen.add(phone)
        preview.recipients.append((phone, row.name or ""))
    return preview


async def running_broadcast(session: AsyncSession) -> ClientBroadcast | None:
    return (
        await session.execute(
            select(ClientBroadcast).where(ClientBroadcast.status == "running")
            .order_by(ClientBroadcast.id).limit(1)
        )
    ).scalar_one_or_none()


async def create_broadcast(
    session: AsyncSession, *, audiences: list[str], message: str, created_by: str | None,
) -> ClientBroadcast:
    """Fige les destinataires et met l'envoi en file. Ne commit pas.

    ⚠️ Un seul envoi à la fois : un double clic, ou deux opérateurs qui écrivent
    au même moment, enverraient sinon deux messages à chaque client.
    """
    audiences = normalize_audiences(audiences)
    message = message.strip()
    if not message:
        raise BroadcastError("Le message est vide")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise BroadcastError(f"Message trop long ({len(message)} caractères, {MAX_MESSAGE_LENGTH} au plus)")
    if not get_settings().whatsapp_direct_available:
        raise BroadcastError(
            "WhatsApp n'est pas configuré sur le serveur (WHATSAPP_INSTANCE_ID / WHATSAPP_TOKEN)"
        )
    current = await running_broadcast(session)
    if current is not None:
        raise BroadcastError(
            f"Un envoi est déjà en cours (n° {current.id}) — attendre sa fin ou l'arrêter"
        )

    preview = await select_audience(session, audiences)
    if not preview.recipients:
        raise BroadcastError("Aucun numéro de téléphone parmi les clients choisis")

    broadcast = ClientBroadcast(
        message=message, audiences=",".join(audiences), status="running",
        created_by=created_by,
    )
    session.add(broadcast)
    await session.flush()
    session.add_all(
        ClientBroadcastRecipient(
            broadcast_id=broadcast.id, phone=phone, name=(name or None) and name[:200],
            status="pending", attempts=0,
        )
        for phone, name in preview.recipients
    )
    await session.flush()
    logger.info(
        "Message aux clients n° %d créé par %s — %d destinataire(s), catégories %s",
        broadcast.id, created_by or "clé API", len(preview.recipients), broadcast.audiences,
    )
    return broadcast


async def status_counts(session: AsyncSession, broadcast_ids: list[int]) -> dict[int, dict[str, int]]:
    """{broadcast_id: {statut: nombre}} — une seule requête pour tout l'historique."""
    if not broadcast_ids:
        return {}
    rows = (
        await session.execute(
            select(ClientBroadcastRecipient.broadcast_id, ClientBroadcastRecipient.status,
                   func.count())
            .where(ClientBroadcastRecipient.broadcast_id.in_(broadcast_ids))
            .group_by(ClientBroadcastRecipient.broadcast_id, ClientBroadcastRecipient.status)
        )
    ).all()
    out: dict[int, dict[str, int]] = {bid: {} for bid in broadcast_ids}
    for bid, status, n in rows:
        out[bid][status] = n
    return out


async def retry_failed(session: AsyncSession, broadcast: ClientBroadcast) -> int:
    """Remet les échecs en file. Ne touche ni aux envoyés ni aux arrêtés. Ne commit pas."""
    if broadcast.status == "running":
        raise BroadcastError("L'envoi est encore en cours — relancer les échecs à la fin")
    other = await running_broadcast(session)
    if other is not None:
        raise BroadcastError(f"Un autre envoi est en cours (n° {other.id})")
    result = await session.execute(
        update(ClientBroadcastRecipient)
        .where(ClientBroadcastRecipient.broadcast_id == broadcast.id,
               ClientBroadcastRecipient.status == "failed")
        .values(status="pending", error=None)
    )
    count = result.rowcount or 0
    if count == 0:
        raise BroadcastError("Aucun échec à relancer")
    broadcast.status = "running"
    broadcast.finished_at = None
    return count


async def cancel(session: AsyncSession, broadcast: ClientBroadcast) -> int:
    """Arrête l'envoi : ce qui n'est pas encore parti ne partira pas. Ne commit pas.

    Le job relit le statut avant CHAQUE message, donc l'arrêt prend effet au
    message suivant — celui en vol (une requête HTTP) ne se rattrape pas.
    """
    if broadcast.status != "running":
        raise BroadcastError("Cet envoi n'est pas en cours")
    result = await session.execute(
        update(ClientBroadcastRecipient)
        .where(ClientBroadcastRecipient.broadcast_id == broadcast.id,
               ClientBroadcastRecipient.status == "pending")
        .values(status="cancelled")
    )
    broadcast.status = "cancelled"
    broadcast.finished_at = datetime.datetime.now(datetime.UTC)
    return result.rowcount or 0


async def _finish_drained(session: AsyncSession) -> None:
    """Passe en `done` tout envoi en cours qui n'a plus personne en attente."""
    pending = (
        select(ClientBroadcastRecipient.id)
        .where(ClientBroadcastRecipient.broadcast_id == ClientBroadcast.id,
               ClientBroadcastRecipient.status == "pending")
        .exists()
    )
    await session.execute(
        update(ClientBroadcast)
        .where(ClientBroadcast.status == "running", ~pending)
        .values(status="done", finished_at=datetime.datetime.now(datetime.UTC))
        .execution_options(synchronize_session=False)
    )


Sender = Callable[[str, str], Awaitable[tuple[bool, str]]]


async def process_queue(
    session: AsyncSession,
    *,
    budget_s: float,
    delay_s: float,
    send: Sender | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """Envoie les messages en attente pendant au plus `budget_s`. Rend le nombre traité.

    Un message, un commit : un redémarrage au milieu d'un envoi n'en renvoie
    aucun (au pire celui qui était en vol). Le destinataire suivant et le statut
    de son envoi sont RELUS à chaque tour, donc un arrêt demandé depuis la page
    prend effet au message suivant.
    """
    send = send or whatsapp_service.send_whatsapp_to
    started = clock()
    handled = 0
    await _finish_drained(session)
    await session.commit()

    while True:
        row = (
            await session.execute(
                select(ClientBroadcastRecipient, ClientBroadcast)
                .join(ClientBroadcast, ClientBroadcast.id == ClientBroadcastRecipient.broadcast_id)
                .where(ClientBroadcast.status == "running",
                       ClientBroadcastRecipient.status == "pending")
                .order_by(ClientBroadcast.id, ClientBroadcastRecipient.id)
                .limit(1)
            )
        ).first()
        if row is None:
            await _finish_drained(session)
            await session.commit()
            return handled

        recipient, broadcast = row
        ok, detail = await send(f"+{COUNTRY_CODE}{recipient.phone}", broadcast.message)
        recipient.attempts += 1
        if ok:
            recipient.status = "sent"
            recipient.error = None
            recipient.sent_at = datetime.datetime.now(datetime.UTC)
        else:
            recipient.status = "failed"
            recipient.error = detail[:300]
            logger.warning("Message aux clients n° %d — échec vers %s : %s",
                           broadcast.id, recipient.phone, detail)
        await session.commit()
        handled += 1

        if clock() - started + delay_s >= budget_s:
            await _finish_drained(session)
            await session.commit()
            return handled
        await sleep(delay_s)
