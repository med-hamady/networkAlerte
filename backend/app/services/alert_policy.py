"""
Alert policy registry — single source of truth for operational metadata
attached to each alert_type:

    severity            : default severity (or DYNAMIC if the rule decides)
    notify_immediately  : if True the notification is sent on the spot
    channels            : tuple of channels eligible for this alert_type
    groupable           : warnings can be batched in a future digest
    recovery_notification: send a RECOVERY message when the incident resolves

The classification table covers every alert_type produced by the engine
(alert_rules + polling jobs). New alert_types must be added here.

Helpers:
    get_policy(alert_type)             — policy lookup with safe fallback
    effective_notify_immediately(...)  — resolves DYNAMIC against actual severity
    should_notify(channel, ...)        — decides if a given channel must fire
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.alert_constants import (
    AT_AIRMAX_DOWN,
    AT_BATTERY_LOW_CRIT,
    AT_BATTERY_LOW_WARN,
    AT_CAPACITY_LOW,
    AT_CAPACITY_UL_LOW,
    AT_CCQ_LOW,
    AT_CCQ_UL_LOW,
    AT_CINR_LOW,
    AT_CINR_UL_LOW,
    AT_CPE_DISCONNECTED,
    AT_DEVICE_UNREACHABLE,
    AT_ETH0_DOWN,
    AT_HIGH_RX_TX_ERRORS,
    AT_LR_BRIDGE_MODE_MISCONFIG,
    AT_LR_DISCOVERED,
    AT_LR_IP_CHANGED,
    AT_LR_LATENCY_HIGH,
    AT_LR_LINK_SUBSTANDARD,
    AT_LR_NO_TRANSIT,
    AT_LR_REASSIGNED,
    AT_MAINS_POWER_LOST,
    AT_PING_INSTABILITY,
    AT_RADIO_INTERFACE_DOWN,
    AT_RADIO_LINK_DEGRADED,
    AT_ROCKET_CLIENT_OVERLOAD,
    AT_ROCKET_DOWN,
    AT_SECURITY_ANOMALY,
    AT_SIGNAL_LOW,
    AT_SWITCH_DOWN,
    AT_SWITCH_PORT_DOWN,
    AT_SWITCH_PORT_SPEED_LOW,
    AT_THROUGHPUT_ANOMALY,
    AT_TRANSIT_UNAVAILABLE,
    AT_UISP_POWER_UNREACH,
    AT_VOLTAGE_ANOMALY,
    AlertChannel,
    Severity,
)

# ---------------------------------------------------------------------------
# Policy dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AlertPolicy:
    """Operational policy for a single alert_type."""

    alert_type: str
    severity: str                          # info | warning | critical | dynamic
    notify_immediately: bool
    channels: tuple[str, ...]
    groupable: bool = False
    recovery_notification: bool = True


# ---------------------------------------------------------------------------
# Default channel sets
# ---------------------------------------------------------------------------

_CHANNELS_CRITICAL: tuple[str, ...] = (AlertChannel.EMAIL,)
_CHANNELS_WARNING: tuple[str, ...] = (AlertChannel.EMAIL,)
_CHANNELS_INFO: tuple[str, ...] = (AlertChannel.EMAIL,)


# ---------------------------------------------------------------------------
# Registry — every alert_type the system can raise
# ---------------------------------------------------------------------------

ALERT_POLICIES: dict[str, AlertPolicy] = {

    # --- Device availability (critical, immediate) ---------------------------

    AT_ROCKET_DOWN: AlertPolicy(
        alert_type=AT_ROCKET_DOWN,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_SWITCH_DOWN: AlertPolicy(
        alert_type=AT_SWITCH_DOWN,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_DEVICE_UNREACHABLE: AlertPolicy(
        alert_type=AT_DEVICE_UNREACHABLE,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_AIRMAX_DOWN: AlertPolicy(
        alert_type=AT_AIRMAX_DOWN,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),

    # --- Interface & local link (critical, immediate) ------------------------

    AT_RADIO_INTERFACE_DOWN: AlertPolicy(
        alert_type=AT_RADIO_INTERFACE_DOWN,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_ETH0_DOWN: AlertPolicy(
        alert_type=AT_ETH0_DOWN,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_CPE_DISCONNECTED: AlertPolicy(
        alert_type=AT_CPE_DISCONNECTED,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),

    # --- Radio quality (warning, deferred unless escalated) ------------------

    AT_SIGNAL_LOW: AlertPolicy(
        alert_type=AT_SIGNAL_LOW,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_CINR_LOW: AlertPolicy(
        alert_type=AT_CINR_LOW,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_CCQ_LOW: AlertPolicy(
        alert_type=AT_CCQ_LOW,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_RADIO_LINK_DEGRADED: AlertPolicy(
        alert_type=AT_RADIO_LINK_DEGRADED,
        severity=Severity.DYNAMIC,    # rule chooses warning OR critical
        notify_immediately=False,     # overridden when severity == critical
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),

    # --- Performance (warning) -----------------------------------------------

    AT_CAPACITY_LOW: AlertPolicy(
        alert_type=AT_CAPACITY_LOW,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_HIGH_RX_TX_ERRORS: AlertPolicy(
        alert_type=AT_HIGH_RX_TX_ERRORS,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_THROUGHPUT_ANOMALY: AlertPolicy(
        alert_type=AT_THROUGHPUT_ANOMALY,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_LR_LINK_SUBSTANDARD: AlertPolicy(
        alert_type=AT_LR_LINK_SUBSTANDARD,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
        groupable=False,
    ),
    AT_ROCKET_CLIENT_OVERLOAD: AlertPolicy(
        alert_type=AT_ROCKET_CLIENT_OVERLOAD,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
        groupable=False,
    ),

    # --- Radio quality UL — uplink (warning, deferred) -----------------------

    AT_CCQ_UL_LOW: AlertPolicy(
        alert_type=AT_CCQ_UL_LOW,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_CINR_UL_LOW: AlertPolicy(
        alert_type=AT_CINR_UL_LOW,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_CAPACITY_UL_LOW: AlertPolicy(
        alert_type=AT_CAPACITY_UL_LOW,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),

    # --- Power & infrastructure ----------------------------------------------

    AT_UISP_POWER_UNREACH: AlertPolicy(
        alert_type=AT_UISP_POWER_UNREACH,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_BATTERY_LOW_WARN: AlertPolicy(
        alert_type=AT_BATTERY_LOW_WARN,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_BATTERY_LOW_CRIT: AlertPolicy(
        alert_type=AT_BATTERY_LOW_CRIT,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_VOLTAGE_ANOMALY: AlertPolicy(
        alert_type=AT_VOLTAGE_ANOMALY,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
        groupable=False,
    ),
    # Coupure secteur SOMELEC : le site bascule sur batterie. Notif immédiate
    # (groupable=False → pas de mise en digest) + message de rétablissement au
    # retour du secteur. WARNING : la batterie fait son travail ; le vrai
    # danger (batterie qui se vide) est couvert par battery_low_*.
    AT_MAINS_POWER_LOST: AlertPolicy(
        alert_type=AT_MAINS_POWER_LOST,
        severity=Severity.WARNING,
        notify_immediately=True,
        channels=_CHANNELS_WARNING,
        groupable=False,
        recovery_notification=True,
    ),
    AT_TRANSIT_UNAVAILABLE: AlertPolicy(
        alert_type=AT_TRANSIT_UNAVAILABLE,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_LR_NO_TRANSIT: AlertPolicy(
        alert_type=AT_LR_NO_TRANSIT,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_LR_LATENCY_HIGH: AlertPolicy(
        alert_type=AT_LR_LATENCY_HIGH,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_SWITCH_PORT_DOWN: AlertPolicy(
        alert_type=AT_SWITCH_PORT_DOWN,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_SWITCH_PORT_SPEED_LOW: AlertPolicy(
        alert_type=AT_SWITCH_PORT_SPEED_LOW,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),

    # --- Auto-discovery lifecycle (informational) ----------------------------

    AT_LR_DISCOVERED: AlertPolicy(
        alert_type=AT_LR_DISCOVERED,
        severity=Severity.INFO,
        notify_immediately=False,
        channels=(AlertChannel.EMAIL,),
        groupable=False,
        recovery_notification=False,
    ),
    AT_LR_IP_CHANGED: AlertPolicy(
        alert_type=AT_LR_IP_CHANGED,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=(AlertChannel.EMAIL,),
        groupable=False,
        recovery_notification=False,
    ),
    AT_LR_REASSIGNED: AlertPolicy(
        alert_type=AT_LR_REASSIGNED,
        severity=Severity.WARNING,
        notify_immediately=True,
        channels=_CHANNELS_WARNING,
        groupable=False,
        recovery_notification=False,
    ),

    # --- Configuration misconfig (warning, deferred) -------------------------

    AT_LR_BRIDGE_MODE_MISCONFIG: AlertPolicy(
        alert_type=AT_LR_BRIDGE_MODE_MISCONFIG,
        severity=Severity.WARNING,
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=False,
        recovery_notification=True,
    ),

    # --- Ping quality (informational, no recovery) ---------------------------

    AT_PING_INSTABILITY: AlertPolicy(
        alert_type=AT_PING_INSTABILITY,
        severity=Severity.INFO,
        notify_immediately=False,
        channels=_CHANNELS_INFO,
        groupable=True,
        recovery_notification=False,
    ),

    # --- Security (critical, immediate, no recovery) -------------------------

    AT_SECURITY_ANOMALY: AlertPolicy(
        alert_type=AT_SECURITY_ANOMALY,
        severity=Severity.CRITICAL,
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
        groupable=False,
        recovery_notification=False,
    ),
}


# ---------------------------------------------------------------------------
# Fallback for unknown alert_types
# ---------------------------------------------------------------------------

_FALLBACK_POLICY = AlertPolicy(
    alert_type="_unknown",
    severity=Severity.WARNING,
    notify_immediately=False,
    channels=(AlertChannel.EMAIL,),
    groupable=False,
    recovery_notification=True,
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def get_policy(alert_type: str | None) -> AlertPolicy:
    """Return the policy for an alert_type, or a safe fallback."""
    if not alert_type:
        return _FALLBACK_POLICY
    return ALERT_POLICIES.get(alert_type, _FALLBACK_POLICY)


# Whitelist of fields a per-device override may touch.
# Severity and alert_type are intentionally excluded: overrides are about
# *how* an alert is delivered, not about reclassifying it.
_OVERRIDABLE_FIELDS: frozenset[str] = frozenset({
    "notify_immediately",
    "channels",
    "groupable",
    "recovery_notification",
})


def merge_overrides(
    base: AlertPolicy,
    override: dict | None,
) -> AlertPolicy:
    """
    Apply a per-device override dict on top of the base policy.

    Override shape: subset of {notify_immediately, channels, groupable,
    recovery_notification}. Unknown keys are ignored (logged at debug).
    Channels are coerced to a tuple of strings; invalid channels are
    silently filtered out so a malformed override never enables an
    unknown delivery target.
    """
    if not override:
        return base

    patch: dict = {}
    for key, value in override.items():
        if key not in _OVERRIDABLE_FIELDS:
            continue
        if key == "channels":
            if not isinstance(value, (list, tuple)):
                continue
            from app.core.alert_constants import CHANNEL_VALUES
            cleaned = tuple(c for c in value if c in CHANNEL_VALUES)
            patch["channels"] = cleaned or base.channels
        else:
            patch[key] = value

    if not patch:
        return base

    from dataclasses import replace
    return replace(base, **patch)


def get_policy_for_device(
    alert_type: str | None,
    device_overrides: dict | None,
) -> AlertPolicy:
    """
    Return the policy for an alert_type, applying any per-device override.

    `device_overrides` is the JSON column read from devices.policy_overrides
    and is expected to be a dict keyed by alert_type.
    """
    base = get_policy(alert_type)
    if not device_overrides or not alert_type:
        return base
    override = device_overrides.get(alert_type)
    if not isinstance(override, dict):
        return base
    return merge_overrides(base, override)


def effective_notify_immediately(
    policy: AlertPolicy,
    incident_severity: str | None,
) -> bool:
    """
    Resolve `notify_immediately` against the actual incident severity.

    Rules:
      - critical incidents always notify immediately, regardless of policy default
      - dynamic-severity policies inherit the immediate flag from the actual severity
      - otherwise the policy default applies
    """
    if incident_severity == Severity.CRITICAL:
        return True
    return policy.notify_immediately


def should_notify(
    channel: str,
    policy: AlertPolicy,
    incident_severity: str | None,
    event: str,
) -> bool:
    """
    Decide whether a given channel must fire for this incident lifecycle event.

    Logic:
      - channel must be listed in policy.channels
      - if event is "resolved" and policy.recovery_notification is False → skip
      - info-severity events that are not flagged immediate are skipped
        (channels are still allowed to log via webhook by including INFO policies
        explicitly — the registry does not currently produce info incidents)
    """
    if channel not in policy.channels:
        return False
    if event == "resolved" and not policy.recovery_notification:
        return False
    return not (
        incident_severity == Severity.INFO
        and not effective_notify_immediately(policy, incident_severity)
    )
