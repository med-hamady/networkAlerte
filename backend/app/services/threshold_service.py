"""
Runtime-configurable alert thresholds.

Thresholds are stored in the system_settings table as string key/value pairs.
get_effective_settings() returns a MergedSettings object that has the same
attribute interface as Settings but overlays DB values on top of env defaults.
This object is drop-in compatible with alert_rules.py and alert_engine.py.
"""

from __future__ import annotations

import contextlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting

# ---------------------------------------------------------------------------
# Schema — all configurable thresholds
# ---------------------------------------------------------------------------

THRESHOLD_SCHEMA: dict[str, dict[str, Any]] = {
    # Radio signal (dBm — negative values, warning > critical)
    "signal_warning_dbm": {
        "label": "Signal — seuil warning",
        "category": "radio_signal",
        "unit": "dBm",
        "type": int,
        "min": -100,
        "max": -30,
        "step": 1,
    },
    "signal_critical_dbm": {
        "label": "Signal — seuil critique",
        "category": "radio_signal",
        "unit": "dBm",
        "type": int,
        "min": -100,
        "max": -30,
        "step": 1,
    },
    "signal_tolerance_dbm": {
        "label": "Signal — bande de tolérance (hystérésis anti-flap)",
        "category": "radio_signal",
        "unit": "dBm",
        "type": float,
        "min": 0,
        "max": 10,
        "step": 0.5,
    },
    # CINR (dB)
    "cinr_warning_db": {
        "label": "CINR — seuil warning",
        "category": "radio_cinr",
        "unit": "dB",
        "type": float,
        "min": 0,
        "max": 40,
        "step": 0.5,
    },
    "cinr_critical_db": {
        "label": "CINR — seuil critique",
        "category": "radio_cinr",
        "unit": "dB",
        "type": float,
        "min": 0,
        "max": 40,
        "step": 0.5,
    },
    "cinr_tolerance_db": {
        "label": "CINR — bande de tolérance (hystérésis anti-flap)",
        "category": "radio_cinr",
        "unit": "dB",
        "type": float,
        "min": 0,
        "max": 10,
        "step": 0.5,
    },
    # CCQ (%)
    "ccq_warning_pct": {
        "label": "CCQ — seuil warning",
        "category": "radio_ccq",
        "unit": "%",
        "type": int,
        "min": 0,
        "max": 100,
        "step": 1,
    },
    "ccq_critical_pct": {
        "label": "CCQ — seuil critique",
        "category": "radio_ccq",
        "unit": "%",
        "type": int,
        "min": 0,
        "max": 100,
        "step": 1,
    },
    "ccq_tolerance_pct": {
        "label": "CCQ — bande de tolérance (hystérésis anti-flap)",
        "category": "radio_ccq",
        "unit": "%",
        "type": float,
        "min": 0,
        "max": 20,
        "step": 1,
    },
    # Link capacity (%)
    "capacity_low_warning_pct": {
        "label": "Capacité DL/UL — seuil warning",
        "category": "capacity",
        "unit": "%",
        "type": float,
        "min": 0,
        "max": 100,
        "step": 1,
    },
    "capacity_low_critical_pct": {
        "label": "Capacité DL/UL — seuil critique",
        "category": "capacity",
        "unit": "%",
        "type": float,
        "min": 0,
        "max": 100,
        "step": 1,
    },
    # Planchers du lien client (lr_link_substandard) — déclinés par famille
    # radio : LTU et airMAX (Litebeam) n'ont pas les mêmes bornes.
    "lr_link_potential_min_pct_ltu": {
        "label": "Potentiel du lien — plancher LTU",
        "category": "lr_link",
        "unit": "%",
        "type": float,
        "min": 0,
        "max": 100,
        "step": 1,
    },
    "lr_link_potential_min_pct_airmax": {
        "label": "Potentiel du lien — plancher airMAX (Litebeam)",
        "category": "lr_link",
        "unit": "%",
        "type": float,
        "min": 0,
        "max": 100,
        "step": 1,
    },
    "lr_total_capacity_min_mbps": {
        "label": "Capacité totale du lien — plancher",
        "category": "lr_link",
        "unit": "Mbps",
        "type": float,
        "min": 0,
        "max": 1000,
        "step": 5,
    },
    "lr_rx_rate_critical_idx_ltu": {
        "label": "Rate — plancher critique LTU (×)",
        "category": "lr_link",
        "unit": "×",
        "type": float,
        "min": 0,
        "max": 12,
        "step": 1,
    },
    "lr_rx_rate_warning_idx_airmax": {
        "label": "Rate — plancher warning airMAX (×)",
        "category": "lr_link",
        "unit": "×",
        "type": float,
        "min": 0,
        "max": 12,
        "step": 1,
    },
    "lr_rx_rate_critical_idx_airmax": {
        "label": "Rate — plancher critique airMAX (×)",
        "category": "lr_link",
        "unit": "×",
        "type": float,
        "min": 0,
        "max": 12,
        "step": 1,
    },
    # RX/TX error rate (%)
    "rx_tx_error_warning_pct": {
        "label": "Taux d'erreurs RX/TX — seuil warning",
        "category": "errors",
        "unit": "%",
        "type": float,
        "min": 0,
        "max": 50,
        "step": 0.1,
    },
    "rx_tx_error_critical_pct": {
        "label": "Taux d'erreurs RX/TX — seuil critique",
        "category": "errors",
        "unit": "%",
        "type": float,
        "min": 0,
        "max": 50,
        "step": 0.1,
    },
    # Battery (UISP Power)
    "battery_warning_pct": {
        "label": "Batterie — seuil warning",
        "category": "battery",
        "unit": "%",
        "type": int,
        "min": 0,
        "max": 100,
        "step": 1,
    },
    "battery_critical_pct": {
        "label": "Batterie — seuil critique",
        "category": "battery",
        "unit": "%",
        "type": int,
        "min": 0,
        "max": 100,
        "step": 1,
    },
    # Latence LR → Internet (ms) — seule métrique de latence exploitée.
    "lr_latency_critical_ms": {
        "label": "Latence LR → Internet — seuil critique",
        "category": "ping_latency",
        "unit": "ms",
        "type": float,
        "min": 1,
        "max": 5000,
        "step": 5,
    },
    "lr_latency_ping_count": {
        "label": "Latence LR → Internet — nombre de pings par mesure",
        "category": "ping_latency",
        "unit": "pings",
        "type": int,
        "min": 1,
        "max": 20,
        "step": 1,
    },
    # Anti-flap: consecutive failures required before opening incident
    "ping_down_threshold": {
        "label": "Ping DOWN — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 1,
        "max": 10,
        "step": 1,
    },
    "ping_instability_threshold": {
        "label": "Ping instabilité — échecs avant email info (0 = désactivé)",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "signal_failure_threshold": {
        "label": "Signal — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "cinr_failure_threshold": {
        "label": "CINR — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "ccq_failure_threshold": {
        "label": "CCQ — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "capacity_failure_threshold": {
        "label": "Capacité — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "error_failure_threshold": {
        "label": "Taux d'erreurs RX/TX — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "radio_degraded_failure_threshold": {
        "label": "Lien radio dégradé — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "throughput_anomaly_failure_threshold": {
        "label": "Anomalie de débit — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "lr_link_substandard_failure_threshold": {
        "label": "Lien client sous le seuil — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    "transit_probe_threshold": {
        "label": "Transit LR → Internet — cycles KO avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 1,
        "max": 10,
        "step": 1,
    },
    "lr_latency_failure_threshold": {
        "label": "Latence LR → Internet — cycles consécutifs avant incident",
        "category": "antiflap",
        "unit": "cycles",
        "type": int,
        "min": 0,
        "max": 10,
        "step": 1,
    },
    # Throughput anomaly
    "throughput_anomaly_drop_pct": {
        "label": "Chute de débit — seuil d'anomalie",
        "category": "throughput",
        "unit": "%",
        "type": float,
        "min": 10,
        "max": 90,
        "step": 5,
    },
    "throughput_anomaly_min_mbps": {
        "label": "Chute de débit — débit minimum pour détection",
        "category": "throughput",
        "unit": "Mbps",
        "type": float,
        "min": 0.1,
        "max": 100,
        "step": 0.5,
    },
}

CATEGORY_LABELS: dict[str, str] = {
    "radio_signal": "Signal radio (dBm)",
    "radio_cinr":   "CINR",
    "radio_ccq":    "CCQ",
    "capacity":     "Capacité de liaison",
    "lr_link":      "Lien client (LR) — planchers LTU / airMAX",
    "errors":       "Taux d'erreurs",
    "battery":      "Batterie (UISP Power)",
    "ping_latency": "Latence ping",
    "antiflap":     "Anti-flapping",
    "throughput":   "Anomalie de débit",
}


# ---------------------------------------------------------------------------
# MergedSettings — drops-in for Settings, overlays DB values
# ---------------------------------------------------------------------------

class MergedSettings:
    """
    Drop-in replacement for Settings that overlays DB threshold overrides
    on top of the env-based defaults. Compatible with alert_rules.py —
    attribute access falls back to the base Settings for non-threshold fields.
    """

    def __init__(self, base: Any, overrides: dict[str, Any]) -> None:
        object.__setattr__(self, '_base', base)
        object.__setattr__(self, '_overrides', overrides)

    def __getattr__(self, name: str) -> Any:
        overrides = object.__getattribute__(self, '_overrides')
        if name in overrides:
            return overrides[name]
        base = object.__getattribute__(self, '_base')
        return getattr(base, name)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _load_overrides(db: AsyncSession) -> dict[str, str]:
    """Return all system_settings rows as a {key: value_str} dict."""
    result = await db.execute(select(SystemSetting))
    return {row.key: row.value for row in result.scalars().all()}


async def get_effective_settings(db: AsyncSession, base_settings: Any) -> MergedSettings:
    """
    Return a MergedSettings object: DB rows override env defaults for known
    threshold keys. Non-threshold attributes fall through to base_settings.
    """
    raw = await _load_overrides(db)
    overrides: dict[str, Any] = {}
    for key, schema in THRESHOLD_SCHEMA.items():
        if key in raw:
            # ignore malformed DB value, fall back to env
            with contextlib.suppress(ValueError, TypeError):
                overrides[key] = schema["type"](raw[key])
    return MergedSettings(base_settings, overrides)


# ---------------------------------------------------------------------------
# Public read/write API (used by the /system/thresholds endpoint)
# ---------------------------------------------------------------------------

async def get_all_thresholds(db: AsyncSession, base_settings: Any) -> list[dict[str, Any]]:
    """
    Return the full threshold list with current effective value, env default,
    and whether the value has been overridden via DB.
    """
    raw = await _load_overrides(db)
    result = []
    for key, schema in THRESHOLD_SCHEMA.items():
        env_default = getattr(base_settings, key, None)
        if key in raw:
            try:
                current = schema["type"](raw[key])
                is_overridden = True
            except (ValueError, TypeError):
                current = env_default
                is_overridden = False
        else:
            current = env_default
            is_overridden = False
        result.append({
            "key": key,
            "label": schema["label"],
            "category": schema["category"],
            "category_label": CATEGORY_LABELS.get(schema["category"], schema["category"]),
            "unit": schema["unit"],
            "type": schema["type"].__name__,
            "min": schema["min"],
            "max": schema["max"],
            "step": schema["step"],
            "value": current,
            "default": env_default,
            "is_overridden": is_overridden,
        })
    return result


async def set_thresholds(db: AsyncSession, updates: dict[str, Any]) -> None:
    """Upsert one or more threshold values in DB."""
    for key, value in updates.items():
        if key not in THRESHOLD_SCHEMA:
            continue
        schema = THRESHOLD_SCHEMA[key]
        value_str = str(schema["type"](value))

        res = await db.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        row = res.scalar_one_or_none()
        if row is None:
            db.add(SystemSetting(key=key, value=value_str))
        else:
            row.value = value_str
    await db.flush()


async def reset_threshold(db: AsyncSession, key: str) -> bool:
    """Remove a DB override for a threshold key (reverts to env default). Returns True if deleted."""
    res = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    row = res.scalar_one_or_none()
    if row is None:
        return False
    await db.delete(row)
    await db.flush()
    return True
