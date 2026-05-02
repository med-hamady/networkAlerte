import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.device_metric import DeviceMetric
from app.schemas.device import DeviceCreate, DeviceRead, DeviceUpdate
from app.services import device_service, ssh_service

router = APIRouter()


class MetricPoint(BaseModel):
    value: float
    unit: str | None
    collected_at: datetime.datetime


@router.get("/", response_model=list[DeviceRead])
async def list_devices(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[DeviceRead]:
    """List all monitored devices."""
    devices = await device_service.get_devices(db, skip=skip, limit=limit)
    return [DeviceRead.model_validate(d) for d in devices]


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DeviceRead:
    """Get a single device by ID."""
    device = await device_service.get_device(db, device_id)
    return DeviceRead.model_validate(device)


@router.post("/", response_model=DeviceRead, status_code=201)
async def create_device(
    data: DeviceCreate,
    db: AsyncSession = Depends(get_db),
) -> DeviceRead:
    """Register a new device for monitoring."""
    device = await device_service.create_device(db, data)
    return DeviceRead.model_validate(device)


@router.put("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: int,
    data: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
) -> DeviceRead:
    """Update an existing device."""
    device = await device_service.update_device(db, device_id, data)
    return DeviceRead.model_validate(device)


@router.get("/{device_id}/metrics/latest", response_model=dict[str, MetricPoint])
async def get_device_metrics_latest(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, MetricPoint]:
    """Return the most recent value of each metric for a device."""
    device = await device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    # Subquery: latest collected_at per metric_name
    sub = (
        select(
            DeviceMetric.metric_name,
            func.max(DeviceMetric.collected_at).label("max_ts"),
        )
        .where(DeviceMetric.device_id == device_id)
        .group_by(DeviceMetric.metric_name)
        .subquery()
    )
    result = await db.execute(
        select(DeviceMetric).join(
            sub,
            (DeviceMetric.metric_name == sub.c.metric_name)
            & (DeviceMetric.collected_at == sub.c.max_ts)
            & (DeviceMetric.device_id == device_id),
        )
    )
    rows = result.scalars().all()
    return {
        row.metric_name: MetricPoint(
            value=row.metric_value,
            unit=row.unit,
            collected_at=row.collected_at,
        )
        for row in rows
    }


@router.delete("/{device_id}", status_code=204)
async def delete_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove a device from monitoring."""
    await device_service.delete_device(db, device_id)


class DiagResult(BaseModel):
    ok: bool
    message: str


def _ssh_credentials(device, settings) -> tuple[str, str, int]:
    """Return (username, password, port) for SSH.

    Per-device ssh_password takes priority over global .env credentials.
    This allows devices with non-default passwords to authenticate correctly.
    """
    if device.device_type == "ltu_lr":
        return (
            device.ssh_username or settings.ltu_lr_ssh_username,
            device.ssh_password or settings.ltu_lr_ssh_password,
            device.ssh_port or settings.ltu_lr_ssh_port,
        )
    return (
        device.ssh_username or settings.ltu_api_username,
        device.ssh_password or settings.ltu_api_password,
        device.ssh_port or 22,
    )



@router.post("/{device_id}/check-ssh", response_model=DiagResult)
async def check_ssh(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiagResult:
    """Test SSH connectivity to the device. On first successful connect we
    record the device's host key fingerprint (TOFU) so subsequent connects
    can detect a swapped device or MITM."""
    device = await device_service.get_device(db, device_id)
    settings = get_settings()
    username, password, port = _ssh_credentials(device, settings)
    ok, msg, observed_fp = await ssh_service.check_ssh_access(
        host=device.ip_address,
        port=port,
        username=username,
        password=password,
        expected_fingerprint=device.ssh_host_fingerprint,
    )
    if ok and observed_fp and device.ssh_host_fingerprint != observed_fp:
        device.ssh_host_fingerprint = observed_fp
        await db.flush()
    return DiagResult(ok=ok, message=msg)


@router.post("/{device_id}/check-ping", response_model=DiagResult)
async def check_ping(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiagResult:
    """SSH into the device and ping 8.8.8.8 to test internet transit."""
    device = await device_service.get_device(db, device_id)
    settings = get_settings()
    username, password, port = _ssh_credentials(device, settings)
    ok, msg, observed_fp = await ssh_service.check_ping_via_ssh(
        host=device.ip_address,
        port=port,
        username=username,
        password=password,
        expected_fingerprint=device.ssh_host_fingerprint,
    )
    if ok and observed_fp and device.ssh_host_fingerprint != observed_fp:
        device.ssh_host_fingerprint = observed_fp
        await db.flush()
    return DiagResult(ok=ok, message=msg)
