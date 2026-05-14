"""Distance-banded warning/critical thresholds for radio signal strength.

At 5 GHz, free-space path loss adds ~6 dB for each doubling of distance,
so a single flat warning seuil either lets short-distance LR slack or
punishes long-distance LR unfairly. The bands below were calibrated to
keep the warning level ~10 dB above a typical noise floor (-90 dBm) at
each distance, which preserves a usable modulation.

Used by:
  - app.services.alert_rules (SignalLowRule, RadioLinkDegradedRule)
    → incidents fire at the right severity for the link length
  - app.services.lr_health_service (Liaisons clients classifier)
    → "Signal — état" signal uses the same bands

When `distance_m` is unknown (LR newly discovered, peer not yet reported
by Rocket API), callers fall back to the global settings (env-configured
SIGNAL_WARN_DBM / SIGNAL_CRIT_DBM, defaults -70 / -80).
"""

from __future__ import annotations

# Each entry is (max_distance_m, warning_dbm, critical_dbm). The first row
# whose max_distance_m is greater than the LR distance is selected.
_BANDS: list[tuple[float, float, float]] = [
    (1_000, -55.0, -65.0),  # < 1 km
    (3_000, -62.0, -72.0),  # 1–3 km
    (7_000, -68.0, -77.0),  # 3–7 km
    (12_000, -73.0, -82.0),  # 7–12 km
]
# > 12 km bucket
_FAR_BAND: tuple[float, float] = (-78.0, -85.0)


def signal_warning_threshold(distance_m: float | None, fallback: float = -70.0) -> float:
    """Warning seuil for signal_dbm given the link distance.

    Returns `fallback` when distance_m is None — keeps backward compat with
    devices where the peer-side metric has not yet been reported.
    """
    if distance_m is None:
        return float(fallback)
    for max_d, warn, _crit in _BANDS:
        if distance_m < max_d:
            return warn
    return _FAR_BAND[0]


def signal_critical_threshold(distance_m: float | None, fallback: float = -80.0) -> float:
    """Critical seuil for signal_dbm given the link distance."""
    if distance_m is None:
        return float(fallback)
    for max_d, _warn, crit in _BANDS:
        if distance_m < max_d:
            return crit
    return _FAR_BAND[1]


def signal_distance_band_label(distance_m: float | None) -> str:
    """Human-readable label of the distance band ('< 1 km', '3–7 km', etc.)."""
    if distance_m is None:
        return "distance inconnue, seuil par défaut"
    if distance_m < 1_000:
        return "< 1 km"
    if distance_m < 3_000:
        return "1–3 km"
    if distance_m < 7_000:
        return "3–7 km"
    if distance_m < 12_000:
        return "7–12 km"
    return "> 12 km"
