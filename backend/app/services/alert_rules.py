"""
Alert rules — pure Python evaluation logic (no DB access).

Each rule inspects a metrics dict and returns an AlertEvalResult.
severity=None means the condition is healthy → the engine will resolve
any open incident for that alert_type.

Metrics dict keys (sourced from SNMP + LTU API + ping):
  radio_if_up       : 1.0/0.0 — radio interface operational status
  eth_if_up         : 1.0/0.0 — eth0 interface operational status
  peer_count        : int     — number of connected CPEs (0 = none)
  peer_uptime_s     : float   — CPE uptime in seconds (0 = just connected / absent)
  signal_dbm        : float   — AP-side DL signal (dBm)
  cinr_db           : float   — DL CINR (dB)
  ccq_pct           : float   — DL CCQ (%)
  ul_ccq_pct        : float   — UL CCQ (%)
  tx_rate_mbps      : float   — current DL throughput (Mbps)
  rx_rate_mbps      : float   — current UL throughput (Mbps)
  tx_ideal_mbps     : float   — rated DL capacity (Mbps)
  rx_ideal_mbps     : float   — rated UL capacity (Mbps)
  total_capacity_mbps : float — capacity.combined (UI "Total Capacity"); informational
  link_potential_pct  : float — mean(linkScore dl/ul) ≈ UI "Link Potential"; informational
  local_rx_rate_idx   : float — mcs.txRate, downlink "Nx" (UI "Local RX Data Rate"); informational
  remote_rx_rate_idx  : float — mcs.rxRate, uplink "Nx" (UI "Remote RX Data Rate"); informational
  radio_in_errors   : float   — cumulative RX error counter
  radio_out_errors  : float   — cumulative TX error counter
  radio_rx_bytes    : float   — cumulative RX byte counter
  radio_tx_bytes    : float   — cumulative TX byte counter
  prev_in_errors    : float   — previous cycle's in_errors (from AlertState)
  prev_out_errors   : float   — previous cycle's out_errors
  prev_rx_bytes     : float   — previous cycle's rx_bytes
  prev_tx_bytes     : float   — previous cycle's tx_bytes
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.radio_thresholds import (
    signal_critical_threshold,
    signal_distance_band_label,
    signal_warning_threshold,
)


@dataclass
class AlertEvalResult:
    """Result of a single rule evaluation."""

    alert_type: str
    severity: str | None  # None = OK, engine resolves open alert
    metric_name: str | None
    metric_value: float | None
    threshold_value: float | None
    message: str
    skip: bool = False  # True = metric absent, engine ignores (no increment, no reset)


# ---------------------------------------------------------------------------
# Base rule
# ---------------------------------------------------------------------------


class AlertRule:
    """Base class for all alert rules."""

    alert_type: str
    # Anti-flap: number of consecutive bad cycles required (0 = immediate)
    failure_threshold: int = 0

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Famille B — Interface et lien local
# ---------------------------------------------------------------------------


class RadioInterfaceDownRule(AlertRule):
    """Détecte l'interface radio (ath0) hors service via SNMP ifOperStatus."""

    alert_type = "radio_interface_down"
    failure_threshold = 0  # immédiat — panne matérielle franche

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        radio_up = metrics.get("radio_if_up")
        if radio_up is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="radio_if_up",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )
        if radio_up == 0.0:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="radio_if_up",
                metric_value=0.0,
                threshold_value=1.0,
                message=f"ALERTE CRITIQUE : interface radio (ath0) hors service sur {device_name}",
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="radio_if_up",
            metric_value=1.0,
            threshold_value=None,
            message=f"RECOVERY : interface radio de {device_name} de nouveau opérationnelle",
        )


class Eth0DownRule(AlertRule):
    """Détecte le lien Ethernet (eth0) hors service via SNMP ifOperStatus."""

    alert_type = "eth0_down"
    failure_threshold = 0  # immédiat

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        eth_up = metrics.get("eth_if_up")
        if eth_up is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="eth_if_up",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )
        if eth_up == 0.0:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="eth_if_up",
                metric_value=0.0,
                threshold_value=1.0,
                message=(
                    f"ALERTE CRITIQUE : interface Ethernet (eth0) de {device_name} DOWN. "
                    f"Câble débranché ou port switch HS."
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="eth_if_up",
            metric_value=1.0,
            threshold_value=None,
            message=f"RECOVERY : lien Ethernet de {device_name} de nouveau actif",
        )


class CPEDisconnectedRule(AlertRule):
    """Détecte l'absence de CPE associé au LTU Rocket (peer_count == 0)."""

    alert_type = "cpe_disconnected"
    failure_threshold = 0  # immédiat

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        peer_count = metrics.get("peer_count")
        if peer_count is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="peer_count",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )
        if peer_count == 0:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="peer_count",
                metric_value=0.0,
                threshold_value=1.0,
                message=(
                    f"ALERTE CRITIQUE : LTU LR déconnecté du Rocket {device_name} "
                    f"— aucun CPE associé détecté"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="peer_count",
            metric_value=float(peer_count),
            threshold_value=None,
            message=f"RECOVERY : CPE reconnecté au Rocket {device_name}",
        )


# ---------------------------------------------------------------------------
# Famille C — Qualité radio
# ---------------------------------------------------------------------------


class SignalLowRule(AlertRule):
    """Signal radio faible (dBm) — seuils configurables."""

    alert_type = "signal_low"

    @property
    def failure_threshold(self):  # type: ignore[override]
        return 2  # will be overridden from settings in engine

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        signal = metrics.get("signal_dbm")
        if signal is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="signal_dbm",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )
        distance_m = metrics.get("distance_m")
        warn_dbm = signal_warning_threshold(distance_m, settings.signal_warning_dbm)
        crit_dbm = signal_critical_threshold(distance_m, settings.signal_critical_dbm)
        band = signal_distance_band_label(distance_m)

        # Hysteresis on a tolerance band (dBm is negative, lower = worse):
        #  - to OPEN : signal must be clearly below the threshold
        #    (threshold − margin), so a 1-2 dBm dip never fires.
        #  - to RESOLVE : while an incident is already open we compare
        #    against the NOMINAL threshold, so it only clears on a genuine
        #    return to nominal — no flapping in the margin band.
        # The engine injects the set of currently-open alert_types.
        margin = float(settings.signal_tolerance_dbm)
        is_open = self.alert_type in (metrics.get("_open_alert_types") or ())
        crit_line = crit_dbm if is_open else crit_dbm - margin
        warn_line = warn_dbm if is_open else warn_dbm - margin

        if signal < crit_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="signal_dbm",
                metric_value=signal,
                threshold_value=float(crit_line),
                message=(
                    f"ALERTE CRITIQUE : signal radio faible sur {device_name} : "
                    f"{signal:.1f} dBm (seuil critique {crit_dbm:.0f} dBm, "
                    f"bande de tolérance {margin:.0f} dBm, {band})"
                ),
            )
        if signal < warn_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="signal_dbm",
                metric_value=signal,
                threshold_value=float(warn_line),
                message=(
                    f"ALERTE WARNING : signal radio dégradé sur {device_name} : "
                    f"{signal:.1f} dBm (seuil warning {warn_dbm:.0f} dBm, "
                    f"bande de tolérance {margin:.0f} dBm, {band})"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="signal_dbm",
            metric_value=signal,
            threshold_value=None,
            message=f"RECOVERY : signal radio de {device_name} de nouveau nominal ({signal:.1f} dBm)",
        )


class CINRLowRule(AlertRule):
    """CINR faible (dB) — seuils configurables."""

    alert_type = "cinr_low"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        cinr = metrics.get("cinr_db")
        if cinr is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="cinr_db",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )
        if cinr < settings.cinr_critical_db:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="cinr_db",
                metric_value=cinr,
                threshold_value=settings.cinr_critical_db,
                message=(
                    f"ALERTE CRITIQUE : CINR DL faible sur {device_name} : "
                    f"{cinr:.1f} dB (seuil critique {settings.cinr_critical_db} dB)"
                ),
            )
        if cinr < settings.cinr_warning_db:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="cinr_db",
                metric_value=cinr,
                threshold_value=settings.cinr_warning_db,
                message=(
                    f"ALERTE WARNING : CINR DL faible sur {device_name} : "
                    f"{cinr:.1f} dB (seuil warning {settings.cinr_warning_db} dB)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="cinr_db",
            metric_value=cinr,
            threshold_value=None,
            message=f"RECOVERY : CINR de {device_name} de nouveau nominal ({cinr:.1f} dB)",
        )


class CCQLowRule(AlertRule):
    """CCQ faible (%) — seuils configurables."""

    alert_type = "ccq_low"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        ccq = metrics.get("ccq_pct")
        if ccq is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="ccq_pct",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )
        if ccq < settings.ccq_critical_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="ccq_pct",
                metric_value=ccq,
                threshold_value=float(settings.ccq_critical_pct),
                message=(
                    f"ALERTE CRITIQUE : CCQ DL faible sur {device_name} : "
                    f"{ccq:.1f}% (seuil critique {settings.ccq_critical_pct}%)"
                ),
            )
        if ccq < settings.ccq_warning_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="ccq_pct",
                metric_value=ccq,
                threshold_value=float(settings.ccq_warning_pct),
                message=(
                    f"ALERTE WARNING : CCQ DL faible sur {device_name} : "
                    f"{ccq:.1f}% (seuil warning {settings.ccq_warning_pct}%)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="ccq_pct",
            metric_value=ccq,
            threshold_value=None,
            message=f"RECOVERY : CCQ de {device_name} de nouveau nominal ({ccq:.1f}%)",
        )


class RadioLinkDegradedRule(AlertRule):
    """
    Dégradation composite du lien radio.

    Déclenche si au moins 2 parmi (signal, CINR, CCQ) sont en état
    warning ou critical selon leurs seuils respectifs.
    N'évalue que si ces métriques sont disponibles.
    """

    alert_type = "radio_link_degraded"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        bad_metrics: list[str] = []

        signal = metrics.get("signal_dbm")
        cinr = metrics.get("cinr_db")
        ccq = metrics.get("ccq_pct")

        if signal is None and cinr is None and ccq is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name=None,
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )

        distance_m = metrics.get("distance_m")
        sig_warn = signal_warning_threshold(distance_m, settings.signal_warning_dbm)
        sig_crit = signal_critical_threshold(distance_m, settings.signal_critical_dbm)

        if signal is not None and signal < sig_warn:
            bad_metrics.append(f"signal={signal:.1f}dBm")

        if cinr is not None and cinr < settings.cinr_warning_db:
            bad_metrics.append(f"CINR={cinr:.1f}dB")

        if ccq is not None and ccq < settings.ccq_warning_pct:
            bad_metrics.append(f"CCQ={ccq:.1f}%")

        if len(bad_metrics) >= 2:
            worst_sev = "warning"
            if (
                (signal is not None and signal < sig_crit)
                or (cinr is not None and cinr < settings.cinr_critical_db)
                or (ccq is not None and ccq < settings.ccq_critical_pct)
            ):
                worst_sev = "critical"

            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=worst_sev,
                metric_name=None,
                metric_value=None,
                threshold_value=None,
                message=(
                    f"ALERTE {worst_sev.upper()} : dégradation radio composite sur {device_name} — "
                    + ", ".join(bad_metrics)
                ),
            )

        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name=None,
            metric_value=None,
            threshold_value=None,
            message=f"RECOVERY : lien radio de {device_name} de nouveau nominal",
        )


# ---------------------------------------------------------------------------
# Famille D — Performance du lien
# ---------------------------------------------------------------------------


class CapacityLowRule(AlertRule):
    """
    Capacité de liaison anormalement faible.

    Évalue tx_rate_mbps / tx_ideal_mbps. Si la capacité idéale n'est pas
    disponible ou vaut 0, la règle ne déclenche pas (pas de baseline).
    """

    alert_type = "capacity_low"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        tx_rate = metrics.get("tx_rate_mbps")
        tx_ideal = metrics.get("tx_ideal_mbps")

        if tx_rate is None or tx_ideal is None or tx_ideal <= 0:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="tx_rate_pct",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )

        capacity_pct = (tx_rate / tx_ideal) * 100.0

        if capacity_pct < settings.capacity_low_critical_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="tx_rate_pct",
                metric_value=round(capacity_pct, 1),
                threshold_value=settings.capacity_low_critical_pct,
                message=(
                    f"ALERTE CRITIQUE : capacité DL très faible sur {device_name} : "
                    f"{capacity_pct:.1f}% de la capacité nominale "
                    f"({tx_rate:.1f}/{tx_ideal:.1f} Mbps)"
                ),
            )
        if capacity_pct < settings.capacity_low_warning_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="tx_rate_pct",
                metric_value=round(capacity_pct, 1),
                threshold_value=settings.capacity_low_warning_pct,
                message=(
                    f"ALERTE WARNING : capacité DL dégradée sur {device_name} : "
                    f"{capacity_pct:.1f}% de la capacité nominale "
                    f"({tx_rate:.1f}/{tx_ideal:.1f} Mbps)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="tx_rate_pct",
            metric_value=round(capacity_pct, 1),
            threshold_value=None,
            message=f"RECOVERY : capacité DL de {device_name} de nouveau nominale",
        )


class HighRxTxErrorsRule(AlertRule):
    """
    Taux d'erreurs RX/TX anormalement élevé.

    Calcule le delta d'erreurs et de bytes entre deux cycles consécutifs
    (valeurs précédentes stockées dans AlertState.last_metric_value via
    metrics["prev_in_errors"] etc. injectées par l'engine).
    Si les données précédentes ne sont pas disponibles (premier cycle),
    la règle ne déclenche pas.
    """

    alert_type = "high_rx_tx_errors"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        in_errors = metrics.get("radio_in_errors")
        out_errors = metrics.get("radio_out_errors")
        rx_bytes = metrics.get("radio_rx_bytes")
        tx_bytes = metrics.get("radio_tx_bytes")

        prev_in = metrics.get("prev_in_errors")
        prev_out = metrics.get("prev_out_errors")
        prev_rx = metrics.get("prev_rx_bytes")
        prev_tx = metrics.get("prev_tx_bytes")

        # Need both current and previous values to compute deltas
        if None in (in_errors, out_errors, rx_bytes, tx_bytes, prev_in, prev_out, prev_rx, prev_tx):
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="error_rate_pct",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )

        delta_errors = max(0.0, (in_errors - prev_in) + (out_errors - prev_out))
        delta_bytes = max(0.0, (rx_bytes - prev_rx) + (tx_bytes - prev_tx))

        # Avoid division by zero — if no traffic, don't alert
        if delta_bytes < 1000:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="error_rate_pct",
                metric_value=0.0,
                threshold_value=None,
                message=f"RECOVERY : taux d'erreurs RX/TX de {device_name} de nouveau normal (pas de trafic significatif)",
            )

        error_rate = (delta_errors / delta_bytes) * 100.0

        if error_rate >= settings.rx_tx_error_critical_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="error_rate_pct",
                metric_value=round(error_rate, 2),
                threshold_value=settings.rx_tx_error_critical_pct,
                message=(
                    f"ALERTE CRITIQUE : taux d'erreurs RX/TX élevé sur {device_name} : "
                    f"{error_rate:.2f}% (seuil {settings.rx_tx_error_critical_pct}%)"
                ),
            )
        if error_rate >= settings.rx_tx_error_warning_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="error_rate_pct",
                metric_value=round(error_rate, 2),
                threshold_value=settings.rx_tx_error_warning_pct,
                message=(
                    f"ALERTE WARNING : taux d'erreurs RX/TX sur {device_name} : "
                    f"{error_rate:.2f}% (seuil {settings.rx_tx_error_warning_pct}%)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="error_rate_pct",
            metric_value=round(error_rate, 2),
            threshold_value=None,
            message=f"RECOVERY : taux d'erreurs RX/TX de {device_name} de nouveau normal",
        )


# ---------------------------------------------------------------------------
# Famille C bis — Qualité radio UL (uplink)
# ---------------------------------------------------------------------------


class CCQLowULRule(AlertRule):
    """CCQ uplink faible (%) — même seuils que le DL."""

    alert_type = "ccq_ul_low"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        ccq_ul = metrics.get("ul_ccq_pct")
        if ccq_ul is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="ul_ccq_pct",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )
        if ccq_ul < settings.ccq_critical_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="ul_ccq_pct",
                metric_value=ccq_ul,
                threshold_value=float(settings.ccq_critical_pct),
                message=(
                    f"ALERTE CRITIQUE : CCQ UL faible sur {device_name} : "
                    f"{ccq_ul:.1f}% (seuil critique {settings.ccq_critical_pct}%)"
                ),
            )
        if ccq_ul < settings.ccq_warning_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="ul_ccq_pct",
                metric_value=ccq_ul,
                threshold_value=float(settings.ccq_warning_pct),
                message=(
                    f"ALERTE WARNING : CCQ UL faible sur {device_name} : "
                    f"{ccq_ul:.1f}% (seuil warning {settings.ccq_warning_pct}%)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="ul_ccq_pct",
            metric_value=ccq_ul,
            threshold_value=None,
            message=f"RECOVERY : CCQ UL de {device_name} de nouveau nominal ({ccq_ul:.1f}%)",
        )


class CINRLowULRule(AlertRule):
    """CINR uplink faible (dB) — même seuils que le DL."""

    alert_type = "cinr_ul_low"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        cinr_ul = metrics.get("ul_cinr_db")
        if cinr_ul is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="ul_cinr_db",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )
        if cinr_ul < settings.cinr_critical_db:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="ul_cinr_db",
                metric_value=cinr_ul,
                threshold_value=settings.cinr_critical_db,
                message=(
                    f"ALERTE CRITIQUE : CINR UL faible sur {device_name} : "
                    f"{cinr_ul:.1f} dB (seuil critique {settings.cinr_critical_db} dB)"
                ),
            )
        if cinr_ul < settings.cinr_warning_db:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="ul_cinr_db",
                metric_value=cinr_ul,
                threshold_value=settings.cinr_warning_db,
                message=(
                    f"ALERTE WARNING : CINR UL faible sur {device_name} : "
                    f"{cinr_ul:.1f} dB (seuil warning {settings.cinr_warning_db} dB)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="ul_cinr_db",
            metric_value=cinr_ul,
            threshold_value=None,
            message=f"RECOVERY : CINR UL de {device_name} de nouveau nominal ({cinr_ul:.1f} dB)",
        )


class CapacityLowULRule(AlertRule):
    """
    Capacité uplink anormalement faible.

    Évalue rx_rate_mbps / rx_ideal_mbps (symétrique de CapacityLowRule sur le DL).
    """

    alert_type = "capacity_ul_low"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        rx_rate = metrics.get("rx_rate_mbps")
        rx_ideal = metrics.get("rx_ideal_mbps")

        if rx_rate is None or rx_ideal is None or rx_ideal <= 0:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="rx_rate_pct",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )

        capacity_pct = (rx_rate / rx_ideal) * 100.0

        if capacity_pct < settings.capacity_low_critical_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="rx_rate_pct",
                metric_value=round(capacity_pct, 1),
                threshold_value=settings.capacity_low_critical_pct,
                message=(
                    f"ALERTE CRITIQUE : capacité UL très faible sur {device_name} : "
                    f"{capacity_pct:.1f}% de la capacité nominale "
                    f"({rx_rate:.1f}/{rx_ideal:.1f} Mbps)"
                ),
            )
        if capacity_pct < settings.capacity_low_warning_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="rx_rate_pct",
                metric_value=round(capacity_pct, 1),
                threshold_value=settings.capacity_low_warning_pct,
                message=(
                    f"ALERTE WARNING : capacité UL dégradée sur {device_name} : "
                    f"{capacity_pct:.1f}% de la capacité nominale "
                    f"({rx_rate:.1f}/{rx_ideal:.1f} Mbps)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="rx_rate_pct",
            metric_value=round(capacity_pct, 1),
            threshold_value=None,
            message=f"RECOVERY : capacité UL de {device_name} de nouveau nominale",
        )


# ---------------------------------------------------------------------------
# Famille D — Anomalie de débit (throughput_anomaly)
# ---------------------------------------------------------------------------


class ThroughputAnomalyRule(AlertRule):
    """
    Détecte une chute soudaine du débit par rapport à la baseline mobile.

    L'engine injecte `tx_rate_ema_mbps` (moyenne exponentielle calculée sur
    les derniers cycles) avant d'appeler evaluate(). Si le débit actuel est
    inférieur à (EMA × (1 - drop_pct/100)) et que la baseline est
    suffisamment haute (≥ min_mbps), une alerte warning est émise.
    """

    alert_type = "throughput_anomaly"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        tx_rate = metrics.get("tx_rate_mbps")
        ema = metrics.get("tx_rate_ema_mbps")

        # Need both current rate and a baseline to compare against
        if tx_rate is None or ema is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="tx_drop_pct",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )

        # Don't alert on nearly-idle links — no meaningful baseline
        if ema < settings.throughput_anomaly_min_mbps:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="tx_drop_pct",
                metric_value=None,
                threshold_value=None,
                message=f"RECOVERY : débit DL de {device_name} de nouveau nominal (baseline insuffisante pour détecter anomalie)",
            )

        drop_pct = max(0.0, (ema - tx_rate) / ema * 100.0)

        if drop_pct >= settings.throughput_anomaly_drop_pct:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="tx_drop_pct",
                metric_value=round(drop_pct, 1),
                threshold_value=settings.throughput_anomaly_drop_pct,
                message=(
                    f"ALERTE WARNING : chute de débit DL sur {device_name} : "
                    f"{tx_rate:.1f} Mbps vs baseline {ema:.1f} Mbps "
                    f"(−{drop_pct:.0f}%)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="tx_drop_pct",
            metric_value=round(drop_pct, 1),
            threshold_value=None,
            message=f"RECOVERY : débit DL de {device_name} de nouveau nominal ({tx_rate:.1f} Mbps)",
        )


class LrLinkSubstandardRule(AlertRule):
    """Lien client sous le seuil — incident CONSOLIDÉ (per-LR).

    Un seul incident critique si AU MOINS un des planchers est franchi :
      - link_potential_pct  < settings.lr_link_potential_min_pct   (60 %)
      - total_capacity_mbps < settings.lr_total_capacity_min_mbps   (60 Mbps)
      - local_rx_rate_idx   < settings.lr_rx_rate_min_idx           (×6)
      - remote_rx_rate_idx  < settings.lr_rx_rate_min_idx           (×6)

    Le message liste les métriques fautives. Seules les métriques présentes
    sont évaluées ; si aucune n'est rapportée → skip (pas de data, on
    n'incrémente pas l'anti-flap). Anti-flap : 5 cycles consécutifs
    (settings.lr_link_substandard_failure_threshold) car ces métriques sont
    très volatiles.
    """

    alert_type = "lr_link_substandard"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        # (label, value, floor, unit, fmt)
        checks = [
            ("Potentiel du lien", metrics.get("link_potential_pct"),
             settings.lr_link_potential_min_pct, "%", "{:.0f}"),
            ("Capacité totale", metrics.get("total_capacity_mbps"),
             settings.lr_total_capacity_min_mbps, " Mbps", "{:.1f}"),
            ("Débit RX local", metrics.get("local_rx_rate_idx"),
             settings.lr_rx_rate_min_idx, "×", "{:.0f}"),
            ("Débit RX distant", metrics.get("remote_rx_rate_idx"),
             settings.lr_rx_rate_min_idx, "×", "{:.0f}"),
        ]
        present = [(n, v, fl, u, f) for (n, v, fl, u, f) in checks if v is not None]
        if not present:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="lr_link_floors",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )

        breached = [(n, v, fl, u, f) for (n, v, fl, u, f) in present if v < fl]
        if breached:
            parts = [
                f"{n} {f.format(v)}{u} (plancher {f.format(fl)}{u})"
                for (n, v, fl, u, f) in breached
            ]
            n0, v0, fl0, _u0, _f0 = breached[0]
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="lr_link_floors",
                metric_value=round(float(v0), 1),
                threshold_value=float(fl0),
                message=(
                    f"ALERTE CRITIQUE : lien client dégradé sur {device_name} — "
                    + " ; ".join(parts)
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="lr_link_floors",
            metric_value=None,
            threshold_value=None,
            message=(
                f"RECOVERY : lien client de {device_name} repassé "
                f"au-dessus de tous les seuils"
            ),
        )


# ---------------------------------------------------------------------------
# Mapping device_type → règles applicables
# ---------------------------------------------------------------------------

_ROCKET_RULES: list[AlertRule] = [
    # Rocket-level alerts: derived from SNMP IF-MIB (radio_if_up, eth_if_up,
    # byte/error counters on the AP's radio interface) + peer_count from the
    # HTTP API (number of connected CPEs).
    RadioInterfaceDownRule(),
    Eth0DownRule(),
    CPEDisconnectedRule(),
    HighRxTxErrorsRule(),
    ThroughputAnomalyRule(),
]

_LR_RULES: list[AlertRule] = [
    # Per-LR alerts: every radio-quality metric reported by the parent Rocket
    # for that specific peer (signal, CCQ, CINR DL+UL, capacity, link grade).
    # Evaluated against each LR individually so issues on one link don't hide
    # behind a healthy peer[0].
    SignalLowRule(),
    CINRLowRule(),
    CCQLowRule(),
    CINRLowULRule(),
    CCQLowULRule(),
    RadioLinkDegradedRule(),
    CapacityLowRule(),
    CapacityLowULRule(),
    LrLinkSubstandardRule(),
]

_SWITCH_RULES: list[AlertRule] = [
    HighRxTxErrorsRule(),
]

# airMAX (airOS) devices: Enterprise MIB gives signal/CCQ/rate;
# no CPE peer discovery (different from LTU auto-discovery).
_AIRMAX_ROCKET_RULES: list[AlertRule] = [
    RadioInterfaceDownRule(),
    Eth0DownRule(),
    SignalLowRule(),
    CINRLowRule(),
    CCQLowRule(),
    RadioLinkDegradedRule(),
    HighRxTxErrorsRule(),
    ThroughputAnomalyRule(),
]

RULES_BY_DEVICE_TYPE: dict[str, list[AlertRule]] = {
    "ltu_rocket": _ROCKET_RULES,
    "lr": _LR_RULES,
    "uisp_switch": _SWITCH_RULES,
    "uisp_power": [],
    "airmax_rocket": _AIRMAX_ROCKET_RULES,
}

# Failure threshold per alert_type — resolved from settings in the engine
FAILURE_THRESHOLDS: dict[str, str] = {
    "signal_low": "signal_failure_threshold",
    "cinr_low": "cinr_failure_threshold",
    "ccq_low": "ccq_failure_threshold",
    "cinr_ul_low": "cinr_failure_threshold",
    "ccq_ul_low": "ccq_failure_threshold",
    "capacity_low": "capacity_failure_threshold",
    "capacity_ul_low": "capacity_failure_threshold",
    "high_rx_tx_errors": "error_failure_threshold",
    "radio_link_degraded": "radio_degraded_failure_threshold",
    "throughput_anomaly": "throughput_anomaly_failure_threshold",
    "lr_link_substandard": "lr_link_substandard_failure_threshold",
    # Immediate rules (threshold = 0) are not listed here
}


def get_rules_for_device(device_type: str) -> list[AlertRule]:
    """Return the ordered list of alert rules applicable to a device type."""
    return RULES_BY_DEVICE_TYPE.get(device_type, [])


def get_failure_threshold(alert_type: str, settings) -> int:
    """Return the number of consecutive bad cycles required for a given alert_type."""
    attr = FAILURE_THRESHOLDS.get(alert_type)
    if attr is None:
        return 0
    return getattr(settings, attr, 0)
