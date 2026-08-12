"""Anomalies du bandeau du dashboard — celles qu'on acquitte à la main.

Trois types (`MANUAL_ACK_ALERT_TYPES`) sont répétés dans un bandeau en haut du
dashboard et n'en partent que sur un clic « Résoudre ». Ce module possède ce
canal de bout en bout : l'enregistrement à la détection, la lecture du bandeau,
l'acquittement.

⚠️ Le cycle de vie des incidents n'est pas touché — voir `alert_constants.
MANUAL_ACK_ALERT_TYPES` pour le pourquoi de la table séparée.
"""

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alert_constants import MANUAL_ACK_ALERT_TYPES
from app.models.device import Device
from app.models.manual_alert import ManualAlert

logger = logging.getLogger(__name__)


def needs_manual_ack(alert_type: str | None) -> bool:
    """True si ce type doit apparaître dans le bandeau à acquitter."""
    return alert_type in MANUAL_ACK_ALERT_TYPES


def record_detection(
    db: AsyncSession,
    device: Device,
    alert_type: str | None,
    title: str,
    severity: str,
    description: str | None,
    detected_at: datetime.datetime,
) -> ManualAlert | None:
    """Pose une ligne de bandeau pour une anomalie qui vient d'être détectée.

    Appelé depuis `incident_service.open_incident` UNIQUEMENT quand un incident
    nouveau est créé (is_new=True) — jamais sur le ré-déclenchement d'un
    incident déjà ouvert. C'est ce qui donne la règle de récidive voulue : une
    anomalie qui dure ne se resignale pas, une anomalie qui revient après
    rétablissement, si.

    Pas de `flush` ici : l'appelant en fait déjà un pour l'incident, et la ligne
    doit partager sa transaction — un bandeau qui signalerait une anomalie dont
    l'incident a été annulé par un rollback mentirait.
    """
    if not needs_manual_ack(alert_type):
        return None

    alert = ManualAlert(
        device_id=device.id,
        alert_type=alert_type,
        severity=severity,
        title=title,
        description=description,
        detected_at=detected_at,
    )
    db.add(alert)
    logger.info(
        "Anomalie à acquitter — %s sur %s (%s)",
        alert_type,
        device.name,
        device.ip_address,
    )
    return alert


async def list_pending(db: AsyncSession, limit: int = 100) -> list[tuple[ManualAlert, Device | None]]:
    """Les anomalies encore à acquitter, la plus récente d'abord.

    Jointure externe sur l'équipement : la FK est en CASCADE donc une ligne
    orpheline ne devrait pas exister, mais le bandeau ne doit jamais tomber sur
    l'affichage — une anomalie sans nom d'équipement reste plus utile qu'une
    erreur.
    """
    result = await db.execute(
        select(ManualAlert, Device)
        .outerjoin(Device, Device.id == ManualAlert.device_id)
        .where(ManualAlert.acknowledged_at.is_(None))
        .order_by(ManualAlert.detected_at.desc())
        .limit(limit),
    )
    return [(row[0], row[1]) for row in result.all()]


async def count_pending(db: AsyncSession) -> int:
    """Nombre d'anomalies en attente d'acquittement."""
    result = await db.execute(
        select(ManualAlert.id).where(ManualAlert.acknowledged_at.is_(None)),
    )
    return len(result.scalars().all())


async def acknowledge(
    db: AsyncSession,
    alert_id: int,
    username: str | None = None,
) -> ManualAlert | None:
    """Acquitte une anomalie. Rend None si l'id n'existe pas.

    Idempotent : ré-acquitter une ligne déjà acquittée ne réécrit ni la date ni
    l'auteur — le premier clic est celui qui compte, et deux onglets ouverts sur
    le même bandeau ne doivent pas se disputer la paternité.
    """
    alert = await db.get(ManualAlert, alert_id)
    if alert is None:
        return None
    if alert.acknowledged_at is not None:
        return alert

    alert.acknowledged_at = datetime.datetime.now(datetime.UTC)
    alert.acknowledged_by = username
    logger.info(
        "Anomalie acquittée — manual_alert_id=%d (%s) par %s",
        alert_id,
        alert.alert_type,
        username or "clé API",
    )
    return alert
