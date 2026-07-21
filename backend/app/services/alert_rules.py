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
  dl_capacity_mbps  : float   — DL CAPACITY (Mbps) : what the link could carry
  ul_capacity_mbps  : float   — UL CAPACITY (Mbps)
  dl_throughput_mbps : float  — DL THROUGHPUT (Mbps) : traffic really flowing.
      Distinct from the capacity above by orders of magnitude on an idle link
      (86 Mbps of capacity for 0.3 Mbps of traffic) — never swap the two.
  ul_throughput_mbps : float  — UL THROUGHPUT (Mbps)
  dl_phy_rate_mbps  : float   — negotiated PHY modulation rate (airMAX-M/SNMP
      only) : neither a capacity nor a throughput
  ul_phy_rate_mbps  : float   — idem, uplink
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

# Famille radio des LR : utilisée par les règles per-LR (lr_link_substandard)
# pour choisir les seuils airMAX vs LTU. La règle lit `model_variant` injecté
# dans la dict metrics par l'engine — voir alert_engine._inject_lr_context.
_AIRMAX_LR_VARIANTS = frozenset({"litebeam_5ac", "litebeam_m5"})


def _is_airmax_lr(metrics: dict) -> bool:
    variant = metrics.get("model_variant")
    return variant in _AIRMAX_LR_VARIANTS


def _rocket_overload_threshold(
    settings, airmax: bool, width_mhz: float | None, override: int | None = None,
) -> int | None:
    """Client-count ceiling for a base-station Rocket.

    A manual ``override`` (operator-set on the Rocket) REPLACES the formula
    entirely and applies even when the channel width is unknown — that's the
    whole point of a manual ceiling. With no override, the ceiling is a formula:
    a per-family base at 10 MHz, then ``+rocket_overload_clients_per_10mhz``
    clients for every additional 10 MHz of channel width (live width rounded to
    the nearest 10 MHz step). Returns None when there is no override and the
    width is unknown or below 10 MHz (no defined ceiling → the rule does not
    fire)."""
    if override is not None:
        return int(override)
    if width_mhz is None:
        return None
    w10 = round(width_mhz / 10.0) * 10
    if w10 < 10:
        return None
    base = (
        settings.rocket_overload_clients_airmax_base
        if airmax
        else settings.rocket_overload_clients_ltu_base
    )
    steps = (w10 - 10) // 10
    return base + settings.rocket_overload_clients_per_10mhz * steps


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
        warn_dbm = float(settings.signal_warning_dbm)
        crit_dbm = float(settings.signal_critical_dbm)

        # Hysteresis on a tolerance band (dBm is negative, lower = worse):
        #  - to OPEN : signal must be clearly below the threshold
        #    (threshold − margin), so a small dip never fires.
        #  - to RESOLVE : while an incident is already open we compare
        #    against the NOMINAL threshold, so it only clears on a genuine
        #    return to nominal — no flapping in the margin band.
        # Defaults strict (margin=0), engine injects the open-incident set.
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
                    f"{signal:.1f} dBm (seuil critique {crit_dbm:.0f} dBm)"
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
                    f"{signal:.1f} dBm (seuil warning {warn_dbm:.0f} dBm)"
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
        # Hysteresis: open at threshold − margin, resolve only at the
        # nominal threshold while an incident is already open (no flapping
        # in the band). The engine injects the open-incident set.
        margin = float(settings.cinr_tolerance_db)
        is_open = self.alert_type in (metrics.get("_open_alert_types") or ())
        crit_line = settings.cinr_critical_db if is_open else settings.cinr_critical_db - margin
        warn_line = settings.cinr_warning_db if is_open else settings.cinr_warning_db - margin

        if cinr < crit_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="cinr_db",
                metric_value=cinr,
                threshold_value=float(crit_line),
                message=(
                    f"ALERTE CRITIQUE : CINR DL faible sur {device_name} : "
                    f"{cinr:.1f} dB (seuil critique {settings.cinr_critical_db} dB, "
                    f"bande de tolérance {margin:.0f} dB)"
                ),
            )
        if cinr < warn_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="cinr_db",
                metric_value=cinr,
                threshold_value=float(warn_line),
                message=(
                    f"ALERTE WARNING : CINR DL faible sur {device_name} : "
                    f"{cinr:.1f} dB (seuil warning {settings.cinr_warning_db} dB, "
                    f"bande de tolérance {margin:.0f} dB)"
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
        # Hysteresis: open at threshold − margin, resolve only at the
        # nominal threshold while an incident is already open.
        margin = float(settings.ccq_tolerance_pct)
        is_open = self.alert_type in (metrics.get("_open_alert_types") or ())
        crit_line = settings.ccq_critical_pct if is_open else settings.ccq_critical_pct - margin
        warn_line = settings.ccq_warning_pct if is_open else settings.ccq_warning_pct - margin

        if ccq < crit_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="ccq_pct",
                metric_value=ccq,
                threshold_value=float(crit_line),
                message=(
                    f"ALERTE CRITIQUE : CCQ DL faible sur {device_name} : "
                    f"{ccq:.1f}% (seuil critique {settings.ccq_critical_pct}%, "
                    f"bande de tolérance {margin:.0f}%)"
                ),
            )
        if ccq < warn_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="ccq_pct",
                metric_value=ccq,
                threshold_value=float(warn_line),
                message=(
                    f"ALERTE WARNING : CCQ DL faible sur {device_name} : "
                    f"{ccq:.1f}% (seuil warning {settings.ccq_warning_pct}%, "
                    f"bande de tolérance {margin:.0f}%)"
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

        sig_warn = float(settings.signal_warning_dbm)
        sig_crit = float(settings.signal_critical_dbm)

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
        # Hysteresis: open at threshold − margin, resolve only at the
        # nominal threshold while an incident is already open.
        margin = float(settings.ccq_tolerance_pct)
        is_open = self.alert_type in (metrics.get("_open_alert_types") or ())
        crit_line = settings.ccq_critical_pct if is_open else settings.ccq_critical_pct - margin
        warn_line = settings.ccq_warning_pct if is_open else settings.ccq_warning_pct - margin

        if ccq_ul < crit_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="ul_ccq_pct",
                metric_value=ccq_ul,
                threshold_value=float(crit_line),
                message=(
                    f"ALERTE CRITIQUE : CCQ UL faible sur {device_name} : "
                    f"{ccq_ul:.1f}% (seuil critique {settings.ccq_critical_pct}%, "
                    f"bande de tolérance {margin:.0f}%)"
                ),
            )
        if ccq_ul < warn_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="ul_ccq_pct",
                metric_value=ccq_ul,
                threshold_value=float(warn_line),
                message=(
                    f"ALERTE WARNING : CCQ UL faible sur {device_name} : "
                    f"{ccq_ul:.1f}% (seuil warning {settings.ccq_warning_pct}%, "
                    f"bande de tolérance {margin:.0f}%)"
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
        # Hysteresis: open at threshold − margin, resolve only at the
        # nominal threshold while an incident is already open.
        margin = float(settings.cinr_tolerance_db)
        is_open = self.alert_type in (metrics.get("_open_alert_types") or ())
        crit_line = settings.cinr_critical_db if is_open else settings.cinr_critical_db - margin
        warn_line = settings.cinr_warning_db if is_open else settings.cinr_warning_db - margin

        if cinr_ul < crit_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="ul_cinr_db",
                metric_value=cinr_ul,
                threshold_value=float(crit_line),
                message=(
                    f"ALERTE CRITIQUE : CINR UL faible sur {device_name} : "
                    f"{cinr_ul:.1f} dB (seuil critique {settings.cinr_critical_db} dB, "
                    f"bande de tolérance {margin:.0f} dB)"
                ),
            )
        if cinr_ul < warn_line:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name="ul_cinr_db",
                metric_value=cinr_ul,
                threshold_value=float(warn_line),
                message=(
                    f"ALERTE WARNING : CINR UL faible sur {device_name} : "
                    f"{cinr_ul:.1f} dB (seuil warning {settings.cinr_warning_db} dB, "
                    f"bande de tolérance {margin:.0f} dB)"
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


# ---------------------------------------------------------------------------
# Famille E — Charge / capacité de l'AP (base-station Rocket)
# ---------------------------------------------------------------------------


class RocketClientOverloadRule(AlertRule):
    """Rocket de base station saturé — trop de clients pour sa capacité.

    Le nombre de clients qu'un AP sert correctement dépend de sa famille radio
    (LTU > airMAX à spectre égal) et croît avec sa largeur de canal. Le seuil est
    une formule : base par famille à 10 MHz, +``rocket_overload_clients_per_10mhz``
    par tranche de +10 MHz. Incident CRITIQUE quand ``peer_count`` (clients
    connectés) ATTEINT ce seuil. Seuils configurables (env + page Seuils) via
    ``_rocket_overload_threshold``.

    Entrées injectées dans ``metrics`` :
      - ``peer_count``        : nombre de clients connectés (jobs de polling)
      - ``channel_width_mhz`` : largeur de canal lue en direct (API)
      - ``is_airmax_rocket``  : famille radio de l'AP (alert_engine)

    La règle ``skip`` si l'une de ces entrées manque (pas de data → on
    n'incrémente pas l'anti-flap) ou si la largeur est < 10 MHz (pas de seuil
    défini). Anti-flap : ``rocket_overload_failure_threshold`` cycles (le compte
    de clients fluctue avec les associations transitoires)."""

    alert_type = "rocket_client_overload"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        clients = metrics.get("peer_count")
        width = metrics.get("channel_width_mhz")
        # A manual per-Rocket ceiling (operator-set) overrides the formula and
        # applies even when the channel width is unknown.
        override = metrics.get("max_clients_override")
        if clients is None:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="peer_count",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )

        airmax = bool(metrics.get("is_airmax_rocket"))
        threshold = _rocket_overload_threshold(settings, airmax, width, override)
        if threshold is None:
            # No manual override and width unknown/below 10 MHz — no defined
            # ceiling, no rule.
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity=None,
                metric_name="peer_count",
                metric_value=None,
                threshold_value=None,
                message="",
                skip=True,
            )

        family = "airMAX" if airmax else "LTU"
        width_str = f"{width:.0f} MHz" if width is not None else "largeur inconnue"
        if clients >= threshold:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="peer_count",
                metric_value=float(clients),
                threshold_value=float(threshold),
                message=(
                    f"ALERTE CRITIQUE : Rocket {device_name} saturé — "
                    f"{clients} clients connectés en {width_str} ({family}), "
                    f"seuil {threshold}. Capacité de l'AP dépassée."
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="peer_count",
            metric_value=float(clients),
            threshold_value=float(threshold),
            message=(
                f"RECOVERY : charge clients de {device_name} repassée sous le "
                f"seuil ({clients}/{threshold} clients en {width_str})"
            ),
        )


class LrLinkSubstandardRule(AlertRule):
    """Lien client sous le seuil — incident CONSOLIDÉ (per-LR).

    Seuils par famille radio (engine injects ``model_variant`` dans metrics) :

    | Métrique                          | LTU                  | airMAX (Litebeam)    |
    |-----------------------------------|----------------------|----------------------|
    | link_potential_pct                | < 50 % → critical    | < 40 % → critical    |
    | total_capacity_mbps               | < 60 Mbps → critical | < 60 Mbps → critical |
    | local_rx_rate_idx / remote_rx_idx | < ×6 → critical      | < ×4 critical /      |
    |                                   | (pas de warning)     | 4 ≤ rx < 6 → warning |

    Sévérité de l'incident = pire sévérité parmi les métriques en infraction
    (critical l'emporte). Le message liste toutes les métriques fautives.
    Seules les métriques présentes sont évaluées ; si aucune n'est rapportée
    → skip (pas de data, on n'incrémente pas l'anti-flap). Anti-flap :
    5 cycles consécutifs (lr_link_substandard_failure_threshold) car ces
    métriques sont très volatiles.
    """

    alert_type = "lr_link_substandard"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        airmax = _is_airmax_lr(metrics)
        link_pot_floor = (
            settings.lr_link_potential_min_pct_airmax
            if airmax
            else settings.lr_link_potential_min_pct_ltu
        )
        rx_rate_crit = (
            settings.lr_rx_rate_critical_idx_airmax
            if airmax
            else settings.lr_rx_rate_critical_idx_ltu
        )
        # airMAX = warning band 4-6 ; LTU = no warning, critical-only at <6
        rx_rate_warn = settings.lr_rx_rate_warning_idx_airmax if airmax else None

        # (metric_name, label, value, crit_floor, warn_floor or None, unit, fmt)
        checks = [
            ("link_potential_pct", "Potentiel du lien", metrics.get("link_potential_pct"),
             link_pot_floor, None, "%", "{:.0f}"),
            ("total_capacity_mbps", "Capacité totale", metrics.get("total_capacity_mbps"),
             settings.lr_total_capacity_min_mbps, None, " Mbps", "{:.1f}"),
            ("local_rx_rate_idx", "Rate local", metrics.get("local_rx_rate_idx"),
             rx_rate_crit, rx_rate_warn, "×", "{:.0f}"),
            ("remote_rx_rate_idx", "Rate distant", metrics.get("remote_rx_rate_idx"),
             rx_rate_crit, rx_rate_warn, "×", "{:.0f}"),
        ]
        present = [c for c in checks if c[2] is not None]
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

        crit_parts: list[str] = []
        warn_parts: list[str] = []
        worst_name: str | None = None
        worst_value: float | None = None
        worst_floor: float | None = None
        for name, label, value, crit_fl, warn_fl, unit, fmt in present:
            if value < crit_fl:
                crit_parts.append(
                    f"{label} {fmt.format(value)}{unit} "
                    f"(plancher critique {fmt.format(crit_fl)}{unit})"
                )
                if worst_value is None:
                    worst_name, worst_value, worst_floor = name, float(value), float(crit_fl)
            elif warn_fl is not None and value < warn_fl:
                warn_parts.append(
                    f"{label} {fmt.format(value)}{unit} "
                    f"(plancher warning {fmt.format(warn_fl)}{unit})"
                )
                if worst_value is None:
                    worst_name, worst_value, worst_floor = name, float(value), float(warn_fl)

        if crit_parts:
            parts = crit_parts + warn_parts
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name=worst_name or "lr_link_floors",
                metric_value=round(worst_value, 1) if worst_value is not None else None,
                threshold_value=worst_floor,
                message=(
                    f"ALERTE CRITIQUE : lien client dégradé sur {device_name} — "
                    + " ; ".join(parts)
                ),
            )
        if warn_parts:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="warning",
                metric_name=worst_name or "lr_link_floors",
                metric_value=round(worst_value, 1) if worst_value is not None else None,
                threshold_value=worst_floor,
                message=(
                    f"ALERTE WARNING : lien client dégradé sur {device_name} — "
                    + " ; ".join(warn_parts)
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
# Famille F — airFiber 60 (AF60-LR), lien backhaul 60 GHz point-à-point
# ---------------------------------------------------------------------------


class Af60LinkDownRule(AlertRule):
    """Lien radio AF60 coupé — ``wireless.radios[0].linkState`` != "connected".

    La métrique ``af60_link_up`` (1.0/0.0) est toujours présente quand le device
    répond. 0.0 = radio déconnectée → critique."""

    alert_type = "af60_link_down"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        up = metrics.get("af60_link_up")
        if up is None:
            return AlertEvalResult(self.alert_type, None, "af60_link_up", None, None, "", skip=True)
        if up < 1.0:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="af60_link_up",
                metric_value=up,
                threshold_value=1.0,
                message=(
                    f"ALERTE CRITIQUE : lien radio AF60 coupé sur {device_name} "
                    f"(radio 60 GHz déconnectée)"
                ),
            )
        return AlertEvalResult(
            alert_type=self.alert_type,
            severity=None,
            metric_name="af60_link_up",
            metric_value=up,
            threshold_value=None,
            message=f"RECOVERY : lien radio AF60 de {device_name} reconnecté",
        )


class Af60SignalLowRule(AlertRule):
    """Signal AF60 faible (dBm) — seuils 60 GHz dédiés (af60_signal_*)."""

    alert_type = "af60_signal_low"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        signal = metrics.get("signal_dbm")
        if signal is None:
            return AlertEvalResult(self.alert_type, None, "signal_dbm", None, None, "", skip=True)
        warn_dbm = float(settings.af60_signal_warning_dbm)
        crit_dbm = float(settings.af60_signal_critical_dbm)
        margin = float(settings.af60_signal_tolerance_dbm)
        is_open = self.alert_type in (metrics.get("_open_alert_types") or ())
        crit_line = crit_dbm if is_open else crit_dbm - margin
        warn_line = warn_dbm if is_open else warn_dbm - margin

        if signal < crit_line:
            return AlertEvalResult(
                self.alert_type, "critical", "signal_dbm", signal, float(crit_line),
                f"ALERTE CRITIQUE : signal AF60 faible sur {device_name} : "
                f"{signal:.1f} dBm (seuil critique {crit_dbm:.0f} dBm)",
            )
        if signal < warn_line:
            return AlertEvalResult(
                self.alert_type, "warning", "signal_dbm", signal, float(warn_line),
                f"ALERTE WARNING : signal AF60 dégradé sur {device_name} : "
                f"{signal:.1f} dBm (seuil warning {warn_dbm:.0f} dBm)",
            )
        return AlertEvalResult(
            self.alert_type, None, "signal_dbm", signal, None,
            f"RECOVERY : signal AF60 de {device_name} de nouveau nominal ({signal:.1f} dBm)",
        )


class Af60SnrLowRule(AlertRule):
    """SNR AF60 faible (dB) — le 60 GHz expose un SNR (et pas de CINR)."""

    alert_type = "af60_snr_low"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        snr = metrics.get("snr_db")
        if snr is None:
            return AlertEvalResult(self.alert_type, None, "snr_db", None, None, "", skip=True)
        warn = float(settings.af60_snr_warning_db)
        crit = float(settings.af60_snr_critical_db)
        margin = float(settings.af60_snr_tolerance_db)
        is_open = self.alert_type in (metrics.get("_open_alert_types") or ())
        crit_line = crit if is_open else crit - margin
        warn_line = warn if is_open else warn - margin

        if snr < crit_line:
            return AlertEvalResult(
                self.alert_type, "critical", "snr_db", snr, float(crit_line),
                f"ALERTE CRITIQUE : SNR AF60 faible sur {device_name} : "
                f"{snr:.1f} dB (seuil critique {crit:.0f} dB)",
            )
        if snr < warn_line:
            return AlertEvalResult(
                self.alert_type, "warning", "snr_db", snr, float(warn_line),
                f"ALERTE WARNING : SNR AF60 dégradé sur {device_name} : "
                f"{snr:.1f} dB (seuil warning {warn:.0f} dB)",
            )
        return AlertEvalResult(
            self.alert_type, None, "snr_db", snr, None,
            f"RECOVERY : SNR AF60 de {device_name} de nouveau nominal ({snr:.1f} dB)",
        )


class Af60LinkSubstandardRule(AlertRule):
    """Lien AF60 dégradé — incident CONSOLIDÉ.

    Critique si le potentiel du lien OU la capacité totale (dl+ul) passe sous
    son plancher (``af60_link_potential_min_pct`` / ``af60_total_capacity_min_mbps``).
    Seules les métriques présentes sont évaluées ; aucune présente → skip.
    Anti-flap ``af60_link_substandard_failure_threshold`` cycles (métriques volatiles)."""

    alert_type = "af60_link_substandard"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        checks = [
            ("link_potential_pct", "Potentiel du lien", metrics.get("link_potential_pct"),
             float(settings.af60_link_potential_min_pct), "%", "{:.0f}"),
            ("total_capacity_mbps", "Capacité totale", metrics.get("total_capacity_mbps"),
             float(settings.af60_total_capacity_min_mbps), " Mbps", "{:.0f}"),
        ]
        present = [c for c in checks if c[2] is not None]
        if not present:
            return AlertEvalResult(
                self.alert_type, None, "af60_link_floors", None, None, "", skip=True
            )
        bad: list[str] = []
        worst_name = worst_value = worst_floor = None
        for name, label, value, floor, unit, fmt in present:
            if value < floor:
                bad.append(f"{label} {fmt.format(value)}{unit} (plancher {fmt.format(floor)}{unit})")
                if worst_value is None:
                    worst_name, worst_value, worst_floor = name, float(value), float(floor)
        if bad:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name=worst_name or "af60_link_floors",
                metric_value=round(worst_value, 1) if worst_value is not None else None,
                threshold_value=worst_floor,
                message=(
                    f"ALERTE CRITIQUE : lien AF60 dégradé sur {device_name} — "
                    + " ; ".join(bad)
                ),
            )
        return AlertEvalResult(
            self.alert_type, None, "af60_link_floors", None, None,
            f"RECOVERY : lien AF60 de {device_name} repassé au-dessus de tous les seuils",
        )


class P2pLinkSubstandardRule(AlertRule):
    """Lien P2P LiteBeam (airMAX) dégradé.

    Équivalent airMAX de ``Af60LinkSubstandardRule`` : ces radios font un
    backhaul inter-sites, pas un AP. Critique si la capacité totale du lien passe
    sous ``airmax_backhaul_capacity_min_mbps`` (plancher dédié, 150 Mbps par
    défaut — un backhaul airMAX porte bien moins que le 1.95 Gb/s d'un AF60).

    Montée uniquement sur la rule_category ``ptp_litebeam`` → pas de garde-fou
    de type ici. ``skip`` tant que la capacité n'a pas été relevée. Anti-flap
    ``p2p_link_substandard_failure_threshold``."""

    alert_type = "p2p_link_substandard"

    def evaluate(self, device_name: str, metrics: dict, settings) -> AlertEvalResult:
        capacity = metrics.get("total_capacity_mbps")
        if capacity is None:
            return AlertEvalResult(
                self.alert_type, None, "total_capacity_mbps", None, None, "", skip=True
            )
        floor = float(settings.airmax_backhaul_capacity_min_mbps)
        if capacity < floor:
            return AlertEvalResult(
                alert_type=self.alert_type,
                severity="critical",
                metric_name="total_capacity_mbps",
                metric_value=round(float(capacity), 1),
                threshold_value=floor,
                message=(
                    f"ALERTE CRITIQUE : lien P2P airMAX dégradé sur {device_name} — "
                    f"capacité totale {capacity:.0f} Mbps (plancher {floor:.0f} Mbps)"
                ),
            )
        return AlertEvalResult(
            self.alert_type, None, "total_capacity_mbps", None, None,
            f"RECOVERY : lien P2P airMAX de {device_name} repassé au-dessus du plancher "
            f"({capacity:.0f} Mbps)",
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
    RocketClientOverloadRule(),
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
    RocketClientOverloadRule(),
]

_AF60_RULES: list[AlertRule] = [
    Af60LinkDownRule(),
    Af60SignalLowRule(),
    Af60SnrLowRule(),
    Af60LinkSubstandardRule(),
]

# PTP LiteBeam (airMAX point-à-point inter-sites) : supervisé sur la capacité du
# lien + la qualité radio, comme un backhaul AF60. PAS de règle AP (overload/CPE).
_PTP_LITEBEAM_RULES: list[AlertRule] = [
    SignalLowRule(),
    CINRLowRule(),
    P2pLinkSubstandardRule(),
]

RULES_BY_DEVICE_TYPE: dict[str, list[AlertRule]] = {
    "ltu_rocket": _ROCKET_RULES,
    "lr": _LR_RULES,
    "uisp_switch": _SWITCH_RULES,
    "uisp_power": [],
    "airmax_rocket": _AIRMAX_ROCKET_RULES,
    "airfiber": _AF60_RULES,
    "ptp_litebeam": _PTP_LITEBEAM_RULES,
}

# Failure threshold per alert_type — resolved from settings in the engine
FAILURE_THRESHOLDS: dict[str, str] = {
    "signal_low": "signal_failure_threshold",
    "cinr_low": "cinr_failure_threshold",
    "ccq_low": "ccq_failure_threshold",
    "cinr_ul_low": "cinr_failure_threshold",
    "ccq_ul_low": "ccq_failure_threshold",
    "high_rx_tx_errors": "error_failure_threshold",
    "radio_link_degraded": "radio_degraded_failure_threshold",
    "lr_link_substandard": "lr_link_substandard_failure_threshold",
    "rocket_client_overload": "rocket_overload_failure_threshold",
    "af60_signal_low": "af60_signal_failure_threshold",
    "af60_snr_low": "af60_snr_failure_threshold",
    "af60_link_down": "af60_link_down_failure_threshold",
    "af60_link_substandard": "af60_link_substandard_failure_threshold",
    "p2p_link_substandard": "p2p_link_substandard_failure_threshold",
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
