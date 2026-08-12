"""Bandeau d'anomalies à acquitter à la main (haut du dashboard).

Trois anomalies (`MANUAL_ACK_ALERT_TYPES`) y restent affichées jusqu'à ce qu'un
opérateur clique « Résoudre », même si l'anomalie s'est rétablie entre-temps.
L'acquittement est PARTAGÉ : un clic retire la ligne pour toute l'équipe.

⚠️ Ce router ne touche PAS aux incidents. Acquitter une ligne ici ne résout pas
l'incident correspondant (qui se résout tout seul au retour à la normale, comme
avant) et résoudre l'incident n'efface pas la ligne d'ici — c'est exactement le
découplage recherché.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user_or_api_key
from app.db.session import get_db
from app.models.user import User
from app.schemas.manual_alert import ManualAlertList, ManualAlertRead
from app.services import manual_alert_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=ManualAlertList)
async def list_manual_alerts(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> ManualAlertList:
    """Les anomalies en attente d'acquittement, la plus récente d'abord."""
    rows = await manual_alert_service.list_pending(db, limit=limit)
    alerts = [ManualAlertRead.from_alert(alert, device) for alert, device in rows]
    return ManualAlertList(alerts=alerts, count=len(alerts))


@router.post("/{alert_id}/acknowledge", response_model=ManualAlertRead)
async def acknowledge_manual_alert(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(require_user_or_api_key),
) -> ManualAlertRead:
    """Retire une anomalie du bandeau (pour toute l'équipe).

    `user` est None quand l'appel est authentifié par clé API — la ligne est
    alors acquittée sans auteur, ce qui reste vrai et n'empêche rien.
    """
    alert = await manual_alert_service.acknowledge(
        db, alert_id, username=user.username if user is not None else None,
    )
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Anomalie {alert_id} introuvable")
    await db.commit()
    return ManualAlertRead.from_alert(alert)
