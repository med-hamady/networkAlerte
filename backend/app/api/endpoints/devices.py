import datetime
import ipaddress
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.device import ClientModem, Device, Lr, Rocket
from app.schemas.device import (
    AirFiberRead,
    ClientModemRead,
    DeviceCreate,
    DeviceRead,
    DeviceUpdate,
    LrRead,
    PtpLiteBeamRead,
    RocketRead,
    UispPowerRead,
    UispSwitchRead,
)
from app.services import (
    client_block_service,
    device_service,
    lan_discovery,
    lr_metric_history_service,
    lr_plan_service,
    ssh_service,
    threshold_service,
)

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
    "ptp_litebeam": PtpLiteBeamRead,
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


class DeviceSearchResult(BaseModel):
    id: int
    name: str
    ip_address: str | None
    device_type: str
    site: str | None
    status: str


@router.get("/search", response_model=list[DeviceSearchResult])
async def search_devices(
    q: str = Query(..., min_length=2, description="Match on name or IP (LR name carries the client phone)."),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[DeviceSearchResult]:
    """Search bar lookup for /sites — match any device by name or IP."""
    rows = await device_service.search_devices(db, q.strip(), limit=limit)
    return [
        DeviceSearchResult(
            id=r.id,
            name=r.name,
            ip_address=r.ip_address,
            device_type=r.device_type,
            site=r.site,
            status=r.status,
        )
        for r in rows
    ]


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


class MetricPointHist(BaseModel):
    bucket_start: datetime.datetime
    avg_value: float
    min_value: float
    max_value: float
    sample_count: int


class MetricOption(BaseModel):
    name: str
    label: str
    unit: str


class MetricHistory(BaseModel):
    device_id: int
    metric_name: str
    label: str
    unit: str
    zero_based: bool
    start: datetime.datetime
    end: datetime.datetime
    bin_seconds: int
    # Seuil d'alerte effectif de cette métrique, et son sens : "max" = alerte
    # au-dessus (latence), "min" = alerte en dessous (capacité). None quand la
    # métrique n'a pas de seuil (les débits du lien).
    threshold: float | None
    threshold_direction: Literal["max", "min"] | None
    # Les métriques que CE device a réellement en historique — les onglets du
    # graphe s'y limitent (un LTU LR et un LiteBeam ne rapportent pas le même jeu).
    available_metrics: list[MetricOption]
    points: list[MetricPointHist]


@router.get("/{device_id}/metric-history", response_model=MetricHistory)
async def get_device_metric_history(
    device_id: int,
    metric: str = Query(
        "lr_latency_ms",
        description="Métrique à tracer — une clé de lr_metric_history_service.GRAPH_METRICS.",
    ),
    period: Literal["24h", "7d", "30d"] = Query(
        "24h", description="Fenêtre relative. Ignorée si start/end sont fournis.",
    ),
    start: datetime.date | None = Query(
        None, description="Début de plage personnalisée (YYYY-MM-DD, UTC). Requiert `end`."
    ),
    end: datetime.date | None = Query(
        None, description="Fin de plage incluse (YYYY-MM-DD, UTC). Requiert `start`."
    ),
    db: AsyncSession = Depends(get_db),
) -> MetricHistory:
    """History of one graphable metric for one device — the device-modal charts.

    Either a preset ``period`` or a custom ``start``/``end`` date range (inclusive
    of both days, UTC — same convention as /clients/consumption), which wins over
    ``period``. Points are 5-min buckets over 24h, re-binned server-side on wider
    windows so the payload stays a few hundred points.

    ``metric`` is checked against the GRAPH_METRICS allowlist rather than passed
    to SQL — an arbitrary metric_name would let a caller mine any series in the
    table, and would silently return an empty chart for a typo.

    A device with no history for this metric returns an empty series rather than
    a 4xx: the modal drives its tabs off ``available_metrics``, and a device that
    simply hasn't been polled yet is not an error.

    Missing buckets are absent from ``points`` (no zero-filling): nothing was
    measured then, and a 0 would read as "0 ms" / "0 Mb/s".
    """
    if (start is None) != (end is None):
        raise HTTPException(
            status_code=422, detail="`start` et `end` doivent être fournis ensemble.",
        )
    if start is not None and end is not None and end < start:
        raise HTTPException(
            status_code=422, detail="`end` doit être postérieure ou égale à `start`.",
        )

    spec = lr_metric_history_service.GRAPH_METRICS.get(metric)
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Métrique '{metric}' non traçable. Valeurs acceptées : "
                f"{', '.join(lr_metric_history_service.GRAPH_METRICS)}."
            ),
        )

    device = await device_service.get_device(db, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    range_start, range_end, bin_seconds = lr_metric_history_service.resolve_range(
        period, start, end,
    )
    points = await lr_metric_history_service.get_history(
        db, device_id, metric,
        start=range_start, end=range_end, bin_seconds=bin_seconds,
    )

    # Le seuil vient des settings EFFECTIFS (surcharge DB comprise) → le graphe
    # trace exactement la ligne qui déclenche l'alerte, sans la coder en dur.
    threshold: float | None = None
    if spec["threshold_setting"]:
        settings = await threshold_service.get_effective_settings(db, get_settings())
        threshold = float(getattr(settings, spec["threshold_setting"]))

    return MetricHistory(
        device_id=device_id,
        metric_name=metric,
        label=spec["label"],
        unit=spec["unit"],
        zero_based=spec["zero_based"],
        start=range_start,
        end=range_end,
        bin_seconds=bin_seconds,
        threshold=threshold,
        threshold_direction=spec["threshold_direction"],
        available_metrics=[
            MetricOption(**m)
            for m in await lr_metric_history_service.available_metrics(db, device_id)
        ],
        points=[MetricPointHist(**p) for p in points],
    )


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


# ── Content block (per-category destination filter) ─────────────────────────
class ContentBlockCategory(BaseModel):
    key: str
    label: str
    domain_count: int


class ContentBlockRequest(BaseModel):
    # Full desired set of category keys to block (empty = clear the filter).
    categories: list[str] = []


class ContentBlockResult(BaseModel):
    ok: bool
    message: str
    blocked_categories: list[str]
    content_block_enforced_at: datetime.datetime | None


@router.get("/content-block/categories", response_model=list[ContentBlockCategory])
async def content_block_categories() -> list[ContentBlockCategory]:
    """List the content-filter categories the operator can toggle per client."""
    settings = get_settings()
    return [
        ContentBlockCategory(
            key=key,
            label=settings.content_block_label(key),
            domain_count=len(domains),
        )
        for key, domains in settings.content_block_catalog().items()
    ]


@router.put("/{device_id}/content-block", response_model=ContentBlockResult)
async def set_content_block(
    device_id: int,
    body: ContentBlockRequest,
    db: AsyncSession = Depends(get_db),
) -> ContentBlockResult:
    """Set a client's per-category content filter (DNS-poison on its LR).

    Independent of block-client: the client keeps full internet except toward
    the selected services (e.g. TikTok). ``categories`` is the complete desired
    set — an empty list clears the filter. Persisted and re-asserted by the
    enforcement job (survives an LR reboot). Only valid on LR devices in router
    mode.
    """
    device = await device_service.get_device(db, device_id)
    if not isinstance(device, Lr):
        raise HTTPException(
            status_code=400,
            detail="Le filtre de contenu n'est disponible que sur les LR.",
        )
    if device.topology_mode == "bridge":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Le LR '{device.name}' est en mode bridge. Le filtre de contenu "
                f"ne peut pas fonctionner (le LR est L2-transparent, dnsmasq est "
                f"contourné). Reconfigurer le LR en mode routeur via airOS, puis "
                f"réessayer."
            ),
        )
    ok, message = await client_block_service.set_content_block(
        db, device, body.categories
    )
    return ContentBlockResult(
        ok=ok,
        message=message,
        blocked_categories=device.blocked_categories or [],
        content_block_enforced_at=device.content_block_enforced_at,
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
