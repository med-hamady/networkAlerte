"""
Alert policy registry — single source of truth for operational metadata
attached to each alert_type:

    severity            : default severity (or DYNAMIC if the rule decides)
    recommended_action  : human-readable next steps for the on-call team
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
    AT_RADIO_INTERFACE_DOWN,
    AT_RADIO_LINK_DEGRADED,
    AT_ROCKET_DOWN,
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
    recommended_action: str
    notify_immediately: bool
    channels: tuple[str, ...]
    groupable: bool = False
    recovery_notification: bool = True


# ---------------------------------------------------------------------------
# Default channel sets
# ---------------------------------------------------------------------------

_CHANNELS_CRITICAL: tuple[str, ...] = (
    AlertChannel.SLACK, AlertChannel.EMAIL, AlertChannel.WEBHOOK, AlertChannel.WHATSAPP,
)
_CHANNELS_WARNING: tuple[str, ...] = (
    AlertChannel.SLACK, AlertChannel.EMAIL, AlertChannel.WEBHOOK,
)
_CHANNELS_INFO: tuple[str, ...] = (
    AlertChannel.WEBHOOK,
)


# ---------------------------------------------------------------------------
# Registry — every alert_type the system can raise
# ---------------------------------------------------------------------------

ALERT_POLICIES: dict[str, AlertPolicy] = {

    # --- Device availability (critical, immediate) ---------------------------

    AT_ROCKET_DOWN: AlertPolicy(
        alert_type=AT_ROCKET_DOWN,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier le switch · Vérifier le port du Rocket sur le switch · "
            "Vérifier l'alimentation du Rocket · Vérifier l'accessibilité locale du Rocket"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_LR_DOWN: AlertPolicy(
        alert_type=AT_LR_DOWN,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier alimentation du LR · Vérifier la liaison radio Rocket↔LR · "
            "Vérifier signal/CINR récents"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_SWITCH_DOWN: AlertPolicy(
        alert_type=AT_SWITCH_DOWN,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier alimentation du switch · Vérifier uplink · "
            "Vérifier accessibilité locale · Vérifier si plusieurs équipements sont impactés"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_DEVICE_UNREACHABLE: AlertPolicy(
        alert_type=AT_DEVICE_UNREACHABLE,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier alimentation et accessibilité réseau de l'équipement · "
            "Vérifier le segment réseau · Vérifier les équipements en amont"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),

    # --- Interface & local link (critical, immediate) ------------------------

    AT_RADIO_INTERFACE_DOWN: AlertPolicy(
        alert_type=AT_RADIO_INTERFACE_DOWN,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier ath0 du Rocket · Redémarrer la radio si accessible · "
            "Vérifier configuration radio"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_ETH0_DOWN: AlertPolicy(
        alert_type=AT_ETH0_DOWN,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier le câble Rocket↔switch · Vérifier le port du switch · "
            "Vérifier le SFP/patch si applicable"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_CPE_DISCONNECTED: AlertPolicy(
        alert_type=AT_CPE_DISCONNECTED,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier que le LTU LR est allumé · Vérifier la liaison radio · "
            "Vérifier l'association CPE · Vérifier les dernières métriques radio"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),

    # --- Radio quality (warning, deferred unless escalated) ------------------

    AT_SIGNAL_LOW: AlertPolicy(
        alert_type=AT_SIGNAL_LOW,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier orientation antenne · Vérifier obstacles ou interférences · "
            "Vérifier si l'impact est local ou global"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_CINR_LOW: AlertPolicy(
        alert_type=AT_CINR_LOW,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier interférences · Vérifier qualité spectrale · "
            "Corréler avec signal_low/ccq_low"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_CCQ_LOW: AlertPolicy(
        alert_type=AT_CCQ_LOW,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier la qualité radio · Vérifier CINR · "
            "Vérifier signal · Vérifier stabilité du lien"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_RADIO_LINK_DEGRADED: AlertPolicy(
        alert_type=AT_RADIO_LINK_DEGRADED,
        severity=Severity.DYNAMIC,    # rule chooses warning OR critical
        recommended_action=(
            "Vérifier signal, CINR et CCQ ensemble · "
            "Identifier la métrique la plus dégradée · Vérifier interférences"
        ),
        notify_immediately=False,     # overridden when severity == critical
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),

    # --- Performance (warning) -----------------------------------------------

    AT_CAPACITY_LOW: AlertPolicy(
        alert_type=AT_CAPACITY_LOW,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier débit théorique vs réel · Vérifier contention · "
            "Vérifier qualité radio"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_HIGH_RX_TX_ERRORS: AlertPolicy(
        alert_type=AT_HIGH_RX_TX_ERRORS,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier qualité du câble · Vérifier interférences radio · "
            "Vérifier saturation interface"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_THROUGHPUT_ANOMALY: AlertPolicy(
        alert_type=AT_THROUGHPUT_ANOMALY,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier si chute soudaine du trafic · Comparer à la baseline · "
            "Vérifier les métriques radio"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),

    # --- Radio quality UL — uplink (warning, deferred) -----------------------

    AT_CCQ_UL_LOW: AlertPolicy(
        alert_type=AT_CCQ_UL_LOW,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier la qualité radio UL · Vérifier CINR UL · "
            "Vérifier signal côté CPE · Vérifier stabilité du lien montant"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_CINR_UL_LOW: AlertPolicy(
        alert_type=AT_CINR_UL_LOW,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier interférences côté CPE · Vérifier qualité spectrale UL · "
            "Corréler avec cinr_low/ccq_ul_low"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_CAPACITY_UL_LOW: AlertPolicy(
        alert_type=AT_CAPACITY_UL_LOW,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier débit UL théorique vs réel · Vérifier contention montante · "
            "Vérifier qualité radio UL"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),

    # --- Power & infrastructure ----------------------------------------------

    AT_UISP_POWER_UNREACH: AlertPolicy(
        alert_type=AT_UISP_POWER_UNREACH,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier alimentation UISP Power · Vérifier accessibilité réseau · "
            "Vérifier API HTTPS"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_BATTERY_LOW_WARN: AlertPolicy(
        alert_type=AT_BATTERY_LOW_WARN,
        severity=Severity.WARNING,
        recommended_action=(
            "Vérifier état batterie UISP Power · Vérifier secteur · "
            "Anticiper remplacement"
        ),
        notify_immediately=False,
        channels=_CHANNELS_WARNING,
        groupable=True,
    ),
    AT_BATTERY_LOW_CRIT: AlertPolicy(
        alert_type=AT_BATTERY_LOW_CRIT,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Intervention immédiate sur l'UPS · Vérifier autonomie restante · "
            "Préparer plan de bascule"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_VOLTAGE_ANOMALY: AlertPolicy(
        alert_type=AT_VOLTAGE_ANOMALY,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Tension hors plage (< 20 V ou > 56 V) — risque matériel · "
            "Vérifier alimentation secteur · Vérifier batterie UISP Power · "
            "Vérifier câblage et disjoncteur"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
        groupable=False,
    ),
    AT_TRANSIT_UNAVAILABLE: AlertPolicy(
        alert_type=AT_TRANSIT_UNAVAILABLE,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier opérateur transit · Vérifier route par défaut · "
            "Vérifier équipement de bordure"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_LR_NO_TRANSIT: AlertPolicy(
        alert_type=AT_LR_NO_TRANSIT,
        severity=Severity.CRITICAL,
        recommended_action=(
            "LTU LR joignable localement (SSH OK) mais sans internet via le lien radio · "
            "Vérifier la liaison radio Rocket↔LR · Vérifier la route par défaut du LR · "
            "Vérifier si le Rocket a une connectivité internet"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_SWITCH_PORT_DOWN: AlertPolicy(
        alert_type=AT_SWITCH_PORT_DOWN,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Vérifier câble du port concerné · Vérifier équipement en bout de lien · "
            "Vérifier configuration du port"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),
    AT_SWITCH_PORT_SPEED_LOW: AlertPolicy(
        alert_type=AT_SWITCH_PORT_SPEED_LOW,
        severity=Severity.CRITICAL,
        recommended_action=(
            "Port UP mais vitesse < 1000 Mbps (lien dégradé) · "
            "Vérifier qualité du câble RJ45 · Vérifier auto-négociation des deux côtés · "
            "Remplacer le câble ou le transceiver si nécessaire"
        ),
        notify_immediately=True,
        channels=_CHANNELS_CRITICAL,
    ),

    # --- Auto-discovery lifecycle (informational) ----------------------------

    AT_LR_DISCOVERED: AlertPolicy(
        alert_type=AT_LR_DISCOVERED,
        severity=Severity.INFO,
        recommended_action=(
            "Nouveau LTU LR détecté en peer d'un Rocket — vérifier les informations "
            "(nom, localisation, MAC) et compléter si nécessaire dans le dashboard."
        ),
        notify_immediately=False,
        channels=(AlertChannel.WEBHOOK, AlertChannel.SLACK),
        groupable=False,
        recovery_notification=False,
    ),
    AT_LR_IP_CHANGED: AlertPolicy(
        alert_type=AT_LR_IP_CHANGED,
        severity=Severity.WARNING,
        recommended_action=(
            "L'adresse IP d'un LR a changé (MAC inchangée) — vérifier la cohérence "
            "DHCP/configuration · Vérifier qu'aucune session SSH/API ne pointe vers "
            "l'ancienne IP"
        ),
        notify_immediately=False,
        channels=(AlertChannel.WEBHOOK, AlertChannel.SLACK),
        groupable=False,
        recovery_notification=False,
    ),
    AT_LR_REASSIGNED: AlertPolicy(
        alert_type=AT_LR_REASSIGNED,
        severity=Severity.WARNING,
        recommended_action=(
            "Un LR a basculé vers un autre Rocket — vérifier la couverture radio · "
            "Vérifier si le bascule est volontaire ou symptôme d'une panne · "
            "Vérifier la qualité du nouveau lien"
        ),
        notify_immediately=True,
        channels=_CHANNELS_WARNING,
        groupable=False,
        recovery_notification=False,
    ),
    AT_LR_DISAPPEARED: AlertPolicy(
        alert_type=AT_LR_DISAPPEARED,
        severity=Severity.WARNING,
        recommended_action=(
            "Un LR auto-découvert n'apparaît plus dans la liste des peers du Rocket · "
            "Vérifier alimentation et liaison radio du LR · "
            "Comparer avec les incidents lr_down/cpe_disconnected"
        ),
        notify_immediately=True,
        channels=_CHANNELS_WARNING,
        groupable=False,
        recovery_notification=True,
    ),
}


# ---------------------------------------------------------------------------
# Fallback for unknown alert_types
# ---------------------------------------------------------------------------

_FALLBACK_POLICY = AlertPolicy(
    alert_type="_unknown",
    severity=Severity.WARNING,
    recommended_action="Investigation requise — type d'alerte non répertorié dans la policy.",
    notify_immediately=False,
    channels=(AlertChannel.WEBHOOK,),
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
# Severity, alert_type and recommended_action are intentionally excluded:
# overrides are about *how* an alert is delivered, not about reclassifying it.
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
