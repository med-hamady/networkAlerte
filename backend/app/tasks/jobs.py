"""
Scheduled supervision jobs.

- heartbeat_job     : sanity check every 60s
- device_ping_job   : ICMP ping all devices every 30s → opens/resolves incidents
- snmp_poll_job     : SNMP metrics for LTU/airMAX devices every 60s → alert engine
- power_poll_job    : UISP Power API polling every 30s → power anomaly detection
- ltu_api_poll_job  : LTU HTTP API polling every 60s → alert engine (radio quality)
- transit_probe_job : Transit connectivity probe every 60s
"""

import asyncio
import datetime
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from app.core.alert_constants import (
    AT_AIRMAX_DOWN,
    AT_LR_DISAPPEARED,
    AT_LR_DOWN,
    AT_LR_NO_TRANSIT,
    AT_PING_LATENCY_HIGH,
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
from app.models.device import Device, Lr, Rocket, UispPower
from app.models.device_metric import DeviceMetric
from app.models.power_status_log import PowerStatusLog
from app.services import (
    alert_engine,
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
_PING_LATENCY_FAILURE_STATE_KEY = "_ping_latency_failures"


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


async def _get_latency_failure_count(session, device_id: int) -> int:
    """Read the persisted consecutive high-latency cycle count."""
    res = await session.execute(
        select(AlertState).where(
            AlertState.device_id == device_id,
            AlertState.alert_type == _PING_LATENCY_FAILURE_STATE_KEY,
        )
    )
    state = res.scalar_one_or_none()
    return state.failure_count if state else 0


async def _set_latency_failure_count(session, device_id: int, count: int) -> None:
    """Upsert the persisted consecutive high-latency cycle count."""
    res = await session.execute(
        select(AlertState).where(
            AlertState.device_id == device_id,
            AlertState.alert_type == _PING_LATENCY_FAILURE_STATE_KEY,
        )
    )
    state = res.scalar_one_or_none()
    now = datetime.datetime.now(datetime.UTC)
    if state is None:
        session.add(AlertState(
            device_id=device_id,
            alert_type=_PING_LATENCY_FAILURE_STATE_KEY,
            failure_count=count,
            last_evaluated_at=now,
        ))
    else:
        state.failure_count = count
        state.last_evaluated_at = now


async def _send_ping_instability_email(
    device: Device, failures: int, threshold: int, latency_ms: float | None
) -> bool:
    """
    Send an INFO email when a device recovered from N consecutive ping failures
    without ever reaching ping_down_threshold (no incident was opened).
    """
    settings = get_settings()
    recipients = settings.notification_email_list
    if not recipients:
        return False

    latency_str = (
        f"{latency_ms:.1f} ms" if latency_ms is not None else "non mesurée"
    )
    subject = f"[INFO] Instabilité ping — {device.name}"
    body_text = (
        f"Information : {device.name} ({device.ip_address}) a eu {failures} ping(s) "
        f"consécutif(s) raté(s) avant de redevenir joignable.\n\n"
        f"Aucun incident n'a été ouvert (seuil critique = {threshold} cycles).\n"
        f"Latence du ping de récupération : {latency_str}.\n\n"
        f"À surveiller : possible dégradation latente du lien."
    )
    body_html = (
        f"<h3>Instabilité ping détectée</h3>"
        f"<p><strong>{device.name}</strong> ({device.ip_address}) a eu "
        f"<strong>{failures} ping(s) consécutif(s) raté(s)</strong> avant de redevenir "
        f"joignable.</p>"
        f"<p>Aucun incident ouvert (seuil critique = {threshold} cycles).<br>"
        f"Latence du ping de récupération : <strong>{latency_str}</strong>.</p>"
        f"<p><em>À surveiller : possible dégradation latente du lien.</em></p>"
    )
    return await email_service.send_email(recipients, subject, body_text, body_html)


async def _send_ping_latency_email(
    device: Device,
    latency_ms: float,
    threshold_ms: float,
    severity: str,
    cycles: int,
    event: str,
) -> bool:
    """
    Send an email for a high-latency event (opened or resolved).
    `event` is "opened" or "resolved".
    """
    settings = get_settings()
    recipients = settings.notification_email_list
    if not recipients:
        return False

    if event == "resolved":
        subject = f"[RECOVERY] Latence redevenue normale — {device.name}"
        body_text = (
            f"La latence vers {device.name} ({device.ip_address}) est revenue sous le seuil "
            f"({latency_ms:.1f} ms)."
        )
        body_html = (
            f"<h3>Latence normale</h3>"
            f"<p>La latence vers <strong>{device.name}</strong> ({device.ip_address}) "
            f"est revenue sous le seuil : <strong>{latency_ms:.1f} ms</strong>.</p>"
        )
    else:
        tag = "ALERTE CRITIQUE" if severity == "critical" else "ALERTE"
        subject = f"[{tag}] Latence élevée — {device.name}"
        body_text = (
            f"{tag} — Latence élevée vers {device.name} ({device.ip_address}).\n"
            f"Latence mesurée : {latency_ms:.1f} ms (seuil {severity} : {threshold_ms:.0f} ms).\n"
            f"Cycles consécutifs au-dessus du seuil : {cycles}."
        )
        body_html = (
            f"<h3>Latence élevée — {severity.upper()}</h3>"
            f"<p><strong>{device.name}</strong> ({device.ip_address}) — "
            f"latence mesurée : <strong>{latency_ms:.1f} ms</strong> "
            f"(seuil {severity} : {threshold_ms:.0f} ms).</p>"
            f"<p>Cycles consécutifs au-dessus du seuil : <strong>{cycles}</strong>.</p>"
        )
    return await email_service.send_email(recipients, subject, body_text, body_html)


# rule_category buckets used to pick SNMP poll variants.
# LTU Rockets → standard IF-MIB. LRs are NOT polled via SNMP — their metrics
# come from the parent Rocket's HTTP API (peer fan-out in ltu_api_poll_job).
RADIO_RULE_CATEGORIES = {"ltu_rocket"}

# airMAX Rockets → UBNT Enterprise MIB + IF-MIB.
AIRMAX_RULE_CATEGORIES = {"airmax_rocket"}

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
        "lr":            AT_LR_DOWN,
        "uisp_switch":   AT_SWITCH_DOWN,
        "airmax_rocket": AT_AIRMAX_DOWN,
    }
    return mapping.get(device.rule_category, AT_UNREACHABLE)


def _down_title_for_device(device: Device) -> str:
    mapping = {
        "ltu_rocket":    "ALERTE CRITIQUE : LTU Rocket indisponible",
        "lr":            "ALERTE CRITIQUE : LTU LR indisponible",
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


async def _evaluate_ping_latency(
    session, device: Device, latency_ms: float, settings,
) -> None:
    """
    Apply latency thresholds: increment counter when above warn, open incident
    after `ping_latency_failure_threshold` consecutive bad cycles, resolve
    when latency drops back. Severity = critical above crit threshold.
    Notifications are sent by email regardless of policy.
    """
    warn = settings.ping_latency_warn_ms
    crit = settings.ping_latency_crit_ms

    if latency_ms < warn:
        # Latency back to normal — reset counter and resolve incident if any.
        prev = await _get_latency_failure_count(session, device.id)
        if prev > 0:
            await _set_latency_failure_count(session, device.id, 0)
        resolved = await incident_service.resolve_incidents(
            session, device.id,
            title=f"Latence élevée — {device.name}",
            alert_type=AT_PING_LATENCY_HIGH,
        )
        for inc in resolved:
            ok = await _send_ping_latency_email(
                device, latency_ms, warn, "warning", 0, "resolved",
            )
            await _create_alert_record(
                session, inc, f"RECOVERY: latence normale — {device.name}", ok,
            )
        return

    # Above warn threshold — bump counter.
    cycles = await _get_latency_failure_count(session, device.id) + 1
    await _set_latency_failure_count(session, device.id, cycles)

    if cycles < settings.ping_latency_failure_threshold:
        logger.info(
            "PING LATENCY HIGH %s (%s) — %.1f ms (%d/%d cycles, seuil non atteint)",
            device.name, device.ip_address, latency_ms,
            cycles, settings.ping_latency_failure_threshold,
        )
        return

    severity = "critical" if latency_ms >= crit else "warning"
    threshold_used = crit if severity == "critical" else warn
    title = f"Latence élevée — {device.name}"
    description = (
        f"{device.name} ({device.ip_address}) — latence ICMP : {latency_ms:.1f} ms "
        f"(seuil {severity} : {threshold_used:.0f} ms). "
        f"{cycles} cycles consécutifs au-dessus du seuil warning."
    )
    incident, is_new = await incident_service.open_incident(
        session, device, title, severity, description,
        alert_type=AT_PING_LATENCY_HIGH,
        metric_name="ping_latency_ms",
        metric_value=latency_ms,
        threshold_value=threshold_used,
    )
    if is_new:
        ok = await _send_ping_latency_email(
            device, latency_ms, threshold_used, severity, cycles, "opened",
        )
        await _create_alert_record(
            session, incident, f"{title} — {severity}", ok,
        )
        logger.warning(
            "PING LATENCY HIGH %s (%s) — incident %s ouvert (%.1f ms)",
            device.name, device.ip_address, severity, latency_ms,
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
    alert_type is device-specific (rocket_down / lr_down / switch_down).
    """
    base_settings = get_settings()
    async with async_session_factory() as _ts_session:
        settings = await threshold_service.get_effective_settings(_ts_session, base_settings)

    async with async_session_factory() as session:
        # Skip client_modem rows: they sit behind an LR's NAT and are NOT
        # reachable by ICMP from the supervisor — pinging them would trigger
        # constant device_unreachable incidents. Their reachability is
        # implicitly checked when the operator opens a shell session.
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
                reachable, latency_ms = False, None
            else:
                reachable, latency_ms = result

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

                if latency_ms is not None:
                    session.add(DeviceMetric(
                        device_id=dev.id,
                        metric_name="ping_latency_ms",
                        metric_value=latency_ms,
                        unit="ms",
                    ))

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
                        dev, prev_failures, settings.ping_down_threshold, latency_ms,
                    )
                    logger.info(
                        "PING INSTABILITY %s (%s) — %d échec(s) puis recovery, "
                        "email %s",
                        dev.name, dev.ip_address, prev_failures,
                        "envoyé" if sent else "non envoyé",
                    )

                # Latency thresholds — anti-flap N consecutive cycles.
                if latency_ms is not None:
                    await _evaluate_ping_latency(
                        session, dev, latency_ms, settings,
                    )

                logger.info(
                    "UP   %s (%s)%s",
                    dev.name, dev.ip_address,
                    f" — {latency_ms:.1f} ms" if latency_ms is not None else "",
                )
            else:
                dev.status = "down"
                failures = await _get_ping_failure_count(session, dev.id) + 1
                await _set_ping_failure_count(session, dev.id, failures)

                if failures >= settings.ping_down_threshold:
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

        # After all devices are processed, run a global correlation pass so that
        # device-down incidents (which bypass alert_engine) get probable_cause set.
        await alert_engine.run_correlation_pass(session)
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
        # subtype columns. LRs are excluded: they are not directly reachable
        # for SNMP (they sit behind the radio link) — their metrics come from
        # the parent Rocket's HTTP API poll fan-out.
        result = await session.execute(
            select(Device).where(
                Device.status == "up",
                Device.snmp_community.is_not(None),
                Device.device_type.in_(("rocket", "uisp_switch")),
            )
        )
        devices = list(result.scalars().all())

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
            for metric_name, value in metrics.items():
                if value is not None:
                    session.add(DeviceMetric(
                        device_id=dev.id,
                        metric_name=metric_name,
                        metric_value=value,
                        unit=unit_map.get(metric_name),
                    ))

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
                metric_units = {
                    "voltage_v": ("voltage", "V"),
                    "current_a": ("current", "A"),
                    "power_w":   ("power",   "W"),
                    "battery_pct":       ("battery_percentage", "%"),
                    "battery_voltage_v": ("battery_voltage",    "V"),
                }
                for metric_name, (key, unit) in metric_units.items():
                    val = readings.get(key)
                    if val is None:
                        continue
                    session.add(DeviceMetric(
                        device_id=dev.id,
                        metric_name=metric_name,
                        metric_value=float(val),
                        unit=unit,
                    ))

                logger.info(
                    "UISP Power %s — voltage=%.1fV current=%.2fA power=%.1fW",
                    dev.name,
                    readings.get("voltage") or 0,
                    readings.get("current") or 0,
                    readings.get("power") or 0,
                )

                # Battery anomaly detection
                batt = readings.get("battery_percentage")
                if batt is not None:
                    if batt < settings.battery_critical_pct:
                        await _open_and_notify(
                            session, dev, INC_BATT_CRIT, "critical",
                            f"Battery critical: {batt}% (threshold {settings.battery_critical_pct}%).",
                            alert_type=AT_BATT_CRIT,
                        )
                        await _resolve_and_notify(
                            session, dev, INC_BATT_WARN, alert_type=AT_BATT_WARN
                        )
                    elif batt < settings.battery_warning_pct:
                        await _open_and_notify(
                            session, dev, INC_BATT_WARN, "warning",
                            f"Battery low: {batt}% (threshold {settings.battery_warning_pct}%).",
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

    unit_map = {
        "signal_dbm":        "dBm",
        "noise_dbm":         "dBm",
        "ccq_pct":           "%",
        "ul_ccq_pct":        "%",
        "cinr_db":           "dB",
        "ul_cinr_db":        "dB",
        "tx_rate_mbps":      "Mbps",
        "rx_rate_mbps":      "Mbps",
        "tx_ideal_mbps":     "Mbps",
        "rx_ideal_mbps":     "Mbps",
        "remote_signal_dbm": "dBm",
        "remote_noise_dbm":  "dBm",
        "remote_eirp_dbm":   "dBm",
        "distance_m":        "m",
        "peer_uptime_s":     "s",
        "peer_cpu_pct":      "%",
        "peer_ram_pct":      "%",
        "peer_tx_kbps":      "Kbps",
        "peer_rx_kbps":      "Kbps",
    }

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
            for metric_name, value in rocket_ap_metrics.items():
                if value is not None:
                    session.add(DeviceMetric(
                        device_id=dev.id,
                        metric_name=metric_name,
                        metric_value=value,
                        unit=unit_map.get(metric_name),
                    ))

            # Rocket-level alert engine pass: only cares about peer_count and
            # whatever the SNMP IF-MIB poll added (radio_if_up, eth_if_up,
            # byte/error counters via _inject_error_deltas inside the engine).
            rocket_engine_metrics = dict(rocket_ap_metrics)
            rocket_engine_metrics["peer_count"] = len(all_peers)
            await alert_engine.evaluate_device_metrics(
                session, dev, rocket_engine_metrics, settings,
            )

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
                for metric_name, value in peer_metrics.items():
                    if value is not None:
                        session.add(DeviceMetric(
                            device_id=lr.id,
                            metric_name=metric_name,
                            metric_value=value,
                            unit=unit_map.get(metric_name),
                        ))
                # Per-LR alert engine pass — radio-quality rules evaluate
                # against this LR's metrics, not the Rocket's peer[0].
                await alert_engine.evaluate_device_metrics(
                    session, lr, dict(peer_metrics), settings,
                )

            await session.commit()


async def lr_transit_probe_job() -> None:
    """
    Vérifie la connectivité internet depuis le LTU LR lui-même via SSH.

    Flux décisionnel
    ----------------
    1. Trouve le device LTU LR en base de données.
    2. Tente une connexion SSH sur son IP locale (ex. 10.135.x.x).
       - Échec SSH → l'équipement est probablement éteint ou non joignable
         sur le réseau local → on ne lève pas d'alerte transit (le job de
         ping gère déjà la disponibilité de l'équipement).
    3. SSH OK → exécute "ping -c 2 -W 3 <cible>" depuis le LTU LR.
       - Au moins une cible répond → transit OK → résoudre l'incident.
       - Aucune cible ne répond → incrémenter le compteur (AlertState) →
         ouvrir un incident critique après N cycles consécutifs.

    Le compteur de cycles est persisté en base (AlertState) et survit
    aux redémarrages du container.
    """
    settings = get_settings()
    probe_ips = [ip.strip() for ip in settings.transit_probe_ips.split(",") if ip.strip()]
    if not probe_ips:
        logger.debug("lr_transit_probe: TRANSIT_PROBE_IPS vide — job ignoré")
        return

    async with async_session_factory() as session:
        # ── 1. Trouver le LTU LR ─────────────────────────────────────────────
        lr_res = await session.execute(select(Lr))
        lr = lr_res.scalars().first()

        if lr is None:
            logger.debug("lr_transit_probe: aucun LTU LR enregistré — ignoré")
            return

        if not lr.ssh_username or not lr.ssh_password:
            logger.warning(
                "lr_transit_probe skip — LTU LR '%s' (id=%d) : credentials SSH "
                "manquants en base. Configure-les via PUT /api/v1/devices/%d.",
                lr.name, lr.id, lr.id,
            )
            return

        # ── 2. Vérifier l'accès SSH sur le réseau local ──────────────────────
        ssh_ok, ssh_msg, observed_fp = await ssh_service.check_ssh_access(
            lr.ip_address,
            lr.ssh_port,
            lr.ssh_username,
            lr.ssh_password,
            expected_fingerprint=lr.ssh_host_fingerprint,
        )

        if ssh_ok and observed_fp and lr.ssh_host_fingerprint != observed_fp:
            # First-seen fingerprint — pin it so subsequent connects detect MITM.
            lr.ssh_host_fingerprint = observed_fp

        if not ssh_ok:
            # L'équipement ne répond pas en SSH sur le réseau local.
            # Il est peut-être éteint ou pas encore démarré.
            # On ne lève pas d'alerte transit — le ping job s'en charge.
            logger.info(
                "lr_transit_probe: SSH %s:%d impossible (%s) — "
                "équipement probablement hors-ligne, alerte transit ignorée",
                lr.ip_address, lr.ssh_port, ssh_msg,
            )
            await session.commit()
            return

        # ── 3. Ping internet depuis le LTU LR ────────────────────────────────
        ping_ok, ping_detail, _ = await ssh_service.ping_targets_via_ssh(
            lr.ip_address,
            lr.ssh_port,
            lr.ssh_username,
            lr.ssh_password,
            probe_ips,
            expected_fingerprint=lr.ssh_host_fingerprint,
        )

        # ── 4. Récupérer / créer l'AlertState pour le compteur anti-flap ─────
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

        now = datetime.datetime.now(datetime.UTC)
        state.last_evaluated_at = now

        # ── 5. Traiter le résultat ────────────────────────────────────────────
        if ping_ok:
            # Transit OK — réinitialiser et résoudre
            state.failure_count = 0
            await _resolve_and_notify(
                session, lr,
                "RECOVERY : LTU LR de nouveau joignable depuis internet via le lien radio",
                alert_type=AT_LR_NO_TRANSIT,
            )
            logger.info(
                "lr_transit_probe: OK — %s atteint depuis %s via SSH",
                ping_detail, lr.ip_address,
            )
        else:
            # Transit KO — incrémenter le compteur
            state.failure_count += 1
            count = state.failure_count

            if count >= settings.transit_probe_threshold:
                title = "ALERTE CRITIQUE : LTU LR sans transit internet via le lien radio"
                desc = (
                    f"SSH vers {lr.ip_address} : OK — l'équipement est joignable sur le "
                    f"réseau local.\n"
                    f"Ping vers {probe_ips} depuis {lr.name} : ÉCHOUE "
                    f"({count} cycles consécutifs).\n"
                    f"Le LTU LR est allumé mais ne peut pas joindre internet — "
                    f"problème probable sur le lien radio ou la configuration de routage."
                )
                await _open_and_notify(
                    session, lr, title, "critical", desc, alert_type=AT_LR_NO_TRANSIT,
                )
                logger.warning(
                    "lr_transit_probe: KO — %s SSH OK, ping %s ÉCHOUE (%d/%d cycles)",
                    lr.ip_address, probe_ips, count, settings.transit_probe_threshold,
                )
            else:
                logger.warning(
                    "lr_transit_probe: KO (%d/%d) — seuil non atteint",
                    count, settings.transit_probe_threshold,
                )

        await session.commit()


async def stale_lr_detection_job() -> None:
    """
    Detect auto-discovered LRs that have stopped appearing in Rocket peer-lists.

    An auto-discovered LR is reconciled (last_discovered_at refreshed) every
    time its parent Rocket's API poll succeeds and reports it as a peer. If
    `last_discovered_at` is older than `stale_lr_minutes`, the LR is either:
      - powered off / disconnected from the radio, or
      - rebooted with a different MAC, or
      - genuinely retired from the network.

    We open AT_LR_DISAPPEARED so the operator investigates. The incident is
    automatically resolved on the next cycle if the LR reappears in any peer
    list (last_discovered_at gets refreshed by reconcile_peers, the threshold
    no longer fires).
    """
    settings = get_settings()
    threshold = datetime.timedelta(minutes=settings.stale_lr_minutes)
    now = datetime.datetime.now(datetime.UTC)
    cutoff = now - threshold

    async with async_session_factory() as session:
        result = await session.execute(
            select(Lr).where(Lr.auto_discovered.is_(True))
        )
        lrs = list(result.scalars().all())

        if not lrs:
            logger.debug("Stale-LR job: no auto-discovered LRs registered")
            return

        for lr in lrs:
            # An LR that never appeared in a peer list (last_discovered_at NULL)
            # is treated as fresh — we wait until it has been discovered at least
            # once before tracking staleness.
            if lr.last_discovered_at is None:
                continue

            if lr.last_discovered_at < cutoff:
                age_minutes = int((now - lr.last_discovered_at).total_seconds() // 60)
                title = f"LR disparu de la liste des peers : {lr.name}"
                description = (
                    f"Le LR auto-découvert '{lr.name}' (MAC={lr.mac_address or 'inconnue'}, "
                    f"IP={lr.ip_address}) n'apparaît plus dans la liste des peers d'aucun "
                    f"Rocket depuis {age_minutes} minutes (seuil : {settings.stale_lr_minutes} min). "
                    f"Vérifier alimentation et lien radio. Comparer avec les incidents "
                    f"lr_down / cpe_disconnected sur ce device et son parent."
                )
                await _open_and_notify(
                    session, lr, title, "warning", description,
                    alert_type=AT_LR_DISAPPEARED,
                )
            else:
                # Recently seen — clear any stale incident
                await _resolve_and_notify(
                    session, lr,
                    f"RECOVERY : {lr.name} de nouveau rapporté par un Rocket",
                    alert_type=AT_LR_DISAPPEARED,
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
    if settings.transit_probe_enabled:
        scheduler.add_job(
            lr_transit_probe_job,
            trigger="interval", seconds=settings.transit_probe_interval,
            id="transit_probe", name="LR SSH transit probe",
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
        warning_digest_job,
        trigger="interval", minutes=settings.warning_digest_minutes,
        id="warning_digest", name="Warning digest flush",
        replace_existing=True,
        **safety,
    )
    scheduler.add_job(
        stale_lr_detection_job,
        trigger="interval", minutes=settings.stale_lr_check_interval_minutes,
        id="stale_lr_detection", name="Stale LR detection",
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



