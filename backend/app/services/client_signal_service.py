"""Qualité du signal d'un client par MAC — service de GET /client-signal.

Un système tiers passe le MAC du LR client ; on renvoie une **catégorie
qualitative** du signal (`excellent` / `bien` / `moyen` / `faible`) calculée à
partir de la **dernière valeur de signal connue en base** (``device_metrics``,
métrique ``signal_dbm``, collapse latest-only alimenté en continu par les polls
de fond). Aucune interrogation live — lecture DB quasi instantanée.
"""

import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Lr
from app.schemas.client_signal import ClientSignalResponse
from app.schemas.device import normalize_mac

_M_SIGNAL = "signal_dbm"

# Libellés renvoyés au consommateur, par catégorie.
_MESSAGES = {
    "excellent": "Signal excellent",
    "bien": "Signal bien",
    "moyen": "Signal moyen",
    "faible": "Signal faible",
    "indetermine": "Signal indéterminé",
}


def classify_signal(signal_dbm: float | None, settings) -> str:
    """Catégorie qualitative d'une valeur de signal en dBm.

    Bandes (réutilisent les seuils d'alerte existants + ``signal_excellent_dbm``) :
      • ``excellent``  : signal ≥ signal_excellent_dbm (défaut -65)
      • ``bien``       : signal_warning_dbm < signal < signal_excellent_dbm (-75..-65)
      • ``moyen``      : signal_critical_dbm ≤ signal ≤ signal_warning_dbm (-80..-75)
      • ``faible``     : signal < signal_critical_dbm (< -80)
      • ``indetermine``: aucune valeur (None)
    """
    if signal_dbm is None:
        return "indetermine"
    if signal_dbm >= settings.signal_excellent_dbm:
        return "excellent"
    if signal_dbm > settings.signal_warning_dbm:
        return "bien"
    if signal_dbm >= settings.signal_critical_dbm:
        return "moyen"
    return "faible"


async def get_client_signal(db: AsyncSession, mac: str) -> ClientSignalResponse | None:
    """Qualité du signal du LR identifié par ``mac``.

    Renvoie ``None`` si aucun LR ne porte ce MAC (→ 404 côté endpoint). Si le LR
    existe mais n'a aucune mesure de signal récente, ``quality='indetermine'``.
    Lève ``ValueError`` si le MAC fourni est mal formé.
    """
    normalized = normalize_mac(mac)  # ValueError si format invalide

    lr = (
        await db.execute(select(Lr).where(func.lower(Lr.mac_address) == normalized))
    ).scalar_one_or_none()
    if lr is None:
        return None

    signal_dbm, measured_at = await _latest_signal(db, lr.id)
    settings = get_settings()
    quality = classify_signal(signal_dbm, settings)

    return ClientSignalResponse(
        mac=normalized,
        lr_id=lr.id,
        lr_name=lr.name,
        status=lr.status,
        signal_dbm=signal_dbm,
        quality=quality,
        message=_MESSAGES[quality],
        measured_at=measured_at,
    )


async def _latest_signal(
    db: AsyncSession, lr_id: int
) -> tuple[float | None, datetime.datetime | None]:
    """Dernière valeur de ``signal_dbm`` du LR + son horodatage, ou (None, None).

    ``signal_dbm`` est collapsé latest-only dans ``device_metrics`` (1 ligne
    fraîche par device), donc c'est un lookup trivial servi par
    ``ix_device_metrics_lookup``."""
    sql = text(
        """
        SELECT metric_value, collected_at
        FROM device_metrics
        WHERE device_id = :lr_id AND metric_name = :metric_name
        ORDER BY collected_at DESC
        LIMIT 1
        """
    )
    row = (await db.execute(sql, {"lr_id": lr_id, "metric_name": _M_SIGNAL})).first()
    if row is None:
        return None, None
    return float(row.metric_value), row.collected_at
