"""
Alert correlation — rule-based root cause analysis with temporal refinement.

determine_probable_cause() examines device statuses, active alert types, and
optionally the detection timestamps of device-down incidents to confirm causal
ordering before attributing a root cause.

Rules are evaluated in priority order (most specific first).
Returns None when no correlation applies (everything nominal).
"""

from __future__ import annotations

import datetime

_RADIO_QUALITY_ALERTS = {"signal_low", "cinr_low", "ccq_low", "radio_link_degraded"}

# Maximum time difference where the switch going down after the rocket can still
# be considered the root cause (accounts for detection/polling delays).
_TEMPORAL_WINDOW = datetime.timedelta(minutes=5)


def determine_probable_cause(
    device_statuses: dict[str, str],
    active_alert_types: set[str],
    incident_timestamps: dict[str, datetime.datetime] | None = None,
) -> str | None:
    """
    Derive a probable root cause from device statuses and active alert types.

    Parameters
    ----------
    device_statuses : dict mapping device role to status string.
        Expected keys: "rocket", "lr", "switch" (all optional).
        Values: "up", "down", "unknown", etc.
    active_alert_types : set of alert_type strings currently active (open incidents).
    incident_timestamps : optional dict mapping alert_type keys ("switch_down",
        "rocket_down") to the datetime when that incident was first detected.
        When provided, temporal ordering is used to confirm or refute Case 1.

    Returns
    -------
    A short cause string or None.

    Cause strings
    -------------
    "switch_down"        — switch down is the root cause of rocket being unreachable
    "local_link_issue"   — eth0 cable/port problem between switch and rocket
    "radio_link_issue"   — radio interface or CPE association problem
    "radio_quality_issue"— signal/CINR/CCQ degradation
    """
    rocket_status = device_statuses.get("rocket", "unknown")
    lr_status = device_statuses.get("lr", "unknown")
    switch_status = device_statuses.get("switch", "unknown")

    rocket_down = rocket_status == "down"
    switch_down = switch_status == "down"
    rocket_up = rocket_status == "up"
    lr_up = lr_status == "up"

    # Cas 1 : switch DOWN + rocket inaccessible
    # → switch failure is the probable root cause of rocket being unreachable.
    # Temporal refinement: if timestamps are available, only attribute this cause
    # when the switch went down at the same time or before the rocket (allowing a
    # detection window). If the switch went down significantly later, the events
    # are likely unrelated.
    if switch_down and rocket_down:
        if incident_timestamps:
            switch_ts = incident_timestamps.get("switch_down")
            rocket_ts = incident_timestamps.get("rocket_down")
            if switch_ts and rocket_ts and switch_ts > rocket_ts + _TEMPORAL_WINDOW:
                # Switch failed long after rocket → unrelated events
                return None
        return "switch_down"

    # Cas 2 : switch UP + eth0_down actif + rocket dégradé ou down
    # → cable or switch port between switch and rocket
    if not switch_down and "eth0_down" in active_alert_types:
        return "local_link_issue"

    # Cas 3 : rocket UP + LR UP + radio interface down ou CPE déconnecté
    # → radio link physical/association problem
    if rocket_up and lr_up and (
        "radio_interface_down" in active_alert_types
        or "cpe_disconnected" in active_alert_types
    ):
        return "radio_link_issue"

    # Cas 4 : rocket UP + LR UP + métriques radio mauvaises
    # → radio quality degradation (RF environment, interference)
    if rocket_up and lr_up and active_alert_types & _RADIO_QUALITY_ALERTS:
        return "radio_quality_issue"

    return None
