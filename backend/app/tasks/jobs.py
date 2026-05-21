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
import re

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from app.core.alert_constants import (
    AT_AIRMAX_DOWN,
    AT_LR_BRIDGE_MODE_MISCONFIG,
    AT_LR_DISAPPEARED,
    AT_LR_DOWN,
    AT_LR_LATENCY_HIGH,
    AT_LR_NO_TRANSIT,
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

# airMAX LRs (LiteBeam family) → polled directly via SNMP. Their parent
# Rocket airMAX exposes peer identification only (ubntStaTable), not the
# per-peer signal/CCQ/rates that LTU Rockets fan out via HTTP — so each
# LiteBeam must be SNMP'd on its own management IP.
AIRMAX_LR_VARIANTS = {"litebeam_5ac", "litebeam_m5"}

# Matches the auto-generated LR names from discovery_service._generate_device_name
# ("LR B44279" / "LR 10.135.7.237" / "LR auto #3"). Used to decide whether the
# SNMP sysName from a LiteBeam can safely overwrite the device's name — a
# manually edited name (anything not matching) is always preserved.
_AUTO_LR_NAME = re.compile(
    r"^LR ([0-9A-F]{6}|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|auto #\d+)$"
)

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
    alert_type is device-specific (rocket_down / lr_down / switch_down).
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

        # airMAX LRs (LiteBeam 5AC/M5) — SNMP'd directly. Their parent
        # Rocket airMAX exposes only peer identification via SNMP, not the
        # per-peer radio metrics LTU Rockets fan out via HTTP.
        # snmp_community must be set explicitly — auto-discovered LRs come in
        # with NULL community and are not polled until an operator opts them
        # in (UI or SQL: UPDATE devices SET snmp_community='public' WHERE …).
        result = await session.execute(
            select(Lr).where(
                Lr.status == "up",
                Lr.snmp_community.is_not(None),
                Lr.model_variant.in_(AIRMAX_LR_VARIANTS),
            )
        )
        devices.extend(result.scalars().all())

    if not devices:
        logger.debug("No SNMP-eligible devices — skipping SNMP poll")
        return

    logger.info("SNMP poll — checking %d device(s)", len(devices))

    for device in devices:
        community = device.snmp_community or settings.snmp_default_community
        category = device.rule_category

        is_airmax_lr = isinstance(device, Lr) and device.model_variant in AIRMAX_LR_VARIANTS
        if is_airmax_lr or category in AIRMAX_RULE_CATEGORIES:
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

            # airMAX LRs — pull sysName so the UI shows the friendly device
            # name configured in airOS instead of the auto-generated MAC-suffix
            # name (e.g. "LR B44279" → "44910449- Habib Khoumein"). Only
            # overwrites `name` when it still matches the auto-generated
            # pattern, so a manually edited name is always preserved.
            if is_airmax_lr:
                sysname = await snmp_service.get_sysname(
                    host=device.ip_address,
                    community=community,
                    port=settings.snmp_port,
                    timeout=settings.snmp_timeout,
                )
                if sysname:
                    if dev.hostname != sysname:
                        dev.hostname = sysname
                    if _AUTO_LR_NAME.match(dev.name) and dev.name != sysname:
                        logger.info(
                            "airMAX LR auto-rename — '%s' (%s) → '%s'",
                            dev.name, device.ip_address, sysname,
                        )
                        dev.name = sysname

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
    """
    settings = get_settings()

    async with async_session_factory() as session:
        result = await session.execute(
            select(Lr).where(
                Lr.ssh_username.is_not(None),
                Lr.ssh_password.is_not(None),
            )
        )
        lrs = list(result.scalars().all())

    if not lrs:
        logger.debug("lr_internet_probe: aucun LR avec credentials SSH — ignoré")
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
            if ping_ok and avg_rtt is not None:
                session.add(DeviceMetric(
                    device_id=dev.id,
                    metric_name="lr_latency_ms",
                    metric_value=avg_rtt,
                    unit="ms",
                ))
                await _evaluate_lr_latency(session, dev, avg_rtt, settings)
            elif ping_ok:
                # Ping a réussi (exit 0) mais RTT non parsé — défensif.
                logger.debug(
                    "lr_internet_probe: %s (%s) ping OK mais RTT non parsé — %s",
                    dev.name, dev.ip_address, msg,
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


async def lr_topology_check_job() -> None:
    """Detect router vs bridge mode on every SSH-reachable LR.

    The client-block feature (full + whatsapp_only) only works on a router-
    mode LR. In bridge mode the LR is L2-transparent: iptables FORWARD and
    the local dnsmasq are not in the client's path, so any block silently
    fails to actually cut anything. We open a warning incident
    (AT_LR_BRIDGE_MODE_MISCONFIG) so the operator reconfigures the LR back
    to router mode, and surface a badge in the UI via Lr.topology_mode.

    Runs hourly — bridge/router is a config decision that doesn't change at
    high frequency, no need to thrash the SSH connection.
    """
    settings = get_settings()
    async with async_session_factory() as session:
        result = await session.execute(
            select(Lr).where(
                Lr.ssh_username.is_not(None),
                Lr.ssh_password.is_not(None),
            )
        )
        lrs = list(result.scalars().all())

    if not lrs:
        logger.debug("LR topology check: no LR with SSH credentials")
        return

    logger.info("LR topology check — probing %d LR(s)", len(lrs))

    async with async_session_factory() as session:
        for lr in lrs:
            dev = await session.get(Lr, lr.id)
            if dev is None:
                continue

            primary_pw = dev.ssh_password
            mode, msg, observed_fp, used_pw = await ssh_service.detect_lr_topology(
                host=dev.ip_address,
                port=dev.ssh_port or 22,
                username=dev.ssh_username,
                password=primary_pw,
                expected_fingerprint=dev.ssh_host_fingerprint,
                fallback_passwords=settings.lr_fallback_password_list,
            )
            if observed_fp and dev.ssh_host_fingerprint != observed_fp:
                dev.ssh_host_fingerprint = observed_fp
            if used_pw and used_pw != primary_pw:
                logger.info(
                    "lr_topology_check: LR '%s' (%s) — fallback password "
                    "succeeded → promoting on LR.",
                    dev.name, dev.ip_address,
                )
                dev.ssh_password = used_pw

            previous = dev.topology_mode
            # Only persist confirmed states — "unknown" (probe failed) must
            # not erase a previously-known router/bridge classification.
            if mode in ("router", "bridge"):
                dev.topology_mode = mode

            if mode == "bridge":
                title = f"Mauvaise config : LR '{dev.name}' en mode bridge"
                description = (
                    f"Le LR {dev.name} ({dev.ip_address}) est détecté en mode "
                    f"bridge ({msg}). Le blocage client (Coupure totale / "
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
                    dev.name, dev.id, msg,
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
            elif mode == "unknown":
                logger.debug(
                    "LR topology — '%s' (id=%d) probe failed : %s",
                    dev.name, dev.id, msg,
                )

            await session.commit()


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
    scheduler.add_job(
        lr_topology_check_job,
        trigger="interval", minutes=settings.lr_topology_check_interval_minutes,
        id="lr_topology_check",
        name="LR topology check (router vs bridge misconfig)",
        replace_existing=True,
        **safety,
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



