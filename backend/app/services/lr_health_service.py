"""Behavior-based classification of client LR installations.

The system evaluates each LR against 10 signals on a 30-day sliding window
and concludes with a verdict (stable / watch / suspect / critical).

Four physical indicators are tracked:
  - Signal dBm       — strength of the link
  - Bruit (noise)    — noise floor at the LR (remote_noise_dbm), local RF pollution
  - CCQ              — link quality / retransmission ratio
  - Disponibilité    — cumulative downtime + frequency of outages (lr_down)

For each indicator, two signals: État (current level) + Tendance (drift over
the window). Plus two outlier signals comparing this LR to siblings on the
same Rocket at similar distance (signal alone, then bruit-or-CCQ).

Total: 4 × 2 + 2 = 10 signals.

Verdict :
  0–2 → stable    (LR not surfaced)
  3–4 → watch     (À surveiller)
  5–7 → suspect   (Suspect — à inspecter)
  8+  → critical  (Critique — à reprendre)

Signal thresholds for `signal_dbm` are distance-banded — at 5 GHz, free-space
path loss adds ~6 dB per doubling of distance, so a flat threshold either
punishes long links or lets short links slack.
"""

import datetime
import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.alert_constants import AT_LR_DOWN
from app.core.radio_thresholds import (
    signal_distance_band_label,
    signal_warning_threshold,
)
from app.models.device import Lr
from app.models.device_metric import DeviceMetric
from app.models.incident import Incident
from app.schemas.lr_health import (
    BadInstallationRow,
    BadInstallationsResponse,
    SignalEvidence,
)

logger = logging.getLogger(__name__)


# Metric column names in `device_metrics.metric_name`.
_M_SIGNAL = "signal_dbm"
_M_NOISE = "remote_noise_dbm"
_M_CCQ = "ccq_pct"
_TRACKED_METRICS: tuple[str, ...] = (_M_SIGNAL, _M_NOISE, _M_CCQ)


# Flat thresholds for the other 3 indicators.
_NOISE_WARNING_DBM = -85.0  # > -85 dBm (toward 0) means noise floor is rising
_CCQ_WARNING_PCT = 75.0
_DOWNTIME_WARNING_PCT = 1.0  # > 1 % of window (~7h on 30 days)

# Trend slopes (per WEEK, computed from per-second slope × 7 × 86400).
_SIGNAL_TREND_DBM_PER_WEEK = -1.0  # signal falling ≥ 1 dBm/week
_NOISE_TREND_DBM_PER_WEEK = 1.0  # noise rising ≥ 1 dBm/week (toward 0)
_CCQ_TREND_PCT_PER_WEEK = -2.0  # CCQ dropping ≥ 2 %/week

# Downtime frequency threshold.
_DOWNTIME_OUTAGES_MIN = 5

# Trend signals need a minimum number of samples to be meaningful — a slope
# fit on 3 points is noise. ~14 samples over 30 days = 1 sample every ~2 days.
_TREND_MIN_SAMPLES = 14

# Outlier comparison: peers on same Rocket within this fraction of distance.
_PEER_DISTANCE_PCT = 0.30
_MIN_PEERS_FOR_OUTLIER = 3

# Verdict thresholds (validated with user).
_VERDICT_WATCH_AT = 3
_VERDICT_SUSPECT_AT = 5
_VERDICT_CRITICAL_AT = 8

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
    downtime_stats = await _fetch_downtime_stats(db, lr_ids, cutoff, days)
    latest_metrics = await _fetch_latest_metrics(db, lr_ids)
    outlier_flags = _compute_outlier(lrs, latest_metrics)

    items: list[BadInstallationRow] = []
    for lr in lrs:
        signals = _build_signals(
            lr=lr,
            metric_stats=metric_stats.get(lr.id, {}),
            downtime=downtime_stats.get(lr.id, {"downtime_s": 0.0, "outages": 0}),
            outlier=outlier_flags.get(lr.id),
            window_days=days,
        )
        active = sum(1 for s in signals if s.active)
        verdict = _verdict(active)
        if verdict == "stable":
            continue

        radio = latest_metrics.get(lr.id, {})
        dt = downtime_stats.get(lr.id, {"downtime_s": 0.0, "outages": 0})
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
                outages_count=int(dt["outages"]),
                downtime_hours=float(dt["downtime_s"]) / 3600.0,
                latest_signal_dbm=radio.get(_M_SIGNAL),
                latest_noise_dbm=radio.get(_M_NOISE),
                latest_ccq_pct=radio.get(_M_CCQ),
                signal_warning_threshold=signal_warning_threshold(lr.distance_m),
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
# Data gathering — one query per concern
# ---------------------------------------------------------------------------


async def _fetch_metric_stats(
    db: AsyncSession,
    lr_ids: list[int],
    cutoff: datetime.datetime,
) -> dict[int, dict[str, dict[str, float]]]:
    """Mean + slope (per second) + sample count for each tracked metric.

    PostgreSQL's regr_slope is used so the linear fit happens in the database.
    Multiplying the per-second slope by 7 × 86 400 yields the per-week trend.
    """
    epoch_secs = func.extract("epoch", DeviceMetric.collected_at)
    q = (
        select(
            DeviceMetric.device_id,
            DeviceMetric.metric_name,
            func.avg(DeviceMetric.metric_value).label("mean"),
            func.regr_slope(DeviceMetric.metric_value, epoch_secs).label("slope_per_s"),
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
            "slope_per_week": (
                float(r.slope_per_s) * 7.0 * 86400.0 if r.slope_per_s is not None else math.nan
            ),
            "samples": int(r.samples),
        }
    return result


async def _fetch_downtime_stats(
    db: AsyncSession,
    lr_ids: list[int],
    cutoff: datetime.datetime,
    window_days: int,
) -> dict[int, dict[str, float]]:
    """Per-LR cumulative downtime (seconds) and outage count over the window.

    Downtime = sum of (resolved_at − detected_at) for `lr_down` incidents
    detected within the window. Open incidents are scored against now().
    Incidents that started before the window are clipped at the cutoff.
    """
    start_clamped = func.greatest(Incident.detected_at, cutoff)
    end_clamped = func.coalesce(Incident.resolved_at, func.now())
    duration_secs = func.extract("epoch", end_clamped - start_clamped)

    q = (
        select(
            Incident.device_id,
            func.count(Incident.id).label("outages"),
            func.sum(duration_secs).label("downtime_s"),
        )
        .where(
            Incident.device_id.in_(lr_ids),
            Incident.alert_type == AT_LR_DOWN,
            # Either it started in-window, or it overlaps the start of the window.
            and_(
                func.coalesce(Incident.resolved_at, func.now()) >= cutoff,
            ),
        )
        .group_by(Incident.device_id)
    )
    rows = (await db.execute(q)).all()
    window_seconds = window_days * 86_400
    result: dict[int, dict[str, float]] = {}
    for r in rows:
        seconds = float(r.downtime_s or 0.0)
        result[r.device_id] = {
            "downtime_s": seconds,
            "outages": int(r.outages),
            "downtime_pct": 100.0 * seconds / window_seconds if window_seconds else 0.0,
        }
    return result


async def _fetch_latest_metrics(db: AsyncSession, lr_ids: list[int]) -> dict[int, dict[str, float]]:
    """Latest value of each tracked metric per LR (for display + outlier)."""
    sub = (
        select(
            DeviceMetric.device_id,
            DeviceMetric.metric_name,
            func.max(DeviceMetric.collected_at).label("max_ts"),
        )
        .where(
            DeviceMetric.device_id.in_(lr_ids),
            DeviceMetric.metric_name.in_(_TRACKED_METRICS),
        )
        .group_by(DeviceMetric.device_id, DeviceMetric.metric_name)
        .subquery()
    )
    q = select(DeviceMetric).join(
        sub,
        (DeviceMetric.device_id == sub.c.device_id)
        & (DeviceMetric.metric_name == sub.c.metric_name)
        & (DeviceMetric.collected_at == sub.c.max_ts),
    )
    rows = (await db.execute(q)).scalars().all()
    out: dict[int, dict[str, float]] = defaultdict(dict)
    for m in rows:
        out[m.device_id][m.metric_name] = m.metric_value
    return out


# ---------------------------------------------------------------------------
# Outlier signal — pure Python (peer groups are small: 3–20 LRs typically)
# ---------------------------------------------------------------------------


def _compute_outlier(
    lrs: Iterable[Lr],
    latest: dict[int, dict[str, float]],
) -> dict[int, dict[str, Any]]:
    """For each LR, mark whether it sits in the worst quartile vs siblings on
    same Rocket at similar distance, per metric. We track signal and
    (noise/CCQ) separately because they get two distinct signals on the
    final verdict."""
    by_rocket: dict[int, list[Lr]] = defaultdict(list)
    for lr in lrs:
        if lr.rocket_id is not None and lr.distance_m is not None:
            by_rocket[lr.rocket_id].append(lr)

    result: dict[int, dict[str, Any]] = {}
    for lr in lrs:
        result[lr.id] = {
            "evaluable_signal": False,
            "evaluable_quality": False,
            "outlier_signal": False,
            "outlier_noise": False,
            "outlier_ccq": False,
            "peer_count": 0,
        }
        if lr.rocket_id is None or lr.distance_m is None or lr.distance_m <= 0:
            continue

        peers = [
            peer
            for peer in by_rocket.get(lr.rocket_id, [])
            if peer.id != lr.id
            and peer.distance_m is not None
            and abs(peer.distance_m - lr.distance_m) / lr.distance_m <= _PEER_DISTANCE_PCT
        ]
        if len(peers) < _MIN_PEERS_FOR_OUTLIER:
            continue

        my = latest.get(lr.id, {})
        result[lr.id]["peer_count"] = len(peers)

        # Signal: lower = worse
        my_signal = my.get(_M_SIGNAL)
        if my_signal is not None:
            peer_signals = [latest.get(p.id, {}).get(_M_SIGNAL) for p in peers]
            peer_signals = [v for v in peer_signals if v is not None]
            if len(peer_signals) >= _MIN_PEERS_FOR_OUTLIER:
                result[lr.id]["evaluable_signal"] = True
                q1 = _percentile(peer_signals, 25)
                if my_signal <= q1:
                    result[lr.id]["outlier_signal"] = True

        # Noise: higher (toward 0) = worse
        my_noise = my.get(_M_NOISE)
        if my_noise is not None:
            peer_noise = [latest.get(p.id, {}).get(_M_NOISE) for p in peers]
            peer_noise = [v for v in peer_noise if v is not None]
            if len(peer_noise) >= _MIN_PEERS_FOR_OUTLIER:
                result[lr.id]["evaluable_quality"] = True
                q3 = _percentile(peer_noise, 75)
                if my_noise >= q3:
                    result[lr.id]["outlier_noise"] = True

        # CCQ: lower = worse
        my_ccq = my.get(_M_CCQ)
        if my_ccq is not None:
            peer_ccq = [latest.get(p.id, {}).get(_M_CCQ) for p in peers]
            peer_ccq = [v for v in peer_ccq if v is not None]
            if len(peer_ccq) >= _MIN_PEERS_FOR_OUTLIER:
                result[lr.id]["evaluable_quality"] = True
                q1 = _percentile(peer_ccq, 25)
                if my_ccq <= q1:
                    result[lr.id]["outlier_ccq"] = True

    return result


def _percentile(values: list[float], p: float) -> float:
    if not values:
        raise ValueError("empty values")
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_v[int(k)]
    return sorted_v[f] * (c - k) + sorted_v[c] * (k - f)


# ---------------------------------------------------------------------------
# Signal evaluation
# ---------------------------------------------------------------------------


def _build_signals(
    lr: Lr,
    metric_stats: dict[str, dict[str, float]],
    downtime: dict[str, float],
    outlier: dict[str, Any] | None,
    window_days: int,
) -> list[SignalEvidence]:
    outlier = outlier or {
        "evaluable_signal": False,
        "evaluable_quality": False,
        "outlier_signal": False,
        "outlier_noise": False,
        "outlier_ccq": False,
        "peer_count": 0,
    }

    signal_stat = metric_stats.get(_M_SIGNAL, {})
    noise_stat = metric_stats.get(_M_NOISE, {})
    ccq_stat = metric_stats.get(_M_CCQ, {})

    signal_warn = signal_warning_threshold(lr.distance_m)
    band = signal_distance_band_label(lr.distance_m)

    # ─── 1. Signal État ─────────────────────────────────────────────────────
    s1_mean = signal_stat.get("mean")
    s1_samples = int(signal_stat.get("samples", 0))
    s1_active = (
        s1_samples > 0
        and s1_mean is not None
        and not math.isnan(s1_mean)
        and s1_mean <= signal_warn
    )
    s1_value = (
        f"Moyenne {s1_mean:.1f} dBm — seuil ≤ {signal_warn:.0f} dBm ({band})"
        if s1_mean is not None and not math.isnan(s1_mean)
        else "Aucune mesure de signal sur la période"
    )

    # ─── 2. Signal Tendance ────────────────────────────────────────────────
    s2_slope = signal_stat.get("slope_per_week")
    s2_active = (
        s1_samples >= _TREND_MIN_SAMPLES
        and s2_slope is not None
        and not math.isnan(s2_slope)
        and s2_slope <= _SIGNAL_TREND_DBM_PER_WEEK
    )
    s2_value = (
        f"{s2_slope:+.2f} dBm / semaine"
        if s2_slope is not None and not math.isnan(s2_slope)
        else "Pas assez de mesures"
    )

    # ─── 3. Bruit État ─────────────────────────────────────────────────────
    n_mean = noise_stat.get("mean")
    n_samples = int(noise_stat.get("samples", 0))
    n_active = (
        n_samples > 0
        and n_mean is not None
        and not math.isnan(n_mean)
        and n_mean >= _NOISE_WARNING_DBM
    )
    n_value = (
        f"Moyenne {n_mean:.1f} dBm — seuil ≥ {_NOISE_WARNING_DBM:.0f} dBm"
        if n_mean is not None and not math.isnan(n_mean)
        else "Aucune mesure de bruit sur la période"
    )

    # ─── 4. Bruit Tendance ─────────────────────────────────────────────────
    n_slope = noise_stat.get("slope_per_week")
    n_trend_active = (
        n_samples >= _TREND_MIN_SAMPLES
        and n_slope is not None
        and not math.isnan(n_slope)
        and n_slope >= _NOISE_TREND_DBM_PER_WEEK
    )
    n_trend_value = (
        f"{n_slope:+.2f} dBm / semaine"
        if n_slope is not None and not math.isnan(n_slope)
        else "Pas assez de mesures"
    )

    # ─── 5. CCQ État ───────────────────────────────────────────────────────
    c_mean = ccq_stat.get("mean")
    c_samples = int(ccq_stat.get("samples", 0))
    c_active = (
        c_samples > 0
        and c_mean is not None
        and not math.isnan(c_mean)
        and c_mean < _CCQ_WARNING_PCT
    )
    c_value = (
        f"Moyenne {c_mean:.1f} % — seuil < {_CCQ_WARNING_PCT:.0f} %"
        if c_mean is not None and not math.isnan(c_mean)
        else "Aucune mesure de CCQ sur la période"
    )

    # ─── 6. CCQ Tendance ───────────────────────────────────────────────────
    c_slope = ccq_stat.get("slope_per_week")
    c_trend_active = (
        c_samples >= _TREND_MIN_SAMPLES
        and c_slope is not None
        and not math.isnan(c_slope)
        and c_slope <= _CCQ_TREND_PCT_PER_WEEK
    )
    c_trend_value = (
        f"{c_slope:+.2f} % / semaine"
        if c_slope is not None and not math.isnan(c_slope)
        else "Pas assez de mesures"
    )

    # ─── 7. Disponibilité État ─────────────────────────────────────────────
    dt_pct = float(downtime.get("downtime_pct", 0.0))
    dt_hours = float(downtime.get("downtime_s", 0.0)) / 3600.0
    dt_active = dt_pct > _DOWNTIME_WARNING_PCT
    dt_value = f"Downtime cumulé {dt_hours:.1f} h ({dt_pct:.2f} % sur {window_days}j)"

    # ─── 8. Disponibilité Fréquence ────────────────────────────────────────
    outages = int(downtime.get("outages", 0))
    freq_active = outages >= _DOWNTIME_OUTAGES_MIN
    freq_value = f"{outages} panne(s) distincte(s)"

    # ─── 9. Outlier signal ─────────────────────────────────────────────────
    o_sig_active = bool(outlier["evaluable_signal"]) and bool(outlier["outlier_signal"])
    o_sig_value = (
        (
            f"Pire quartile sur signal vs {outlier['peer_count']} voisin(s) "
            f"à ±{int(_PEER_DISTANCE_PCT * 100)}% de distance"
        )
        if outlier["evaluable_signal"]
        else "Non évaluable (pas assez de voisins à distance similaire)"
    )

    # ─── 10. Outlier qualité (bruit OU CCQ) ───────────────────────────────
    o_qual_active = bool(outlier["evaluable_quality"]) and (
        bool(outlier["outlier_noise"]) or bool(outlier["outlier_ccq"])
    )
    if outlier["evaluable_quality"]:
        parts = []
        if outlier["outlier_noise"]:
            parts.append("bruit")
        if outlier["outlier_ccq"]:
            parts.append("CCQ")
        if parts:
            o_qual_value = f"Pire quartile sur {' + '.join(parts)}"
        else:
            o_qual_value = f"Aligné sur ses {outlier['peer_count']} voisin(s)"
    else:
        o_qual_value = "Non évaluable (pas assez de voisins)"

    return [
        SignalEvidence(
            key="signal_state",
            label="Signal — état",
            active=s1_active,
            value=s1_value,
            detail=(
                f"Seuil distance-bandé : -55/-62/-68/-73/-78 dBm selon"
                f" la distance LR–Rocket. Ce LR : {band} → seuil {signal_warn:.0f} dBm."
            ),
        ),
        SignalEvidence(
            key="signal_trend",
            label="Signal — tendance",
            active=s2_active,
            value=s2_value,
            detail=(
                f"Seuil : pente ≤ {_SIGNAL_TREND_DBM_PER_WEEK} dBm / semaine sur la fenêtre."
                f" Min. {_TREND_MIN_SAMPLES} mesures requises."
            ),
        ),
        SignalEvidence(
            key="noise_state",
            label="Bruit — état",
            active=n_active,
            value=n_value,
            detail=f"Seuil : noise floor moyen ≥ {_NOISE_WARNING_DBM:.0f} dBm sur la fenêtre.",
        ),
        SignalEvidence(
            key="noise_trend",
            label="Bruit — tendance",
            active=n_trend_active,
            value=n_trend_value,
            detail=(
                f"Seuil : pente ≥ +{_NOISE_TREND_DBM_PER_WEEK} dBm / semaine"
                f" (le bruit qui monte = pire)."
            ),
        ),
        SignalEvidence(
            key="ccq_state",
            label="CCQ — état",
            active=c_active,
            value=c_value,
            detail=f"Seuil : CCQ moyen < {_CCQ_WARNING_PCT:.0f} % sur la fenêtre.",
        ),
        SignalEvidence(
            key="ccq_trend",
            label="CCQ — tendance",
            active=c_trend_active,
            value=c_trend_value,
            detail=f"Seuil : pente ≤ {_CCQ_TREND_PCT_PER_WEEK} % / semaine.",
        ),
        SignalEvidence(
            key="downtime_state",
            label="Disponibilité — état",
            active=dt_active,
            value=dt_value,
            detail=(
                f"Seuil : downtime cumulé > {_DOWNTIME_WARNING_PCT} % de la fenêtre"
                f" ({_DOWNTIME_WARNING_PCT * window_days * 24 / 100:.1f} h sur {window_days}j)."
            ),
        ),
        SignalEvidence(
            key="downtime_frequency",
            label="Disponibilité — fréquence",
            active=freq_active,
            value=freq_value,
            detail=f"Seuil : ≥ {_DOWNTIME_OUTAGES_MIN} pannes lr_down distinctes sur la fenêtre.",
        ),
        SignalEvidence(
            key="outlier_signal",
            label="Anormal vs voisins — signal",
            active=o_sig_active,
            value=o_sig_value,
            detail=(
                "Pire quartile sur signal_dbm parmi les LR du même Rocket"
                f" à ±{int(_PEER_DISTANCE_PCT * 100)}% de distance"
                f" (min. {_MIN_PEERS_FOR_OUTLIER} voisins)."
            ),
        ),
        SignalEvidence(
            key="outlier_quality",
            label="Anormal vs voisins — qualité",
            active=o_qual_active,
            value=o_qual_value,
            detail=(
                "Pire quartile sur bruit OU CCQ parmi les LR du même Rocket"
                f" à ±{int(_PEER_DISTANCE_PCT * 100)}% de distance"
                f" (min. {_MIN_PEERS_FOR_OUTLIER} voisins)."
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
