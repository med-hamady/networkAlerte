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
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.alert_state import AlertState
from app.models.device import Device, Lr, Rocket
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
# Incident lifecycle
# ---------------------------------------------------------------------------

async def _open_alert(
    db: AsyncSession,
    device: Device,
    result: AlertEvalResult,
) -> Incident | None:
    """Open (or refresh) an incident for this alert. Notifies only on first open.

    Returns None when the incident is suppressed as client-side (the engine
    ignores the return value either way) — see incident_service.open_incident.
    """
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
        await notification_service.notify_incident_opened(device, incident)
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
    # resolve_incidents already hard-deletes non-availability incidents; the
    # returned objects keep their loaded attributes so we can still notify.
    resolved = await incident_service.resolve_incidents(
        db, device.id, title=recovery_message, alert_type=alert_type
    )
    for inc in resolved:
        await notification_service.notify_incident_resolved(device, inc)
        logger.info("ALERT RESOLVED %s — %s", device.name, alert_type)


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
    # Inject open-incident set for hysteresis-aware rules (signal_low)
    metrics = await _inject_open_alert_types(db, device.id, metrics)
    # For LRs, surface model_variant so per-family rules (lr_link_substandard)
    # can pick LTU vs airMAX thresholds without changing the rule signature.
    if isinstance(device, Lr) and "model_variant" not in metrics:
        metrics = dict(metrics)
        metrics["model_variant"] = device.model_variant
    # For base-station Rockets, surface the radio family so the per-family
    # rocket_client_overload rule picks airMAX vs LTU client ceilings.
    if device.rule_category in ("ltu_rocket", "airmax_rocket") and "is_airmax_rocket" not in metrics:
        metrics = dict(metrics)
        metrics["is_airmax_rocket"] = device.rule_category == "airmax_rocket"
    # Surface the manual client-capacity ceiling (operator-set) so the overload
    # rule uses it instead of the per-family/width formula. None = auto formula.
    if isinstance(device, Rocket) and "max_clients_override" not in metrics:
        metrics = dict(metrics)
        metrics["max_clients_override"] = device.max_clients_override

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
