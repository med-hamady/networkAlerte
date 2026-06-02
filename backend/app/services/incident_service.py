import datetime
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.incident import Incident

logger = logging.getLogger(__name__)

_SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}

# incidents.title is VARCHAR(255). Rule messages that concatenate several
# offending metrics (e.g. lr_link_substandard listing 4 floors) plus a long
# device name can exceed that. The full text always lives in `description`
# (TEXT), so the title is safely truncated. Postgres varchar(n) counts
# characters, so Python len() is the right measure here.
_TITLE_MAX_LEN = 255


def _truncate_title(title: str) -> str:
    if len(title) <= _TITLE_MAX_LEN:
        return title
    return title[: _TITLE_MAX_LEN - 1] + "…"


async def get_open_incident(
    db: AsyncSession,
    device_id: int,
    title: str,
    alert_type: str | None = None,
) -> Incident | None:
    """Return an open incident matching device and alert_type (preferred) or title."""
    if alert_type:
        result = await db.execute(
            select(Incident).where(
                Incident.device_id == device_id,
                Incident.status == "open",
                Incident.alert_type == alert_type,
            )
        )
        inc = result.scalar_one_or_none()
        if inc is not None:
            return inc

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device_id,
            Incident.status == "open",
            Incident.title == title,
        )
    )
    return result.scalar_one_or_none()


async def open_incident(
    db: AsyncSession,
    device: Device,
    title: str,
    severity: str = "critical",
    description: str | None = None,
    alert_type: str | None = None,
    metric_name: str | None = None,
    metric_value: float | None = None,
    threshold_value: float | None = None,
) -> tuple[Incident, bool]:
    """
    Open a new incident for a device if no open incident with the same alert_type/title exists.
    Returns (incident, is_new) — is_new is False when an existing incident was found.
    When not new, last_triggered_at is updated to now.
    """
    # Truncate before both the dedup lookup and the insert so the title used
    # for matching is identical to the one stored (avoids dedup misses).
    title = _truncate_title(title)
    existing = await get_open_incident(db, device.id, title, alert_type=alert_type)
    if existing:
        existing.last_triggered_at = datetime.datetime.now(datetime.UTC)
        if metric_value is not None:
            existing.metric_value = metric_value
            # Refresh metric_name/threshold too so the diagnostic reflects the
            # CURRENT worst offender (e.g. lr_link_substandard surfaces which
            # specific floor is breached, not the generic label at first open).
            if metric_name is not None:
                existing.metric_name = metric_name
            if threshold_value is not None:
                existing.threshold_value = threshold_value
        if severity and _SEVERITY_RANK.get(severity, 0) > _SEVERITY_RANK.get(existing.severity or "", 0):
            existing.severity = severity
        return existing, False

    now = datetime.datetime.now(datetime.UTC)
    incident = Incident(
        device_id=device.id,
        title=title,
        description=description,
        severity=severity,
        status="open",
        detected_at=now,
        alert_type=alert_type,
        metric_name=metric_name,
        metric_value=metric_value,
        threshold_value=threshold_value,
        last_triggered_at=now,
    )
    db.add(incident)
    await db.flush()
    logger.warning(
        "Incident opened — [%s] %s (%s) | %s",
        severity.upper(),
        device.name,
        device.ip_address,
        title,
    )
    return incident, True


async def resolve_incidents(
    db: AsyncSession,
    device_id: int,
    title: str,
    alert_type: str | None = None,
) -> list[Incident]:
    """
    Resolve all open incidents matching device and alert_type (preferred) or title.
    Returns the list of resolved incidents (empty if none were open).
    """
    conditions = [
        Incident.device_id == device_id,
        Incident.status == "open",
    ]
    if alert_type:
        conditions.append(Incident.alert_type == alert_type)
    else:
        conditions.append(Incident.title == title)

    result = await db.execute(select(Incident).where(*conditions))
    incidents = list(result.scalars().all())
    if not incidents:
        return []

    now = datetime.datetime.now(datetime.UTC)
    for inc in incidents:
        inc.status = "resolved"
        inc.resolved_at = now

    logger.info(
        "Incident resolved — device_id=%d | %s (%d resolved)",
        device_id,
        alert_type or title,
        len(incidents),
    )
    return incidents


async def delete_open_incidents(
    db: AsyncSession,
    device_id: int,
) -> int:
    """
    Hard-delete all OPEN incidents for a device. Returns the number deleted.

    Used when a client-side LR goes down: a down LR is a subscriber-side outage
    (client power cut / LR unplugged), never our infra problem, so its open
    incidents (radio quality, link substandard, no transit…) are stale noise.
    We purge them outright rather than leaving them dangling in /incidents —
    consistent with the "LR-side problems are never incidents" policy.

    The alerts FK (alerts.incident_id) is ON DELETE CASCADE, so the matching
    notification audit rows are removed by Postgres in the same statement.
    """
    result = await db.execute(
        delete(Incident)
        .where(
            Incident.device_id == device_id,
            Incident.status == "open",
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0


async def get_incidents(
    db: AsyncSession,
    status: str | None = None,
    severity: str | None = None,
    device_id: int | None = None,
    alert_type: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> list[Incident]:
    """List incidents with optional filters."""
    query = select(Incident).order_by(Incident.detected_at.desc())
    if status:
        query = query.where(Incident.status == status)
    if severity:
        query = query.where(Incident.severity == severity)
    if device_id:
        query = query.where(Incident.device_id == device_id)
    if alert_type:
        query = query.where(Incident.alert_type == alert_type)
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_incident(db: AsyncSession, incident_id: int) -> Incident | None:
    """Get a single incident by ID."""
    result = await db.execute(select(Incident).where(Incident.id == incident_id))
    return result.scalar_one_or_none()
