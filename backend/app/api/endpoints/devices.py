import datetime
import ipaddress
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.device import AirFiber, ClientModem, Device, Lr, Rocket
from app.schemas.device import (
    AirFiberRead,
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
from app.services import (
    af60_api_service,
    client_block_service,
    device_service,
    lan_discovery,
    lr_plan_service,
    ltu_api_service,
    snmp_service,
    ssh_service,
)

_AIRMAX_LR_VARIANTS = {"litebeam_5ac", "litebeam_m5"}

logger = logging.getLogger(__name__)

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
    "airfiber": AirFiberRead,
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
    site: str | None = Query(
        None,
        description="Filter by resolved site name (indexed) — used by the /sites drill-down.",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[DeviceRead]:
    """List monitored devices — polymorphic read returns subclass-specific fields.

    Pass `site` to load only one site's equipment (fast, indexed) instead of the
    whole fleet.
    """
    devices = await device_service.get_devices(db, skip=skip, limit=limit, site=site)
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


# Latest-metric lookup as a loose index scan (recursive enumeration of the
# distinct metric_names) + a LATERAL "latest row per name" probe. Both halves
# are served by ix_device_metrics_lookup (device_id, metric_name, collected_at),
# so the cost is O(#distinct metrics × log n) and independent of how many rows
# the device has accumulated.
#
# Replaces a GROUP BY metric_name + MAX(collected_at) subquery that the planner
# resolved with a parallel SEQ SCAN of device_metrics (16 M+ rows). On a
# high-cardinality device — the UISP Switch emits ~130 metrics/cycle → 1.6 M
# rows → the query took ~11 s and timed out, so its modal showed no metrics.
# See migration c1f2a3b4d5e6 for the backing index.
_LATEST_METRICS_SQL = text("""
    WITH RECURSIVE names AS (
        (
            SELECT metric_name
            FROM device_metrics
            WHERE device_id = :device_id
            ORDER BY metric_name
            LIMIT 1
        )
        UNION ALL
        SELECT (
            SELECT dm.metric_name
            FROM device_metrics dm
            WHERE dm.device_id = :device_id
              AND dm.metric_name > names.metric_name
            ORDER BY dm.metric_name
            LIMIT 1
        )
        FROM names
        WHERE names.metric_name IS NOT NULL
    )
    SELECT names.metric_name, latest.metric_value, latest.unit, latest.collected_at
    FROM names
    CROSS JOIN LATERAL (
        SELECT dm.metric_value, dm.unit, dm.collected_at
        FROM device_metrics dm
        WHERE dm.device_id = :device_id
          AND dm.metric_name = names.metric_name
        ORDER BY dm.collected_at DESC
        LIMIT 1
    ) AS latest
    WHERE names.metric_name IS NOT NULL
""")


@router.get("/{device_id}/metrics/latest", response_model=dict[str, MetricPoint])
async def get_device_metrics_latest(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, MetricPoint]:
    """Return the most recent value of each metric for a device."""
    device = await device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await db.execute(_LATEST_METRICS_SQL, {"device_id": device_id})
    return {
        row.metric_name: MetricPoint(
            value=row.metric_value,
            unit=row.unit,
            collected_at=row.collected_at,
        )
        for row in result
    }


_AIRMAX_METRIC_UNITS = {
    "signal_dbm": "dBm",
    "noise_dbm": "dBm",
    "cinr_db": "dB",
    "ccq_pct": "%",
    "tx_rate_mbps": "Mbps",
    "rx_rate_mbps": "Mbps",
    "radio_rx_bytes": "B",
    "radio_tx_bytes": "B",
    "radio_in_errors": "",
    "radio_out_errors": "",
    "radio_if_up": "",
    "eth_if_up": "",
    "uptime_seconds": "s",
}


async def _live_metrics_airmax_snmp(device: Lr) -> dict[str, MetricPoint]:
    """Live SNMP fetch on an airMAX LR (LiteBeam 5AC/M5)."""
    settings = get_settings()
    community = device.snmp_community or settings.snmp_default_community
    if not community:
        raise HTTPException(status_code=503, detail="SNMP community non configurée pour ce LR.")
    metrics = await snmp_service.collect_airmax_metrics(
        host=device.ip_address,
        community=community,
        port=settings.snmp_port,
        timeout=settings.snmp_timeout,
    )
    if not any(v is not None for v in metrics.values()):
        raise HTTPException(status_code=502, detail="LR injoignable en SNMP.")
    now = datetime.datetime.now(datetime.UTC)
    return {
        name: MetricPoint(
            value=value,
            unit=_AIRMAX_METRIC_UNITS.get(name, ""),
            collected_at=now,
        )
        for name, value in metrics.items()
        if value is not None
    }


async def _live_metrics_af60(device: AirFiber) -> dict[str, MetricPoint]:
    """Live fetch on an airFiber 60 (UDAPI, identique aux LTU)."""
    if not device.ssh_username or not device.ssh_password:
        raise HTTPException(status_code=503, detail="Identifiants API de l'AF60 manquants en base.")
    metrics = await af60_api_service.collect_af60_metrics(
        host=device.ip_address,
        username=device.ssh_username,
        password=device.ssh_password,
        port=device.ssh_port or 443,
    )
    if metrics is None:
        raise HTTPException(status_code=502, detail="AF60 injoignable (API HTTP).")
    now = datetime.datetime.now(datetime.UTC)
    return {
        name: MetricPoint(
            value=value,
            unit=af60_api_service.METRIC_UNITS.get(name),
            collected_at=now,
        )
        for name, value in metrics.items()
        if value is not None
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
        # airMAX LRs (LiteBeam) — fetched SNMP-direct because the parent
        # Rocket airMAX exposes peer identification only, not per-peer radio
        # metrics. LTU LRs continue through the parent-HTTP path below.
        if device.model_variant in _AIRMAX_LR_VARIANTS:
            return await _live_metrics_airmax_snmp(device)
        if device.rocket_id is None:
            raise HTTPException(status_code=409, detail="LR sans Rocket parent — pas de source live.")
        rocket = (
            await db.execute(select(Rocket).where(Rocket.id == device.rocket_id))
        ).scalar_one_or_none()
        if rocket is None:
            raise HTTPException(status_code=409, detail="Rocket parent introuvable.")
        lr_mac = device.mac_address
    elif device.device_type == "airfiber":
        # AF60 backhaul — même UDAPI que LTU, interrogé directement à son IP.
        return await _live_metrics_af60(device)
    else:
        raise HTTPException(status_code=400, detail="Métriques live : AF60, LR ou Rocket.")

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


def _promote_lr_password(
    device: Device, primary: str | None, used: str | None, db: AsyncSession
) -> None:
    """Persist a fallback password that authenticated on an LR (auto-heal).

    Restricted to Lr devices: a Rocket's ``ssh_password`` is its HTTPS API
    secret (port 443), not a real SSH password, so we never clobber it. The
    session is committed by the get_db dependency on request teardown.
    """
    if isinstance(device, Lr) and used and primary and used != primary:
        logger.info(
            "check-ssh: LR '%s' (%s) — fallback password succeeded, "
            "promoting on LR row.",
            device.name, device.ip_address,
        )
        device.ssh_password = used


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
    ok, msg, observed_fp, used_pw = await ssh_service.check_ssh_access(
        host=device.ip_address,
        port=port,
        username=username,
        password=password,
        expected_fingerprint=fingerprint,
        fallback_passwords=get_settings().lr_fallback_password_list,
    )
    if ok and observed_fp and fingerprint != observed_fp:
        device.ssh_host_fingerprint = observed_fp
        await db.flush()
    _promote_lr_password(device, password, used_pw, db)
    return DiagResult(ok=ok, message=msg)


@router.post("/{device_id}/ping-from-lr", response_model=DiagResult)
async def ping_from_lr(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> DiagResult:
    """Ping a client modem from its parent LR.

    The modem sits behind the LR's NAT and is unreachable from the
    supervisor directly, so reachability is checked from the LR itself.
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
    ok, msg, observed_fp, used_pw = await ssh_service.ping_targets_via_ssh(
        host=lr.ip_address,
        port=lr.ssh_port or 22,
        username=lr.ssh_username,
        password=lr.ssh_password,
        targets=[device.ip_address],
        expected_fingerprint=lr.ssh_host_fingerprint,
        fallback_passwords=get_settings().lr_fallback_password_list,
    )
    if ok and observed_fp and lr.ssh_host_fingerprint != observed_fp:
        lr.ssh_host_fingerprint = observed_fp
        await db.flush()
    _promote_lr_password(lr, lr.ssh_password, used_pw, db)
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
    ok, msg, observed_fp, used_pw = await ssh_service.ping_targets_via_ssh(
        host=device.ip_address,
        port=device.ssh_port or 22,
        username=device.ssh_username,
        password=device.ssh_password,
        targets=[target],
        expected_fingerprint=device.ssh_host_fingerprint,
        fallback_passwords=get_settings().lr_fallback_password_list,
    )
    if ok and observed_fp and device.ssh_host_fingerprint != observed_fp:
        device.ssh_host_fingerprint = observed_fp
        await db.flush()
    _promote_lr_password(device, device.ssh_password, used_pw, db)
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
        neighbours, used_pw = await lan_discovery.discover_via_lr(
            host=device.ip_address,
            port=device.ssh_port or 22,
            username=device.ssh_username,
            password=device.ssh_password,
            expected_fingerprint=device.ssh_host_fingerprint,
            fallback_passwords=get_settings().lr_fallback_password_list,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SSH discovery failed: {exc}") from exc
    _promote_lr_password(device, device.ssh_password, used_pw, db)

    return DiscoverModemsResponse(
        lr_id=device.id,
        candidates=[LanNeighborOut(**n.__dict__) for n in neighbours],
    )


class BlockClientRequest(BaseModel):
    reason: str | None = None
    # "full" = total cut (shut LAN port). "whatsapp_only" = iptables allowlist
    # leaving DNS + WhatsApp/Meta reachable. Omitted → server default.
    mode: Literal["full", "whatsapp_only"] | None = None


class ClientBlockResult(BaseModel):
    ok: bool
    message: str
    client_blocked: bool
    block_mode: str
    client_block_enforced_at: datetime.datetime | None


def _block_result(lr: Lr, ok: bool, message: str) -> ClientBlockResult:
    return ClientBlockResult(
        ok=ok,
        message=message,
        client_blocked=lr.client_blocked,
        block_mode=lr.block_mode,
        client_block_enforced_at=lr.client_block_enforced_at,
    )


@router.post("/{device_id}/block-client", response_model=ClientBlockResult)
async def block_client(
    device_id: int,
    body: BlockClientRequest,
    db: AsyncSession = Depends(get_db),
) -> ClientBlockResult:
    """Cut a client's internet by shutting its LR's LAN port over SSH.

    Two modes: `full` (shut the LAN port — total cut) or `whatsapp_only`
    (iptables allowlist leaving DNS + WhatsApp reachable). SSH reaches the LR
    through the radio link, so the LR stays manageable. The block is persisted
    and re-asserted by the enforcement job — it survives an LR reboot. Only
    valid on LR devices.
    """
    device = await device_service.get_device(db, device_id)
    if not isinstance(device, Lr):
        raise HTTPException(
            status_code=400,
            detail="Le blocage client n'est disponible que sur les LR.",
        )
    if device.topology_mode == "bridge":
        # Bridge mode → iptables FORWARD and the local dnsmasq are bypassed.
        # Any block we apply would silently fail to actually cut the client.
        # Refuse with a clear message; the misconfig is also tracked as an
        # incident (AT_LR_BRIDGE_MODE_MISCONFIG) so the operator is notified.
        raise HTTPException(
            status_code=409,
            detail=(
                f"Le LR '{device.name}' est en mode bridge. Le blocage client "
                f"ne peut pas fonctionner dans cette configuration (le LR est "
                f"L2-transparent, iptables et dnsmasq sont contournés). "
                f"Reconfigurer le LR en mode routeur via son interface web "
                f"(airOS), puis réessayer."
            ),
        )
    ok, message = await client_block_service.block_client(
        db, device, body.reason, body.mode
    )
    return _block_result(device, ok, message)


class ShaperRule(BaseModel):
    devname: str
    role: str          # "wan" (radio uplink) | "lan" (customer-facing)
    direction: str     # "download" | "upload"
    rate_kbps: int
    rate_mbps: float


class LrPlanResult(BaseModel):
    ok: bool
    message: str
    shaper_enabled: bool = False
    download_mbps: float | None = None
    upload_mbps: float | None = None
    rules: list[ShaperRule] = []


class PlanSyncSummary(BaseModel):
    eligible: int
    updated: int
    no_shaper: int
    failed: int


@router.post("/plans/sync", response_model=PlanSyncSummary)
async def sync_lr_plans(
    db: AsyncSession = Depends(get_db),
) -> PlanSyncSummary:
    """Lit et met en cache le forfait de TOUS les LR joignables via SSH.

    Pour chaque LR ``up`` avec credentials SSH, lit les caps du traffic shaper
    et stocke ``plan_download_mbps`` / ``plan_upload_mbps`` (affichés ensuite
    sur le frontend sans re-SSH). Tâche lourde — un SSH par LR, concurrence
    bornée par ``lr_probe_concurrency``. Le job ``lr_plan_sync`` la relance
    quotidiennement ; cet endpoint permet de la déclencher à la demande.
    """
    summary = await lr_plan_service.sync_all_lr_plans(db)
    return PlanSyncSummary(**summary)


@router.get("/{device_id}/plan", response_model=LrPlanResult)
async def get_lr_plan(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> LrPlanResult:
    """Read a client's subscription plan (forfait) from its LR's traffic shaper.

    The plan (download/upload Mbps) is provisioned on the LR as an airOS
    traffic-shaper rate cap — it is not exposed by any device HTTP API, so we
    read it over SSH. ``rules`` shows exactly which interface/direction each cap
    came from. The commercial plan *name* is not on the device (CRM-only). Only
    valid on LR devices.
    """
    device = await device_service.get_device(db, device_id)
    if not isinstance(device, Lr):
        raise HTTPException(
            status_code=400,
            detail="Le forfait n'est lisible que sur les LR (équipement client).",
        )
    ok, plan, message = await lr_plan_service.get_lr_plan(device)
    plan = plan or {}
    return LrPlanResult(
        ok=ok,
        message=message,
        shaper_enabled=plan.get("shaper_enabled", False),
        download_mbps=plan.get("download_mbps"),
        upload_mbps=plan.get("upload_mbps"),
        rules=plan.get("rules", []),
    )


@router.post("/{device_id}/unblock-client", response_model=ClientBlockResult)
async def unblock_client(
    device_id: int,
    db: AsyncSession = Depends(get_db),
) -> ClientBlockResult:
    """Restore a client's internet by bringing its LR's LAN port back up."""
    device = await device_service.get_device(db, device_id)
    if not isinstance(device, Lr):
        raise HTTPException(
            status_code=400,
            detail="Le déblocage client n'est disponible que sur les LR.",
        )
    ok, message = await client_block_service.unblock_client(db, device)
    return _block_result(device, ok, message)


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
    ok, msg, observed_fp, used_pw = await ssh_service.check_ping_via_ssh(
        host=device.ip_address,
        port=port,
        username=username,
        password=password,
        expected_fingerprint=fingerprint,
        fallback_passwords=get_settings().lr_fallback_password_list,
    )
    if ok and observed_fp and fingerprint != observed_fp:
        device.ssh_host_fingerprint = observed_fp
        await db.flush()
    _promote_lr_password(device, password, used_pw, db)
    return DiagResult(ok=ok, message=msg)
