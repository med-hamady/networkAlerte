"""
Scheduled supervision jobs.

- heartbeat_job     : sanity check every 60s
- infra_ping_job    : ICMP ping des équipements d'infra every 30s → opens/resolves incidents
- client_ping_job   : ICMP ping des LR clients every 60s (statut seul, pas d'incident infra)
- snmp_poll_job     : SNMP metrics for LTU/airMAX devices every 60s → alert engine
- power_poll_job    : UISP Power API polling every 30s → power anomaly detection
- ltu_api_poll_job  : LTU HTTP API polling every 60s → alert engine (radio quality)
- airos_api_poll_job: airMAX LR (LiteBeam) airOS HTTP API polling every 60s → alert engine
- transit_probe_job : Transit connectivity probe every 60s
"""

import asyncio
import datetime
import functools
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.alert_constants import (
    AT_AIRMAX_DOWN,
    AT_BATTERY_EXTERNAL_LOW,
    AT_BATTERY_INTERNAL_LOW,
    AT_DEVICE_FLAPPING,
    AT_LR_BRIDGE_MODE_MISCONFIG,
    AT_LR_LATENCY_HIGH,
    AT_LR_NO_TRANSIT,
    AT_MAINS_POWER_LOST,
    AT_ROCKET_DOWN,
    AT_SWITCH_DOWN,
    AVAILABILITY_ALERT_TYPES,
)
from app.core.alert_constants import AT_BATTERY_LOW_CRIT as AT_BATT_CRIT
from app.core.alert_constants import AT_BATTERY_LOW_WARN as AT_BATT_WARN
from app.core.alert_constants import AT_DEVICE_UNREACHABLE as AT_UNREACHABLE
from app.core.alert_constants import AT_SWITCH_PORT_DOWN as AT_SWITCH_PORT
from app.core.alert_constants import AT_SWITCH_PORT_SPEED_LOW as AT_SWITCH_PORT_SPEED
from app.core.alert_constants import AT_UISP_POWER_UNREACH as AT_POWER_UNREACH
from app.core.alert_constants import AT_VOLTAGE_ANOMALY as AT_VOLT_ANOMALY
from app.core.config import get_settings
from app.db.session import async_session_factory
from app.models.alert_state import AlertState
from app.models.audit_log import AuditLog
from app.models.device import AirFiber, Device, Lr, PtpLiteBeam, Rocket, UispPower
from app.models.device_metric import DeviceMetric
from app.models.incident import Incident
from app.models.power_status_log import PowerStatusLog
from app.services import (
    af60_api_service,
    airos_api_service,
    alert_engine,
    client_block_service,
    digest_service,
    discovery_service,
    incident_service,
    lr_health_service,
    lr_plan_service,
    ltu_api_service,
    notification_service,
    poller,
    saturation_report_service,
    site_infra_service,
    snmp_service,
    ssh_service,
    threshold_service,
    uisp_power_service,
    uisp_sync_service,
    whatsapp_service,
)

logger = logging.getLogger(__name__)


# Last-run duration per scheduled job (seconds), updated by @_timed_job. In the
# scheduler PROCESS only — the API runs in a separate container, so exposing this
# via /system needs a DB-backed store (P2.2 volet B). For now it powers the
# duration log line + lets a same-process caller read it.
JOB_LAST_DURATION: dict[str, float] = {}


# ── Anti-deadlock retry (transient Postgres serialization conflicts) ──────────
# Plusieurs jobs de polling écrivent dans `devices` (ping → last_seen/status,
# ltu/airos/af60/snmp → last_discovered_at, rename, topologie). Ils tournent en
# parallèle sur la boucle asyncio ; quand deux transactions verrouillent les
# mêmes lignes `devices` dans un ordre différent, Postgres tue l'une des deux
# avec `deadlock detected` (SQLSTATE 40P01). C'est transitoire : la transaction
# victime est rollback par Postgres, et les jobs de polling sont idempotents
# (latest-wins) → il suffit de **rejouer le job**. On NE sérialise PAS les jobs
# (un verrou global tiendrait pendant les envois SMTP faits dans la session, eux
# lents jusqu'à 8 s) : on rejoue, donc rien n'est perdu et aucune I/O n'est
# bloquée. On couvre aussi 40001 (serialization_failure) par sécurité.
_RETRYABLE_SQLSTATES: frozenset[str] = frozenset({"40P01", "40001"})
_JOB_DB_MAX_RETRIES = 2          # → 3 tentatives au total
_JOB_DB_RETRY_BASE_S = 0.2       # backoff exponentiel de base
_JOB_DB_RETRY_JITTER_S = 0.5     # jitter aléatoire pour désynchroniser les rejeux


def _retryable_db_conflict(exc: BaseException) -> bool:
    """True si l'exception est un deadlock / serialization failure Postgres."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    return code in _RETRYABLE_SQLSTATES


def _timed_job(fn):
    """Log how long each run of a scheduled job takes (observability P2.2) and
    rejoue le job sur deadlock/serialization Postgres (transitoire, sans perte).

    Gives an at-a-glance « durée vs intervalle » signal to spot a job creeping
    toward saturation BEFORE it starts skipping cycles — no more grepping for
    `skipped: maximum instances` after the fact.
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        started = time.monotonic()
        try:
            attempt = 0
            while True:
                try:
                    return await fn(*args, **kwargs)
                except DBAPIError as exc:
                    # Seuls les conflits transitoires sont rejoués ; toute autre
                    # DBAPIError remonte inchangée. La transaction victime est
                    # déjà rollback par Postgres → rejouer le job repart propre.
                    if attempt >= _JOB_DB_MAX_RETRIES or not _retryable_db_conflict(exc):
                        raise
                    attempt += 1
                    backoff = _JOB_DB_RETRY_BASE_S * (2 ** (attempt - 1)) + random.uniform(
                        0, _JOB_DB_RETRY_JITTER_S
                    )
                    logger.warning(
                        "JOB %s — conflit DB transitoire (deadlock/serialization), "
                        "rejeu %d/%d dans %.2f s",
                        fn.__name__, attempt, _JOB_DB_MAX_RETRIES, backoff,
                    )
                    await asyncio.sleep(backoff)
        finally:
            elapsed = time.monotonic() - started
            JOB_LAST_DURATION[fn.__name__] = elapsed
            logger.info("JOB %s — tour terminé en %.2f s", fn.__name__, elapsed)

    return wrapper


# alert_type sentinel used to persist the consecutive-ping-failure counter in
# AlertState. Picking a leading underscore keeps it out of the regular alert
# vocabulary (no policy, no incident, no formatter touches it).
_PING_FAILURE_STATE_KEY = "_ping_failures"


# ── device_metrics persistence policy ───────────────────────────────────────
# The ONLY metrics still read as a TIME SERIES (history) are the cumulative byte
# counters, consumed by consumption_service via LAG() deltas (24h/7d/30d). Every
# other polled metric — including all radio quality metrics (signal/ccq/cinr/
# link_potential/capacity/rate idx) since the 30-day report sections were
# removed — is read ONLY as "latest" (the /metrics/latest LATERAL probe). The
# alert engine reads its baselines (EMA throughput, error deltas) from
# AlertState, never from device_metrics, so collapsing a non-history metric to a
# single row breaks no alert. We therefore APPEND the byte counters (one row per
# cycle) and COLLAPSE everything else to a single row per (device_id,
# metric_name) via DELETE+INSERT — the strategy first proven on UISP Switch
# metrics. Without this a single UISP Power device appends ~25 metrics every
# 30 s (~70k rows/day) that nothing reads past the last point.
HISTORY_METRICS: frozenset[str] = frozenset({
    # consumption_service — cumulative byte counters → only meaningful as deltas
    "peer_tx_bytes", "peer_rx_bytes", "radio_rx_bytes", "radio_tx_bytes",
})


async def persist_device_metrics(
    session,
    device_id: int,
    metrics: dict[str, float | None],
    unit_map: dict[str, str] | None = None,
    *,
    now: datetime.datetime | None = None,
) -> None:
    """Persist one poll cycle of metrics, honouring the history/latest policy.

    - metric_name in :data:`HISTORY_METRICS` → APPEND a new row (series kept).
    - otherwise → COLLAPSE to a single latest row per (device_id, metric_name)
      via DELETE+INSERT (no append-forever bloat).

    ``None`` values are skipped. The caller owns the transaction (no commit).

    The collapse DELETE is **batched** : one ``DELETE … WHERE metric_name IN
    (...)`` for ALL collapse metrics of the device, instead of one DELETE per
    metric. A switch emits ~130 metrics/cycle → this turns ~130 DELETEs into 1
    (×15 switches ≈ 2000 → 15 DELETEs per SNMP cycle). The INSERTs stay ORM
    ``add`` — SQLAlchemy batches them into one ``executemany`` at flush. The
    DELETE rides ``ix_device_metrics_lookup`` (device_id, metric_name); on the
    first cycle after a metric becomes collapse-only it also absorbs its
    historical backlog, inside the scheduler (never on the startup path).
    """
    if now is None:
        now = datetime.datetime.now(datetime.UTC)
    units = unit_map or {}

    # Serialize concurrent writers of THIS device's metrics. A Rocket is persisted
    # every cycle by BOTH snmp_poll_job (IF-MIB) and ltu_api_poll_job (HTTP API);
    # their collapse DELETE+INSERT on the same (device_id, metric_name) rows raced
    # and Postgres killed one with `deadlock detected` (40P01) — the SNMP/LTU jobs
    # failed a whole cycle each time. A per-device transaction-level advisory lock
    # makes the two passes serialize on the device instead of deadlocking; it is
    # released automatically at COMMIT/ROLLBACK. No new cycle is possible: every
    # writer holds the Rocket's key first (ltu before its LR fan-out), so two
    # transactions never grab a shared pair of keys in opposite order.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(device_id)},
    )

    collapse_names = [
        name for name, value in metrics.items()
        if value is not None and name not in HISTORY_METRICS
    ]
    # One batched DELETE for every collapse metric of this device.
    if collapse_names:
        await session.execute(
            delete(DeviceMetric).where(
                DeviceMetric.device_id == device_id,
                DeviceMetric.metric_name.in_(collapse_names),
            )
        )

    for metric_name, value in metrics.items():
        if value is None:
            continue
        session.add(DeviceMetric(
            device_id=device_id,
            metric_name=metric_name,
            metric_value=float(value),
            unit=units.get(metric_name),
            collected_at=now,
        ))


# rule_category buckets used to pick SNMP poll variants.
# LTU Rockets → standard IF-MIB. LTU LRs are NOT polled via SNMP — their
# metrics come from the parent Rocket's HTTP API (peer fan-out in
# ltu_api_poll_job).
RADIO_RULE_CATEGORIES = {"ltu_rocket"}

# airMAX Rockets → UBNT Enterprise MIB + IF-MIB.
AIRMAX_RULE_CATEGORIES = {"airmax_rocket"}

# airMAX LRs (LiteBeam family) → polled directly via their own airOS HTTP API
# (airos_api_poll_job) on their management IP. Their parent Rocket airMAX
# exposes peer identification only (ubntStaTable), not the per-peer link
# metrics; the airOS status.cgi gives the full dashboard set (Link Potential,
# Total Capacity, rate index, signal/CINR) the SNMP MIB lacks.
AIRMAX_LR_VARIANTS = {"litebeam_5ac", "litebeam_m5"}

# Switches → standard SNMP (uptime only).
SWITCH_RULE_CATEGORIES = {"uisp_switch"}

# ---------------------------------------------------------------------------
# Stable alert_type identifiers re-exported above from core/alert_constants
# (single source of truth shared with alert_policy and alert_formatter).
# Legacy title strings kept for notify_incident_resolved compatibility
# ---------------------------------------------------------------------------
INC_UNREACHABLE   = "Device unreachable"
INC_POWER_UNREACH = "UISP Power unreachable"
INC_BATT_WARN     = "Low battery level"
INC_BATT_CRIT     = "Critical battery level"
INC_BATT_INTERNAL = "Batterie interne (Li-Ion) critique"
INC_BATT_EXTERNAL = "Batterie externe (banc plomb) critique"
INC_VOLT_ANOMALY  = "Voltage anomaly"
INC_MAINS_LOST    = "Coupure secteur (sur batterie)"
# Cycles consécutifs sans secteur AC avant d'ouvrir mains_power_lost. À 30 s de
# poll, 2 cycles ≈ 1 min : filtre les micro-coupures / flickers sans retarder
# l'alerte d'une vraie coupure. Résolution dès le 1er cycle où le secteur revient.
MAINS_LOSS_THRESHOLD = 2
# Libellé humain par type de batterie pour les messages d'alerte.
_BATTERY_HUMAN = {
    "li-ion": "batterie interne (Li-Ion)",
    "lead-acid": "banc externe (plomb)",
}
INC_TRANSIT       = "Transit réseau indisponible"
INC_SWITCH_PORT       = "Switch port critique down"
INC_SWITCH_PORT_SPEED = "Port switch vitesse insuffisante"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _alert_type_for_device(device: Device) -> str:
    """Return the appropriate device-down alert_type for a given device type."""
    mapping = {
        "ltu_rocket":    AT_ROCKET_DOWN,
        "uisp_switch":   AT_SWITCH_DOWN,
        "airmax_rocket": AT_AIRMAX_DOWN,
    }
    return mapping.get(device.rule_category, AT_UNREACHABLE)


def _down_title_for_device(device: Device) -> str:
    mapping = {
        "ltu_rocket":    "ALERTE CRITIQUE : LTU Rocket indisponible",
        "uisp_switch":   "ALERTE CRITIQUE : UISP Switch indisponible",
        "airmax_rocket": "ALERTE CRITIQUE : Rocket airMAX indisponible",
    }
    return mapping.get(device.rule_category, INC_UNREACHABLE)


async def _open_and_notify(
    session,
    device: Device,
    title: str,
    severity: str,
    description: str,
    alert_type: str | None = None,
) -> None:
    """Open an incident (if not already open) and send a notification."""
    incident, is_new = await incident_service.open_incident(
        session, device, title, severity, description, alert_type=alert_type
    )
    if is_new:
        await notification_service.notify_incident_opened(device, incident)


async def _resolve_and_notify(
    session,
    device: Device,
    title: str,
    alert_type: str | None = None,
) -> None:
    """Resolve open incidents with the given alert_type/title and send a notification."""
    resolved = await incident_service.resolve_incidents(
        session, device.id, title, alert_type=alert_type
    )
    for incident in resolved:
        await notification_service.notify_incident_resolved(device, incident)


async def _evaluate_lr_latency(
    session, lr: Device, avg_rtt_ms: float, settings,
) -> None:
    """
    LR → Internet latency anti-flap: open a critical incident when the average
    RTT measured from the LR stays at or above `lr_latency_critical_ms` for
    `lr_latency_failure_threshold` consecutive cycles. Resolve as soon as one
    cycle drops back below the threshold.

    State (consecutive bad cycles) is persisted in AlertState keyed by
    AT_LR_LATENCY_HIGH so it survives a backend restart.
    """
    threshold = settings.lr_latency_critical_ms
    needed = settings.lr_latency_failure_threshold

    state_res = await session.execute(
        select(AlertState).where(
            AlertState.device_id == lr.id,
            AlertState.alert_type == AT_LR_LATENCY_HIGH,
        )
    )
    state = state_res.scalar_one_or_none()
    if state is None:
        state = AlertState(
            device_id=lr.id,
            alert_type=AT_LR_LATENCY_HIGH,
            failure_count=0,
        )
        session.add(state)
        await session.flush()

    now = datetime.datetime.now(datetime.UTC)
    state.last_evaluated_at = now

    if avg_rtt_ms < threshold:
        # Back under threshold — reset and resolve any open incident.
        if state.failure_count > 0:
            state.failure_count = 0
        await _resolve_and_notify(
            session, lr,
            title=f"RECOVERY : latence {lr.name} → Internet redevenue normale",
            alert_type=AT_LR_LATENCY_HIGH,
        )
        logger.info(
            "lr_latency: %s → %s = %.1f ms (< %.0f ms, OK)",
            lr.name, settings.lr_latency_target, avg_rtt_ms, threshold,
        )
        return

    state.failure_count += 1
    count = state.failure_count

    if count < needed:
        logger.warning(
            "lr_latency: %s → %s = %.1f ms ≥ %.0f ms (%d/%d cycles, seuil non atteint)",
            lr.name, settings.lr_latency_target, avg_rtt_ms, threshold,
            count, needed,
        )
        return

    title = f"ALERTE CRITIQUE : latence élevée {lr.name} → Internet"
    description = (
        f"Latence moyenne mesurée depuis {lr.name} ({lr.ip_address}) vers "
        f"{settings.lr_latency_target} : {avg_rtt_ms:.1f} ms "
        f"(seuil critique : {threshold:.0f} ms, "
        f"moyenne sur {settings.lr_latency_ping_count} pings).\n"
        f"{count} cycles consécutifs au-dessus du seuil."
    )
    incident, is_new = await incident_service.open_incident(
        session, lr, title, "critical", description,
        alert_type=AT_LR_LATENCY_HIGH,
        metric_name="lr_latency_ms",
        metric_value=avg_rtt_ms,
        threshold_value=threshold,
    )
    if is_new:
        await notification_service.notify_incident_opened(lr, incident)
        logger.warning(
            "lr_latency: %s → %s = %.1f ms ≥ %.0f ms — incident critique ouvert (%d cycles)",
            lr.name, settings.lr_latency_target, avg_rtt_ms, threshold, count,
        )


# ---------------------------------------------------------------------------
# Correlation helper (used by ping job for switch context)
# ---------------------------------------------------------------------------

async def _build_switch_context(session, target_device: Device) -> str:
    """
    Enrich an incident description with switch/wiring context.

    Reads the latest eth_if_up metric from the LTU Rocket to determine
    whether the cable between Rocket and switch was already down.
    Only relevant for ltu_rocket devices.
    """
    if target_device.rule_category != "ltu_rocket":
        return ""

    sub = (
        select(func.max(DeviceMetric.collected_at))
        .where(
            DeviceMetric.device_id == target_device.id,
            DeviceMetric.metric_name == "eth_if_up",
        )
        .scalar_subquery()
    )
    res = await session.execute(
        select(DeviceMetric).where(
            DeviceMetric.device_id == target_device.id,
            DeviceMetric.metric_name == "eth_if_up",
            DeviceMetric.collected_at == sub,
        )
    )
    metric = res.scalars().first()

    if metric is None:
        return "\n\nContexte switch : état du lien switch↔Rocket non encore mesuré."
    if metric.metric_value == 0.0:
        return (
            "\n\nContexte switch : l'interface Ethernet (eth0) du LTU Rocket était déjà DOWN "
            "→ câble débranché ou port du switch HS entre le switch et le LTU Rocket."
        )
    return (
        "\n\nContexte switch : l'interface Ethernet (eth0) du LTU Rocket était UP "
        "→ le switch fonctionne correctement, le problème est probablement "
        "côté alimentation ou radio du LTU Rocket."
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

async def heartbeat_job() -> None:
    """Confirm the scheduler is running."""
    logger.info("Scheduler heartbeat — system is alive")


async def _reconfirm_unreachable(
    ips: list[str], settings,
) -> dict[str, bool]:
    """Re-pingue chaque IP suspecte ISOLÉMENT (un `ping` dédié par hôte, hors
    burst) → {ip: reachable}. Sert à dissiper les faux "down" du sweep groupé :
    une radio Ubiquiti rate-limite tout le burst ICMP d'un coup, mais répond à un
    ping isolé. Concurrence bornée (réutilise ping_concurrency) pour ne pas
    re-créer une rafale ; les suspects sont normalement peu nombreux."""
    sem = asyncio.Semaphore(settings.ping_concurrency)

    async def _one(ip: str) -> tuple[str, bool]:
        async with sem:
            reachable, _ = await poller.ping_host(
                ip,
                timeout=settings.ping_infra_reconfirm_timeout_s,
                count=settings.ping_infra_reconfirm_count,
            )
        return ip, reachable

    results = await asyncio.gather(
        *[_one(ip) for ip in ips], return_exceptions=True,
    )
    out: dict[str, bool] = {}
    for r in results:
        if isinstance(r, BaseException):
            continue
        ip, reachable = r
        out[ip] = reachable
    return out


async def _ping_sweep(*, infra: bool) -> None:
    """Ping un sous-ensemble du parc (INFRA ou LR clients) via ICMP, met à jour
    status/last_seen, ouvre/résout les incidents *_down.

    Corps partagé par les deux jobs (``infra_ping_job`` / ``client_ping_job``).
    La séparation infra/LR sert deux buts :
      - FIABILITÉ : un seul fping sur tout le parc (`-r 1 -t 800 -i 1`) envoyait
        ~680 paquets en ~0,7 s ; ce burst noyait l'ICMP que le CPU de management
        des Rockets rate-limite → 2 sondes perdues → faux "down" → le Rocket
        sortait des polls API/SNMP. Le sweep INFRA utilise des params fiables
        (plus de retries + timeout large) sur un petit lot.
      - CHARGE : les LR clients (nombreux ; un LR down = panne côté abonné SANS
        incident infra) sont sondés moins souvent (client_ping_interval_seconds).

    Anti-flapping: an incident is only opened after ping_down_threshold consecutive
    failures (default 3 = 90 s). A single successful ping resolves the incident.
    alert_type is device-specific (rocket_down / switch_down). LRs are the
    exception: a down LR is a subscriber-side outage (client power cut / LR
    unplugged), so it never raises an incident — only its status flips to down.
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    # Light load (id + ip) only — we must NOT hold a DB session open during the
    # fping network sweep. client_modem rows are skipped: behind an LR's NAT,
    # unreachable by ICMP from the supervisor (checked via ping-from-LR).
    async with async_session_factory() as session:
        rows = (await session.execute(
            select(Device.id, Device.ip_address).where(
                Device.device_type != "client_modem",
                # A NULL ip is a stale LR binding freed during DHCP churn,
                # awaiting rediscovery by its own Rocket — nothing to ping.
                Device.ip_address.is_not(None),
                # Sweep séparé : LR clients d'un côté, tout le reste (infra) de l'autre.
                Device.device_type == "lr" if not infra else Device.device_type != "lr",
            )
        )).all()

    label = "infra" if infra else "LR client"
    if not rows:
        logger.debug("Ping poll %s — aucun device", label)
        return

    logger.info("Ping poll %s — checking %d device(s)", label, len(rows))

    # Un process `fping` pingue ce lot en parallèle → {ip: reachable}. INFRA =
    # params fiables (retries + timeout larges, cf. ping_infra_*) ; LR = défauts
    # tolérants/rapides de ping_hosts_bulk. Fallback ping par hôte si fping absent.
    ips = [ip for _id, ip in rows]
    if infra:
        reachable_by_ip = await poller.ping_hosts_bulk(
            ips,
            retries=settings.ping_infra_retries,
            timeout_ms=settings.ping_infra_timeout_ms,
        )
        # Re-confirmation ISOLÉE des suspects : le faux "down" vient du burst
        # fping. On re-pingue chaque hôte injoignable TOUT SEUL (hors burst) ;
        # un équipement sain répond du premier coup → on le re-marque UP. "down"
        # reste basé sur le ping, mais sur un ping fiable. Coût nul si infra saine.
        suspects = [ip for ip in ips if not reachable_by_ip.get(ip, False)]
        if suspects:
            logger.info(
                "Ping infra — %d suspect(s) down, re-confirmation isolée", len(suspects)
            )
            confirmed = await _reconfirm_unreachable(suspects, settings)
            for ip in suspects:
                reachable_by_ip[ip] = confirmed.get(ip, False)
    else:
        reachable_by_ip = await poller.ping_hosts_bulk(ips)

    now = datetime.datetime.now(datetime.UTC)
    ids = [i for i, _ip in rows]

    # ── P2.1 : une session, BULK loads, traitement en mémoire, commits par paquets.
    # Avant : par device (×N) un session.get + 2 SELECT AlertState + un SELECT
    # resolve + un commit ≈ ~5N requêtes/cycle (~12 s à 600 devices). Ici : 3
    # SELECT bulk au total ; les requêtes restantes ne tombent qu'aux TRANSITIONS
    # (recovery d'un device qui avait un incident, ou passage down au seuil) — rares.
    async with async_session_factory() as session:
        devices = (await session.execute(
            select(Device).where(Device.id.in_(ids))
        )).scalars().all()
        # Compteurs d'échecs ping de TOUS les devices en une requête.
        states = (await session.execute(
            select(AlertState).where(
                AlertState.alert_type == _PING_FAILURE_STATE_KEY,
                AlertState.device_id.in_(ids),
            )
        )).scalars().all()
        state_by_dev = {s.device_id: s for s in states}
        # Devices ayant un incident de disponibilité OUVERT → seuls ceux-là ont
        # besoin d'un resolve au recovery (sinon, pour un device up sans incident,
        # c'était un SELECT resolve no-op par device).
        open_down = set((await session.execute(
            select(Incident.device_id).where(
                Incident.status == "open",
                Incident.device_id.in_(ids),
                Incident.alert_type.in_(tuple(AVAILABILITY_ALERT_TYPES)),
            )
        )).scalars().all())

        def _set_failures(device_id: int, count: int) -> None:
            """Met à jour le compteur d'échecs ping en mémoire (flush au commit)."""
            st = state_by_dev.get(device_id)
            if st is None:
                st = AlertState(
                    device_id=device_id, alert_type=_PING_FAILURE_STATE_KEY,
                    failure_count=count, last_evaluated_at=now,
                )
                session.add(st)
                state_by_dev[device_id] = st
            else:
                st.failure_count = count
                st.last_evaluated_at = now

        for i, device in enumerate(devices, 1):
            reachable = reachable_by_ip.get(device.ip_address, False)
            at = _alert_type_for_device(device)
            st = state_by_dev.get(device.id)
            prev_failures = st.failure_count if st else 0

            if reachable:
                _set_failures(device.id, 0)
                device.status = "up"
                device.last_seen = now
                # Recovery : uniquement si le device avait un incident *_down ouvert.
                # On résout TOUT incident de disponibilité ouvert (peu importe son
                # alert_type exact) — robuste au reclassement d'un device après
                # l'ouverture de l'incident (ex. airMAX Rocket → ptp_litebeam par
                # le sync UISP : l'incident garde alert_type=airmax_down alors que
                # le device est maintenant device_unreachable, et un resolve sur un
                # seul type ne matcherait plus → incident bloqué « hors ligne »).
                if device.id in open_down:
                    resolved = await incident_service.resolve_availability_incidents(
                        session, device.id
                    )
                    for incident in resolved:
                        await notification_service.notify_incident_resolved(device, incident)
                logger.info("UP   %s (%s)", device.name, device.ip_address)
            else:
                failures = prev_failures + 1
                _set_failures(device.id, failures)
                if failures >= settings.ping_down_threshold:
                    # Anti-flap du statut : on ne bascule "down" qu'au seuil (jamais
                    # sur un ICMP perdu — radios Ubiquiti rate-limitent leur mgmt).
                    device.status = "down"
                    if device.rule_category == "lr":
                        # Un LR down = panne côté abonné : aucun incident infra. On
                        # purge ses incidents ouverts (qualité/lien/transit), devenus
                        # du bruit obsolète tant qu'il ne répond pas.
                        purged = await incident_service.delete_open_incidents(session, device.id)
                        logger.info(
                            "LR DOWN (côté client) %s (%s) — %d échecs, %d incident(s) purgé(s)",
                            device.name, device.ip_address, failures, purged,
                        )
                    else:
                        context = await _build_switch_context(session, device)
                        down_title = _down_title_for_device(device)
                        await _open_and_notify(
                            session, device,
                            title=down_title,
                            severity="critical",
                            description=(
                                f"{device.name} ({device.ip_address}) ne répond pas au ping ICMP"
                                f" ({failures} tentatives consécutives échouées).{context}"
                            ),
                            alert_type=at,
                        )
                        logger.warning(
                            "DOWN %s (%s) — %d/%d échecs consécutifs",
                            device.name, device.ip_address, failures, settings.ping_down_threshold,
                        )
                else:
                    logger.warning(
                        "PING KO %s (%s) — %d/%d (seuil non atteint)",
                        device.name, device.ip_address, failures, settings.ping_down_threshold,
                    )

            # Commit par paquets : ~N/200 commits au lieu d'un par device, tout
            # en bornant le blast radius si un commit échoue.
            if i % 200 == 0:
                await session.commit()

        await session.commit()


@_timed_job
async def infra_ping_job() -> None:
    """Ping ICMP des équipements d'INFRA (Rockets / switches / UISP Power / AF60).

    Sweep rapide (ping_interval_seconds, 30 s) avec params fping fiables — c'est
    lui qui ouvre/résout les incidents *_down (équipements critiques)."""
    await _ping_sweep(infra=True)


@_timed_job
async def client_ping_job() -> None:
    """Ping ICMP des LR CLIENTS (sweep plus lent, client_ping_interval_seconds).

    Un LR down = panne côté abonné → aucun incident infra, juste le statut qui
    bascule ; inutile de sonder aussi souvent que l'infra."""
    await _ping_sweep(infra=False)


@_timed_job
async def snmp_poll_job() -> None:
    """
    Collect SNMP metrics from Ubiquiti radio and switch devices.
    Stores metrics in device_metrics, then delegates anomaly detection
    to the alert engine (radio_interface_down, eth0_down, high_rx_tx_errors).
    Only polls devices with status 'up' and a configured snmp_community.
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    async with async_session_factory() as session:
        # Polymorphic load — pulls Rocket and UispSwitch instances with their
        # subtype columns. LTU LRs stay excluded: their per-peer metrics come
        # from the parent Rocket's HTTP API fan-out in ltu_api_poll_job.
        result = await session.execute(
            select(Device).where(
                Device.status == "up",
                Device.snmp_community.is_not(None),
                Device.device_type.in_(("rocket", "uisp_switch")),
            )
        )
        devices = list(result.scalars().all())

    # airMAX LRs (LiteBeam 5AC/M5) are NOT polled here anymore: they are
    # polled via their own airOS HTTP API in airos_api_poll_job, which yields
    # the composite Link Potential / Total Capacity / rate-index metrics the
    # SNMP MIB can't. The airMAX Rocket SNMP poll below still discovers them.

    if not devices:
        logger.debug("No SNMP-eligible devices — skipping SNMP poll")
        return

    logger.info("SNMP poll — checking %d device(s)", len(devices))

    # Snapshot the fields both phases need (read from the already-loaded
    # instances). Switch-only columns (max_ports / rocket_port_index /
    # port_min_speed_mbps) are read only for switches.
    def _snap(d: Device) -> tuple:
        is_switch = d.rule_category in SWITCH_RULE_CATEGORIES
        is_airmax = d.rule_category in AIRMAX_RULE_CATEGORIES
        return (
            d.id, d.name, d.ip_address,
            d.snmp_community or settings.snmp_default_community,
            d.rule_category,
            d.max_ports if is_switch else None,
            d.rocket_port_index if is_switch else None,
            d.port_min_speed_mbps if is_switch else None,
            # airOS creds (ssh_username/ssh_password) — only needed for airMAX
            # Rockets, to read the channel width (chanbw) via status.cgi.
            d.ssh_username if is_airmax else None,
            d.ssh_password if is_airmax else None,
        )

    targets = [_snap(d) for d in devices]

    # ── Phase 1 : fetch SNMP de tous les devices EN PARALLÈLE (borné) ──
    # Le job était série (un walk à la fois) → à 78 devices, aggravé par les
    # timeouts des airMAX SNMP-off qui s'additionnaient, un tour dépassait 60 s.
    # Les collecteurs pysnmp sont async → gather + sémaphore (les timeouts
    # tournent en parallèle). On fait AUSSI la découverte airMAX (autre walk
    # SNMP) ici. dev_id -> (metrics, airmax_peers | None).
    snmp_port = settings.snmp_port
    snmp_timeout = settings.snmp_timeout
    sem = asyncio.Semaphore(settings.snmp_concurrency)
    fetched: dict[int, tuple] = {}

    async def _fetch(snap: tuple) -> None:
        dev_id, name, ip, community, category, max_ports, _pidx, _pmin, airos_user, airos_pwd = snap
        async with sem:
            if category in AIRMAX_RULE_CATEGORIES:
                metrics = await snmp_service.collect_airmax_metrics(
                    host=ip, community=community, port=snmp_port, timeout=snmp_timeout,
                )
            elif category in RADIO_RULE_CATEGORIES:
                metrics = await snmp_service.collect_ltu_metrics(
                    host=ip, community=community, port=snmp_port, timeout=snmp_timeout,
                )
            elif category in SWITCH_RULE_CATEGORIES:
                metrics = await snmp_service.collect_switch_port_metrics(
                    host=ip, community=community, port=snmp_port, timeout=snmp_timeout,
                    max_ports=max_ports,
                )
            else:
                metrics = await snmp_service.collect_standard_metrics(
                    host=ip, community=community, port=snmp_port, timeout=snmp_timeout,
                )

            if not any(v is not None for v in metrics.values()):
                logger.warning("SNMP no data — %s (%s)", name, ip)
                return

            logger.info(
                "SNMP %s (%s) — %s", name, ip,
                " | ".join(f"{k}={v}" for k, v in metrics.items() if v is not None),
            )

            # airMAX peer discovery — also an SNMP walk → done here in Phase 1.
            airmax_peers = None
            channel_width_mhz = None
            if category in AIRMAX_RULE_CATEGORIES:
                airmax_peers = await snmp_service.discover_airmax_peers(
                    host=ip, community=community, port=snmp_port, timeout=snmp_timeout,
                )
                # Channel width (chanbw) is NOT in SNMP — read it from airOS
                # status.cgi for the rocket_client_overload rule. Needs airOS
                # creds on the device; None (missing/unreachable) → rule skips.
                if airos_user and airos_pwd:
                    channel_width_mhz = await airos_api_service.collect_airos_channel_width(
                        host=ip, username=airos_user, password=airos_pwd, port=443,
                    )
        fetched[dev_id] = (metrics, airmax_peers, channel_width_mhz)

    await asyncio.gather(*[_fetch(s) for s in targets], return_exceptions=True)

    # ── Phase 2 : persist + alert engine + découverte + ports switch (série DB) ──
    snap_by_id = {s[0]: s for s in targets}
    for dev_id, (metrics, airmax_peers, channel_width_mhz) in fetched.items():
        (_id, _name, _ip, _community, category, _mp, rocket_port_index,
         port_min_speed, _airos_user, _airos_pwd) = snap_by_id[dev_id]
        async with async_session_factory() as session:
            dev = await session.get(Device, dev_id)
            if dev is None:
                continue

            # Persist each metric as a DeviceMetric row
            unit_map = {
                "uptime_seconds":   "s",
                "radio_rx_bytes":   "B",
                "radio_tx_bytes":   "B",
                "radio_in_errors":  "",
                "radio_out_errors": "",
                "radio_if_up":      "",
                "eth_if_up":        "",
            }
            # airMAX Rocket : alimente la règle rocket_client_overload ET la page
            # Capacité réseau — nombre de clients = stations découvertes par le
            # walk SNMP (Phase 1), largeur de canal = chanbw lu via airOS
            # status.cgi (Phase 1). Une largeur < 10 MHz n'a pas de seuil → la
            # règle ne déclenche pas. On les ajoute AVANT la persistance pour que
            # peer_count / channel_width_mhz soient interrogeables au query-time
            # (latest-only collapse — pas dans HISTORY_METRICS).
            if category in AIRMAX_RULE_CATEGORIES and airmax_peers is not None:
                metrics = dict(metrics)
                metrics["peer_count"] = len(airmax_peers)
                if channel_width_mhz is not None:
                    metrics["channel_width_mhz"] = channel_width_mhz

            for key in metrics:
                if key not in unit_map:
                    if "_rx_bytes" in key or "_tx_bytes" in key:
                        unit_map[key] = "B"
                    elif "_speed_mbps" in key:
                        unit_map[key] = "Mbps"
                    else:
                        unit_map[key] = ""
            # History metrics (radio_rx/tx_bytes, signal_dbm…) are appended;
            # everything else (all switch port metrics, noise, rates…) collapses
            # to a single latest row. See persist_device_metrics / HISTORY_METRICS.
            await persist_device_metrics(session, dev.id, metrics, unit_map)

            # Delegate anomaly detection to alert engine
            await alert_engine.evaluate_device_metrics(session, dev, metrics, settings)

            # Auto-discovery for airMAX Rockets — station table walked in Phase 1
            # (airmax_peers), fed to the same reconcile_peers() pipeline as LTU API.
            # Peers are persisted as Lr rows with model_variant="litebeam_5ac"
            # by default; the operator can override via PUT after creation.
            if category in AIRMAX_RULE_CATEGORIES and airmax_peers:
                recon = await discovery_service.reconcile_peers(
                    session, dev, airmax_peers,
                )
                if recon.created or recon.ip_changed or recon.reassigned:
                    logger.info(
                        "Discovery — airMAX '%s' : %d nouveau(x), %d IP changée(s), %d rebascule(s)",
                        dev.name, len(recon.created),
                        len(recon.ip_changed), len(recon.reassigned),
                    )

            # Switch port monitoring (not handled by alert engine — device-level rule).
            # Per-switch settings come from the UispSwitch row (port index + min speed).
            if category in SWITCH_RULE_CATEGORIES:
                port_idx = rocket_port_index or 0
                min_speed = port_min_speed
                if port_idx > 0:
                    port_status = metrics.get(f"port_{port_idx}_up")
                    if port_status is not None:
                        if port_status == 0.0:
                            await _open_and_notify(
                                session, dev, INC_SWITCH_PORT, "critical",
                                f"GigabitEthernet{port_idx} du {dev.name} "
                                f"(connecté au LTU Rocket) est DOWN. "
                                f"Vérifiez le câble entre le switch et le LTU Rocket.",
                                alert_type=AT_SWITCH_PORT,
                            )
                            await _resolve_and_notify(
                                session, dev, INC_SWITCH_PORT_SPEED, alert_type=AT_SWITCH_PORT_SPEED
                            )
                        else:
                            await _resolve_and_notify(
                                session, dev, INC_SWITCH_PORT, alert_type=AT_SWITCH_PORT
                            )
                            # Port is UP — check link speed
                            speed = metrics.get(f"port_{port_idx}_speed_mbps")
                            if speed is not None:
                                if speed < min_speed:
                                    await _open_and_notify(
                                        session, dev, INC_SWITCH_PORT_SPEED, "critical",
                                        f"GigabitEthernet{port_idx} du {dev.name} UP "
                                        f"mais vitesse négociée à {speed:.0f} Mbps "
                                        f"(seuil minimum : {min_speed:.0f} Mbps). "
                                        f"Vérifiez la qualité du câble et l'auto-négociation.",
                                        alert_type=AT_SWITCH_PORT_SPEED,
                                    )
                                else:
                                    await _resolve_and_notify(
                                        session, dev, INC_SWITCH_PORT_SPEED, alert_type=AT_SWITCH_PORT_SPEED
                                    )

            await session.commit()


def _fmt_autonomy(seconds: float | None) -> str:
    """Human-friendly remaining battery autonomy from a runtime in seconds.

    "≈ 1 h 30 min" / "≈ 45 min". Returns "inconnue" when the device reports no
    estimate (None) or a non-positive one.
    """
    if seconds is None or seconds <= 0:
        return "inconnue"
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"≈ {hours} h {minutes:02d} min"
    return f"≈ {minutes} min"


def _active_battery_label(batteries: list[dict]) -> str:
    """Name the battery/batteries currently discharging (in use on outage).

    Falls back to a generic label if the device doesn't flag any as
    discharging (e.g. load momentarily null right at the cutover).
    """
    active = [b for b in batteries if b.get("discharging")]
    if not active:
        return "ses batteries"
    return " + ".join(_BATTERY_HUMAN.get(b.get("type"), b.get("type") or "batterie") for b in active)


async def _evaluate_mains_power(
    session, dev: Device, ac_connected: bool | None, batteries: list[dict],
) -> None:
    """
    Mains (SOMELEC) outage detection for a UISP Power.

    Ouvre l'incident `mains_power_lost` (affiché dans /incidents) SANS notifier :
    le type n'est pas dans WHATSAPP_ALERT_TYPES, donc _open_and_notify crée bien
    l'incident mais le dispatch WhatsApp est court-circuité (politique 2026-06-11 :
    coupure secteur visible en base, pas de notification).

    `ac_connected` comes straight from the device API (any AC input slot
    connected). None = firmware didn't report AC slots → unknown, skip.
    `batteries` lets the alert name which battery is actually discharging
    (internal Li-Ion vs external lead-acid bank).

    Anti-flap: open `mains_power_lost` only after MAINS_LOSS_THRESHOLD
    consecutive cycles on battery (filters brief flickers the battery rides
    through); resolve as soon as the mains is back. The consecutive count is
    persisted in AlertState (keyed by AT_MAINS_POWER_LOST) so it survives a
    scheduler restart.
    """
    if ac_connected is None:
        return  # device doesn't expose AC presence — nothing to evaluate

    state_res = await session.execute(
        select(AlertState).where(
            AlertState.device_id == dev.id,
            AlertState.alert_type == AT_MAINS_POWER_LOST,
        )
    )
    state = state_res.scalar_one_or_none()
    if state is None:
        state = AlertState(device_id=dev.id, alert_type=AT_MAINS_POWER_LOST, failure_count=0)
        session.add(state)
        await session.flush()
    state.last_evaluated_at = datetime.datetime.now(datetime.UTC)

    if ac_connected:
        # Mains present — reset and resolve any open outage.
        if state.failure_count > 0:
            state.failure_count = 0
        await _resolve_and_notify(session, dev, INC_MAINS_LOST, alert_type=AT_MAINS_POWER_LOST)
        return

    state.failure_count += 1
    if state.failure_count < MAINS_LOSS_THRESHOLD:
        logger.warning(
            "mains: %s (%s) sur batterie (%d/%d cycles, seuil non atteint)",
            dev.name, dev.ip_address, state.failure_count, MAINS_LOSS_THRESHOLD,
        )
        return

    source = _active_battery_label(batteries)
    await _open_and_notify(
        session, dev, INC_MAINS_LOST, "warning",
        f"Coupure secteur détectée : {dev.name} ({dev.ip_address}) est passé sur "
        f"batterie (aucune entrée AC connectée, {state.failure_count} cycles). "
        f"Batterie en service : {source}. "
        f"Le site tient sur batterie — surveiller le niveau de charge.",
        alert_type=AT_MAINS_POWER_LOST,
    )


@_timed_job
async def power_poll_job() -> None:
    """
    Poll UISP Power devices via their local REST API.
    Stores readings in power_status_logs and detects power anomalies.
    """
    settings = get_settings()

    async with async_session_factory() as session:
        result = await session.execute(select(UispPower))
        devices = list(result.scalars().all())

    if not devices:
        logger.debug("No UISP Power devices registered — skipping power poll")
        return

    logger.info("Power poll — checking %d UISP Power device(s)", len(devices))

    # ── Phase 1 : fetch REST de tous les UISP Power EN PARALLÈLE (borné + deadline) ──
    # Le job était série → à beaucoup de UISP Power (ou des injoignables qui
    # timeout), un tour dépassait l'intervalle 30 s → APScheduler skippait chaque
    # cycle. Le fetch HTTP est async → gather + sémaphore sous deadline globale.
    # Phase 2 (persist + détection d'anomalies) reste en série DB.
    for device in devices:
        if not device.api_username or not device.api_password:
            logger.warning(
                "UISP Power skip — %s (%s) : credentials manquants en base "
                "(api_username/api_password). "
                "Configure-les via PUT /api/v1/devices/%d.",
                device.name, device.ip_address, device.id,
            )

    sem = asyncio.Semaphore(settings.power_concurrency)
    fetched: dict[int, dict | None] = {}

    async def _fetch_power(did: int, ip: str, user: str, pwd: str, port: int) -> None:
        async with sem:
            fetched[did] = await uisp_power_service.poll_uisp_power(
                host=ip, username=user, password=pwd, port=port or 443,
            )

    ptasks = [
        asyncio.ensure_future(
            _fetch_power(d.id, d.ip_address, d.api_username, d.api_password, d.api_port)
        )
        for d in devices
        if d.api_username and d.api_password
    ]
    try:
        await asyncio.wait_for(
            asyncio.gather(*ptasks, return_exceptions=True),
            timeout=_POWER_POLL_DEADLINE_S,
        )
    except TimeoutError:  # asyncio.TimeoutError is an alias of builtin on 3.11+
        for t in ptasks:
            t.cancel()
        logger.warning(
            "Power poll : deadline %.0fs atteinte — %d/%d device(s) récupéré(s), "
            "le reste sera repris au prochain cycle.",
            _POWER_POLL_DEADLINE_S, len(fetched), len(ptasks),
        )

    # ── Phase 2 : persist + détection d'anomalies en série DB ──
    for device in devices:
        if device.id not in fetched:
            # Creds manquants (déjà signalé) ou fetch annulé par la deadline.
            continue
        readings = fetched.get(device.id)

        async with async_session_factory() as session:
            dev = await session.get(Device, device.id)
            if dev is None:
                continue

            if readings is None:
                # Injoignable : on NE crée PAS d'incident ici. Le device_ping_job
                # ouvre déjà `device_unreachable` (notifié) pour un UISP Power down
                # — uisp_power_unreachable retiré pour éviter le doublon.
                logger.warning("UISP Power injoignable (API) — %s (%s)", dev.name, dev.ip_address)
            else:
                # Nettoyage silencieux des types UISP Power retirés (legacy) — on
                # ne les émet plus, on ferme ceux encore ouverts sans notifier :
                # uisp_power_unreachable / voltage_anomaly.
                for _legacy_title, _legacy_at in (
                    (INC_POWER_UNREACH, AT_POWER_UNREACH),
                    (INC_VOLT_ANOMALY, AT_VOLT_ANOMALY),
                ):
                    await incident_service.resolve_incidents(
                        session, dev.id, _legacy_title, alert_type=_legacy_at
                    )

                session.add(PowerStatusLog(
                    device_id=dev.id,
                    voltage=readings.get("voltage"),
                    current=readings.get("current"),
                    power=readings.get("power"),
                    status="online",
                ))

                # Mirror readings into device_metrics so the unified
                # /devices/{id}/metrics/latest endpoint exposes them to the UI.
                # None of these are in HISTORY_METRICS (power history lives in
                # PowerStatusLog), so persist_device_metrics collapses them all
                # to one latest row each — see the policy note on that helper.
                # (name, value, unit) — value None entries are skipped downstream.
                power_entries: list[tuple[str, float | None, str]] = [
                    ("voltage_v",          readings.get("voltage"),            "V"),
                    ("current_a",          readings.get("current"),            "A"),
                    ("power_w",            readings.get("power"),              "W"),
                    ("battery_pct",        readings.get("battery_percentage"), "%"),
                    ("battery_voltage_v",  readings.get("battery_voltage"),    "V"),
                    ("uptime_seconds",     readings.get("uptime_seconds"),     "s"),
                    ("output_max_power_w", readings.get("output_max_power_w"), "W"),
                    ("output_energy_wh",   readings.get("output_energy"),      "Wh"),
                    ("ac_connected",       readings.get("ac_connected"),       "bool"),
                ]

                # Per-battery metrics — charge/voltage/capacity/autonomy per
                # battery the device reports (internal Li-Ion UPS, external
                # lead-acid bank…), keyed by type slug so the UI shows each one.
                for batt_entry in readings.get("batteries") or []:
                    slug = batt_entry.get("type_slug") or "unknown"
                    power_entries += [
                        (f"battery_{slug}_pct",         batt_entry.get("percentage"),      "%"),
                        (f"battery_{slug}_voltage_v",   batt_entry.get("voltage"),         "V"),
                        (f"battery_{slug}_capacity_ah", batt_entry.get("capacity_ah"),     "Ah"),
                        (f"battery_{slug}_runtime_s",   batt_entry.get("runtime_seconds"), "s"),
                        # 1 = cette batterie débite (en service sur coupure secteur).
                        (f"battery_{slug}_discharging", batt_entry.get("discharging"),     "bool"),
                    ]

                # Per-DC-output metrics — electrical readings + connection state
                # (1/0) for each output port the device exposes.
                for out in readings.get("dc_outputs") or []:
                    oid = out.get("id")
                    if oid is None:
                        continue
                    power_entries += [
                        (f"dc_output_{oid}_voltage_v", out.get("voltage"), "V"),
                        (f"dc_output_{oid}_current_a", out.get("current"), "A"),
                        (f"dc_output_{oid}_power_w",   out.get("power"),   "W"),
                        (f"dc_output_{oid}_connected", 1.0 if out.get("connected") else 0.0, "bool"),
                    ]

                power_metrics = {name: val for name, val, _ in power_entries}
                power_units = {name: unit for name, _, unit in power_entries}
                await persist_device_metrics(session, dev.id, power_metrics, power_units)

                logger.info(
                    "UISP Power %s — voltage=%.1fV current=%.2fA power=%.1fW",
                    dev.name,
                    readings.get("voltage") or 0,
                    readings.get("current") or 0,
                    readings.get("power") or 0,
                )

                # Battery anomaly detection (politique UISP Power 2026-06-11) :
                # DEUX alertes batterie distinctes, toutes deux CRITIQUES + notif
                # immédiate, évaluées par batterie connectée :
                #   - INTERNE (Li-Ion UPS, type_slug "li_ion")  < 50 %
                #   - EXTERNE (banc plomb, type_slug "lead_acid") < 30 %
                # Retour ≥ seuil = fermeture SILENCIEUSE (resolve_incidents direct).
                # On garde la pire charge par catégorie (plusieurs slots possibles).
                # On garde la pire BATTERIE (dict complet) par catégorie, pas
                # seulement sa charge : le message d'alerte ajoute aussi son
                # autonomie estimée (runtime_seconds) à côté du pourcentage.
                internal_batt: dict | None = None
                external_batt: dict | None = None
                for b in readings.get("batteries") or []:
                    pct = b.get("percentage")
                    if pct is None or not b.get("connected"):
                        continue
                    slug = b.get("type_slug")
                    if slug == "li_ion" and (internal_batt is None or pct < internal_batt["percentage"]):
                        internal_batt = b
                    elif slug == "lead_acid" and (external_batt is None or pct < external_batt["percentage"]):
                        external_batt = b
                internal_pct = internal_batt["percentage"] if internal_batt else None
                external_pct = external_batt["percentage"] if external_batt else None

                # Batterie interne (Li-Ion UPS) < seuil → critique.
                if internal_pct is not None and internal_pct < settings.battery_internal_critical_pct:
                    await _open_and_notify(
                        session, dev, INC_BATT_INTERNAL, "critical",
                        f"Batterie interne (Li-Ion UPS) de {dev.name} à {internal_pct:.0f}% "
                        f"— autonomie estimée {_fmt_autonomy(internal_batt.get('runtime_seconds'))} "
                        f"(seuil {settings.battery_internal_critical_pct}%).",
                        alert_type=AT_BATTERY_INTERNAL_LOW,
                    )
                else:
                    await incident_service.resolve_incidents(
                        session, dev.id, INC_BATT_INTERNAL, alert_type=AT_BATTERY_INTERNAL_LOW
                    )

                # Batterie externe (banc plomb) < seuil → critique.
                if external_pct is not None and external_pct < settings.battery_external_critical_pct:
                    await _open_and_notify(
                        session, dev, INC_BATT_EXTERNAL, "critical",
                        f"Batterie externe (banc plomb) de {dev.name} à {external_pct:.0f}% "
                        f"— autonomie estimée {_fmt_autonomy(external_batt.get('runtime_seconds'))} "
                        f"(seuil {settings.battery_external_critical_pct}%).",
                        alert_type=AT_BATTERY_EXTERNAL_LOW,
                    )
                else:
                    await incident_service.resolve_incidents(
                        session, dev.id, INC_BATT_EXTERNAL, alert_type=AT_BATTERY_EXTERNAL_LOW
                    )

                # Nettoyage silencieux des anciennes alertes batterie uniques.
                await incident_service.resolve_incidents(
                    session, dev.id, INC_BATT_WARN, alert_type=AT_BATT_WARN
                )
                await incident_service.resolve_incidents(
                    session, dev.id, INC_BATT_CRIT, alert_type=AT_BATT_CRIT
                )

                # Coupure secteur (SOMELEC) — ouvre `mains_power_lost`, AFFICHÉ
                # dans /incidents mais NON notifié (hors WHATSAPP_ALERT_TYPES).
                await _evaluate_mains_power(
                    session, dev, readings.get("ac_connected"),
                    readings.get("batteries") or [],
                )

            await session.commit()


# Power poll Phase 1 (fetch) global deadline — keeps the job under its 30 s
# interval so it never overruns and skips. Stragglers are cancelled and retried
# next cycle. Concurrency is settings.power_concurrency.
_POWER_POLL_DEADLINE_S = 25.0


# Concurrency controls for the LTU API poll. The job used to be serial (one
# login + statistics at a time); with a growing fleet a full pass exceeded the
# 60 s interval → APScheduler skipped cycles ("maximum number of running
# instances reached") and discovery/metrics lagged by minutes. We now fetch all
# Rockets concurrently (bounded by a semaphore) under a global deadline, then
# process the results sequentially in DB. Same proven pattern as
# lr_health_service._fetch_live_metrics.
_LTU_POLL_CONCURRENCY = 10
_LTU_POLL_DEADLINE_S = 40.0

# airOS poll Phase 1 (fetch) global deadline — bounds the job well under its
# interval (3 min) so it never overruns and cascades "max instances reached"
# onto the other heavy jobs. Stragglers (slow/unreachable airOS) are cancelled
# and retried next cycle. Concurrency is settings.airos_concurrency.
_AIROS_POLL_DEADLINE_S = 90.0


@_timed_job
async def ltu_api_poll_job() -> None:
    """
    Poll LTU Rockets via HTTP API (signal, CCQ, CINR, rates, CPE info).

    Phase 1 fetches every Rocket's ``/statistics`` CONCURRENTLY (I/O-bound,
    bounded by a semaphore + a global deadline so slow/unreachable Rockets never
    stall the others). Phase 2 persists metrics + runs discovery/alerting
    sequentially in DB. Radio quality anomaly detection is delegated to the
    alert engine.
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    async with async_session_factory() as session:
        # Poll LTU Rockets — airMAX uses different SNMP MIBs handled in snmp_poll.
        result = await session.execute(
            select(Rocket).where(
                Rocket.status == "up",
                Rocket.radio_tech == "ltu",
            )
        )
        # Snapshot the plain columns we need (id/name/ip/creds) while the session
        # is open — Phase 1 runs the network fetch outside any session.
        targets = [
            (d.id, d.name, d.ip_address, d.ssh_username, d.ssh_password)
            for d in result.scalars().all()
        ]

    if not targets:
        logger.debug("LTU API poll — no eligible devices")
        return

    logger.info("LTU API poll — checking %d device(s)", len(targets))
    unit_map = ltu_api_service.METRIC_UNITS

    # ── Phase 1 : fetch every Rocket's HTTP API concurrently (bounded) ──
    sem = asyncio.Semaphore(_LTU_POLL_CONCURRENCY)
    # dev_id -> (rocket_ap_metrics, all_peers, per_peer_metrics)
    fetched: dict[int, tuple] = {}

    async def _fetch(dev_id: int, name: str, ip: str, user: str | None, pwd: str | None) -> None:
        if not user or not pwd:
            logger.warning(
                "LTU API skip — %s (%s) : credentials manquants en base "
                "(ssh_username/ssh_password). Configure-les via PUT /api/v1/devices/%d.",
                name, ip, dev_id,
            )
            return
        async with sem:
            res = await ltu_api_service.collect_ltu_api_full(
                host=ip, username=user, password=pwd, port=443,  # firmware forces TLS
            )
        if res[0] is None:  # rocket_ap_metrics is None → unreachable / auth fail
            logger.debug("LTU API no response — %s (%s)", name, ip)
            return
        fetched[dev_id] = res

    tasks = [asyncio.create_task(_fetch(*t)) for t in targets]
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_LTU_POLL_DEADLINE_S,
        )
    except TimeoutError:  # asyncio.TimeoutError is an alias of builtin on 3.11+
        for t in tasks:
            t.cancel()
        logger.warning(
            "LTU API poll : délai global de %.0fs atteint — %d/%d Rocket(s) "
            "récupéré(s), le reste sera repris au prochain cycle.",
            _LTU_POLL_DEADLINE_S, len(fetched), len(tasks),
        )

    # ── Phase 2 : persist + discovery + alerting sequentially (DB-bound) ──
    for dev_id, (rocket_ap_metrics, all_peers, per_peer_metrics) in fetched.items():
        async with async_session_factory() as session:
            dev = await session.get(Device, dev_id)
            if dev is None:
                continue

            # Take the Rocket's per-device advisory lock UP FRONT (before reconcile
            # writes any device row) so this transaction serializes with the
            # concurrent snmp_poll_job pass on the same Rocket from the very first
            # write — see persist_device_metrics for the deadlock rationale.
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(dev.id)},
            )

            # Auto-discovery: create / update / link LR devices from the Rocket's peer list.
            # MAC-based identity → IP changes and Rocket reassignments are detected reliably.
            recon = await discovery_service.reconcile_peers(session, dev, all_peers)
            if recon.created or recon.ip_changed or recon.reassigned:
                logger.info(
                    "Discovery — Rocket '%s' : %d nouveau(x), %d IP changée(s), %d rebascule(s)",
                    dev.name, len(recon.created), len(recon.ip_changed), len(recon.reassigned),
                )

            # Persist AP-wide metrics on the Rocket (noise_dbm, channel_width_mhz).
            # peer_count (connected clients) feeds the rocket_client_overload rule
            # AND the Network Capacity page, so persist it here (latest-only
            # collapse — not in HISTORY_METRICS) instead of only on the engine
            # copy. Per-link metrics (signal/CCQ/CINR/etc.) belong to each LR and
            # are stored in the fan-out loop below.
            rocket_ap_metrics["peer_count"] = len(all_peers)
            await persist_device_metrics(session, dev.id, rocket_ap_metrics, unit_map)

            # Rocket-level alert engine pass: cares about peer_count and whatever
            # the SNMP IF-MIB poll added (radio_if_up, eth_if_up, byte/error
            # counters via _inject_error_deltas inside the engine).
            rocket_engine_metrics = dict(rocket_ap_metrics)
            await alert_engine.evaluate_device_metrics(
                session, dev, rocket_engine_metrics, settings,
            )

            # netMode (router/bridge) per peer — the LTU equivalent of airOS
            # netrole, free in the same API response. Keyed by MAC for the
            # fan-out below so the client-block topology guard stays fresh
            # every cycle without an SSH probe.
            net_mode_by_mac = {
                p["mac"]: p.get("net_mode")
                for p in all_peers
                if p.get("mac")
            }

            # Fan-out per-peer metrics to each child LR (matched by MAC).
            # Each LR gets its own DeviceMetric rows AND its own alert engine
            # pass so signal_low / ccq_low / cinr_low / etc. fire per-link,
            # not just on whichever peer happened to be peers[0].
            for peer_mac, peer_metrics in per_peer_metrics:
                if not peer_mac:
                    continue
                if not any(v is not None for v in peer_metrics.values()):
                    continue
                lr_q = await session.execute(
                    select(Lr).where(
                        Lr.rocket_id == dev.id,
                        Lr.mac_address == peer_mac,
                    )
                )
                lr = lr_q.scalar_one_or_none()
                if lr is None:
                    continue
                # Sync the stable distance_m column for quick UI display.
                distance = peer_metrics.get("distance_m")
                if distance is not None:
                    lr.distance_m = distance
                await persist_device_metrics(session, lr.id, dict(peer_metrics), unit_map)
                # Per-LR alert engine pass — radio-quality rules evaluate
                # against this LR's metrics, not the Rocket's peer[0].
                await alert_engine.evaluate_device_metrics(
                    session, lr, dict(peer_metrics), settings,
                )
                # Router vs bridge from the same API response (no SSH).
                await _apply_lr_topology(
                    session, lr, net_mode_by_mac.get(peer_mac),
                    "netMode via API LTU Rocket",
                )

            await session.commit()


@_timed_job
async def airos_api_poll_job() -> None:
    """
    Poll airMAX LR (LiteBeam) devices via their own airOS HTTP API (status.cgi).
    Replaces SNMP for these LRs: the UBNT MIB lacks Link Potential / Total
    Capacity / rate index, which the airOS dashboard (and status.cgi) expose.
    Each LiteBeam is queried directly at its own IP; metrics use the same keys
    as the LTU API so the alert engine / modal work unchanged.

    Also polls **PTP LiteBeams** (device_type ptp_litebeam, both ends of a P2P
    link): same airOS path yields their total_capacity for p2p_link_substandard +
    the inter-site P2P section. They are handled here (not snmp_poll_job) because
    they expose link capacity only via airOS and often have SNMP off. LR-specific
    steps (distance_m, topology_mode) are skipped for them.
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    async with async_session_factory() as session:
        lr_rows = (
            await session.execute(
                select(Lr).where(
                    Lr.status == "up",
                    Lr.model_variant.in_(AIRMAX_LR_VARIANTS),
                )
            )
        ).scalars().all()
        # PTP LiteBeams expose Link Potential / Total Capacity only via airOS
        # status.cgi (and often have SNMP off) → polled HERE, not snmp_poll_job.
        ptp_rows = (
            await session.execute(
                select(PtpLiteBeam).where(PtpLiteBeam.status == "up")
            )
        ).scalars().all()
        # Snapshot creds/ip for the concurrent fetch (no session held during HTTP).
        targets = [
            (d.id, d.name, d.ip_address, d.ssh_username, d.ssh_password)
            for d in [*lr_rows, *ptp_rows]
        ]

    if not targets:
        logger.debug("airOS API poll — no eligible devices")
        return

    logger.info("airOS API poll — checking %d device(s)", len(targets))

    # airOS HTTP metric keys reuse the LTU units; add the few SNMP-style keys
    # this poll also fills (byte counters, uptime).
    unit_map = {
        **ltu_api_service.METRIC_UNITS,
        "radio_rx_bytes": "B",
        "radio_tx_bytes": "B",
        "uptime_seconds": "s",
    }

    # ── Phase 1 : fetch airOS status.cgi de tous les LiteBeam EN PARALLÈLE ──
    # Le job était série (un login + status.cgi à la fois) → à beaucoup de LR
    # airMAX (découverts dès le SNMP du Rocket parent), un tour dépassait 250 s.
    # Le fetch HTTP est async → gather + sémaphore. Phase 2 (persist/alert/topo)
    # reste en série DB, une session par LR. dev_id -> (metrics, hostname, netrole).
    sem = asyncio.Semaphore(settings.airos_concurrency)
    fetched: dict[int, tuple] = {}

    async def _fetch(dev_id: int, name: str, ip: str, user: str | None, pwd: str | None) -> None:
        if not user or not pwd:
            logger.warning(
                "airOS API skip — %s (%s) : credentials manquants en base "
                "(ssh_username/ssh_password). Configure-les via PUT /api/v1/devices/%d.",
                name, ip, dev_id,
            )
            return
        async with sem:
            collected = await airos_api_service.collect_airos_link_metrics(
                host=ip, username=user, password=pwd, port=443,  # airOS forces HTTPS
            )
        if collected is None:
            logger.debug("airOS API no response — %s (%s)", name, ip)
            return
        metrics, hostname, netrole = collected
        if not any(v is not None for v in metrics.values()):
            logger.warning("airOS API no data — %s (%s)", name, ip)
            return
        logger.info(
            "airOS %s (%s) — %s", name, ip,
            " | ".join(f"{k}={v}" for k, v in metrics.items() if v is not None),
        )
        fetched[dev_id] = (metrics, hostname, netrole)

    tasks = [asyncio.ensure_future(_fetch(*t)) for t in targets]
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_AIROS_POLL_DEADLINE_S,
        )
    except TimeoutError:  # asyncio.TimeoutError is an alias of builtin on 3.11+
        for t in tasks:
            t.cancel()
        logger.warning(
            "airOS API poll : deadline %.0fs atteinte — %d/%d device(s) "
            "récupéré(s), le reste sera repris au prochain cycle.",
            _AIROS_POLL_DEADLINE_S, len(fetched), len(tasks),
        )

    # ── Phase 2 : persist + alert engine + topologie en série DB ──
    for dev_id, (metrics, hostname, netrole) in fetched.items():
        async with async_session_factory() as session:
            dev = await session.get(Device, dev_id)
            if dev is None:
                continue

            # Sync the stable distance_m column for quick UI display (LR + PTP
            # LiteBeam both carry it; a generic Device does not).
            distance = metrics.get("distance_m")
            if distance is not None and isinstance(dev, (Lr, PtpLiteBeam)):
                dev.distance_m = distance

            # Name always tracks the airOS-configured hostname for airMAX LRs:
            # airOS is the source of truth, so a rename on the device propagates
            # here every cycle. Manual renames in our UI are intentionally
            # overwritten — change the hostname in airOS instead.
            if hostname:
                if dev.hostname != hostname:
                    dev.hostname = hostname
                if dev.name != hostname:
                    logger.info(
                        "airMAX LR name ← airOS hostname — '%s' (%s) → '%s'",
                        dev.name, dev.ip_address, hostname,
                    )
                    dev.name = hostname

            await persist_device_metrics(session, dev.id, metrics, unit_map)

            # Per-LR alert engine pass — radio-quality + link_substandard rules
            # evaluate against this LR's metrics (engine injects model_variant
            # so airMAX-family thresholds apply).
            await alert_engine.evaluate_device_metrics(session, dev, dict(metrics), settings)

            # Router vs bridge — netrole comes free in status.cgi, so the
            # client-block topology guard is kept fresh every cycle without any
            # SSH probe (the former hourly lr_topology_check_job was removed:
            # both families now read mode from their HTTP poll). LR only: a P2P
            # backhaul Rocket has no client-block / topology_mode column.
            if isinstance(dev, Lr):
                await _apply_lr_topology(session, dev, netrole, "netrole via airOS status.cgi")

            await session.commit()


@_timed_job
async def af60_api_poll_job() -> None:
    """Poll airFiber 60 (AF60-LR) — liens backhaul 60 GHz via leur UDAPI locale.

    Même mécanique que airos_api_poll_job, mais pour les AF60 : chaque lien est
    interrogé à son IP (POST /api/auth + GET /api/v1.0/statistics, identique aux
    LTU), les métriques de lien sont persistées et l'alert engine évalue les
    règles AF60 (lien coupé, signal, SNR, lien dégradé consolidé). Lien
    point-à-point → pas d'auto-découverte de peers. Le device doit être `up`
    (joignable en mgmt) : un AF60 injoignable est géré par device_ping_job.
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    async with async_session_factory() as session:
        result = await session.execute(
            select(AirFiber).where(
                AirFiber.status == "up",
                AirFiber.ssh_username.is_not(None),
                AirFiber.ssh_password.is_not(None),
            )
        )
        devices = list(result.scalars().all())

    if not devices:
        logger.debug("AF60 API poll — no eligible devices")
        return

    logger.info("AF60 API poll — checking %d device(s)", len(devices))
    unit_map = af60_api_service.METRIC_UNITS

    for device in devices:
        metrics = await af60_api_service.collect_af60_metrics(
            host=device.ip_address,
            username=device.ssh_username,
            password=device.ssh_password,
            port=device.ssh_port or 443,
        )
        if metrics is None:
            logger.debug("AF60 API no response — %s (%s)", device.name, device.ip_address)
            continue

        logger.info(
            "AF60 %s (%s) — %s",
            device.name,
            device.ip_address,
            " | ".join(f"{k}={v}" for k, v in metrics.items() if v is not None),
        )

        async with async_session_factory() as session:
            dev = await session.get(AirFiber, device.id)
            if dev is None:
                continue

            distance = metrics.get("distance_m")
            if distance is not None:
                dev.distance_m = distance

            await persist_device_metrics(session, dev.id, metrics, unit_map)

            # Évaluation per-device : règles AF60 (rule_category == "airfiber").
            await alert_engine.evaluate_device_metrics(session, dev, dict(metrics), settings)
            await session.commit()


async def _evaluate_lr_transit(
    session, lr: Device, ping_ok: bool, settings,
) -> None:
    """Anti-flap "LR sans transit" — ouvre/résout AT_LR_NO_TRANSIT.

    Appelée par `lr_internet_probe_job` après une connexion SSH réussie.
    Compteur persisté en AlertState (clé = AT_LR_NO_TRANSIT), seuil =
    `transit_probe_threshold` cycles consécutifs KO avant ouverture critique.
    Résolution dès qu'un cycle repasse en succès.
    """
    state_res = await session.execute(
        select(AlertState).where(
            AlertState.device_id == lr.id,
            AlertState.alert_type == AT_LR_NO_TRANSIT,
        )
    )
    state = state_res.scalar_one_or_none()
    if state is None:
        state = AlertState(
            device_id=lr.id,
            alert_type=AT_LR_NO_TRANSIT,
            failure_count=0,
        )
        session.add(state)
        await session.flush()

    state.last_evaluated_at = datetime.datetime.now(datetime.UTC)

    if ping_ok:
        if state.failure_count > 0:
            state.failure_count = 0
        await _resolve_and_notify(
            session, lr,
            "RECOVERY : LTU LR de nouveau joignable depuis internet via le lien radio",
            alert_type=AT_LR_NO_TRANSIT,
        )
        return

    state.failure_count += 1
    count = state.failure_count

    if count < settings.transit_probe_threshold:
        logger.warning(
            "lr_internet_probe: %s (%s) KO transit %d/%d cycles — seuil non atteint",
            lr.name, lr.ip_address, count, settings.transit_probe_threshold,
        )
        return

    title = "ALERTE CRITIQUE : LTU LR sans transit internet via le lien radio"
    desc = (
        f"SSH vers {lr.ip_address} : OK — l'équipement est joignable sur le "
        f"réseau local.\n"
        f"Ping vers {settings.lr_latency_target} depuis {lr.name} : ÉCHOUE "
        f"({count} cycles consécutifs).\n"
        f"Le LTU LR est allumé mais ne peut pas joindre internet — "
        f"problème probable sur le lien radio ou la configuration de routage."
    )
    await _open_and_notify(
        session, lr, title, "critical", desc, alert_type=AT_LR_NO_TRANSIT,
    )
    logger.warning(
        "lr_internet_probe: %s (%s) — SSH OK, ping %s ÉCHOUE (%d/%d cycles)",
        lr.name, lr.ip_address, settings.lr_latency_target,
        count, settings.transit_probe_threshold,
    )


# ── Backoff SSH par LR (in-memory, comme les compteurs de ping) ──────────────
# Diagnostic terrain 2026-06-16 : les LR au SSH sain (connexion solo < 1,5 s)
# échouaient sous concurrence (« No existing session ») car 150 poignées SSH
# simultanées saturent le médium radio partagé → pertes pendant le kex. On ne
# RÉPARE pas un LR cassé ici : on arrête de RE-MARTELER à chaque cycle ceux qui
# enchaînent les échecs SSH, ce qui réduit la charge radio simultanée (et donc
# les échecs des LR sains restés dans le lot). Un LR chroniquement KO n'est plus
# sondé qu'1 cycle sur _LR_SSH_BACKOFF_DIVISOR (étalé par dev_id pour lisser),
# et revient immédiatement dans le pool dès qu'un sondage réussit.
_LR_SSH_FAIL_THRESHOLD = 3          # échecs SSH consécutifs avant mise en backoff
_LR_SSH_BACKOFF_DIVISOR = 10        # un LR en backoff sondé ~1 cycle sur 10
_lr_ssh_fail_streak: dict[int, int] = {}
_lr_probe_cycle = 0

# Pool de threads SSH partagé sur toute la vie du process scheduler. Avant il
# était recréé (puis join) à CHAQUE cycle → churn de N threads/cycle. Singleton :
# les workers sont créés une fois et réutilisés. La taille est figée au 1er appel
# (lr_probe_concurrency, réglage infra non surchargeable au runtime).
_lr_probe_pool: ThreadPoolExecutor | None = None


def _get_lr_probe_pool(max_workers: int) -> ThreadPoolExecutor:
    global _lr_probe_pool
    if _lr_probe_pool is None:
        _lr_probe_pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="lr-probe",
        )
    return _lr_probe_pool


@_timed_job
async def lr_internet_probe_job() -> None:
    """Sonde par LR : accès Internet (transit) + latence vers Google.

    Pour chaque LR ayant des credentials SSH, une seule connexion SSH par
    cycle exécute ``ping -c N`` vers `lr_latency_target` (8.8.8.8) puis :

      - SSH KO              → équipement injoignable, géré par device_ping_job,
                              on saute (pas d'alerte ici).
      - SSH OK, ping KO     → `AT_LR_NO_TRANSIT` (anti-flap
                              `transit_probe_threshold` cycles, critique).
      - SSH OK, ping OK     → `AT_LR_NO_TRANSIT` résolu si présent, puis
                              persistance `lr_latency_ms` et évaluation de la
                              latence (`AT_LR_LATENCY_HIGH` critique si avg ≥
                              `lr_latency_critical_ms` sur
                              `lr_latency_failure_threshold` cycles).

    Remplace l'ancien `lr_transit_probe_job` (qui n'évaluait qu'un seul LR
    pour des raisons historiques de maquette). Tous les LR sont désormais
    couverts, avec une seule session SSH par cycle.

    Les seuils (transit_probe_threshold, lr_latency_critical_ms,
    lr_latency_failure_threshold, lr_latency_ping_count) sont lus via
    get_effective_settings → une surcharge faite dans la page Seuils prend
    effet au cycle suivant sans redémarrage, comme pour les autres jobs.
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    async with async_session_factory() as session:
        # Skip LRs already known DOWN by device_ping_job — sinon chaque cycle
        # gaspille un timeout SSH (~10–30 s) par LR mort. Un LR down ne lève
        # aucun incident (panne côté client) ; on reprend la sonde dès qu'il
        # remonte (status repassé à up).
        result = await session.execute(
            select(Lr).where(
                Lr.ssh_username.is_not(None),
                Lr.ssh_password.is_not(None),
                Lr.status == "up",
                Lr.ip_address.is_not(None),
            )
        )
        # Snapshot des champs nécessaires à la sonde SSH (lus session ouverte) —
        # la Phase 1 tourne hors session.
        targets = [
            (lr.id, lr.name, lr.ip_address, lr.ssh_port or 22,
             lr.ssh_username, lr.ssh_password, lr.ssh_host_fingerprint)
            for lr in result.scalars().all()
        ]

    if not targets:
        logger.debug("lr_internet_probe: aucun LR up avec credentials SSH — ignoré")
        return

    # Backoff : on saute ce cycle-ci les LR qui enchaînent ≥ _LR_SSH_FAIL_THRESHOLD
    # échecs SSH, sauf 1 cycle sur _LR_SSH_BACKOFF_DIVISOR (étalé par dev_id).
    # Réduit le nombre de poignées SSH simultanées sur la radio → moins d'échecs
    # pour les LR sains. Les LR sondés normalement (streak < seuil) passent tous.
    global _lr_probe_cycle
    _lr_probe_cycle += 1
    cycle = _lr_probe_cycle

    def _is_due(dev_id: int) -> bool:
        streak = _lr_ssh_fail_streak.get(dev_id, 0)
        if streak < _LR_SSH_FAIL_THRESHOLD:
            return True
        return (cycle + dev_id) % _LR_SSH_BACKOFF_DIVISOR == 0

    total = len(targets)
    targets = [t for t in targets if _is_due(t[0])]
    backed_off = total - len(targets)
    if not targets:
        logger.debug("lr_internet_probe: tous les LR en backoff ce cycle — ignoré")
        return

    logger.info(
        "lr_internet_probe — sonde sur %d/%d LR(s) (%d en backoff SSH)",
        len(targets), total, backed_off,
    )

    # ── Phase 1 : sonder tous les LR EN PARALLÈLE (borné par le pool) ──
    # Le job était série (1 SSH à la fois) → ~1 h/tour à 500 LR. On exécute la
    # sonde paramiko sync (_measure_latency_via_ssh_sync, déjà bornée par ses
    # timeouts connect 6 s / ping ~20 s) sur un pool de threads dédié : le pool
    # limite lui-même la concurrence à lr_probe_concurrency, le reste fait la
    # queue. dev_id -> (ssh_ok, ping_ok, avg_rtt, msg, observed_fp, used_pw).
    loop = asyncio.get_running_loop()
    results: dict[int, tuple] = {}

    async def _probe(t: tuple, pool: ThreadPoolExecutor) -> None:
        dev_id, name, ip, port, user, pwd, fp = t
        try:
            results[dev_id] = await loop.run_in_executor(
                pool,
                functools.partial(
                    ssh_service._measure_latency_via_ssh_sync,
                    ip, port, user, pwd,
                    settings.lr_latency_target,
                    settings.lr_latency_ping_count,
                    fp,
                    settings.lr_fallback_password_list,
                ),
            )
        except Exception:
            logger.exception("lr_internet_probe: sonde %s (%s) a crashé", name, ip)

    pool = _get_lr_probe_pool(settings.lr_probe_concurrency)
    await asyncio.gather(
        *[_probe(t, pool) for t in targets], return_exceptions=True,
    )

    # ── Phase 2 : traitement DB séquentiel des résultats récupérés ──
    # LR "up" (ping OK) mais dont la poignée SSH échoue : creds erronés, SSH
    # désactivé sur le LR, port 22 filtré, banner non lu… On les collecte pour
    # un récap actionnable en fin de cycle (paramiko lui-même est mis en sourdine
    # dans core/logging.py).
    ssh_failures: list[tuple[str, str, str]] = []
    async with async_session_factory() as session:
        for dev_id, (ssh_ok, ping_ok, avg_rtt, msg, observed_fp, used_pw) in results.items():
            dev = await session.get(Lr, dev_id)
            if dev is None:
                continue

            if observed_fp and dev.ssh_host_fingerprint != observed_fp:
                dev.ssh_host_fingerprint = observed_fp
            if used_pw and used_pw != dev.ssh_password:
                logger.info(
                    "lr_internet_probe: LR '%s' (%s) — fallback password "
                    "succeeded → promoting on LR.",
                    dev.name, dev.ip_address,
                )
                dev.ssh_password = used_pw

            if not ssh_ok:
                # Géré par device_ping_job (le LR est down). Pas d'alerte ici.
                # Incrémente le streak d'échec SSH → backoff si chronique.
                _lr_ssh_fail_streak[dev_id] = _lr_ssh_fail_streak.get(dev_id, 0) + 1
                ssh_failures.append((dev.name, dev.ip_address, msg or "—"))
                logger.debug(
                    "lr_internet_probe: %s (%s) SSH KO — %s (skip, géré par device_ping_job)",
                    dev.name, dev.ip_address, msg,
                )
                await session.commit()
                continue

            # SSH OK — sort du backoff immédiatement.
            _lr_ssh_fail_streak.pop(dev_id, None)

            # D'abord évaluer le transit (binary OK/KO).
            await _evaluate_lr_transit(session, dev, ping_ok, settings)

            # Si transit OK et RTT mesuré → métrique + évaluation latence.
            # lr_latency_ms n'est lu qu'en "latest" (LATERAL dans lr_health) →
            # collapse (1 ligne/LR) via persist_device_metrics.
            if ping_ok and avg_rtt is not None:
                await persist_device_metrics(
                    session, dev.id, {"lr_latency_ms": avg_rtt}, {"lr_latency_ms": "ms"}
                )
                await _evaluate_lr_latency(session, dev, avg_rtt, settings)
            elif ping_ok:
                # Ping a réussi (exit 0) mais RTT non parsé — défensif.
                logger.debug(
                    "lr_internet_probe: %s (%s) ping OK mais RTT non parsé — %s",
                    dev.name, dev.ip_address, msg,
                )

            await session.commit()

    # Récap actionnable : quels LR sont up (ping OK) mais injoignables en SSH.
    # Une seule ligne WARNING par cycle (pas un flood par device), capée pour
    # rester lisible. À investiguer : creds SSH erronés / SSH désactivé / port 22.
    if ssh_failures:
        shown = ssh_failures[:80]
        listing = ", ".join(f"{name} ({ip}) [{reason}]" for name, ip, reason in shown)
        extra = f" … +{len(ssh_failures) - len(shown)} autre(s)" if len(ssh_failures) > len(shown) else ""
        logger.warning(
            "lr_internet_probe: %d/%d LR up mais SSH KO — %s%s",
            len(ssh_failures), len(results), listing, extra,
        )


async def warning_digest_job() -> None:
    """
    Flush the pending warning digest: collect all open undigested warnings
    whose alert_type policy is groupable, send a single batched notification
    per channel, and mark the included incidents as digested.
    """
    async with async_session_factory() as session:
        try:
            sent = await digest_service.flush_warning_digest(session)
            if sent:
                logger.info("Warning digest flushed — %d warnings", sent)
            else:
                logger.debug("Warning digest: nothing to send")
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _apply_lr_topology(session, dev: Lr, mode: str | None, source_msg: str) -> None:
    """Persist Lr.topology_mode and open/resolve AT_LR_BRIDGE_MODE_MISCONFIG.

    `mode` is "router", "bridge", or None/anything else (probe failed → no-op,
    keep the previously-known classification). Shared by the SSH topology check
    (LTU LRs) and the airOS HTTP poll (airMAX LRs, where netrole is free in
    status.cgi). `source_msg` describes how the mode was observed and is shown
    in the incident description.
    """
    if mode not in ("router", "bridge"):
        return

    previous = dev.topology_mode
    dev.topology_mode = mode

    if mode == "bridge":
        title = f"Mauvaise config : LR '{dev.name}' en mode bridge"
        description = (
            f"Le LR {dev.name} ({dev.ip_address}) est détecté en mode "
            f"bridge ({source_msg}). Le blocage client (Coupure totale / "
            f"WhatsApp autorisé) ne peut PAS fonctionner sur ce LR "
            f"tant qu'il reste en bridge — le trafic du client ne "
            f"traverse ni iptables FORWARD ni le dnsmasq local. "
            f"Reconfigurer le LR en mode routeur via son interface "
            f"web (airOS) pour que la feature soit opérationnelle."
        )
        await _open_and_notify(
            session, dev, title, "warning", description,
            alert_type=AT_LR_BRIDGE_MODE_MISCONFIG,
        )
        logger.warning(
            "LR topology — '%s' (id=%d) en mode bridge : %s",
            dev.name, dev.id, source_msg,
        )
    elif mode == "router" and previous == "bridge":
        # Topology fixed by the operator → resolve the incident
        await _resolve_and_notify(
            session, dev,
            title=f"RECOVERY : LR '{dev.name}' repassé en mode routeur",
            alert_type=AT_LR_BRIDGE_MODE_MISCONFIG,
        )
        logger.info(
            "LR topology — '%s' (id=%d) repassé en mode routeur",
            dev.name, dev.id,
        )


async def client_consumption_matview_refresh_job() -> None:
    """Refresh the `client_consumption_30d` materialized view.

    REFRESH MATERIALIZED VIEW CONCURRENTLY so readers are never blocked;
    it must run outside an explicit transaction → AUTOCOMMIT on the engine
    connection. Pre-computes the 30-day byte-delta aggregate used by
    /clients/consumption?period=30d (was ~36 s of seq scan + Python delta
    loop in prod 2026-06-02, target <100 ms after).

    The view is bounded to 30 days at REFRESH time via `now() - interval
    '30 days'` — the sliding window moves forward by 15 min each refresh.
    """
    from sqlalchemy import text

    from app.db.session import engine

    started = datetime.datetime.now(datetime.UTC)
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text("REFRESH MATERIALIZED VIEW CONCURRENTLY client_consumption_30d")
            )
        elapsed = (datetime.datetime.now(datetime.UTC) - started).total_seconds()
        logger.info("client_consumption matview refresh — done in %.2f s", elapsed)
        if elapsed > 60:
            logger.warning(
                "client_consumption matview refresh took %.1f s (> 60 s) — "
                "device_metrics is large, plan retention.",
                elapsed,
            )
    except Exception:
        logger.exception(
            "client_consumption matview refresh failed — page will keep "
            "serving the previous snapshot until next attempt",
        )


async def client_consumption_7d_refresh_job() -> None:
    """Refresh the `client_consumption_7d` materialized view.

    Same pattern as `client_consumption_matview_refresh_job` but for the
    7-day window. Separate matview rather than slicing the 30d one because
    the 30d aggregate is a single SUM that cannot be subtracted down to a
    narrower window. Cheap (~4 s of refresh) and unlocks the 7d tab.
    """
    from sqlalchemy import text

    from app.db.session import engine

    started = datetime.datetime.now(datetime.UTC)
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text("REFRESH MATERIALIZED VIEW CONCURRENTLY client_consumption_7d")
            )
        elapsed = (datetime.datetime.now(datetime.UTC) - started).total_seconds()
        logger.info("client_consumption_7d matview refresh — done in %.2f s", elapsed)
        if elapsed > 60:
            logger.warning(
                "client_consumption_7d matview refresh took %.1f s (> 60 s) — "
                "device_metrics is large, plan retention.",
                elapsed,
            )
    except Exception:
        logger.exception(
            "client_consumption_7d matview refresh failed — page will keep "
            "serving the previous snapshot until next attempt",
        )


@_timed_job
async def traffic_stats_retention_job() -> None:
    """Purge traffic_dest_stats buckets older than the retention window.

    The NetFlow collector aggregates per (5-min bucket, ASN), so this table is
    small, but it still grows unbounded without pruning. Batched delete on
    bucket_start (indexed by ix_traffic_dest_stats_bucket), in small committed
    chunks (never one giant transaction over the table).
    """
    from sqlalchemy import text

    settings = get_settings()
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        days=settings.traffic_stats_retention_days
    )
    batch = 50_000
    total = 0
    try:
        async with async_session_factory() as session:
            while True:
                result = await session.execute(
                    text(
                        "DELETE FROM traffic_dest_stats WHERE id IN ("
                        "  SELECT id FROM traffic_dest_stats"
                        "  WHERE bucket_start < :cutoff"
                        "  LIMIT :batch"
                        ")"
                    ),
                    {"cutoff": cutoff, "batch": batch},
                )
                await session.commit()
                deleted = result.rowcount or 0
                total += deleted
                if deleted < batch:
                    break
        if total:
            logger.info(
                "traffic_dest_stats retention — purged %d rows older than %d days",
                total, settings.traffic_stats_retention_days,
            )
        else:
            logger.debug("traffic_dest_stats retention — nothing to purge")
    except Exception:
        logger.exception("traffic_stats retention job failed")


@_timed_job
async def client_block_enforcement_job() -> None:
    """Re-assert every active client block so it survives an LR reboot.

    A rebooted LR comes back with its LAN port UP and its iptables flushed —
    the client would silently regain internet. This job re-applies the active
    block (port shutdown or WhatsApp-only filter, per Lr.block_mode) on every
    LR still marked client_blocked, and retries blocks that couldn't be applied
    at click time. Idempotent: a no-op when nothing changed.
    """
    async with async_session_factory() as session:
        try:
            n = await client_block_service.enforce_blocked_clients(session)
            if n:
                logger.info("Client-block enforcement — %d LR(s) renforcé(s)", n)
            else:
                logger.debug("Client-block enforcement — rien à renforcer")
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Security — abnormal API write volume detection
# ---------------------------------------------------------------------------

# Per-IP cooldown: a sustained attack must not produce one email per check
# cycle. The map is module-level and reset on backend restart, which is the
# right behaviour: a restart counts as "operator intervened, re-arm alerts".
_security_alerted_until: dict[str, datetime.datetime] = {}


async def security_anomaly_detection_job() -> None:
    """Detect a flood of state-changing API calls and notify operators.

    Counts rows in `audit_log` per `client_ip` over the last
    `audit_anomaly_window_minutes`. Any IP above `audit_anomaly_max_mutations`
    triggers a security alert (email). Each IP is rate-limited by
    `audit_anomaly_alert_cooldown_minutes` so a sustained attack does not
    spam the inbox — one email per attacker per cooldown window.

    Born of incident 2026-05-17 (15 h of automated scanning went undetected).
    """
    settings = get_settings()
    if not settings.audit_log_enabled:
        return

    now = datetime.datetime.now(datetime.UTC)
    since = now - datetime.timedelta(minutes=settings.audit_anomaly_window_minutes)
    threshold = settings.audit_anomaly_max_mutations
    cooldown = datetime.timedelta(minutes=settings.audit_anomaly_alert_cooldown_minutes)

    async with async_session_factory() as session:
        result = await session.execute(
            select(AuditLog.client_ip, func.count().label("n"))
            .where(AuditLog.created_at >= since)
            .group_by(AuditLog.client_ip)
            .having(func.count() > threshold),
        )
        offenders = list(result.all())

    for client_ip, count in offenders:
        # Per-IP cooldown — empty/null IP collapses to a single bucket.
        key = client_ip or "(unknown)"
        alerted_until = _security_alerted_until.get(key)
        if alerted_until and now < alerted_until:
            logger.debug(
                "security_anomaly: IP %s still in cooldown (%s left)",
                key, alerted_until - now,
            )
            continue
        _security_alerted_until[key] = now + cooldown

        logger.error(
            "Security anomaly detected — IP %s made %d mutating requests in "
            "the last %d min (threshold %d). Possible attack.",
            key, count, settings.audit_anomaly_window_minutes, threshold,
        )
        subject = (
            f"[SÉCURITÉ] Volume anormal d'écritures API "
            f"({count} en {settings.audit_anomaly_window_minutes} min)"
        )
        body = (
            "Le superviseur a détecté un volume anormal d'opérations d'écriture\n"
            "(POST/PUT/PATCH/DELETE) sur l'API :\n\n"
            f"  IP source : {key}\n"
            f"  Nombre    : {count}\n"
            f"  Fenêtre   : {settings.audit_anomaly_window_minutes} minutes\n"
            f"  Seuil     : {threshold}\n\n"
            "Cela peut indiquer une attaque automatisée ou un client buggé.\n"
            "Consulter les logs backend et la table audit_log pour les détails.\n\n"
            f"Prochaine alerte pour cette IP au plus tôt dans "
            f"{settings.audit_anomaly_alert_cooldown_minutes} min."
        )
        try:
            await notification_service.notify_security_event(subject, body)
        except Exception:
            # Notification failure must not crash the detection job — the log
            # ERROR above is already the durable signal.
            logger.exception("security_anomaly: notify_security_event raised")


# ---------------------------------------------------------------------------
# Equipment flapping — > N availability incidents over a rolling window
# ---------------------------------------------------------------------------

@_timed_job
async def flap_detection_job() -> None:
    """Open a `device_flapping` incident for infra devices that keep bouncing.

    A flapping device repeatedly goes down then recovers. Each down event is one
    availability incident (rocket_down / switch_down / device_unreachable /
    airmax_down). Those rows are KEPT in DB after resolution (the downtime
    journal needs them), so we can count, per device, how many were detected over
    the last `flap_window_hours`. More than `flap_threshold_24h` of them ⇒
    unstable ⇒ open a critical incident (→ WhatsApp). Fewer again ⇒ resolve it.

    **UISP Power devices are excluded** : they naturally go up/down on mains
    (SOMELEC) outages, which is expected and already covered by mains_power_lost
    — counting that as "flapping" would be noise. Only the other infra equipment
    (Rockets, switches…) is evaluated.

    Only infra devices accumulate availability incidents (an LR down is never an
    incident), so this naturally never fires on a subscriber LR — and
    is_suppressed_incident would drop it anyway.
    """
    settings = get_settings()
    threshold = settings.flap_threshold_24h
    window = datetime.timedelta(hours=settings.flap_window_hours)
    since = datetime.datetime.now(datetime.UTC) - window

    async with async_session_factory() as session:
        try:
            # Per-device count of availability incidents in the window.
            # UISP Power excluded (device_type=="uisp_power") — their mains-outage
            # up/down cycles are expected, not equipment instability.
            count_res = await session.execute(
                select(Incident.device_id, func.count().label("n"))
                .join(Device, Device.id == Incident.device_id)
                .where(
                    Incident.alert_type.in_(AVAILABILITY_ALERT_TYPES),
                    Incident.detected_at >= since,
                    Device.device_type != "uisp_power",
                )
                .group_by(Incident.device_id)
            )
            counts: dict[int, int] = {row.device_id: row.n for row in count_res.all()}

            # Devices that already have an open flapping incident — candidates to
            # resolve when their count drops back to/under the threshold.
            open_res = await session.execute(
                select(Incident.device_id).where(
                    Incident.alert_type == AT_DEVICE_FLAPPING,
                    Incident.status == "open",
                )
            )
            open_flapping: set[int] = {row.device_id for row in open_res.all()}

            flapping_ids = {did for did, n in counts.items() if n > threshold}
            to_resolve = open_flapping - flapping_ids
            relevant = flapping_ids | to_resolve
            if not relevant:
                logger.debug("flap_detection: aucun équipement instable")
                return

            dev_res = await session.execute(
                select(Device).where(Device.id.in_(relevant))
            )
            devices = {d.id: d for d in dev_res.scalars().all()}

            for did in flapping_ids:
                device = devices.get(did)
                if device is None:
                    continue
                n = counts[did]
                title = f"ALERTE CRITIQUE : équipement instable {device.name} (flapping)"
                description = (
                    f"{device.name} ({device.ip_address}) a connu {n} coupures "
                    f"sur les dernières {settings.flap_window_hours} h "
                    f"(seuil : {threshold}). Lien/alimentation instable à investiguer."
                )
                await _open_and_notify(
                    session, device, title, "critical", description,
                    alert_type=AT_DEVICE_FLAPPING,
                )

            for did in to_resolve:
                device = devices.get(did)
                if device is None:
                    continue
                await _resolve_and_notify(
                    session, device,
                    title=f"RECOVERY : {device.name} stabilisé (plus de flapping)",
                    alert_type=AT_DEVICE_FLAPPING,
                )

            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Network-wide high latency — share of client LRs above the latency threshold
# ---------------------------------------------------------------------------

@_timed_job
async def network_latency_aggregate_job() -> None:
    """Daily check (WhatsApp) when more than N% of client LRs have high latency.

    Contrôle QUOTIDIEN (cadence network_latency_check_interval_minutes = 1440 min) :
    on évalue la part des LR `up` dont le dernier lr_latency_ms ≥ seuil (100 ms)
    et, si elle dépasse network_high_latency_pct (20 %), on envoie un message
    WhatsApp. Pas de flag franchissement / pas de message de rétabli : c'est un
    rapport quotidien qui ne part QUE si la condition est remplie.

    Signal réseau-wide, pas un incident par device (un Incident exige un
    device_id) → envoi WhatsApp direct. Réutilise
    lr_health_service.network_latency_summary (dernier lr_latency_ms par LR via
    LATERAL — pas de sonde live). Sous network_latency_min_sample relevés, le
    réseau est trop petit pour juger → on n'évalue pas.
    """
    settings = get_settings()
    threshold_pct = settings.network_high_latency_pct
    min_sample = settings.network_latency_min_sample

    async with async_session_factory() as session:
        summary = await lr_health_service.network_latency_summary(session)

    total = int(summary["total"])
    high = int(summary["high"])
    pct = float(summary["pct"])
    threshold_ms = float(summary["threshold_ms"])

    if total < min_sample:
        logger.debug(
            "network_latency: échantillon trop faible (%d < %d) — pas d'évaluation",
            total, min_sample,
        )
        return

    if pct > threshold_pct:
        logger.warning(
            "network_latency: %.0f%% des clients (%d/%d) ≥ %.0f ms — alerte WhatsApp",
            pct, high, total, threshold_ms,
        )
        message = (
            f"*🔴 Latence réseau élevée (rapport quotidien)*\n"
            f"{pct:.0f}% des clients ({high}/{total}) ont une latence "
            f"≥ {threshold_ms:.0f} ms vers Internet.\n"
            f"Seuil d'alerte : {threshold_pct}% du parc."
        )
        await whatsapp_service.send_whatsapp(message)
    else:
        logger.info(
            "network_latency: %.0f%% des clients (%d/%d) ≥ %.0f ms — sous le seuil "
            "(%d%%), pas d'alerte",
            pct, high, total, threshold_ms, threshold_pct,
        )


async def _claim_daily_run(key: str) -> bool:
    """Atomically claim today's run for a once-per-day report.

    Returns True if this is the first run for the current UTC day (caller should
    proceed), False if the report already ran today (caller should skip). Backed
    by an upsert on the ``system_settings`` key/value table so a report can't be
    re-sent on every container restart — the ``next_run_time=now`` boot trigger
    fires on each (re)start, which otherwise spams the WhatsApp group several
    times a day when the scheduler restarts (e.g. after an OOM kill).

    The upsert only writes (and so only returns a row) when the stored date
    differs from today, making the claim race-safe even across processes.
    """
    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    async with async_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "INSERT INTO system_settings (key, value) "
                    "VALUES (:key, :today) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value "
                    "WHERE system_settings.value <> EXCLUDED.value "
                    "RETURNING id"
                ),
                {"key": key, "today": today},
            )
        ).first()
        await session.commit()
    if row is None:
        logger.info("%s déjà envoyé aujourd'hui (%s) — saut", key, today)
    return row is not None


async def rocket_saturation_report_job() -> None:
    """Daily WhatsApp PDF report of saturated base-station Rockets.

    Builds a PDF listing every Rocket whose installed clients reached its
    capacity ceiling (current >= max, the rocket_client_overload condition) via
    saturation_report_service and sends it to the WhatsApp group as a document.
    Unlike network_latency_aggregate_job this is a CONTROL report: it is sent
    EVERY day even when no Rocket is saturated (empty-list PDF), so the absence
    of a message can't be mistaken for a missed run. Gated by
    rocket_saturation_report_enabled; the document send no-ops if WhatsApp is
    unconfigured.
    """
    settings = get_settings()
    if not settings.rocket_saturation_report_enabled:
        return
    # Once-per-day guard: a restart re-fires next_run_time=now and would re-send.
    if not await _claim_daily_run("last_run:rocket_saturation_report"):
        return

    async with async_session_factory() as session:
        pdf_bytes, saturated = await saturation_report_service.build_saturation_report(
            session
        )

    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    filename = f"rockets-satures-{today}.pdf"
    count = len(saturated)
    if count:
        caption = (
            f"*📄 Rapport quotidien — Rockets saturés*\n"
            f"{count} Rocket(s) ont atteint leur capacité maximale (clients "
            f"installés ≥ max). Détail dans le PDF."
        )
    else:
        caption = (
            "*📄 Rapport quotidien — Rockets saturés*\n"
            "Aucun Rocket saturé aujourd'hui. ✅"
        )

    logger.info("rocket_saturation_report: %d Rocket(s) saturé(s) — envoi PDF", count)
    await whatsapp_service.send_whatsapp_document(pdf_bytes, filename, caption)


async def site_infra_report_job() -> None:
    """Daily WhatsApp PDF report of per-site infra-equipment budget.

    Builds a PDF listing every site with its infra device count (Rockets + AF60 +
    PTP LiteBeam; switches/UISP Power excluded) against SITE_INFRA_MAX (14), and
    its margin: +N free slots or -N over budget. Sent EVERY day as a control
    report (same contract as rocket_saturation_report_job). Gated by
    site_infra_report_enabled; the document send no-ops if WhatsApp is
    unconfigured.
    """
    settings = get_settings()
    if not settings.site_infra_report_enabled:
        return
    # Once-per-day guard: a restart re-fires next_run_time=now and would re-send.
    if not await _claim_daily_run("last_run:site_infra_report"):
        return

    async with async_session_factory() as session:
        pdf_bytes, rollup = await site_infra_service.build_site_infra_report(session)

    today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    filename = f"capacite-infra-sites-{today}.pdf"
    threshold = rollup["threshold"]
    over = sum(1 for s in rollup["sites"] if s["over"])
    if over:
        caption = (
            f"*📄 Rapport quotidien — Capacité infra par site*\n"
            f"{over} site(s) dépassent le maximum de {threshold} équipements "
            f"infra. Détail dans le PDF."
        )
    else:
        caption = (
            f"*📄 Rapport quotidien — Capacité infra par site*\n"
            f"Tous les sites sont sous le maximum de {threshold} équipements "
            f"infra. ✅"
        )

    logger.info("site_infra_report: %d site(s) en dépassement — envoi PDF", over)
    await whatsapp_service.send_whatsapp_document(pdf_bytes, filename, caption)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@_timed_job
async def uisp_sync_job() -> None:
    """Import infrastructure devices from the UISP controller (name/IP/site).

    Disabled unless UISP_SYNC_ENABLED and a base URL + credentials (token or
    username/password) are configured. Subscriber stations are ignored; nothing
    is ever deleted. See services/uisp_sync_service.
    """
    settings = get_settings()
    if not settings.uisp_sync_enabled:
        return
    has_auth = settings.uisp_api_token or (settings.uisp_username and settings.uisp_password)
    if not settings.uisp_base_url or not has_auth:
        logger.warning(
            "UISP sync enabled but UISP_BASE_URL / credentials missing — skipping cycle",
        )
        return
    try:
        async with async_session_factory() as session:
            await uisp_sync_service.sync_uisp_devices(session)
            # Client stations run AFTER infra so the just-upserted Rockets exist
            # to resolve each station's parent AP. Gated separately so the infra
            # import can run without pulling ~1000 client rows.
            if settings.uisp_station_sync_enabled:
                await uisp_sync_service.sync_uisp_stations(session)
            await session.commit()
    except uisp_sync_service.uisp_service.UISPAuthError as exc:
        logger.error("UISP sync auth failed: %s", exc)
    except Exception as exc:
        logger.error("UISP sync cycle failed: %s", exc)


async def lr_plan_sync_job() -> None:
    """Lit le forfait (caps du traffic shaper airOS) de chaque LR up via SSH et
    le met en cache (plan_download_mbps / plan_upload_mbps / plan_synced_at).

    Cadence lente (`lr_plan_sync_interval_minutes`, défaut 24 h) car le forfait
    change rarement ; tourne aussi 1× au démarrage du scheduler et est
    déclenchable à la demande via POST /devices/plans/sync. La concurrence SSH
    est bornée par `lr_probe_concurrency`. Voir `lr_plan_service`.
    """
    try:
        async with async_session_factory() as session:
            summary = await lr_plan_service.sync_all_lr_plans(session)
        logger.info("lr_plan_sync_job terminé — %s", summary)
    except Exception as exc:
        logger.error("lr_plan_sync_job cycle failed: %s", exc)


# ── Classification des jobs par groupe de process (scheduler_group) ───────────
# À ~1000+ devices, faire tourner TOUS les jobs dans un seul process saturait le
# GIL : la sonde SSH (ThreadPoolExecutor) affamait le device_ping → last_seen
# figé ~20 min. On scinde la charge sur 2 process (containers) : "fast" porte la
# disponibilité + la maintenance (tout async léger, latence-critique) ; "heavy"
# porte le SSH et les gros fan-outs API. Le heartbeat tourne dans CHAQUE process
# (liveness). En "all" (dev, process unique) rien n'est élagué.
_ALWAYS_JOB_IDS = {"heartbeat"}
_FAST_JOB_IDS = {
    "infra_ping", "client_ping", "warning_digest", "flap_detection",
    "network_latency_aggregate", "client_consumption_matview_refresh",
    "client_consumption_7d_refresh",
    "traffic_stats_retention",
    "security_anomaly_detection", "rocket_saturation_report",
    "site_infra_report",
}
_HEAVY_JOB_IDS = {
    "snmp_poll", "power_poll", "lr_internet_probe", "ltu_api_poll",
    "airos_api_poll", "af60_api_poll", "lr_plan_sync",
    "client_block_enforcement", "uisp_sync",
}


def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """Register all scheduled jobs. Intervals are read from settings.

    Safety params on every job:
      - max_instances=1     : never run a second copy if the previous one is still running
      - coalesce=True       : if the scheduler missed several runs, only fire once
      - misfire_grace_time  : ignore runs older than this when catching up

    ``settings.scheduler_group`` ("all" | "fast" | "heavy") sélectionne le
    sous-ensemble effectif : tous les jobs sont d'abord enregistrés, puis ceux
    hors groupe sont retirés (élagage robuste aux jobs conditionnels).
    """
    settings = get_settings()
    safety = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 15}

    scheduler.add_job(
        heartbeat_job,
        trigger="interval", seconds=60,
        id="heartbeat", name="Heartbeat check",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        infra_ping_job,
        trigger="interval", seconds=settings.ping_interval_seconds,
        id="infra_ping", name="Infra ping poll (Rockets/switches/power/AF60)",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        client_ping_job,
        trigger="interval", seconds=settings.client_ping_interval_seconds,
        id="client_ping", name="LR client ping poll",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        snmp_poll_job,
        trigger="interval", seconds=settings.snmp_interval_seconds,
        id="snmp_poll", name="SNMP metrics poll",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        power_poll_job,
        trigger="interval", seconds=settings.power_interval_seconds,
        id="power_poll", name="UISP Power poll",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        lr_internet_probe_job,
        trigger="interval", seconds=settings.lr_latency_interval,
        id="lr_internet_probe",
        name="LR → Internet probe (transit + latency, all LRs)",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        ltu_api_poll_job,
        trigger="interval", seconds=settings.snmp_interval_seconds,
        id="ltu_api_poll", name="LTU HTTP API poll",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        airos_api_poll_job,
        trigger="interval", seconds=settings.snmp_interval_seconds,
        id="airos_api_poll", name="airOS HTTP API poll (airMAX LR)",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        af60_api_poll_job,
        trigger="interval", seconds=settings.snmp_interval_seconds,
        id="af60_api_poll", name="airFiber 60 HTTP API poll (AF60 backhaul)",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        warning_digest_job,
        trigger="interval", minutes=settings.warning_digest_minutes,
        id="warning_digest", name="Warning digest flush",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        client_consumption_matview_refresh_job,
        trigger="interval",
        minutes=settings.client_consumption_matview_refresh_interval_minutes,
        id="client_consumption_matview_refresh",
        name="Client consumption matview refresh (30-day byte deltas)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    scheduler.add_job(
        client_consumption_7d_refresh_job,
        trigger="interval",
        minutes=settings.client_consumption_7d_refresh_interval_minutes,
        id="client_consumption_7d_refresh",
        name="Client consumption matview refresh (7-day byte deltas)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    scheduler.add_job(
        traffic_stats_retention_job,
        trigger="interval",
        minutes=settings.traffic_stats_retention_interval_minutes,
        id="traffic_stats_retention",
        name="traffic_dest_stats retention (purge NetFlow aggregates older than N days)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        lr_plan_sync_job,
        trigger="interval", minutes=settings.lr_plan_sync_interval_minutes,
        id="lr_plan_sync",
        name="LR subscription plan sync (traffic-shaper rate caps via SSH)",
        replace_existing=True,
        # Run once right after the scheduler boots so plans populate at deploy
        # instead of waiting a full day.
        next_run_time=datetime.datetime.now(),
        max_instances=1, coalesce=True, misfire_grace_time=120,
    )
    scheduler.add_job(
        flap_detection_job,
        trigger="interval", minutes=settings.flap_check_interval_minutes,
        id="flap_detection",
        name="Equipment flapping detection (> N outages / window)",
        replace_existing=True,
        max_instances=1, coalesce=True, misfire_grace_time=120,
    )
    scheduler.add_job(
        network_latency_aggregate_job,
        trigger="interval", minutes=settings.network_latency_check_interval_minutes,
        id="network_latency_aggregate",
        name="Network-wide high-latency share — daily WhatsApp report",
        replace_existing=True,
        # Rapport quotidien : tolérance de misfire large (un redémarrage du
        # scheduler ne doit pas annuler le contrôle du jour).
        max_instances=1, coalesce=True, misfire_grace_time=3600,
    )
    if settings.rocket_saturation_report_enabled:
        scheduler.add_job(
            rocket_saturation_report_job,
            trigger="cron", hour=settings.rocket_saturation_report_hour, minute=0,
            timezone="UTC",
            id="rocket_saturation_report",
            name="Saturated Rockets — daily WhatsApp PDF report",
            replace_existing=True,
            # Run once right after the scheduler boots (i.e. at deploy) so the
            # report goes out immediately; the cron then fires daily at
            # rocket_saturation_report_hour:00 UTC (Mauritania GMT → 07:00 local).
            next_run_time=datetime.datetime.now(),
            # Rapport quotidien : tolérance de misfire large (un redémarrage du
            # scheduler ne doit pas annuler le contrôle du jour).
            max_instances=1, coalesce=True, misfire_grace_time=3600,
        )
    if settings.site_infra_report_enabled:
        scheduler.add_job(
            site_infra_report_job,
            trigger="cron", hour=settings.site_infra_report_hour, minute=0,
            timezone="UTC",
            id="site_infra_report",
            name="Per-site infra-equipment budget — daily WhatsApp PDF report",
            replace_existing=True,
            # Run once at scheduler boot (deploy) then daily at the hour, UTC.
            next_run_time=datetime.datetime.now(),
            max_instances=1, coalesce=True, misfire_grace_time=3600,
        )
    if settings.client_block_enforcement_enabled:
        scheduler.add_job(
            client_block_enforcement_job,
            trigger="interval", seconds=settings.client_block_enforce_interval,
            id="client_block_enforcement",
            name="Client block enforcement (re-assert blocks after LR reboot)",
            replace_existing=True,
            **safety,
        )
    if settings.audit_log_enabled:
        scheduler.add_job(
            security_anomaly_detection_job,
            trigger="interval",
            seconds=settings.audit_anomaly_check_interval_seconds,
            id="security_anomaly_detection",
            name="Security anomaly detection (abnormal API write volume)",
            replace_existing=True,
            **safety,
        )
    if settings.uisp_sync_enabled:
        scheduler.add_job(
            uisp_sync_job,
            trigger="cron", hour=settings.uisp_sync_hour, minute=0, timezone="UTC",
            id="uisp_sync",
            name="UISP controller inventory sync (infra + client stations)",
            replace_existing=True,
            # Run once right after the scheduler boots (i.e. at deploy) so the
            # import happens immediately; the cron then fires daily at
            # uisp_sync_hour:00 UTC (Mauritania is GMT/UTC+0 → 07:00 local).
            next_run_time=datetime.datetime.now(),
            max_instances=1, coalesce=True, misfire_grace_time=3600,
        )

    # ── Élagage par groupe de process ────────────────────────────────────────
    # "all" → on garde tout (dev, process unique). "fast"/"heavy" → on retire les
    # jobs hors groupe. Un job non classé est conservé en "fast" par défaut (plus
    # sûr que de le perdre silencieusement dans les deux process).
    group = (settings.scheduler_group or "all").lower()
    if group in ("fast", "heavy"):
        classified = _ALWAYS_JOB_IDS | _FAST_JOB_IDS | _HEAVY_JOB_IDS
        active = _FAST_JOB_IDS if group == "fast" else _HEAVY_JOB_IDS

        def _drop(job_id: str) -> None:
            # remove_job sur un job pending (avant start) est supporté ; on blinde
            # quand même pour qu'un échec n'empêche pas le scheduler de démarrer.
            try:
                scheduler.remove_job(job_id)
            except Exception as exc:  # noqa: BLE001 — boot must never crash here
                logger.warning("Élagage job '%s' échoué (%s) — ignoré", job_id, exc)

        for job in list(scheduler.get_jobs()):
            if job.id in _ALWAYS_JOB_IDS:
                continue
            if job.id not in classified:
                logger.warning(
                    "Job '%s' non classé en groupe scheduler — conservé en 'fast'", job.id
                )
                if group != "fast":
                    _drop(job.id)
                continue
            if job.id not in active:
                _drop(job.id)
        logger.info(
            "Scheduler group '%s' — %d job(s) actifs : %s",
            group,
            len(scheduler.get_jobs()),
            ", ".join(sorted(j.id for j in scheduler.get_jobs())),
        )


