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
# NB: pas d'alerte pour un LR down — un LR injoignable est une panne côté client
# (courant coupé / LR débranché), pas notre infra. Voir device_ping_job.
AT_ROCKET_DOWN          = "rocket_down"
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

# Lien client sous le seuil — incident CONSOLIDÉ (per-LR) : déclenché si ≥1
# plancher parmi potentiel du lien / capacité totale / débit RX local / RX
# distant est franchi. Sévérité critique, anti-flap 5 cycles.
AT_LR_LINK_SUBSTANDARD = "lr_link_substandard"

# Uplink (UL) quality — métriques bidirectionnelles du lien radio
AT_CCQ_UL_LOW      = "ccq_ul_low"
AT_CINR_UL_LOW     = "cinr_ul_low"
AT_CAPACITY_UL_LOW = "capacity_ul_low"

# airMAX (airOS) device availability — ping-based, device_ping_job
AT_AIRMAX_DOWN     = "airmax_down"

# Auto-discovery lifecycle events — fired by discovery_service
# NB: pas d'alerte « LR disparu » — un LR qui ne réapparaît plus dans la liste
# des peers est une panne côté client (courant coupé / LR débranché), pas notre
# infra. Comme lr_down, ce type a été supprimé entièrement.
AT_LR_DISCOVERED   = "lr_discovered"   # nouveau LR détecté en peer d'un Rocket
AT_LR_IP_CHANGED   = "lr_ip_changed"   # MAC connue mais IP différente
AT_LR_REASSIGNED   = "lr_reassigned"   # LR vu sur un autre Rocket que son parent actuel

# Configuration misconfig — LR en mode bridge alors qu'on attend du routeur.
# En bridge, le client-block (full / whatsapp_only) ne marche pas (le trafic
# client ne passe ni par iptables FORWARD ni par le dnsmasq local du LR).
# Levé par lr_topology_check_job, résolu automatiquement si le LR repasse en routeur.
AT_LR_BRIDGE_MODE_MISCONFIG = "lr_bridge_mode_misconfig"

# Ping quality — instabilité ponctuelle (pas d'incident, info email).
AT_PING_INSTABILITY = "ping_instability"

# Latence LR → internet (Google) mesurée par SSH depuis le LR : on ping
# 8.8.8.8 depuis le LR et on ouvre un incident critique si la moyenne RTT
# est ≥ seuil pendant K cycles consécutifs. Seul signal de latence du
# système — la latence superviseur → device n'est pas exploitée.
AT_LR_LATENCY_HIGH = "lr_latency_high"

# Sécurité — volume anormal d'écritures API détecté par
# security_anomaly_detection_job sur la base de la table audit_log. N'est PAS
# attaché à un device (événement système) — envoyé directement par email via
# notification_service.notify_security_event.
AT_SECURITY_ANOMALY = "security_anomaly"


KNOWN_ALERT_TYPES: frozenset[str] = frozenset({
    AT_ROCKET_DOWN, AT_SWITCH_DOWN, AT_DEVICE_UNREACHABLE,
    AT_RADIO_INTERFACE_DOWN, AT_ETH0_DOWN, AT_CPE_DISCONNECTED,
    AT_SIGNAL_LOW, AT_CINR_LOW, AT_CCQ_LOW, AT_RADIO_LINK_DEGRADED,
    AT_CAPACITY_LOW, AT_HIGH_RX_TX_ERRORS, AT_THROUGHPUT_ANOMALY,
    AT_UISP_POWER_UNREACH, AT_BATTERY_LOW_WARN, AT_BATTERY_LOW_CRIT,
    AT_VOLTAGE_ANOMALY, AT_TRANSIT_UNAVAILABLE, AT_SWITCH_PORT_DOWN,
    AT_LR_NO_TRANSIT, AT_SWITCH_PORT_SPEED_LOW, AT_LR_LINK_SUBSTANDARD,
    AT_CCQ_UL_LOW, AT_CINR_UL_LOW, AT_CAPACITY_UL_LOW,
    AT_AIRMAX_DOWN,
    AT_LR_DISCOVERED, AT_LR_IP_CHANGED, AT_LR_REASSIGNED,
    AT_LR_BRIDGE_MODE_MISCONFIG,
    AT_PING_INSTABILITY, AT_LR_LATENCY_HIGH,
    AT_SECURITY_ANOMALY,
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
