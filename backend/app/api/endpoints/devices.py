import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.device import Device, Lr, Rocket
from app.models.device_metric import DeviceMetric
from app.schemas.device import (
    ClientModemRead,
    DeviceCreate,
    DeviceRead,
    DeviceUpdate,
    LrRead,
    RocketRead,
    UispPowerRead,
    UispSwitchRead,
)
from app.services import device_service, lan_discovery, ssh_service

router = APIRouter()


class MetricPoint(BaseModel):
    value: float
    unit: str | None
    collected_at: datetime.datetime


# Picks the Pydantic *Read class matching a Device subclass instance.
_READ_BY_TYPE: dict[str, type] = {
    "rocket": RocketRead,
    "lr": LrRead,
    "uisp_power": UispPowerRead,
    "uisp_switch": UispSwitchRead,
    "client_modem": ClientModemRead,
}


def _to_read(device: Device) -> DeviceRead:
    """Convert a polymorphic Device ORM instance into the right *Read schema."""
    read_cls = _READ_BY_TYPE.get(device.device_type)
    if read_cls is None:
        raise HTTPException(
            status_code=500,
            detail=f"Unknown device_type {device.device_type!r} — schema mismatch.",
        )
    return read_cls.model_validate(device)


@router.get("", response_model=list[DeviceRead])
async def list_devices(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[DeviceRead]:
    """List all monitored devices — polymorphic read returns subclass-specific fields."""
    devices = await device_service.get_devices(db, skip=skip, limit=limit)
    return [_to_read(d) for d in devices]


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DeviceRead:
    """Get a single device by ID — returns the type-specific shape."""
    device = await device_service.get_device(db, device_id)
    return _to_read(device)


@router.post("", response_model=DeviceRead, status_code=201)
async def create_device(
    data: DeviceCreate,
    db: AsyncSession = Depends(get_db),
) -> DeviceRead:
    """Register a new device for monitoring. Payload is discriminated on `device_type`."""
    device = await device_service.create_device(db, data)
    return _to_read(device)


@router.put("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: int,
    data: DeviceUpdate,
    db: AsyncSession = Depends(get_db),
) -> DeviceRead:
    """Update an existing device. Caller must send a payload matching the device's type."""
    device = await device_service.update_device(db, device_id, data)
    return _to_read(device)


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
    """Remove a device from monitoring (cascades to type-specific row + dependents)."""
    await device_service.delete_device(db, device_id)


class DiagResult(BaseModel):
    ok: bool
    message: str


def _ssh_credentials(device: Device) -> tuple[str | None, str | None, int]:
    """Return (username, password, port) for SSH. Only Rocket and Lr expose these.

    Callers handle the None/None case by surfacing a 400-style error message,
    so this stays loose — no type check, just attribute access.
    """
    if isinstance(device, (Rocket, Lr)):
        return (device.ssh_username, device.ssh_password, device.ssh_port or 22)
    return (None, None, 22)


@router.post("/{device_id}/check-ssh", response_model=DiagResult)
async def check_ssh(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiagResult:
    """Test SSH connectivity. On first success we pin the host key fingerprint (TOFU)."""
    device = await device_service.get_device(db, device_id)
    username, password, port = _ssh_credentials(device)
    if not username or not password:
        return DiagResult(
            ok=False,
            message="SSH credentials missing on this device — set ssh_username/ssh_password via PUT /api/v1/devices/{id}.",
        )
    fingerprint = getattr(device, "ssh_host_fingerprint", None)
    ok, msg, observed_fp = await ssh_service.check_ssh_access(
        host=device.ip_address,
        port=port,
        username=username,
        password=password,
        expected_fingerprint=fingerprint,
    )
    if ok and observed_fp and fingerprint != observed_fp:
        device.ssh_host_fingerprint = observed_fp
        await db.flush()
    return DiagResult(ok=ok, message=msg)


class LanNeighborOut(BaseModel):
    ip: str
    mac: str
    interface: str
    is_default_gateway: bool
    vendor: str
    model_guess: str | None = None


class DiscoverModemsResponse(BaseModel):
    lr_id: int
    candidates: list[LanNeighborOut]


@router.post("/{device_id}/discover-modems", response_model=DiscoverModemsResponse)
async def discover_modems(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiscoverModemsResponse:
    """Discover TP-Link modems on the LR's LAN side via SSH + ARP scrape.

    The endpoint is mounted on the LR (not on a candidate modem) — the modem
    typically does not exist yet in the DB when this is called from the create
    form. Returns gateway-first list; the operator picks one.
    """
    device = await device_service.get_device(db, device_id)
    if not isinstance(device, Lr):
        raise HTTPException(
            status_code=400,
            detail="Modem discovery is only available on LR devices.",
        )
    if not (device.ssh_username and device.ssh_password):
        raise HTTPException(
            status_code=400,
            detail="LR has no SSH credentials — set ssh_username/ssh_password first.",
        )
    try:
        neighbours = await lan_discovery.discover_via_lr(
            host=device.ip_address,
            port=device.ssh_port or 22,
            username=device.ssh_username,
            password=device.ssh_password,
            expected_fingerprint=device.ssh_host_fingerprint,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH discovery failed: {exc}") from exc

    return DiscoverModemsResponse(
        lr_id=device.id,
        candidates=[LanNeighborOut(**n.__dict__) for n in neighbours],
    )


@router.post("/{device_id}/check-ping", response_model=DiagResult)
async def check_ping(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiagResult:
    """SSH into the device and ping 8.8.8.8 to test internet transit."""
    device = await device_service.get_device(db, device_id)
    username, password, port = _ssh_credentials(device)
    if not username or not password:
        return DiagResult(
            ok=False,
            message="SSH credentials missing on this device — set ssh_username/ssh_password via PUT /api/v1/devices/{id}.",
        )
    fingerprint = getattr(device, "ssh_host_fingerprint", None)
    ok, msg, observed_fp = await ssh_service.check_ping_via_ssh(
        host=device.ip_address,
        port=port,
        username=username,
        password=password,
        expected_fingerprint=fingerprint,
    )
    if ok and observed_fp and fingerprint != observed_fp:
        device.ssh_host_fingerprint = observed_fp
        await db.flush()
    return DiagResult(ok=ok, message=msg)
