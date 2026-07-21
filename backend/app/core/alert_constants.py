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

    WHATSAPP = "whatsapp"  # Ultramsg — the only notification transport


CHANNEL_VALUES: frozenset[str] = frozenset({AlertChannel.WHATSAPP})


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
AT_HIGH_RX_TX_ERRORS    = "high_rx_tx_errors"

# Power & infrastructure (jobs.power_poll_job, jobs.transit_probe_job, jobs.snmp_poll_job)
AT_UISP_POWER_UNREACH   = "uisp_power_unreachable"
AT_BATTERY_LOW_WARN     = "battery_low_warning"
AT_BATTERY_LOW_CRIT     = "battery_low_critical"
# UISP Power — deux alertes batterie DISTINCTES (politique 2026-06-11) :
#   - batterie INTERNE (Li-Ion UPS) < 50 %  → critique + notif immédiate
#   - batterie EXTERNE (banc plomb)  < 30 %  → critique + notif immédiate
# Remplacent l'ancienne alerte unique battery_low_warning pour les UISP Power.
AT_BATTERY_INTERNAL_LOW = "battery_internal_low"
AT_BATTERY_EXTERNAL_LOW = "battery_external_low"
AT_VOLTAGE_ANOMALY      = "voltage_anomaly"
# Coupure secteur (SOMELEC) : le UISP Power n'a plus d'entrée AC connectée et
# bascule sur batterie. Lu directement depuis l'API (power[].psuType=="AC" /
# connected). Anti-flap pour ignorer les micro-coupures.
AT_MAINS_POWER_LOST     = "mains_power_lost"
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
# Levé par le poll HTTP de chaque LR (airMAX netrole / LTU netMode), résolu automatiquement si le LR repasse en routeur.
AT_LR_BRIDGE_MODE_MISCONFIG = "lr_bridge_mode_misconfig"

# Latence LR → internet (Google) mesurée par SSH depuis le LR : on ping
# 8.8.8.8 depuis le LR et on ouvre un incident critique si la moyenne RTT
# est ≥ seuil pendant K cycles consécutifs. Seul signal de latence du
# système — la latence superviseur → device n'est pas exploitée.
AT_LR_LATENCY_HIGH = "lr_latency_high"

# airFiber 60 (AF60-LR) — lien backhaul 60 GHz point-à-point (alert_rules).
AT_AF60_LINK_DOWN        = "af60_link_down"        # radios[0].linkState != "connected"
AT_AF60_SIGNAL_LOW       = "af60_signal_low"       # signal local sous seuil 60 GHz
AT_AF60_SNR_LOW          = "af60_snr_low"          # SNR local sous seuil (pas de CINR en 60 GHz)
AT_AF60_LINK_SUBSTANDARD = "af60_link_substandard" # consolidé : potentiel / capacité sous plancher

# Lien P2P LiteBeam (device_type ptp_litebeam) dégradé — capacité totale
# sous le plancher dédié (airmax_backhaul_capacity_min_mbps). Équivalent airMAX
# de af60_link_substandard : ces radios font un backhaul inter-sites, pas un AP.
AT_P2P_LINK_SUBSTANDARD  = "p2p_link_substandard"

# Sécurité — volume anormal d'écritures API détecté par
# security_anomaly_detection_job sur la base de la table audit_log. N'est PAS
# attaché à un device (événement système) — routé directement via WhatsApp par
# notification_service.notify_security_event.
AT_SECURITY_ANOMALY = "security_anomaly"

# Équipement instable (flapping) — un device d'infra qui enchaîne les cycles
# down/up : flap_detection_job compte ses incidents de disponibilité sur les
# dernières flap_window_hours et ouvre cet incident au-delà de flap_threshold_24h.
# Critique. PAS un type de disponibilité (se résout/purge normalement).
AT_DEVICE_FLAPPING = "device_flapping"

# Charge / capacité de l'AP — un Rocket de base station dépasse le nombre de
# clients qu'il peut servir correctement pour sa (famille radio × largeur de
# canal). Incident critique quand clients connectés ≥ seuil (AP saturé). La
# largeur de canal est lue en direct (LTU channelWidth.tx / airMAX chwidth) ;
# si elle est inconnue ou hors {10, 20} MHz, la règle ne déclenche pas.
AT_ROCKET_CLIENT_OVERLOAD = "rocket_client_overload"


# Availability / outage alert_types — a device fully unreachable. These are the
# ONLY incidents kept in DB after resolution: the downtime journal
# (network_uptime_service) reconstructs past outages + availability % from their
# resolved_at. Every OTHER resolved incident is hard-deleted on resolution
# (there is no /archive view anymore). Keep this in sync with the journal's
# query — network_uptime_service imports this set.
# Sentinelle d'`AlertState.alert_type` servant à persister le compteur d'échecs
# de ping consécutifs (anti-flap du sweep). L'underscore de tête la garde HORS
# du vocabulaire d'alerting : aucune policy, aucun incident, aucun formatter ne
# la touche — et elle n'est volontairement PAS dans KNOWN_ALERT_TYPES. Elle vit
# ici parce que deux modules doivent s'accorder dessus : `tasks/jobs.py` qui
# l'incrémente, et `discovery_service` qui la purge quand un LR perd son IP.
PING_FAILURE_STATE_KEY = "_ping_failures"


AVAILABILITY_ALERT_TYPES: frozenset[str] = frozenset({
    AT_ROCKET_DOWN, AT_SWITCH_DOWN, AT_DEVICE_UNREACHABLE,
    AT_UISP_POWER_UNREACH, AT_AIRMAX_DOWN,
})


KNOWN_ALERT_TYPES: frozenset[str] = frozenset({
    AT_ROCKET_DOWN, AT_SWITCH_DOWN, AT_DEVICE_UNREACHABLE,
    AT_RADIO_INTERFACE_DOWN, AT_ETH0_DOWN, AT_CPE_DISCONNECTED,
    AT_SIGNAL_LOW, AT_CINR_LOW, AT_CCQ_LOW, AT_RADIO_LINK_DEGRADED,
    AT_HIGH_RX_TX_ERRORS,
    AT_UISP_POWER_UNREACH, AT_BATTERY_LOW_WARN, AT_BATTERY_LOW_CRIT,
    AT_BATTERY_INTERNAL_LOW, AT_BATTERY_EXTERNAL_LOW,
    AT_VOLTAGE_ANOMALY, AT_MAINS_POWER_LOST,
    AT_TRANSIT_UNAVAILABLE, AT_SWITCH_PORT_DOWN,
    AT_LR_NO_TRANSIT, AT_SWITCH_PORT_SPEED_LOW, AT_LR_LINK_SUBSTANDARD,
    AT_CCQ_UL_LOW, AT_CINR_UL_LOW,
    AT_AIRMAX_DOWN,
    AT_LR_DISCOVERED, AT_LR_IP_CHANGED, AT_LR_REASSIGNED,
    AT_LR_BRIDGE_MODE_MISCONFIG,
    AT_LR_LATENCY_HIGH,
    AT_AF60_LINK_DOWN, AT_AF60_SIGNAL_LOW, AT_AF60_SNR_LOW, AT_AF60_LINK_SUBSTANDARD,
    AT_P2P_LINK_SUBSTANDARD,
    AT_SECURITY_ANOMALY,
    AT_ROCKET_CLIENT_OVERLOAD,
    AT_DEVICE_FLAPPING,
})


# ---------------------------------------------------------------------------
# WhatsApp allowlist (policy 2026-06-11)
# ---------------------------------------------------------------------------
# WhatsApp ne pousse QUE ces anomalies — les SEULES que l'opérateur veut
# recevoir. Tout autre incident (équipement injoignable, qualité radio, voltage,
# coupure secteur, sécurité, découverte LR…) n'est notifié NULLE PART : le
# pipeline ouvre/résout toujours l'incident côté DB, mais notification_service
# court-circuite l'envoi si l'alert_type n'est pas ici. C'est l'unique point de
# contrôle (chokepoint), cf. notification_service._dispatch / digest_service.
#
# Conditions demandées :
#   1. Port switch dégradé .......... switch_port_speed_low + switch_port_down
#   2. Équipement instable (flapping) device_flapping
#   3. Batterie UISP Power ........... battery_internal_low (Li-Ion UPS < 50 %)
#      + battery_external_low (banc plomb < 30 %), toutes deux critiques
#   4. > 20 % clients en latence .... network_latency_aggregate_job (envoi
#      DIRECT, pas un incident → toujours actif, pas concerné par cette liste)
#   5. Liaison P2P dégradée ......... af60_link_substandard + af60_link_down
#      + p2p_link_substandard (backhaul airMAX inter-sites < plancher capacité)
#   6. Équipement injoignable (down)  rocket_down + switch_down +
#      device_unreachable + airmax_down (couvre aussi un UISP Power down, via
#      device_unreachable du ping job → uisp_power_unreachable HORS liste, plus
#      émis, pour éviter le doublon ; voltage/coupure secteur retirés).
WHATSAPP_ALERT_TYPES: frozenset[str] = frozenset({
    AT_SWITCH_PORT_SPEED_LOW, AT_SWITCH_PORT_DOWN,
    AT_DEVICE_FLAPPING,
    AT_BATTERY_INTERNAL_LOW, AT_BATTERY_EXTERNAL_LOW,
    AT_AF60_LINK_SUBSTANDARD, AT_AF60_LINK_DOWN,
    AT_P2P_LINK_SUBSTANDARD,
    AT_ROCKET_DOWN, AT_SWITCH_DOWN, AT_DEVICE_UNREACHABLE, AT_AIRMAX_DOWN,
})


# ---------------------------------------------------------------------------
# Client-side incident suppression (policy 2026-06-09)
# ---------------------------------------------------------------------------
# The /incidents page surfaces INFRASTRUCTURE incidents only. Anything raised
# on a subscriber radio (a device whose rule_category == CLIENT_RULE_CATEGORY)
# is client-side and is neither created nor stored — see
# incident_service.is_suppressed_incident, the single chokepoint.
#
# The split is by DEVICE, not by alert_type: the radio alert_types (signal_low,
# ccq_low, cinr_low, radio_link_degraded, high_rx_tx_errors,
# high_rx_tx_errors) fire on BOTH base-station Rockets (infra → kept) and
# subscriber LRs (client → dropped), so filtering on the alert_type string would
# wrongly silence real infra alerts. Two explicit exceptions override the device
# rule:
#   - CLIENT_KEPT_ALERT_TYPES — kept even when raised on an LR: the operator
#     must act on them. (Currently empty.)
#   - INFRA_DEVICE_SUPPRESSED_ALERT_TYPES — dropped even when raised on an infra
#     device:
#       * cpe_disconnected is a Rocket-side signal that a subscriber CPE
#         vanished, i.e. client-side churn, not our outage.
#       * rocket_client_overload (Rocket saturation) is owned by the /capacity
#         page (policy 2026-06-25) — surfaced there, never an /incidents row.
#       * lr_bridge_mode_misconfig (LR in bridge mode) is owned by the /access
#         page (policy 2026-06-25) — surfaced there, never an /incidents row.
CLIENT_RULE_CATEGORY: str = "lr"

CLIENT_KEPT_ALERT_TYPES: frozenset[str] = frozenset()

INFRA_DEVICE_SUPPRESSED_ALERT_TYPES: frozenset[str] = frozenset({
    AT_CPE_DISCONNECTED,
    AT_ROCKET_CLIENT_OVERLOAD,
    AT_LR_BRIDGE_MODE_MISCONFIG,
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
