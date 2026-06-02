"""
Alert engine — orchestrates rule evaluation and incident lifecycle.

Main entry point: evaluate_device_metrics()

The engine:
  1. Loads applicable rules for the device type
  2. Injects previous metric values (delta-based rules like error counters)
  3. Evaluates each rule
  4. Manages failure counts persisted in AlertState (survives restarts)
  5. Opens incidents when the failure threshold is reached
  6. Resolves incidents when the condition clears
  7. Updates last_triggered_at on every active cycle
  8. Calls the correlation engine to set probable_cause
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.alert import Alert
from app.models.alert_state import AlertState
from app.models.device import Device, Lr
from app.models.incident import Incident
from app.services import incident_service, notification_service
from app.services.alert_rules import AlertEvalResult, get_failure_threshold, get_rules_for_device

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AlertState helpers (DB-backed failure counters)
# ---------------------------------------------------------------------------

async def _get_or_create_state(
    db: AsyncSession,
    device_id: int,
    alert_type: str,
) -> AlertState:
    """Fetch the AlertState row for (device_id, alert_type), creating it if absent."""
    result = await db.execute(
        select(AlertState).where(
            AlertState.device_id == device_id,
            AlertState.alert_type == alert_type,
        )
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = AlertState(device_id=device_id, alert_type=alert_type, failure_count=0)
        db.add(state)
        await db.flush()
    return state


async def _increment_failure(db: AsyncSession, state: AlertState) -> int:
    state.failure_count += 1
    state.last_evaluated_at = datetime.datetime.now(datetime.UTC)
    await db.flush()
    return state.failure_count


async def _reset_failure(db: AsyncSession, state: AlertState) -> None:
    state.failure_count = 0
    state.last_evaluated_at = datetime.datetime.now(datetime.UTC)
    await db.flush()


# ---------------------------------------------------------------------------
# Alert record helpers
# ---------------------------------------------------------------------------

async def _create_alert_record(
    db: AsyncSession,
    incident: Incident,
    message: str,
    notif_success: bool,
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    db.add(Alert(
        incident_id=incident.id,
        message=message,
        status="sent" if notif_success else "failed",
        sent_at=now if notif_success else None,
    ))


async def _open_alert(
    db: AsyncSession,
    device: Device,
    result: AlertEvalResult,
) -> Incident:
    """Open (or refresh) an incident for this alert. Notifies only on first open."""
    incident, is_new = await incident_service.open_incident(
        db,
        device,
        title=result.message,
        severity=result.severity,
        description=result.message,
        alert_type=result.alert_type,
        metric_name=result.metric_name,
        metric_value=result.metric_value,
        threshold_value=result.threshold_value,
    )
    if is_new:
        ok = await notification_service.notify_incident_opened(device, incident)
        await _create_alert_record(db, incident, result.message, ok)
        logger.warning(
            "ALERT OPENED [%s] %s — %s",
            result.severity.upper(),
            device.name,
            result.alert_type,
        )
    return incident


async def _resolve_alert(
    db: AsyncSession,
    device: Device,
    alert_type: str,
    recovery_message: str,
) -> None:
    """Resolve open incidents for this alert_type and notify."""
    resolved = await incident_service.resolve_incidents(
        db, device.id, title=recovery_message, alert_type=alert_type
    )
    for inc in resolved:
        ok = await notification_service.notify_incident_resolved(device, inc)
        await _create_alert_record(db, inc, recovery_message, ok)
        logger.info("ALERT RESOLVED %s — %s", device.name, alert_type)


# ---------------------------------------------------------------------------
# Throughput baseline injection (EMA-based anomaly detection)
# ---------------------------------------------------------------------------

_THROUGHPUT_EMA_ALPHA = 0.05  # ~20-cycle half-life at 60 s/cycle ≈ 20 minutes


async def _inject_throughput_baseline(
    db: AsyncSession,
    device_id: int,
    metrics: dict,
) -> dict:
    """
    Maintain an exponential moving average of tx_rate_mbps in AlertState and
    inject it as tx_rate_ema_mbps for ThroughputAnomalyRule.
    """
    tx_rate = metrics.get("tx_rate_mbps")
    if tx_rate is None:
        return metrics

    metrics = dict(metrics)
    state = await _get_or_create_state(db, device_id, "_throughput_ema")

    if state.last_metric_value is None:
        ema = tx_rate  # cold start — seed with current value
    else:
        ema = _THROUGHPUT_EMA_ALPHA * tx_rate + (1 - _THROUGHPUT_EMA_ALPHA) * state.last_metric_value

    state.last_metric_value = ema
    state.last_evaluated_at = datetime.datetime.now(datetime.UTC)
    metrics["tx_rate_ema_mbps"] = ema
    return metrics


# ---------------------------------------------------------------------------
# Error counter injection (delta-based rules)
# ---------------------------------------------------------------------------

async def _inject_error_deltas(
    db: AsyncSession,
    device_id: int,
    metrics: dict,
) -> dict:
    """
    Fetch the previous error/byte counters from AlertState and inject them
    as prev_* keys into the metrics dict for HighRxTxErrorsRule.
    Then persist the current counters as the new "previous" values.
    """
    metrics = dict(metrics)

    # Use 4 separate AlertState rows — one per counter.
    # This is cleaner than trying to pack multiple values into one float.
    counter_keys = [
        ("radio_in_errors", "prev_in_errors", "_cnt_in_errors"),
        ("radio_out_errors", "prev_out_errors", "_cnt_out_errors"),
        ("radio_rx_bytes", "prev_rx_bytes", "_cnt_rx_bytes"),
        ("radio_tx_bytes", "prev_tx_bytes", "_cnt_tx_bytes"),
    ]

    for current_key, prev_key, state_key in counter_keys:
        current_val = metrics.get(current_key)
        if current_val is None:
            continue

        s = await _get_or_create_state(db, device_id, state_key)
        if s.last_metric_value is not None:
            metrics[prev_key] = s.last_metric_value
        s.last_metric_value = current_val
        s.last_evaluated_at = datetime.datetime.now(datetime.UTC)

    return metrics


async def _inject_open_alert_types(
    db: AsyncSession,
    device_id: int,
    metrics: dict,
) -> dict:
    """Inject the set of alert_types that currently have an OPEN incident
    for this device as ``_open_alert_types``.

    Hysteresis-aware rules (signal_low) read this to use a stricter line to
    OPEN and the nominal line to RESOLVE — no flapping in the margin band.
    """
    metrics = dict(metrics)
    rows = await db.execute(
        select(Incident.alert_type).where(
            Incident.device_id == device_id,
            Incident.status == "open",
            Incident.alert_type.is_not(None),
        )
    )
    metrics["_open_alert_types"] = set(rows.scalars().all())
    return metrics


# ---------------------------------------------------------------------------
# Correlation — update probable_cause on open incidents
# ---------------------------------------------------------------------------

async def _apply_correlation(
    db: AsyncSession,
    device: Device,
    active_alert_types: set[str],
) -> None:
    """
    Call the correlation engine (with temporal refinement) and update
    probable_cause on all open incidents for this device.
    """
    from app.services.alert_correlation import determine_probable_cause

    # Build device statuses map — LIMIT keeps memory bounded on large deployments
    result = await db.execute(select(Device).limit(500))
    all_devices = list(result.scalars().all())

    device_statuses: dict[str, str] = {}
    device_by_role: dict[str, Device] = {}
    for d in all_devices:
        if d.rule_category == "ltu_rocket":
            device_statuses["rocket"] = d.status
            device_by_role["rocket"] = d
        elif d.rule_category == "lr":
            device_statuses["lr"] = d.status
        elif d.rule_category == "uisp_switch":
            device_statuses["switch"] = d.status
            device_by_role["switch"] = d

    # Fetch detection timestamps for switch/rocket down incidents (temporal correlation)
    incident_timestamps: dict[str, datetime.datetime] = {}
    _device_down_alert_types = {
        "switch_down", "rocket_down", "device_down",
    }
    for role, d in device_by_role.items():
        ts_result = await db.execute(
            select(Incident.detected_at).where(
                Incident.device_id == d.id,
                Incident.status == "open",
                Incident.alert_type.in_(_device_down_alert_types),
            ).order_by(Incident.detected_at.asc()).limit(1)
        )
        ts = ts_result.scalar_one_or_none()
        if ts is not None:
            incident_timestamps[f"{role}_down"] = ts

    cause = determine_probable_cause(
        device_statuses, active_alert_types, incident_timestamps or None
    )
    if cause is None:
        return

    open_incidents = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.status == "open",
        )
    )
    for inc in open_incidents.scalars().all():
        if inc.probable_cause != cause:
            inc.probable_cause = cause


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def evaluate_device_metrics(
    db: AsyncSession,
    device: Device,
    metrics: dict,
    settings: Settings,
) -> None:
    """
    Evaluate all alert rules applicable to a device given its latest metrics.

    Parameters
    ----------
    db       : AsyncSession
    device   : Device ORM object (must be bound to db's session)
    metrics  : dict of metric_name → value collected this cycle
    settings : Settings instance
    """
    rules = get_rules_for_device(device.rule_category)
    if not rules:
        return

    # Inject delta counters for error-based rules
    metrics = await _inject_error_deltas(db, device.id, metrics)
    # Inject EMA baseline for throughput anomaly detection
    metrics = await _inject_throughput_baseline(db, device.id, metrics)
    # Inject open-incident set for hysteresis-aware rules (signal_low)
    metrics = await _inject_open_alert_types(db, device.id, metrics)
    # For LRs, surface model_variant so per-family rules (lr_link_substandard)
    # can pick LTU vs airMAX thresholds without changing the rule signature.
    if isinstance(device, Lr) and "model_variant" not in metrics:
        metrics = dict(metrics)
        metrics["model_variant"] = device.model_variant

    active_alert_types: set[str] = set()

    for rule in rules:
        eval_result: AlertEvalResult = rule.evaluate(device.name, metrics, settings)
        if eval_result.skip:
            continue
        alert_type = eval_result.alert_type
        threshold = get_failure_threshold(alert_type, settings)

        if eval_result.severity is not None:
            # Condition is bad — increment failure counter
            state = await _get_or_create_state(db, device.id, alert_type)
            count = await _increment_failure(db, state)

            if count > threshold:
                # Threshold reached (count is 1-based, threshold is 0-based minimum)
                # threshold=0 → opens on first bad cycle (count=1 > 0)
                # threshold=2 → opens on third bad cycle (count=3 > 2)
                await _open_alert(db, device, eval_result)
                active_alert_types.add(alert_type)
            else:
                logger.debug(
                    "ALERT pending %s/%s — %d/%d cycles",
                    device.name, alert_type, count, threshold,
                )
        else:
            # Condition is OK — reset counter and resolve any open incident
            state = await _get_or_create_state(db, device.id, alert_type)
            if state.failure_count > 0:
                await _reset_failure(db, state)
            await _resolve_alert(db, device, alert_type, eval_result.message)

    # Update probable_cause on open incidents based on currently active alerts
    if active_alert_types:
        await _apply_correlation(db, device, active_alert_types)


# ---------------------------------------------------------------------------
# Global correlation pass (called by jobs that bypass evaluate_device_metrics)
# ---------------------------------------------------------------------------

async def run_correlation_pass(db: AsyncSession) -> None:
    """
    Re-evaluate probable_cause for every device that has open incidents.

    Called at the end of device_ping_job so that device-down incidents (which
    bypass evaluate_device_metrics) also benefit from correlation logic, e.g.
    rocket_down gets probable_cause="switch_down" when the switch is also down.
    """
    result = await db.execute(select(Device))
    all_devices = list(result.scalars().all())

    for device in all_devices:
        open_result = await db.execute(
            select(Incident.alert_type).where(
                Incident.device_id == device.id,
                Incident.status == "open",
                Incident.alert_type.is_not(None),
            )
        )
        active_alert_types = set(open_result.scalars().all())
        if not active_alert_types:
            continue
        await _apply_correlation(db, device, active_alert_types)
