import datetime
import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alert_constants import (
    AVAILABILITY_ALERT_TYPES,
    CLIENT_KEPT_ALERT_TYPES,
    CLIENT_RULE_CATEGORY,
    INFRA_DEVICE_SUPPRESSED_ALERT_TYPES,
)
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


def is_suppressed_incident(device: Device, alert_type: str | None) -> bool:
    """True if this (device, alert_type) is a client-side incident we neither
    create nor store.

    The /incidents page is infrastructure-only. The split is by DEVICE
    (rule_category), not by alert_type, because radio alert_types fire on both
    base-station Rockets (kept) and subscriber LRs (dropped). Two exceptions
    override the device rule — see alert_constants for the full rationale:
      - INFRA_DEVICE_SUPPRESSED_ALERT_TYPES: dropped even on an infra device
        (cpe_disconnected = a subscriber CPE vanished, client-side churn).
      - CLIENT_KEPT_ALERT_TYPES: kept even on an LR (lr_bridge_mode_misconfig
        breaks the client-block feature, the operator must act).
    """
    if alert_type in INFRA_DEVICE_SUPPRESSED_ALERT_TYPES:
        return True
    return (
        device.rule_category == CLIENT_RULE_CATEGORY
        and alert_type not in CLIENT_KEPT_ALERT_TYPES
    )


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
) -> tuple[Incident | None, bool]:
    """
    Open a new incident for a device if no open incident with the same alert_type/title exists.
    Returns (incident, is_new) — is_new is False when an existing incident was found.
    When not new, last_triggered_at is updated to now.

    Client-side incidents are suppressed at this single chokepoint: when
    is_suppressed_incident() matches, no row is created and (None, False) is
    returned. Every caller only dereferences the incident when is_new is True,
    so a None incident is safe (see alert_engine._open_alert, jobs._open_and_notify,
    discovery_service._emit_lifecycle_event).
    """
    if is_suppressed_incident(device, alert_type):
        return None, False

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


def is_availability_incident(alert_type: str | None) -> bool:
    """True if this alert_type is an availability/outage type (device down).

    Only these incidents are kept in DB once resolved — the downtime journal
    reconstructs past outages + availability % from their resolved_at. Every
    other resolved incident is purged on resolution (no /archive view).
    """
    return alert_type in AVAILABILITY_ALERT_TYPES


async def resolve_incidents(
    db: AsyncSession,
    device_id: int,
    title: str,
    alert_type: str | None = None,
) -> list[Incident]:
    """
    Resolve all open incidents matching device and alert_type (preferred) or title.
    Returns the list of resolved incidents (empty if none were open).

    Resolution is terminal: non-availability incidents are hard-deleted right
    away (there is no /archive view anymore). Availability incidents (device
    down) are kept with status=resolved so the downtime journal can still
    reconstruct past outages + availability %. The returned objects keep their
    loaded attributes either way, so the caller can still send a recovery
    notification for the purged ones. The alerts FK is ON DELETE CASCADE, so a
    purged incident's audit rows go with it — the caller must therefore avoid
    creating an audit row for a purged incident (see alert_engine._resolve_alert).
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

    # Purge everything that isn't an availability/outage incident — those don't
    # belong to the downtime journal and there is no /archive to view them in.
    # Issued as a bulk core DELETE: the in-memory objects keep their already
    # loaded attributes for the recovery notification, and the alerts FK
    # (ON DELETE CASCADE) drops their audit rows in the same statement.
    purge_ids = [inc.id for inc in incidents if not is_availability_incident(inc.alert_type)]
    if purge_ids:
        await db.execute(
            delete(Incident)
            .where(Incident.id.in_(purge_ids))
            .execution_options(synchronize_session=False)
        )

    logger.info(
        "Incident resolved — device_id=%d | %s (%d resolved, %d purged)",
        device_id,
        alert_type or title,
        len(incidents),
        len(purge_ids),
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
