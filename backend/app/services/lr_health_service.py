"""Behavior-based classification of client LR installations.

Each LR is evaluated against **5 level indicators** on a 30-day sliding
window and gets a verdict (stable / watch / suspect / critical).

The 5 indicators (each is a "État" — the 30-day mean vs a floor; no trend):
  1. Signal dBm       — mean ≤ settings.signal_warning_dbm (flat, default -75)
  2. Potentiel du lien — mean < family floor (LTU 50 % / airMAX 40 %)
  3. Capacité totale  — mean < 60 Mbps   (total_capacity_mbps)
  4. Débit RX local   — mean < family floor (LTU ×6 / airMAX ×6)
  5. Débit RX distant — mean < family floor (LTU ×6 / airMAX ×6)

Verdict (number of active indicators out of 5):
  0  → stable    (LR not surfaced)
  1  → watch     (À surveiller)
  2  → suspect   (Suspect — à inspecter)
  3+ → critical  (Critique — à reprendre)

An indicator only fires when there is at least one sample in the window
(missing data never penalizes a LR).
"""

import datetime
import logging
import math
from collections import defaultdict

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.device import Lr

_AIRMAX_LR_VARIANTS = frozenset({"litebeam_5ac", "litebeam_m5"})


def _is_airmax_variant(model_variant: str | None) -> bool:
    return model_variant in _AIRMAX_LR_VARIANTS


def _link_potential_floor(model_variant: str | None, settings) -> float:
    return (
        settings.lr_link_potential_min_pct_airmax
        if _is_airmax_variant(model_variant)
        else settings.lr_link_potential_min_pct_ltu
    )


def _rx_rate_floor(model_variant: str | None, settings) -> float:
    """Indicator floor for the lr-health page — the threshold that makes the
    rate "État" indicator active. airMAX has a separate critical floor (×4)
    but for the page we use the warning band (×6) so liaisons à risque
    sortent dans le rapport 30 j."""
    if _is_airmax_variant(model_variant):
        return settings.lr_rx_rate_warning_idx_airmax
    return settings.lr_rx_rate_critical_idx_ltu
from app.models.device_metric import DeviceMetric
from app.schemas.lr_health import (
    BadInstallationRow,
    BadInstallationsResponse,
    SignalEvidence,
)

logger = logging.getLogger(__name__)


# Metric column names in `device_metrics.metric_name`.
_M_SIGNAL = "signal_dbm"
_M_LINK_POT = "link_potential_pct"
_M_TOTAL_CAP = "total_capacity_mbps"
_M_LOCAL_RATE = "local_rx_rate_idx"
_M_REMOTE_RATE = "remote_rx_rate_idx"
_TRACKED_METRICS: tuple[str, ...] = (
    _M_SIGNAL,
    _M_LINK_POT,
    _M_TOTAL_CAP,
    _M_LOCAL_RATE,
    _M_REMOTE_RATE,
)

# Floors live in Settings (single source shared with the lr_link_substandard
# alert rule). Read at request time so a config change applies to both the
# page and the alerting without code edits. Family-banded (LTU vs airMAX)
# since 2026-05-21 — voir _link_potential_floor / _rx_rate_floor.

# Verdict thresholds (out of 5 active indicators).
_VERDICT_WATCH_AT = 1
_VERDICT_SUSPECT_AT = 2
_VERDICT_CRITICAL_AT = 3

_VERDICT_ORDER = {"critical": 0, "suspect": 1, "watch": 2}


async def get_bad_installations(
    db: AsyncSession,
    days: int = 30,
) -> BadInstallationsResponse:
    """Return LRs whose behavior over the window suggests poor installation."""
    now = datetime.datetime.now(datetime.UTC)
    cutoff = now - datetime.timedelta(days=days)

    lrs = (await db.execute(select(Lr).options(selectinload(Lr.rocket)))).scalars().all()
    if not lrs:
        return BadInstallationsResponse(period_days=days, generated_at=now, items=[])

    lr_ids = [lr.id for lr in lrs]

    metric_stats = await _fetch_metric_stats(db, lr_ids, cutoff)
    latest_metrics = await _fetch_latest_metrics(db, lr_ids)
    settings = get_settings()

    items: list[BadInstallationRow] = []
    for lr in lrs:
        signals = _build_signals(
            lr=lr, metric_stats=metric_stats.get(lr.id, {}), settings=settings
        )
        active = sum(1 for s in signals if s.active)
        verdict = _verdict(active)
        if verdict == "stable":
            continue

        radio = latest_metrics.get(lr.id, {})
        items.append(
            BadInstallationRow(
                lr_id=lr.id,
                lr_name=lr.name,
                lr_ip=lr.ip_address,
                lr_mac=lr.mac_address,
                model_variant=lr.model_variant,
                distance_m=lr.distance_m,
                first_discovered_at=lr.first_discovered_at,
                rocket_id=lr.rocket.id if lr.rocket else None,
                rocket_name=lr.rocket.name if lr.rocket else None,
                verdict=verdict,
                active_signals_count=active,
                signals=signals,
                latest_signal_dbm=radio.get(_M_SIGNAL),
                latest_link_potential_pct=radio.get(_M_LINK_POT),
                latest_total_capacity_mbps=radio.get(_M_TOTAL_CAP),
                latest_local_rx_rate_idx=radio.get(_M_LOCAL_RATE),
                latest_remote_rx_rate_idx=radio.get(_M_REMOTE_RATE),
                signal_warning_threshold=float(settings.signal_warning_dbm),
                link_potential_floor_pct=_link_potential_floor(lr.model_variant, settings),
                total_capacity_floor_mbps=settings.lr_total_capacity_min_mbps,
                rx_rate_floor_idx=_rx_rate_floor(lr.model_variant, settings),
            )
        )

    items.sort(
        key=lambda r: (
            _VERDICT_ORDER[r.verdict],
            -r.active_signals_count,
            r.lr_name,
        )
    )
    return BadInstallationsResponse(period_days=days, generated_at=now, items=items)


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


async def _fetch_metric_stats(
    db: AsyncSession,
    lr_ids: list[int],
    cutoff: datetime.datetime,
) -> dict[int, dict[str, dict[str, float]]]:
    """Mean + sample count for each tracked metric over the window."""
    q = (
        select(
            DeviceMetric.device_id,
            DeviceMetric.metric_name,
            func.avg(DeviceMetric.metric_value).label("mean"),
            func.count(DeviceMetric.id).label("samples"),
        )
        .where(
            DeviceMetric.device_id.in_(lr_ids),
            DeviceMetric.metric_name.in_(_TRACKED_METRICS),
            DeviceMetric.collected_at >= cutoff,
        )
        .group_by(DeviceMetric.device_id, DeviceMetric.metric_name)
    )
    rows = (await db.execute(q)).all()
    result: dict[int, dict[str, dict[str, float]]] = defaultdict(dict)
    for r in rows:
        result[r.device_id][r.metric_name] = {
            "mean": float(r.mean) if r.mean is not None else math.nan,
            "samples": int(r.samples),
        }
    return result


async def _fetch_latest_metrics(
    db: AsyncSession,
    lr_ids: list[int],
) -> dict[int, dict[str, float]]:
    """Latest value of each tracked metric per LR (for display).

    Uses a LATERAL join so Postgres performs one indexed `ORDER BY DESC LIMIT 1`
    per (device_id, metric_name) pair instead of a self-join against a
    GROUP BY subquery. The GROUP BY variant scans the entire `device_metrics`
    table (~16M rows in prod 2026-06-01) because the planner picks a Parallel
    Seq Scan once the filter is broad enough; the LATERAL variant performs
    340 O(log n) lookups via `ix_device_metrics_lookup`.

    Measured on prod 2026-06-01: 3.3 s → 16 ms (200× faster).
    """
    if not lr_ids:
        return {}

    sql = text(
        """
        SELECT pairs.device_id, pairs.metric_name, m.metric_value
        FROM (
            SELECT did.device_id, mn.metric_name
            FROM unnest(CAST(:lr_ids AS integer[])) AS did(device_id)
            CROSS JOIN unnest(CAST(:metric_names AS text[])) AS mn(metric_name)
        ) pairs
        JOIN LATERAL (
            SELECT metric_value
            FROM device_metrics dm
            WHERE dm.device_id = pairs.device_id
              AND dm.metric_name = pairs.metric_name
            ORDER BY dm.collected_at DESC
            LIMIT 1
        ) m ON true
        """
    )
    rows = (
        await db.execute(
            sql,
            {"lr_ids": lr_ids, "metric_names": list(_TRACKED_METRICS)},
        )
    ).all()
    out: dict[int, dict[str, float]] = defaultdict(dict)
    for r in rows:
        out[r.device_id][r.metric_name] = float(r.metric_value)
    return out


# ---------------------------------------------------------------------------
# Indicator construction — 5 "État" (level) signals, no trend
# ---------------------------------------------------------------------------


def _level_signal(
    *,
    key: str,
    label: str,
    stat: dict[str, float],
    floor: float,
    unit: str,
    detail: str,
    fmt: str = "{:.1f}",
) -> SignalEvidence:
    """One "level below floor" indicator. Active when there is ≥1 sample and
    the 30-day mean is strictly below ``floor``. Missing data → inactive."""
    mean = stat.get("mean")
    samples = int(stat.get("samples", 0))
    has = samples > 0 and mean is not None and not math.isnan(mean)
    active = has and mean < floor
    if has:
        value = f"Moyenne {fmt.format(mean)}{unit} — plancher {fmt.format(floor)}{unit}"
    else:
        value = "Aucune mesure sur la période"
    return SignalEvidence(key=key, label=label, active=active, value=value, detail=detail)


def _build_signals(
    lr: Lr,
    metric_stats: dict[str, dict[str, float]],
    settings,
) -> list[SignalEvidence]:
    family = "airMAX" if _is_airmax_variant(lr.model_variant) else "LTU"
    pot_floor = _link_potential_floor(lr.model_variant, settings)
    cap_floor = settings.lr_total_capacity_min_mbps
    rate_floor = _rx_rate_floor(lr.model_variant, settings)
    # ─── 1. Signal — état (flat threshold, no distance-banding) ─────────────
    signal_stat = metric_stats.get(_M_SIGNAL, {})
    signal_warn = float(settings.signal_warning_dbm)
    s_mean = signal_stat.get("mean")
    s_samples = int(signal_stat.get("samples", 0))
    s_has = s_samples > 0 and s_mean is not None and not math.isnan(s_mean)
    s_active = s_has and s_mean <= signal_warn
    s_value = (
        f"Moyenne {s_mean:.1f} dBm — seuil ≤ {signal_warn:.0f} dBm"
        if s_has
        else "Aucune mesure de signal sur la période"
    )
    signal_sig = SignalEvidence(
        key="signal_state",
        label="Signal — état",
        active=s_active,
        value=s_value,
        detail=(
            f"Seuil plat (sans distance-banding) : indicateur actif "
            f"si la moyenne 30 j est ≤ {signal_warn:.0f} dBm."
        ),
    )

    # ─── 2-5. Floors on the new link metrics ───────────────────────────────
    return [
        signal_sig,
        _level_signal(
            key="link_potential_state",
            label="Potentiel du lien — état",
            stat=metric_stats.get(_M_LINK_POT, {}),
            floor=pot_floor,
            unit=" %",
            fmt="{:.0f}",
            detail=(
                f"Potentiel du lien (moyenne des linkScore DL/UL) — famille {family}. "
                f"Indicateur actif si la moyenne 30 j est < {pot_floor:.0f} %."
            ),
        ),
        _level_signal(
            key="total_capacity_state",
            label="Capacité totale — état",
            stat=metric_stats.get(_M_TOTAL_CAP, {}),
            floor=cap_floor,
            unit=" Mbps",
            fmt="{:.0f}",
            detail=(
                "Capacité totale du lien (capacity.combined). "
                f"Indicateur actif si la moyenne 30 j est < {cap_floor:.0f} Mbps."
            ),
        ),
        _level_signal(
            key="local_rate_state",
            label="Débit RX local — état",
            stat=metric_stats.get(_M_LOCAL_RATE, {}),
            floor=rate_floor,
            unit="×",
            fmt="{:.0f}",
            detail=(
                f"Multiplicateur de modulation RX local (mcs.txRate) — famille {family}. "
                f"Indicateur actif si la moyenne 30 j est < ×{rate_floor:.0f}."
            ),
        ),
        _level_signal(
            key="remote_rate_state",
            label="Débit RX distant — état",
            stat=metric_stats.get(_M_REMOTE_RATE, {}),
            floor=rate_floor,
            unit="×",
            fmt="{:.0f}",
            detail=(
                f"Multiplicateur de modulation RX distant (mcs.rxRate) — famille {family}. "
                f"Indicateur actif si la moyenne 30 j est < ×{rate_floor:.0f}."
            ),
        ),
    ]


def _verdict(active_count: int) -> str:
    if active_count >= _VERDICT_CRITICAL_AT:
        return "critical"
    if active_count >= _VERDICT_SUSPECT_AT:
        return "suspect"
    if active_count >= _VERDICT_WATCH_AT:
        return "watch"
    return "stable"
