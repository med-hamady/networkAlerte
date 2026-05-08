"""
Alert constants — centralised severity, channel and alert_type keys.

Single source of truth for the strings used across the alert pipeline:
  - Severity values stored in incidents.severity
  - Notification channel identifiers used by the policy
  - Stable alert_type keys produced by alert rules and polling jobs

Other modules (alert_engine, alert_rules, alert_policy, jobs, notification_service)
import from here instead of redefining string literals.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------

class Severity:
    """Severity values stored in Incident.severity."""

    INFO     = "info"      # informational, baseline context
    WARNING  = "warning"   # degradation or risk, deferred action acceptable
    CRITICAL = "critical"  # real incident, immediate action required
    DYNAMIC  = "dynamic"   # policy marker — actual severity comes from the rule


SEVERITY_VALUES: frozenset[str] = frozenset({
    Severity.INFO, Severity.WARNING, Severity.CRITICAL,
})


# ---------------------------------------------------------------------------
# Notification channels
# ---------------------------------------------------------------------------

class AlertChannel:
    """Notification channel identifiers used by alert policies."""

    EMAIL = "email"


CHANNEL_VALUES: frozenset[str] = frozenset({AlertChannel.EMAIL})


# ---------------------------------------------------------------------------
# Alert type keys — stable identifiers stored in Incident.alert_type
# ---------------------------------------------------------------------------

# Device availability (ping-based, jobs.device_ping_job)
AT_ROCKET_DOWN          = "rocket_down"
AT_LR_DOWN              = "lr_down"
AT_SWITCH_DOWN          = "switch_down"
AT_DEVICE_UNREACHABLE   = "device_unreachable"

# Interface and local link (alert_rules)
AT_RADIO_INTERFACE_DOWN = "radio_interface_down"
AT_ETH0_DOWN            = "eth0_down"
AT_CPE_DISCONNECTED     = "cpe_disconnected"

# Radio quality (alert_rules)
AT_SIGNAL_LOW           = "signal_low"
AT_CINR_LOW             = "cinr_low"
AT_CCQ_LOW              = "ccq_low"
AT_RADIO_LINK_DEGRADED  = "radio_link_degraded"

# Performance (alert_rules)
AT_CAPACITY_LOW         = "capacity_low"
AT_HIGH_RX_TX_ERRORS    = "high_rx_tx_errors"
AT_THROUGHPUT_ANOMALY   = "throughput_anomaly"

# Power & infrastructure (jobs.power_poll_job, jobs.transit_probe_job, jobs.snmp_poll_job)
AT_UISP_POWER_UNREACH   = "uisp_power_unreachable"
AT_BATTERY_LOW_WARN     = "battery_low_warning"
AT_BATTERY_LOW_CRIT     = "battery_low_critical"
AT_VOLTAGE_ANOMALY      = "voltage_anomaly"
AT_TRANSIT_UNAVAILABLE  = "transit_unavailable"
AT_SWITCH_PORT_DOWN     = "switch_port_down"

# LTU LR transit probe — SSH-based (LR joignable localement mais sans internet)
AT_LR_NO_TRANSIT        = "lr_no_transit"

# Switch port speed degraded (port UP mais vitesse < 1000 Mbps)
AT_SWITCH_PORT_SPEED_LOW = "switch_port_speed_low"

# Uplink (UL) quality — métriques bidirectionnelles du lien radio
AT_CCQ_UL_LOW      = "ccq_ul_low"
AT_CINR_UL_LOW     = "cinr_ul_low"
AT_CAPACITY_UL_LOW = "capacity_ul_low"

# airMAX (airOS) device availability — ping-based, device_ping_job
AT_AIRMAX_DOWN     = "airmax_down"

# Auto-discovery lifecycle events — fired by discovery_service / stale_lr_detection_job
AT_LR_DISCOVERED   = "lr_discovered"   # nouveau LR détecté en peer d'un Rocket
AT_LR_IP_CHANGED   = "lr_ip_changed"   # MAC connue mais IP différente
AT_LR_REASSIGNED   = "lr_reassigned"   # LR vu sur un autre Rocket que son parent actuel
AT_LR_DISAPPEARED  = "lr_disappeared"  # LR auto-découvert plus rapporté depuis N min

# Ping quality — instabilité ponctuelle (pas d'incident, info email)
# et latence élevée (incident warning/critical, email forcé)
AT_PING_INSTABILITY = "ping_instability"
AT_PING_LATENCY_HIGH = "ping_latency_high"


KNOWN_ALERT_TYPES: frozenset[str] = frozenset({
    AT_ROCKET_DOWN, AT_LR_DOWN, AT_SWITCH_DOWN, AT_DEVICE_UNREACHABLE,
    AT_RADIO_INTERFACE_DOWN, AT_ETH0_DOWN, AT_CPE_DISCONNECTED,
    AT_SIGNAL_LOW, AT_CINR_LOW, AT_CCQ_LOW, AT_RADIO_LINK_DEGRADED,
    AT_CAPACITY_LOW, AT_HIGH_RX_TX_ERRORS, AT_THROUGHPUT_ANOMALY,
    AT_UISP_POWER_UNREACH, AT_BATTERY_LOW_WARN, AT_BATTERY_LOW_CRIT,
    AT_VOLTAGE_ANOMALY, AT_TRANSIT_UNAVAILABLE, AT_SWITCH_PORT_DOWN,
    AT_LR_NO_TRANSIT, AT_SWITCH_PORT_SPEED_LOW,
    AT_CCQ_UL_LOW, AT_CINR_UL_LOW, AT_CAPACITY_UL_LOW,
    AT_AIRMAX_DOWN,
    AT_LR_DISCOVERED, AT_LR_IP_CHANGED, AT_LR_REASSIGNED, AT_LR_DISAPPEARED,
    AT_PING_INSTABILITY, AT_PING_LATENCY_HIGH,
})


# ---------------------------------------------------------------------------
# Notification events
# ---------------------------------------------------------------------------

class NotificationEvent:
    """Lifecycle events the formatter knows how to render."""

    OPENED   = "opened"
    RESOLVED = "resolved"


EVENT_VALUES: frozenset[str] = frozenset({
    NotificationEvent.OPENED, NotificationEvent.RESOLVED,
})
