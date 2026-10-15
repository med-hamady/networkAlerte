"""Message aux clients (WhatsApp et/ou SMS) — page /broadcast.

L'endpoint ne fait qu'ENREGISTRER l'envoi : ce sont les jobs
`client_broadcast_job` (WhatsApp) et `client_broadcast_sms_job` (SMS) qui
écrivent aux clients, chacun à son rythme. Un envoi dure
~1 h pour 800 abonnés, bien au-delà des 30 s qu'nginx laisse à une requête, et
doit survivre à la fermeture de l'onglet comme à un redémarrage.

⚠️ Deux droits distincts : `broadcast.view` (voir les envois et leur résultat)
et `broadcast.send` (écrire à tout un parc depuis le numéro de l'entreprise,
relancer, arrêter). L'aperçu est sous `broadcast.send` : il ne sert qu'à
préparer un envoi.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission, require_user_or_api_key
from app.core.config import get_settings
from app.db.session import get_db
from app.models.client_broadcast import ClientBroadcast, ClientBroadcastRecipient
from app.models.user import User
from app.schemas.client_broadcast import (
    BroadcastAudienceRequest,
    BroadcastCounts,
    BroadcastCreateRequest,
    BroadcastDetail,
    BroadcastFailure,
    BroadcastList,
    BroadcastPreview,
    BroadcastRead,
)
from app.services import client_broadcast_service as svc

logger = logging.getLogger(__name__)
router = APIRouter()

_VIEW = [Depends(require_permission("broadcast.view", "broadcast.send"))]
_SEND = [Depends(require_permission("broadcast.send"))]


def _read(b: ClientBroadcast, by_channel: dict[str, dict[str, int]]) -> BroadcastRead:
    per_channel = {
        ch: BroadcastCounts(**counts, total=sum(counts.values()))
        for ch, counts in by_channel.items()
    }
    total: dict[str, int] = {}
    for counts in by_channel.values():
        for status, n in counts.items():
            total[status] = total.get(status, 0) + n
    remaining = 0
    if b.status == "running":
        # Les canaux avancent en parallèle : c'est le plus lent qui fixe la fin.
        remaining = max(
            (int(c.pending * svc.channel_delay(ch)) for ch, c in per_channel.items()),
            default=0,
        )
    return BroadcastRead(
        id=b.id, created_at=b.created_at, created_by=b.created_by,
        finished_at=b.finished_at, status=b.status,
        audiences=[a for a in b.audiences.split(",") if a],
        channels=[c for c in b.channels.split(",") if c],
        message=b.message,
        counts=BroadcastCounts(**total, total=sum(total.values())),
        by_channel=per_channel,
        remaining_seconds=remaining,
    )


async def _get_or_404(db: AsyncSession, broadcast_id: int) -> ClientBroadcast:
    b = await db.get(ClientBroadcast, broadcast_id)
    if b is None:
        raise HTTPException(status_code=404, detail=f"Envoi {broadcast_id} introuvable")
    return b


async def _detail(db: AsyncSession, b: ClientBroadcast) -> BroadcastDetail:
    counts = (await svc.status_counts(db, [b.id]))[b.id]
    failures = (
        await db.execute(
            select(ClientBroadcastRecipient)
            .where(ClientBroadcastRecipient.broadcast_id == b.id,
                   ClientBroadcastRecipient.status == "failed")
            .order_by(ClientBroadcastRecipient.channel, ClientBroadcastRecipient.id)
        )
    ).scalars().all()
    return BroadcastDetail(
        **_read(b, counts).model_dump(),
        failures=[
            BroadcastFailure(channel=f.channel, phone=f.phone, name=f.name,
                             error=f.error, attempts=f.attempts)
            for f in failures
        ],
    )


@router.get("", response_model=BroadcastList, dependencies=_VIEW)
async def list_broadcasts(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> BroadcastList:
    """Les derniers envois, le plus récent d'abord, avec leurs compteurs."""
    rows = (
        await db.execute(select(ClientBroadcast).order_by(ClientBroadcast.id.desc()).limit(limit))
    ).scalars().all()
    counts = await svc.status_counts(db, [b.id for b in rows])
    running = next((b.id for b in rows if b.status == "running"), None)
    if running is None:
        current = await svc.running_broadcast(db)
        running = current.id if current else None
    return BroadcastList(
        broadcasts=[_read(b, counts[b.id]) for b in rows],
        running_id=running,
        delay_seconds=get_settings().client_broadcast_delay_seconds,
        service_enabled=svc.service_enabled(),
    )


@router.post("/preview", response_model=BroadcastPreview, dependencies=_SEND)
async def preview_broadcast(
    body: BroadcastAudienceRequest, db: AsyncSession = Depends(get_db),
) -> BroadcastPreview:
    """Qui recevrait le message — sans rien envoyer."""
    try:
        audiences = svc.normalize_audiences(body.audiences)
        channels = svc.normalize_channels(body.channels)
    except svc.BroadcastError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    p = await svc.select_audience(db, audiences)
    settings = get_settings()
    return BroadcastPreview(
        audiences=audiences, channels=channels, lr_count=p.lr_count,
        recipient_count=len(p.recipients),
        duplicate_count=p.duplicate_count, without_phone=p.without_phone,
        estimated_seconds=max(int(len(p.recipients) * svc.channel_delay(c)) for c in channels),
        whatsapp_available=settings.whatsapp_direct_available,
        sms_available=settings.sms_available,
    )


@router.post("", response_model=BroadcastDetail, status_code=201, dependencies=_SEND)
async def create_broadcast(
    body: BroadcastCreateRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(require_user_or_api_key),
) -> BroadcastDetail:
    """Met l'envoi en file. Les messages partent ensuite, un par un, par le job."""
    try:
        b = await svc.create_broadcast(
            db, audiences=list(body.audiences), message=body.message,
            channels=list(body.channels),
            created_by=user.username if user is not None else None,
        )
    except svc.BroadcastDisabledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except svc.BroadcastError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return await _detail(db, b)


@router.get("/{broadcast_id}", response_model=BroadcastDetail, dependencies=_VIEW)
async def get_broadcast(broadcast_id: int, db: AsyncSession = Depends(get_db)) -> BroadcastDetail:
    return await _detail(db, await _get_or_404(db, broadcast_id))


@router.post("/{broadcast_id}/retry-failed", response_model=BroadcastDetail, dependencies=_SEND)
async def retry_failed(broadcast_id: int, db: AsyncSession = Depends(get_db)) -> BroadcastDetail:
    """Renvoie le message aux SEULS numéros en échec — jamais à ceux qui l'ont reçu."""
    b = await _get_or_404(db, broadcast_id)
    try:
        await svc.retry_failed(db, b)
    except svc.BroadcastDisabledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except svc.BroadcastError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return await _detail(db, b)


@router.post("/{broadcast_id}/cancel", response_model=BroadcastDetail, dependencies=_SEND)
async def cancel_broadcast(broadcast_id: int, db: AsyncSession = Depends(get_db)) -> BroadcastDetail:
    """Arrête l'envoi : les messages pas encore partis ne partiront pas."""
    b = await _get_or_404(db, broadcast_id)
    try:
        await svc.cancel(db, b)
    except svc.BroadcastError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await db.commit()
    return await _detail(db, b)
