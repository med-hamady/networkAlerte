import datetime
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.device import ClientModem, Device, Lr, Rocket
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
    normalize_mac,
)
from app.services import device_service, lan_discovery, ltu_api_service, ssh_service

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


@router.get("/{device_id}/metrics/live", response_model=dict[str, MetricPoint])
async def get_device_metrics_live(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, MetricPoint]:
    """Fetch radio metrics LIVE from the device, bypassing the 60 s snapshot.

    Radio values (signal, capacity, link potential, RX rates…) fluctuate
    per-second; the poll job's last DB row lags the device dashboard. For an
    LR we hit its parent Rocket's HTTP API and extract this LR's peer
    (matched by MAC); for a Rocket we hit its own API. Display-only — the
    poll job still owns history/alerting. Non-2xx on any failure so the UI
    can keep showing the DB value.
    """
    device = await device_service.get_device(db, device_id)

    if device.device_type == "rocket":
        rocket: Rocket = device  # type: ignore[assignment]
        lr_mac: str | None = None
    elif device.device_type == "lr":
        if device.rocket_id is None:
            raise HTTPException(status_code=409, detail="LR sans Rocket parent — pas de source live.")
        rocket = (
            await db.execute(select(Rocket).where(Rocket.id == device.rocket_id))
        ).scalar_one_or_none()
        if rocket is None:
            raise HTTPException(status_code=409, detail="Rocket parent introuvable.")
        lr_mac = device.mac_address
    else:
        raise HTTPException(status_code=400, detail="Métriques live : LR ou Rocket uniquement.")

    if rocket.radio_tech != "ltu":
        raise HTTPException(status_code=409, detail="Live indisponible : Rocket airMAX (SNMP only).")
    if not rocket.ssh_username or not rocket.ssh_password:
        raise HTTPException(status_code=503, detail="Identifiants API du Rocket manquants en base.")

    rocket_ap, all_peers, per_peer = await ltu_api_service.collect_ltu_api_full(
        host=rocket.ip_address,
        username=rocket.ssh_username,
        password=rocket.ssh_password,
        port=443,
    )
    if rocket_ap is None:
        raise HTTPException(status_code=502, detail="Rocket injoignable (API HTTP).")

    if lr_mac is not None:
        try:
            want = normalize_mac(lr_mac)
        except ValueError:
            want = lr_mac.lower()
        src: dict[str, float | None] | None = next(
            (m for mac, m in per_peer if mac and mac.lower() == want), None
        )
        if src is None:
            raise HTTPException(
                status_code=404,
                detail="LR absent des peers du Rocket à cet instant (lien radio down ?).",
            )
    else:
        src = dict(rocket_ap)
        src["peer_count"] = float(len(all_peers))

    now = datetime.datetime.now(datetime.UTC)
    return {
        name: MetricPoint(
            value=value,
            unit=ltu_api_service.METRIC_UNITS.get(name),
            collected_at=now,
        )
        for name, value in src.items()
        if value is not None
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


@router.post("/{device_id}/ping-from-lr", response_model=DiagResult)
async def ping_from_lr(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiagResult:
    """Ping a client modem from its parent LR (the SSH jump host).

    The modem sits behind the LR's NAT and is unreachable from the
    supervisor directly, so reachability is checked from the LR itself —
    the same path the interactive shell uses. Verifies L3 connectivity
    before the operator opens a terminal.
    """
    device = await device_service.get_device(db, device_id)
    if not isinstance(device, ClientModem):
        raise HTTPException(
            status_code=400,
            detail="Ping-from-LR is only available on client_modem devices.",
        )
    lr = device.lr
    if lr is None:
        return DiagResult(
            ok=False,
            message="Ce modem n'a pas de LR de rattachement — définis lr_id d'abord.",
        )
    if not (lr.ssh_username and lr.ssh_password):
        return DiagResult(
            ok=False,
            message=f"Le LR parent ({lr.name}) n'a pas d'identifiants SSH.",
        )
    ok, msg, observed_fp = await ssh_service.ping_targets_via_ssh(
        host=lr.ip_address,
        port=lr.ssh_port or 22,
        username=lr.ssh_username,
        password=lr.ssh_password,
        targets=[device.ip_address],
        expected_fingerprint=lr.ssh_host_fingerprint,
    )
    if ok and observed_fp and lr.ssh_host_fingerprint != observed_fp:
        lr.ssh_host_fingerprint = observed_fp
        await db.flush()
    if ok:
        message = f"Modem {device.ip_address} joignable depuis le LR {lr.name}."
    else:
        message = (
            f"Modem {device.ip_address} injoignable depuis le LR {lr.name} — {msg}"
        )
    return DiagResult(ok=ok, message=message)


class PingTargetRequest(BaseModel):
    target: str


@router.post("/{device_id}/ping-target", response_model=DiagResult)
async def ping_target(
    device_id: int,
    body: PingTargetRequest,
    db: AsyncSession = Depends(get_db),
) -> DiagResult:
    """Ping an arbitrary IP from an LR (jump host).

    Lets the operator test a discovered modem candidate's reachability
    *before* it is saved as a client_modem device. Mounted on the LR (its
    id is known from discovery); ``target`` is the candidate IP.
    """
    device = await device_service.get_device(db, device_id)
    if not isinstance(device, Lr):
        raise HTTPException(
            status_code=400,
            detail="Ping-target is only available on LR devices.",
        )
    if not (device.ssh_username and device.ssh_password):
        return DiagResult(
            ok=False,
            message=f"Le LR {device.name} n'a pas d'identifiants SSH.",
        )
    target = body.target.strip()
    try:
        ipaddress.ip_address(target)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="target doit être une adresse IP valide.",
        ) from exc
    ok, msg, observed_fp = await ssh_service.ping_targets_via_ssh(
        host=device.ip_address,
        port=device.ssh_port or 22,
        username=device.ssh_username,
        password=device.ssh_password,
        targets=[target],
        expected_fingerprint=device.ssh_host_fingerprint,
    )
    if ok and observed_fp and device.ssh_host_fingerprint != observed_fp:
        device.ssh_host_fingerprint = observed_fp
        await db.flush()
    if ok:
        message = f"{target} joignable depuis le LR {device.name}."
    else:
        message = f"{target} injoignable depuis le LR {device.name} — {msg}"
    return DiagResult(ok=ok, message=message)


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
