"""
Scheduled supervision jobs.

- heartbeat_job     : sanity check every 60s
- device_ping_job   : ICMP ping all devices every 30s → opens/resolves incidents
- snmp_poll_job     : SNMP metrics for LTU/airMAX devices every 60s → alert engine
- power_poll_job    : UISP Power API polling every 30s → power anomaly detection
- ltu_api_poll_job  : LTU HTTP API polling every 60s → alert engine (radio quality)
- airos_api_poll_job: airMAX LR (LiteBeam) airOS HTTP API polling every 60s → alert engine
- transit_probe_job : Transit connectivity probe every 60s
"""

import asyncio
import datetime
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import delete, func, select

from app.core.alert_constants import (
    AT_AIRMAX_DOWN,
    AT_LR_BRIDGE_MODE_MISCONFIG,
    AT_LR_LATENCY_HIGH,
    AT_LR_NO_TRANSIT,
    AT_MAINS_POWER_LOST,
    AT_ROCKET_DOWN,
    AT_SWITCH_DOWN,
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
from app.models.alert import Alert
from app.models.alert_state import AlertState
from app.models.audit_log import AuditLog
from app.models.device import AirFiber, Device, Lr, Rocket, UispPower
from app.models.device_metric import DeviceMetric
from app.models.power_status_log import PowerStatusLog
from app.services import (
    af60_api_service,
    airos_api_service,
    alert_engine,
    client_block_service,
    digest_service,
    discovery_service,
    email_service,
    incident_service,
    ltu_api_service,
    notification_service,
    poller,
    snmp_service,
    ssh_service,
    threshold_service,
    uisp_power_service,
)

logger = logging.getLogger(__name__)

# alert_type sentinel used to persist the consecutive-ping-failure counter in
# AlertState. Picking a leading underscore keeps it out of the regular alert
# vocabulary (no policy, no incident, no formatter touches it).
_PING_FAILURE_STATE_KEY = "_ping_failures"


# ── device_metrics persistence policy ───────────────────────────────────────
# Only these metric names are ever read as a TIME SERIES (history) by a real
# consumer:
#   - byte counters → consumption_service LAG() deltas (24h/7d/30d)
#   - signal/link_potential/capacity/rate idx → lr_health_metric_stats_30d
#     matview (30-day avg, keep this set in sync with that view's metric list)
#   - signal/cinr/ccq → report_service avg/min over the report window
# Everything else we poll is read ONLY as "latest" (the /metrics/latest LATERAL
# probe). The alert engine reads its baselines (EMA throughput, error deltas)
# from AlertState, never from device_metrics — so collapsing a non-history
# metric to a single row breaks no alert. We therefore APPEND history metrics
# (one row per cycle) and COLLAPSE all others to a single row per
# (device_id, metric_name) via DELETE+INSERT — the same strategy already proven
# on UISP Switch metrics. Without this a single UISP Power device appends ~25
# metrics every 30 s (~70k rows/day) that nothing reads past the last point.
HISTORY_METRICS: frozenset[str] = frozenset({
    # consumption_service — cumulative byte counters → only meaningful as deltas
    "peer_tx_bytes", "peer_rx_bytes", "radio_rx_bytes", "radio_tx_bytes",
    # lr_health_metric_stats_30d matview (mirror of _TRACKED_METRICS)
    "signal_dbm", "link_potential_pct", "total_capacity_mbps",
    "local_rx_rate_idx", "remote_rx_rate_idx",
    # report_service RADIO_METRIC_NAMES (signal_dbm already above)
    "cinr_db", "ccq_pct",
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
    Each per-metric DELETE rides ``ix_device_metrics_lookup`` (device_id,
    metric_name), so in steady state it removes exactly one row; on the first
    cycle after a metric becomes collapse-only it also absorbs that metric's
    historical backlog, entirely inside the scheduler (never on the backend
    startup path — a bulk migration delete once stalled the healthcheck).
    """
    if now is None:
        now = datetime.datetime.now(datetime.UTC)
    units = unit_map or {}
    for metric_name, value in metrics.items():
        if value is None:
            continue
        if metric_name not in HISTORY_METRICS:
            await session.execute(
                delete(DeviceMetric).where(
                    DeviceMetric.device_id == device_id,
                    DeviceMetric.metric_name == metric_name,
                )
            )
        session.add(DeviceMetric(
            device_id=device_id,
            metric_name=metric_name,
            metric_value=float(value),
            unit=units.get(metric_name),
            collected_at=now,
        ))


async def _get_ping_failure_count(session, device_id: int) -> int:
    """Read the persisted consecutive ping-failure count for a device."""
    res = await session.execute(
        select(AlertState).where(
            AlertState.device_id == device_id,
            AlertState.alert_type == _PING_FAILURE_STATE_KEY,
        )
    )
    state = res.scalar_one_or_none()
    return state.failure_count if state else 0


async def _set_ping_failure_count(session, device_id: int, count: int) -> None:
    """Upsert the consecutive ping-failure count for a device."""
    res = await session.execute(
        select(AlertState).where(
            AlertState.device_id == device_id,
            AlertState.alert_type == _PING_FAILURE_STATE_KEY,
        )
    )
    state = res.scalar_one_or_none()
    now = datetime.datetime.now(datetime.UTC)
    if state is None:
        session.add(AlertState(
            device_id=device_id,
            alert_type=_PING_FAILURE_STATE_KEY,
            failure_count=count,
            last_evaluated_at=now,
        ))
    else:
        state.failure_count = count
        state.last_evaluated_at = now


async def _send_ping_instability_email(
    device: Device, failures: int, threshold: int,
) -> bool:
    """
    Send an INFO email when a device recovered from N consecutive ping failures
    without ever reaching ping_down_threshold (no incident was opened).
    """
    settings = get_settings()
    recipients = settings.notification_email_list
    if not recipients:
        return False

    subject = f"[INFO] Instabilité ping — {device.name}"
    body_text = (
        f"Information : {device.name} ({device.ip_address}) a eu {failures} ping(s) "
        f"consécutif(s) raté(s) avant de redevenir joignable.\n\n"
        f"Aucun incident n'a été ouvert (seuil critique = {threshold} cycles).\n\n"
        f"À surveiller : possible dégradation latente du lien."
    )
    body_html = (
        f"<h3>Instabilité ping détectée</h3>"
        f"<p><strong>{device.name}</strong> ({device.ip_address}) a eu "
        f"<strong>{failures} ping(s) consécutif(s) raté(s)</strong> avant de redevenir "
        f"joignable.</p>"
        f"<p>Aucun incident ouvert (seuil critique = {threshold} cycles).</p>"
        f"<p><em>À surveiller : possible dégradation latente du lien.</em></p>"
    )
    return await email_service.send_email(recipients, subject, body_text, body_html)


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

async def _create_alert_record(
    session,
    incident: object,
    message: str,
    notif_success: bool,
) -> None:
    """Persist an Alert row recording the notification attempt."""
    now = datetime.datetime.now(datetime.UTC)
    alert = Alert(
        incident_id=incident.id,
        message=message,
        status="sent" if notif_success else "failed",
        sent_at=now if notif_success else None,
    )
    session.add(alert)


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
        ok = await notification_service.notify_incident_opened(device, incident)
        await _create_alert_record(session, incident, f"{title} — {device.name}", ok)


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
        ok = await notification_service.notify_incident_resolved(device, incident)
        await _create_alert_record(
            session, incident, f"RECOVERY: {title} — {device.name}", ok
        )


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
        ok = await notification_service.notify_incident_opened(lr, incident)
        await _create_alert_record(session, incident, f"{title}", ok)
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


async def device_ping_job() -> None:
    """
    Ping all registered devices via ICMP.
    Updates status/last_seen, opens or resolves device-down incidents.

    Anti-flapping: an incident is only opened after ping_down_threshold consecutive
    failures (default 3 = 90 s). A single successful ping resolves the incident.
    alert_type is device-specific (rocket_down / switch_down). LRs are the
    exception: a down LR is a subscriber-side outage (client power cut / LR
    unplugged), so it never raises an incident — only its status flips to down.
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    async with async_session_factory() as session:
        # Skip client_modem rows: they sit behind an LR's NAT and are NOT
        # reachable by ICMP from the supervisor — pinging them would trigger
        # constant device_unreachable incidents. Reachability is checked
        # on demand via the ping-from-LR diagnostic instead.
        result = await session.execute(
            select(Device).where(Device.device_type != "client_modem")
        )
        devices = list(result.scalars().all())

    if not devices:
        logger.debug("No devices registered — skipping ping poll")
        return

    logger.info("Ping poll — checking %d device(s)", len(devices))

    ping_results = await asyncio.gather(
        *[poller.ping_host(d.ip_address) for d in devices],
        return_exceptions=True,
    )

    now = datetime.datetime.now(datetime.UTC)

    # Single session for the entire loop — commits after each device so that
    # one device failure never rolls back another device's state changes.
    async with async_session_factory() as session:
        for device, result in zip(devices, ping_results, strict=True):
            if isinstance(result, Exception):
                reachable = False
            else:
                reachable, _ = result  # latency_ms discarded — supervisor-side latency unused

            dev = await session.get(Device, device.id)
            if dev is None:
                continue

            at = _alert_type_for_device(dev)

            if reachable:
                # Capture previous failure count to detect instability that
                # never reached the down threshold (info-only signal).
                prev_failures = await _get_ping_failure_count(session, dev.id)
                await _set_ping_failure_count(session, dev.id, 0)
                dev.status = "up"
                dev.last_seen = now

                # La latence ICMP superviseur ↔ device n'est plus exploitée :
                # seule la latence LR → Internet (lr_internet_probe_job) est
                # utile. On garde le ping en lui-même pour le UP/DOWN, mais on
                # ne persiste pas le RTT.

                recovery_title = f"RECOVERY : {dev.name} de nouveau disponible"
                await _resolve_and_notify(session, dev, recovery_title, alert_type=at)

                # Ping instability — recovered after N partial failures without
                # ever opening a *_down incident.
                if (
                    settings.ping_instability_threshold > 0
                    and prev_failures >= settings.ping_instability_threshold
                    and prev_failures < settings.ping_down_threshold
                ):
                    sent = await _send_ping_instability_email(
                        dev, prev_failures, settings.ping_down_threshold,
                    )
                    logger.info(
                        "PING INSTABILITY %s (%s) — %d échec(s) puis recovery, "
                        "email %s",
                        dev.name, dev.ip_address, prev_failures,
                        "envoyé" if sent else "non envoyé",
                    )

                logger.info("UP   %s (%s)", dev.name, dev.ip_address)
            else:
                dev.status = "down"
                failures = await _get_ping_failure_count(session, dev.id) + 1
                await _set_ping_failure_count(session, dev.id, failures)

                if failures >= settings.ping_down_threshold:
                    # Un LR qui ne répond plus = panne côté client (courant coupé
                    # chez l'abonné, LR débranché), pas une panne de notre infra.
                    # On ne lève donc AUCUN incident pour un LR down. Son
                    # indisponibilité reste visible via `status=down` dans
                    # /devices, mais ne génère ni incident ni notification.
                    # Une vraie panne de notre côté (Rocket/Switch) reste signalée
                    # par ses propres incidents rocket_down / switch_down.
                    if dev.rule_category == "lr":
                        # Un LR down = panne côté abonné. On purge en plus ses
                        # incidents ouverts (qualité radio, lien, transit…) :
                        # ils sont devenus du bruit obsolète puisque le LR ne
                        # répond plus. Aucun poller ne les recréera tant que le
                        # LR est down (les autres jobs ne ciblent que status=up).
                        purged = await incident_service.delete_open_incidents(session, dev.id)
                        logger.info(
                            "LR DOWN (côté client) %s (%s) — %d échecs, "
                            "%d incident(s) ouvert(s) purgé(s)",
                            dev.name, dev.ip_address, failures, purged,
                        )
                        await session.commit()
                        continue

                    context = await _build_switch_context(session, dev)
                    down_title = _down_title_for_device(dev)
                    await _open_and_notify(
                        session, dev,
                        title=down_title,
                        severity="critical",
                        description=(
                            f"{dev.name} ({dev.ip_address}) ne répond pas au ping ICMP"
                            f" ({failures} tentatives consécutives échouées).{context}"
                        ),
                        alert_type=at,
                    )
                    logger.warning(
                        "DOWN %s (%s) — %d/%d échecs consécutifs",
                        dev.name, dev.ip_address, failures, settings.ping_down_threshold,
                    )
                else:
                    logger.warning(
                        "PING KO %s (%s) — %d/%d (seuil non atteint)",
                        dev.name, dev.ip_address, failures, settings.ping_down_threshold,
                    )

            await session.commit()


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

    for device in devices:
        community = device.snmp_community or settings.snmp_default_community
        category = device.rule_category

        if category in AIRMAX_RULE_CATEGORIES:
            metrics = await snmp_service.collect_airmax_metrics(
                host=device.ip_address,
                community=community,
                port=settings.snmp_port,
                timeout=settings.snmp_timeout,
            )
        elif category in RADIO_RULE_CATEGORIES:
            metrics = await snmp_service.collect_ltu_metrics(
                host=device.ip_address,
                community=community,
                port=settings.snmp_port,
                timeout=settings.snmp_timeout,
            )
        elif category in SWITCH_RULE_CATEGORIES:
            metrics = await snmp_service.collect_switch_port_metrics(
                host=device.ip_address,
                community=community,
                port=settings.snmp_port,
                timeout=settings.snmp_timeout,
                max_ports=device.max_ports,
            )
        else:
            metrics = await snmp_service.collect_standard_metrics(
                host=device.ip_address,
                community=community,
                port=settings.snmp_port,
                timeout=settings.snmp_timeout,
            )

        if not any(v is not None for v in metrics.values()):
            logger.warning("SNMP no data — %s (%s)", device.name, device.ip_address)
            continue

        logger.info(
            "SNMP %s (%s) — %s",
            device.name,
            device.ip_address,
            " | ".join(f"{k}={v}" for k, v in metrics.items() if v is not None),
        )

        async with async_session_factory() as session:
            dev = await session.get(Device, device.id)
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

            # Auto-discovery for airMAX Rockets — walks the UBNT station table
            # via SNMP and feeds the same reconcile_peers() pipeline as LTU API.
            # Peers are persisted as Lr rows with model_variant="litebeam_5ac"
            # by default; the operator can override via PUT after creation.
            if category in AIRMAX_RULE_CATEGORIES:
                airmax_peers = await snmp_service.discover_airmax_peers(
                    host=device.ip_address,
                    community=community,
                    port=settings.snmp_port,
                    timeout=settings.snmp_timeout,
                )
                if airmax_peers:
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
                port_idx = device.rocket_port_index or 0
                min_speed = device.port_min_speed_mbps
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

    for device in devices:
        if not device.api_username or not device.api_password:
            logger.warning(
                "UISP Power skip — %s (%s) : credentials manquants en base "
                "(api_username/api_password). "
                "Configure-les via PUT /api/v1/devices/%d.",
                device.name, device.ip_address, device.id,
            )
            continue

        readings = await uisp_power_service.poll_uisp_power(
            host=device.ip_address,
            username=device.api_username,
            password=device.api_password,
            port=device.api_port or 443,
        )

        async with async_session_factory() as session:
            dev = await session.get(Device, device.id)
            if dev is None:
                continue

            if readings is None:
                await _open_and_notify(
                    session, dev, INC_POWER_UNREACH, "critical",
                    f"UISP Power device {dev.name} ({dev.ip_address}) is not responding to API.",
                    alert_type=AT_POWER_UNREACH,
                )
                logger.warning("UISP Power unreachable — %s (%s)", dev.name, dev.ip_address)
            else:
                await _resolve_and_notify(
                    session, dev, INC_POWER_UNREACH, alert_type=AT_POWER_UNREACH
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

                # Battery anomaly detection — runs on the canonical battery
                # (lowest-charge connected battery, see parse_power_readings).
                # The type is named in the message so the operator knows whether
                # it's the internal Li-Ion UPS or the external lead-acid bank.
                batt = readings.get("battery_percentage")
                batt_label = readings.get("battery_type") or "battery"
                if batt is not None:
                    if batt < settings.battery_critical_pct:
                        await _open_and_notify(
                            session, dev, INC_BATT_CRIT, "critical",
                            f"Battery critical ({batt_label}): {batt}% "
                            f"(threshold {settings.battery_critical_pct}%).",
                            alert_type=AT_BATT_CRIT,
                        )
                        await _resolve_and_notify(
                            session, dev, INC_BATT_WARN, alert_type=AT_BATT_WARN
                        )
                    elif batt < settings.battery_warning_pct:
                        await _open_and_notify(
                            session, dev, INC_BATT_WARN, "warning",
                            f"Battery low ({batt_label}): {batt}% "
                            f"(threshold {settings.battery_warning_pct}%).",
                            alert_type=AT_BATT_WARN,
                        )
                        await _resolve_and_notify(
                            session, dev, INC_BATT_CRIT, alert_type=AT_BATT_CRIT
                        )
                    else:
                        await _resolve_and_notify(
                            session, dev, INC_BATT_WARN, alert_type=AT_BATT_WARN
                        )
                        await _resolve_and_notify(
                            session, dev, INC_BATT_CRIT, alert_type=AT_BATT_CRIT
                        )

                # Voltage anomaly
                voltage = readings.get("voltage")
                if voltage is not None and (voltage < 20.0 or voltage > 56.0):
                    await _open_and_notify(
                        session, dev, INC_VOLT_ANOMALY, "critical",
                        f"Voltage out of range: {voltage:.1f}V.",
                        alert_type=AT_VOLT_ANOMALY,
                    )
                elif voltage is not None:
                    await _resolve_and_notify(
                        session, dev, INC_VOLT_ANOMALY, alert_type=AT_VOLT_ANOMALY
                    )

                # Mains (SOMELEC) presence — open mains_power_lost when on
                # battery, naming which battery is actually discharging.
                await _evaluate_mains_power(
                    session, dev, readings.get("ac_connected"),
                    readings.get("batteries") or [],
                )

            await session.commit()


async def ltu_api_poll_job() -> None:
    """
    Poll LTU Rocket via HTTP API (signal, CCQ, CINR, rates, CPE info).
    Delegates radio quality anomaly detection to the alert engine.
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
        devices = list(result.scalars().all())

    if not devices:
        logger.debug("LTU API poll — no eligible devices")
        return

    logger.info("LTU API poll — checking %d device(s)", len(devices))

    unit_map = ltu_api_service.METRIC_UNITS

    for device in devices:
        if not device.ssh_username or not device.ssh_password:
            logger.warning(
                "LTU API skip — %s (%s) : credentials manquants en base "
                "(ssh_username/ssh_password). Configure-les via PUT /api/v1/devices/%d.",
                device.name, device.ip_address, device.id,
            )
            continue

        rocket_ap_metrics, all_peers, per_peer_metrics = await ltu_api_service.collect_ltu_api_full(
            host=device.ip_address,
            username=device.ssh_username,
            password=device.ssh_password,
            port=443,  # LTU HTTP API requires HTTPS — firmware forces TLS
        )

        if rocket_ap_metrics is None:
            logger.debug("LTU API no response — %s (%s)", device.name, device.ip_address)
            continue

        async with async_session_factory() as session:
            dev = await session.get(Device, device.id)
            if dev is None:
                continue

            # Auto-discovery: create / update / link LR devices from the Rocket's peer list.
            # MAC-based identity → IP changes and Rocket reassignments are detected reliably.
            recon = await discovery_service.reconcile_peers(session, dev, all_peers)
            if recon.created or recon.ip_changed or recon.reassigned:
                logger.info(
                    "Discovery — Rocket '%s' : %d nouveau(x), %d IP changée(s), %d rebascule(s)",
                    dev.name, len(recon.created), len(recon.ip_changed), len(recon.reassigned),
                )

            # Persist AP-wide metrics on the Rocket (noise_dbm). Per-link
            # metrics (signal/CCQ/CINR/etc.) belong to each LR and are stored
            # in the fan-out loop below.
            await persist_device_metrics(session, dev.id, rocket_ap_metrics, unit_map)

            # Rocket-level alert engine pass: only cares about peer_count and
            # whatever the SNMP IF-MIB poll added (radio_if_up, eth_if_up,
            # byte/error counters via _inject_error_deltas inside the engine).
            rocket_engine_metrics = dict(rocket_ap_metrics)
            rocket_engine_metrics["peer_count"] = len(all_peers)
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


async def airos_api_poll_job() -> None:
    """
    Poll airMAX LR (LiteBeam) devices via their own airOS HTTP API (status.cgi).
    Replaces SNMP for these LRs: the UBNT MIB lacks Link Potential / Total
    Capacity / rate index, which the airOS dashboard (and status.cgi) expose.
    Each LiteBeam is queried directly at its own IP; metrics use the same keys
    as the LTU API so the alert engine / modal work unchanged.
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Lr).where(
                Lr.status == "up",
                Lr.model_variant.in_(AIRMAX_LR_VARIANTS),
            )
        )
        devices = list(result.scalars().all())

    if not devices:
        logger.debug("airOS API poll — no eligible devices")
        return

    logger.info("airOS API poll — checking %d device(s)", len(devices))

    # airOS HTTP metric keys reuse the LTU units; add the few SNMP-style keys
    # this poll also fills (byte counters, uptime).
    unit_map = {
        **ltu_api_service.METRIC_UNITS,
        "radio_rx_bytes": "B",
        "radio_tx_bytes": "B",
        "uptime_seconds": "s",
    }

    for device in devices:
        if not device.ssh_username or not device.ssh_password:
            logger.warning(
                "airOS API skip — %s (%s) : credentials manquants en base "
                "(ssh_username/ssh_password). Configure-les via PUT /api/v1/devices/%d.",
                device.name, device.ip_address, device.id,
            )
            continue

        collected = await airos_api_service.collect_airos_link_metrics(
            host=device.ip_address,
            username=device.ssh_username,
            password=device.ssh_password,
            port=443,  # airOS HTTP API requires HTTPS
        )
        if collected is None:
            logger.debug("airOS API no response — %s (%s)", device.name, device.ip_address)
            continue
        metrics, hostname, netrole = collected

        if not any(v is not None for v in metrics.values()):
            logger.warning("airOS API no data — %s (%s)", device.name, device.ip_address)
            continue

        logger.info(
            "airOS %s (%s) — %s",
            device.name,
            device.ip_address,
            " | ".join(f"{k}={v}" for k, v in metrics.items() if v is not None),
        )

        async with async_session_factory() as session:
            dev = await session.get(Device, device.id)
            if dev is None:
                continue

            # Sync the stable distance_m column for quick UI display.
            distance = metrics.get("distance_m")
            if distance is not None:
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
                        dev.name, device.ip_address, hostname,
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
            # both families now read mode from their HTTP poll).
            await _apply_lr_topology(session, dev, netrole, "netrole via airOS status.cgi")

            await session.commit()


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
        # gaspille un timeout SSH (~10–30 s) par LR mort, en série. Un LR down
        # ne lève aucun incident (panne côté client) ; on reprend la sonde dès
        # qu'il remonte (status repassé à up).
        result = await session.execute(
            select(Lr).where(
                Lr.ssh_username.is_not(None),
                Lr.ssh_password.is_not(None),
                Lr.status == "up",
            )
        )
        lrs = list(result.scalars().all())

    if not lrs:
        logger.debug("lr_internet_probe: aucun LR up avec credentials SSH — ignoré")
        return

    logger.info("lr_internet_probe — sonde sur %d LR(s)", len(lrs))

    async with async_session_factory() as session:
        for lr in lrs:
            dev = await session.get(Lr, lr.id)
            if dev is None:
                continue

            primary_pw = dev.ssh_password
            ssh_ok, ping_ok, avg_rtt, msg, observed_fp, used_pw = (
                await ssh_service.measure_latency_via_ssh(
                    host=dev.ip_address,
                    port=dev.ssh_port or 22,
                    username=dev.ssh_username,
                    password=primary_pw,
                    target=settings.lr_latency_target,
                    count=settings.lr_latency_ping_count,
                    expected_fingerprint=dev.ssh_host_fingerprint,
                    fallback_passwords=settings.lr_fallback_password_list,
                )
            )

            if observed_fp and dev.ssh_host_fingerprint != observed_fp:
                dev.ssh_host_fingerprint = observed_fp
            if used_pw and used_pw != primary_pw:
                logger.info(
                    "lr_internet_probe: LR '%s' (%s) — fallback password "
                    "succeeded → promoting on LR.",
                    dev.name, dev.ip_address,
                )
                dev.ssh_password = used_pw

            if not ssh_ok:
                # Géré par device_ping_job (le LR est down). Pas d'alerte ici.
                logger.debug(
                    "lr_internet_probe: %s (%s) SSH KO — %s (skip, géré par device_ping_job)",
                    dev.name, dev.ip_address, msg,
                )
                await session.commit()
                continue

            # SSH OK — d'abord évaluer le transit (binary OK/KO).
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


async def lr_health_matview_refresh_job() -> None:
    """Refresh the `lr_health_metric_stats_30d` materialized view.

    Uses REFRESH MATERIALIZED VIEW CONCURRENTLY so readers of /lr-health are
    never blocked — Postgres computes the new state into a staging table
    then swaps atomically. Requires the UNIQUE index on (device_id,
    metric_name) created by migration o6a7b8c9d0e1.

    REFRESH CONCURRENTLY must run outside an explicit transaction → we use
    AUTOCOMMIT on the engine connection.

    Cost: ~5 s of background CPU/IO every 15 min (≈ 0.5 % of one CPU).
    Side effect: keeps the /lr-health/bad-installations endpoint <100 ms
    instead of ~4 s seq-scan on device_metrics (16 M+ rows in prod).
    """
    from sqlalchemy import text

    from app.db.session import engine

    started = datetime.datetime.now(datetime.UTC)
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text("REFRESH MATERIALIZED VIEW CONCURRENTLY lr_health_metric_stats_30d")
            )
        elapsed = (datetime.datetime.now(datetime.UTC) - started).total_seconds()
        logger.info("lr_health matview refresh — done in %.2f s", elapsed)
        if elapsed > 60:
            # Refresh getting slow → device_metrics is outgrowing the
            # view's scan budget. Time to think about retention.
            logger.warning(
                "lr_health matview refresh took %.1f s (> 60 s) — "
                "device_metrics is large, plan retention.",
                elapsed,
            )
    except Exception:
        logger.exception(
            "lr_health matview refresh failed — page will keep serving "
            "the previous snapshot until next attempt",
        )


async def client_consumption_matview_refresh_job() -> None:
    """Refresh the `client_consumption_30d` materialized view.

    Same pattern as `lr_health_matview_refresh_job` — see that docstring
    for the AUTOCOMMIT rationale. Pre-computes the 30-day byte-delta
    aggregate used by /clients/consumption?period=30d (was ~36 s of seq
    scan + Python delta loop in prod 2026-06-02, target <100 ms after).

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


async def device_metrics_retention_job() -> None:
    """Purge device_metrics rows older than the retention window.

    Only HISTORY_METRICS still accumulate rows (everything else is collapsed
    to one latest row by persist_device_metrics), so in practice this prunes
    the byte-counter and radio-quality series feeding the 30-day matviews and
    the report. 90 days (default) covers those with margin.

    The delete is BATCHED (delete a bounded set of ids per statement, loop
    until none remain) so it never holds one giant transaction over the table
    — a bulk delete on device_metrics (16 M+ rows in prod) once stalled the
    backend healthcheck when run on the startup path. Here it runs in the
    scheduler, in small committed chunks.
    """
    from sqlalchemy import text

    from app.db.session import engine

    settings = get_settings()
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        days=settings.device_metrics_retention_days
    )
    batch = settings.device_metrics_retention_batch_size

    # Ensure the index that makes the purge's `collected_at < cutoff` scan an
    # index range (not a 16 M-row seq scan every run). Built CONCURRENTLY here
    # in the scheduler — NOT in a startup migration — because a non-concurrent
    # build locks writes and a concurrent build in a migration would stall the
    # backend healthcheck (same lesson as u2a3b4c5d6e7's switch backlog). IF
    # NOT EXISTS makes it a cheap no-op after the first build. CONCURRENTLY
    # cannot run inside a transaction → AUTOCOMMIT connection.
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(
                    "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                    "ix_device_metrics_collected_at ON device_metrics (collected_at)"
                )
            )
    except Exception:
        # A failed CONCURRENTLY build can leave an INVALID index; the purge
        # still works (seq scan), just slower. Don't abort the purge.
        logger.exception("device_metrics retention — collected_at index ensure failed")

    started = datetime.datetime.now(datetime.UTC)
    total = 0
    try:
        async with async_session_factory() as session:
            while True:
                # DELETE ... WHERE id IN (SELECT id ... LIMIT n) — Postgres has
                # no DELETE ... LIMIT, so we bound each pass via a subquery on
                # the PK. ix on (collected_at) keeps the lookup cheap.
                result = await session.execute(
                    text(
                        "DELETE FROM device_metrics WHERE id IN ("
                        "  SELECT id FROM device_metrics"
                        "  WHERE collected_at < :cutoff"
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
        elapsed = (datetime.datetime.now(datetime.UTC) - started).total_seconds()
        if total:
            logger.info(
                "device_metrics retention — purged %d rows older than %d days in %.1f s",
                total, settings.device_metrics_retention_days, elapsed,
            )
        else:
            logger.debug("device_metrics retention — nothing to purge")
    except Exception:
        logger.exception("device_metrics retention job failed")


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
# Registration
# ---------------------------------------------------------------------------

def register_jobs(scheduler: AsyncIOScheduler) -> None:
    """Register all scheduled jobs. Intervals are read from settings.

    Safety params on every job:
      - max_instances=1     : never run a second copy if the previous one is still running
      - coalesce=True       : if the scheduler missed several runs, only fire once
      - misfire_grace_time  : ignore runs older than this when catching up
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
        device_ping_job,
        trigger="interval", seconds=settings.ping_interval_seconds,
        id="device_ping", name="Device ping poll",
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
        lr_health_matview_refresh_job,
        trigger="interval",
        minutes=settings.lr_health_matview_refresh_interval_minutes,
        id="lr_health_matview_refresh",
        name="LR health matview refresh (30-day aggregate)",
        replace_existing=True,
        # Override misfire_grace_time: a missed refresh isn't urgent — readers
        # just see slightly older data until the next cycle.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
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
        device_metrics_retention_job,
        trigger="interval",
        minutes=settings.device_metrics_retention_interval_minutes,
        id="device_metrics_retention",
        name="device_metrics retention (purge history older than N days)",
        replace_existing=True,
        # A missed purge isn't urgent — rows just live a little longer.
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
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



