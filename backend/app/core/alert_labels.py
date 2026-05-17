"""
Human-readable French labels for alert_types and metric names.

Single source of truth used by:
  - alert_formatter (emails: subject, header, metric row, digest)
  - report_service (PDF/HTML reports)
  - any future channel that needs to show alerts to operators

Keep technical keys out of operator-facing output: every code path that would
render `ccq_ul_low` or `ul_ccq_pct` to a human goes through these helpers.
"""

from __future__ import annotations

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
    AT_LR_DISAPPEARED,
    AT_LR_DISCOVERED,
    AT_LR_DOWN,
    AT_LR_IP_CHANGED,
    AT_LR_NO_TRANSIT,
    AT_LR_REASSIGNED,
    AT_PING_INSTABILITY,
    AT_PING_LATENCY_HIGH,
    AT_RADIO_INTERFACE_DOWN,
    AT_RADIO_LINK_DEGRADED,
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
)

# ---------------------------------------------------------------------------
# Alert type labels — what the operator reads in emails / dashboards
# ---------------------------------------------------------------------------

ALERT_TYPE_LABELS: dict[str, str] = {
    # Availability
    AT_ROCKET_DOWN:          "Station de base (Rocket) hors ligne",
    AT_LR_DOWN:              "Client (LR) hors ligne",
    AT_SWITCH_DOWN:          "Switch hors ligne",
    AT_DEVICE_UNREACHABLE:   "Équipement injoignable",
    AT_AIRMAX_DOWN:          "Station de base airMAX hors ligne",

    # Interfaces & local link
    AT_RADIO_INTERFACE_DOWN: "Interface radio coupée",
    AT_ETH0_DOWN:            "Lien Ethernet coupé",
    AT_CPE_DISCONNECTED:     "Aucun client connecté à la station",

    # Radio quality (downlink — base → client)
    AT_SIGNAL_LOW:           "Signal radio faible",
    AT_CINR_LOW:             "Qualité du signal radio faible (CINR)",
    AT_CCQ_LOW:              "Qualité de connexion radio faible",
    AT_RADIO_LINK_DEGRADED:  "Lien radio dégradé",

    # Performance
    AT_CAPACITY_LOW:         "Capacité du lien radio faible",
    AT_HIGH_RX_TX_ERRORS:    "Taux d'erreurs réseau élevé",
    AT_THROUGHPUT_ANOMALY:   "Anomalie de débit détectée",

    # Radio quality UL (uplink — client → base)
    AT_CCQ_UL_LOW:           "Qualité de connexion côté client faible",
    AT_CINR_UL_LOW:          "Qualité du signal côté client faible (CINR)",
    AT_CAPACITY_UL_LOW:      "Capacité montante (côté client) faible",

    # Power & infra
    AT_UISP_POWER_UNREACH:   "UISP Power injoignable",
    AT_BATTERY_LOW_WARN:     "Batterie faible",
    AT_BATTERY_LOW_CRIT:     "Batterie critique",
    AT_VOLTAGE_ANOMALY:      "Tension d'alimentation anormale",

    # Switch
    AT_SWITCH_PORT_DOWN:     "Port du switch coupé",
    AT_SWITCH_PORT_SPEED_LOW: "Vitesse du port switch dégradée",

    # Transit
    AT_TRANSIT_UNAVAILABLE:  "Transit Internet indisponible",
    AT_LR_NO_TRANSIT:        "Client (LR) sans accès Internet",

    # Ping quality
    AT_PING_INSTABILITY:     "Latence ping instable",
    AT_PING_LATENCY_HIGH:    "Latence ping élevée",

    # Sécurité
    AT_SECURITY_ANOMALY:     "Volume anormal d'écritures API détecté",

    # Auto-discovery lifecycle
    AT_LR_DISCOVERED:        "Nouveau client (LR) détecté",
    AT_LR_IP_CHANGED:        "Adresse IP d'un client modifiée",
    AT_LR_REASSIGNED:        "Client (LR) reconnecté à une autre station",
    AT_LR_DISAPPEARED:       "Client (LR) disparu",
}


# ---------------------------------------------------------------------------
# Metric labels — what the operator reads in the "Métrique" row of emails
# ---------------------------------------------------------------------------

METRIC_LABELS: dict[str, str] = {
    # Radio quality
    "signal_dbm":      "Niveau de signal (dBm)",
    "cinr_db":         "Qualité signal/bruit CINR (dB)",
    "ccq_pct":         "Qualité de connexion CCQ (%)",
    "ul_ccq_pct":      "Qualité de connexion côté client (%)",
    "ul_cinr_db":      "Qualité signal/bruit côté client (dB)",

    # Performance
    "tx_rate_pct":     "Capacité d'émission (%)",
    "rx_rate_pct":     "Capacité de réception (%)",
    "error_rate_pct":  "Taux d'erreurs (%)",
    "tx_drop_pct":     "Taux de paquets perdus (%)",

    # Interfaces / counts
    "radio_if_up":     "État interface radio",
    "eth_if_up":       "État interface Ethernet",
    "peer_count":      "Nombre de clients connectés",

    # Ping
    "ping_latency_ms": "Latence ping (ms)",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def alert_type_label(alert_type: str | None) -> str:
    """Human label for an alert_type, falling back to the raw key."""
    if not alert_type:
        return "incident"
    return ALERT_TYPE_LABELS.get(alert_type, alert_type)


def metric_label(metric_name: str | None) -> str:
    """Human label for a metric_name, falling back to the raw key."""
    if not metric_name:
        return ""
    return METRIC_LABELS.get(metric_name, metric_name)
