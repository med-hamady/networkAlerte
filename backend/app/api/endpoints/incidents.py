import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.device import Device
from app.models.incident import Incident
from app.schemas.incident import IncidentRead, IncidentUpdate
from app.services import incident_service

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_STATUSES = {"open", "acknowledged", "resolved"}


async def _devices_by_id(
    db: AsyncSession, incidents: list[Incident],
) -> dict[int, Device]:
    """Bulk-load the devices referenced by a list of incidents."""
    if not incidents:
        return {}
    ids = {i.device_id for i in incidents}
    result = await db.execute(select(Device).where(Device.id.in_(ids)))
    return {d.id: d for d in result.scalars().all()}


@router.get("/", response_model=list[IncidentRead])
async def list_incidents(
    status: str | None = Query(None, description="Filter by status: open|acknowledged|resolved"),
    severity: str | None = Query(None, description="Filter by severity: info|warning|critical"),
    device_id: int | None = Query(None, description="Filter by device ID"),
    alert_type: str | None = Query(None, description="Filter by alert type e.g. signal_low, ccq_low"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[IncidentRead]:
    """List incidents with optional filters. Sorted by detection date (newest first)."""
    incidents = await incident_service.get_incidents(
        db,
        status=status,
        severity=severity,
        device_id=device_id,
        alert_type=alert_type,
        skip=skip,
        limit=limit,
    )
    devices = await _devices_by_id(db, incidents)
    return [IncidentRead.from_incident(i, devices.get(i.device_id)) for i in incidents]


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: int,
    db: AsyncSession = Depends(get_db),
) -> IncidentRead:
    """Get a single incident by ID."""
    incident = await incident_service.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")
    device = await db.get(Device, incident.device_id)
    return IncidentRead.from_incident(incident, device)


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident_status(
    incident_id: int,
    data: IncidentUpdate,
    db: AsyncSession = Depends(get_db),
) -> IncidentRead:
    """
    Update incident status manually.
    Allowed transitions: open → acknowledged, open/acknowledged → resolved.
    """
    if data.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{data.status}'. Must be one of: {sorted(VALID_STATUSES)}",
        )

    incident = await incident_service.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"Incident {incident_id} not found")

    if data.status == "resolved" and incident.status != "resolved":
        incident.status = "resolved"
        incident.resolved_at = datetime.datetime.now(datetime.UTC)
    elif data.status == "acknowledged" and incident.status == "open":
        incident.status = "acknowledged"
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Cannot transition from '{incident.status}' to '{data.status}'",
        )

    logger.info("Incident %d manually set to '%s'", incident_id, data.status)
    device = await db.get(Device, incident.device_id)
    return IncidentRead.from_incident(incident, device)
