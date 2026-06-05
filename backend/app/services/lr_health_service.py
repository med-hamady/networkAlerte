"""Behavior-based classification of client LR installations.

Each LR is evaluated against **5 level indicators** and gets a verdict
(stable / suspect / critical). Two entry points share the same indicators,
floors and verdict thresholds — they differ only in the data compared to each
floor:

  • :func:`get_bad_installations` — **moyenne glissante 30 j** servie par la
    matview ``lr_health_metric_stats_30d``. Utilisée par le rapport `/reports`
    (étude périodique). Un indicateur ne se déclenche que s'il y a ≥1 mesure
    sur la fenêtre (une donnée manquante ne pénalise jamais le LR).
  • :func:`get_live_link_health` — **état actuel** : chaque équipement est
    interrogé en direct à l'ouverture de la page « Liaisons clients » (LTU via
    le Rocket parent, airMAX via airOS). Un LR injoignable en live est exclu.

The 5 indicators (each is a "État" — value vs a floor; no trend):
  1. Signal dBm       — value ≤ settings.signal_warning_dbm (flat, default -75)
  2. Potentiel du lien — value < family floor (LTU 50 % / airMAX 40 %)
  3. Capacité totale  — value < 60 Mbps   (total_capacity_mbps)
  4. Débit RX local   — value < family floor (LTU ×6 / airMAX ×6)
  5. Débit RX distant — value < family floor (LTU ×6 / airMAX ×6)

Verdict (number of active indicators out of 5):
  0-2 → stable    (LR not surfaced)
  3   → suspect   (Suspect — à inspecter)
  4+  → critical  (Critique — à reprendre)
"""

import asyncio
import datetime
import logging
import math
from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.device import Lr, Rocket
from app.schemas.device import normalize_mac
from app.schemas.lr_health import (
    BadInstallationRow,
    BadInstallationsResponse,
    LiveLinkHealthResponse,
    SignalEvidence,
)
from app.services import airos_api_service, ltu_api_service

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

# LR → Internet RTT, maintenu par lr_internet_probe_job (sonde SSH 60 s). Affiché
# sur la page mais HORS scoring (pas dans _TRACKED_METRICS) : c'est une info de
# transit, pas un indicateur de qualité du lien radio.
_M_LATENCY = "lr_latency_ms"

# Floors live in Settings (single source shared with the lr_link_substandard
# alert rule). Read at request time so a config change applies to both the
# page and the alerting without code edits. Family-banded (LTU vs airMAX)
# since 2026-05-21 — voir _link_potential_floor / _rx_rate_floor.

# Verdict thresholds (out of 5 active indicators).
# 3 → suspect (à inspecter), 4-5 → critical. Below 3 → stable (not surfaced).
_VERDICT_SUSPECT_AT = 3
_VERDICT_CRITICAL_AT = 4

_VERDICT_ORDER = {"critical": 0, "suspect": 1}


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
        # On ne surface que les LR à ≥3/5 indicateurs actifs.
        # 3 → suspect (à inspecter), 4-5 → critical. <3 → non affiché.
        if active < _VERDICT_SUSPECT_AT:
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
# Live evaluation — état actuel (page « Liaisons clients »)
# ---------------------------------------------------------------------------

# Bound the fan-out so opening the page never opens hundreds of device HTTP
# sessions at once. These are I/O-bound network calls (login + status), so a
# fairly wide window is fine — the real safety net is the global deadline below.
_LIVE_FETCH_CONCURRENCY = 24

# Hard wall-clock cap on the whole live sweep. A slow/unreachable device can
# take up to ~15 s (login GET + POST + status, each with its own timeout); with
# many Rockets/LiteBeams the serialized tail would otherwise blow past any HTTP
# gateway timeout and freeze the page. Past this deadline we stop waiting and
# return what came back — every device that did NOT answer in time is simply
# counted as unreachable (same semantics as a radio-down LR: excluded). So the
# page always renders in bounded time no matter how large the parc grows.
_LIVE_FETCH_DEADLINE_S = 12.0


async def get_live_link_health(db: AsyncSession) -> LiveLinkHealthResponse:
    """Classer les LR clients sur leur **état actuel** (valeurs live).

    Contrairement à :func:`get_bad_installations` (moyenne glissante 30 j servie
    par la matview, utilisée par le rapport), cette fonction interroge chaque
    équipement **en direct** à l'ouverture de la page : LTU via l'API HTTP du
    Rocket parent (un appel par Rocket, peers mappés par MAC), airMAX via
    l'API airOS de chaque LiteBeam. Les 5 indicateurs et le verdict (≥3/5)
    sont identiques — seule la donnée comparée au plancher change (valeur
    actuelle au lieu de la moyenne 30 j).

    Un LR **injoignable en live** (lien radio down, auth KO, timeout, creds
    manquants) est **exclu** : pas de repli sur la dernière valeur en base.
    """
    now = datetime.datetime.now(datetime.UTC)

    lrs = (await db.execute(select(Lr).options(selectinload(Lr.rocket)))).scalars().all()
    if not lrs:
        return LiveLinkHealthResponse(generated_at=now, unreachable_count=0, items=[])

    live_metrics = await _fetch_live_metrics(lrs)
    settings = get_settings()

    items: list[BadInstallationRow] = []
    for lr in lrs:
        metrics = live_metrics.get(lr.id)
        if metrics is None:
            continue  # injoignable en live → exclu (pas de repli DB)

        # Réutilise la logique d'indicateurs : on enveloppe chaque valeur live
        # dans la même forme {mean, samples} que la matview (samples=1), avec le
        # wording « Actuel ».
        stats = {
            name: {"mean": value, "samples": 1}
            for name, value in metrics.items()
            if name in _TRACKED_METRICS and value is not None
        }
        signals = _build_signals(
            lr=lr,
            metric_stats=stats,
            settings=settings,
            value_word="Actuel",
            basis_phrase="la valeur actuelle",
        )
        active = sum(1 for s in signals if s.active)
        if active < _VERDICT_SUSPECT_AT:
            continue

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
                verdict=_verdict(active),
                active_signals_count=active,
                signals=signals,
                latest_signal_dbm=metrics.get(_M_SIGNAL),
                latest_link_potential_pct=metrics.get(_M_LINK_POT),
                latest_total_capacity_mbps=metrics.get(_M_TOTAL_CAP),
                latest_local_rx_rate_idx=metrics.get(_M_LOCAL_RATE),
                latest_remote_rx_rate_idx=metrics.get(_M_REMOTE_RATE),
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

    # Latence LR → Internet : dernier relevé en base (sonde SSH 60 s), uniquement
    # pour les LR surfacés. Affichage seul, ne change pas le verdict. On ne sonde
    # PAS en live (un ping SSH par LR doublerait le temps de page) — la valeur ≤60 s
    # est déjà une mesure réelle fraîche, cohérente avec « état actuel ».
    if items:
        latency = await _fetch_latest_latency(db, [it.lr_id for it in items])
        for it in items:
            it.latency_ms = latency.get(it.lr_id)

    unreachable = sum(1 for lr in lrs if lr.id not in live_metrics)
    return LiveLinkHealthResponse(
        generated_at=now, unreachable_count=unreachable, items=items
    )


async def _fetch_latest_latency(
    db: AsyncSession,
    lr_ids: list[int],
) -> dict[int, float]:
    """Dernier ``lr_latency_ms`` (RTT LR → Internet) par LR, via LATERAL.

    Même pattern indexé que :func:`_fetch_latest_metrics` mais pour une seule
    métrique : un ``ORDER BY collected_at DESC LIMIT 1`` par LR sur
    ``ix_device_metrics_lookup`` plutôt qu'un scan. Absent du dict si le LR n'a
    aucun relevé de latence (jamais de transit mesuré)."""
    if not lr_ids:
        return {}

    sql = text(
        """
        SELECT did.device_id, m.metric_value
        FROM unnest(CAST(:lr_ids AS integer[])) AS did(device_id)
        JOIN LATERAL (
            SELECT metric_value
            FROM device_metrics dm
            WHERE dm.device_id = did.device_id
              AND dm.metric_name = :metric_name
            ORDER BY dm.collected_at DESC
            LIMIT 1
        ) m ON true
        """
    )
    rows = (
        await db.execute(sql, {"lr_ids": lr_ids, "metric_name": _M_LATENCY})
    ).all()
    return {r.device_id: float(r.metric_value) for r in rows}


async def _fetch_live_metrics(
    lrs: list[Lr],
) -> dict[int, dict[str, float | None]]:
    """Interroger les équipements en direct ; ``{lr_id: metrics}`` pour les seuls
    LR effectivement joignables (les autres sont absents → exclus par l'appelant).

    - LTU : un appel ``collect_ltu_api_full`` par Rocket parent, peers mappés par
      MAC vers chaque LR enfant (plusieurs LTU LR partagent un Rocket → un seul
      appel les couvre tous).
    - airMAX : un appel ``collect_airos_link_metrics`` par LiteBeam, à son IP.

    Les appels sont parallélisés (sémaphore ``_LIVE_FETCH_CONCURRENCY``).
    """
    airmax_lrs = [lr for lr in lrs if _is_airmax_variant(lr.model_variant)]

    # Regroupe les LTU LR par Rocket parent joignable (LTU + creds API présents).
    rockets: dict[int, Rocket] = {}
    ltu_by_rocket: dict[int, list[Lr]] = defaultdict(list)
    for lr in lrs:
        if _is_airmax_variant(lr.model_variant):
            continue
        rocket = lr.rocket
        if (
            rocket is None
            or rocket.radio_tech != "ltu"
            or not rocket.ssh_username
            or not rocket.ssh_password
        ):
            continue  # pas de source live → exclu
        rockets[rocket.id] = rocket
        ltu_by_rocket[rocket.id].append(lr)

    result: dict[int, dict[str, float | None]] = {}
    sem = asyncio.Semaphore(_LIVE_FETCH_CONCURRENCY)

    async def fetch_rocket(rocket: Rocket, children: list[Lr]) -> None:
        async with sem:
            rocket_ap, _all_peers, per_peer = await ltu_api_service.collect_ltu_api_full(
                host=rocket.ip_address,
                username=rocket.ssh_username,
                password=rocket.ssh_password,
                port=443,
            )
        if rocket_ap is None:
            return  # Rocket injoignable → tous ses LR restent exclus
        by_mac = {mac.lower(): m for mac, m in per_peer if mac}
        for lr in children:
            if not lr.mac_address:
                continue
            try:
                want = normalize_mac(lr.mac_address)
            except ValueError:
                want = lr.mac_address.lower()
            metrics = by_mac.get(want)
            if metrics and any(v is not None for v in metrics.values()):
                result[lr.id] = metrics

    async def fetch_airmax(lr: Lr) -> None:
        if not lr.ssh_username or not lr.ssh_password:
            return  # creds airOS manquants → exclu
        async with sem:
            collected = await airos_api_service.collect_airos_link_metrics(
                host=lr.ip_address,
                username=lr.ssh_username,
                password=lr.ssh_password,
                port=443,
            )
        if collected is None:
            return
        metrics, _hostname, _netrole = collected
        if any(v is not None for v in metrics.values()):
            result[lr.id] = metrics

    coros = [fetch_rocket(rockets[rid], children) for rid, children in ltu_by_rocket.items()]
    coros += [fetch_airmax(lr) for lr in airmax_lrs]
    if not coros:
        return result

    # Each task writes its LR(s) into `result` as soon as it succeeds, so a
    # partial `result` after a timeout is always consistent — devices that
    # didn't finish in time simply never wrote, and the caller counts them as
    # unreachable. We cancel the stragglers explicitly so no orphaned HTTP
    # session keeps the event loop busy after we've returned.
    tasks = [asyncio.create_task(c) for c in coros]
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=_LIVE_FETCH_DEADLINE_S,
        )
    except TimeoutError:  # asyncio.TimeoutError is an alias of builtin on 3.11+
        for t in tasks:
            t.cancel()
        logger.warning(
            "Live link-health : délai global de %.0fs atteint — %d/%d sonde(s) "
            "terminée(s), le reste est compté injoignable.",
            _LIVE_FETCH_DEADLINE_S,
            sum(1 for t in tasks if t.done() and not t.cancelled()),
            len(tasks),
        )
    return result


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


async def _fetch_metric_stats(
    db: AsyncSession,
    lr_ids: list[int],
    cutoff: datetime.datetime,
) -> dict[int, dict[str, dict[str, float]]]:
    """Mean + sample count for each tracked metric over the 30-day window.

    Reads from the `lr_health_metric_stats_30d` materialized view (created in
    migration o6a7b8c9d0e1, refreshed every 15 min by
    lr_health_matview_refresh_job). The view pre-aggregates the same query
    that used to run inline here — at prod scale (16 M+ rows) that inline
    query did a 4 s Parallel Seq Scan on every page load. Now it's a 340-row
    lookup on a small view, <50 ms.

    The `cutoff` arg is accepted for API compatibility but ignored: the view
    bakes in `now() - interval '30 days'` at REFRESH time, so the window
    sliding is handled by the refresh job, not by the caller.
    """
    if not lr_ids:
        return {}

    sql = text(
        """
        SELECT device_id, metric_name, mean, samples
        FROM lr_health_metric_stats_30d
        WHERE device_id = ANY(CAST(:lr_ids AS integer[]))
        """
    )
    rows = (await db.execute(sql, {"lr_ids": lr_ids})).all()
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
    value_word: str = "Moyenne",
    fmt: str = "{:.1f}",
) -> SignalEvidence:
    """One "level below floor" indicator. Active when there is ≥1 sample and
    the evaluated value is strictly below ``floor``. Missing data → inactive.

    ``value_word`` distinguishes the 30-day report wording ("Moyenne") from the
    live page wording ("Actuel") — the comparison logic is identical, only the
    displayed prose differs."""
    mean = stat.get("mean")
    samples = int(stat.get("samples", 0))
    has = samples > 0 and mean is not None and not math.isnan(mean)
    active = has and mean < floor
    if has:
        value = f"{value_word} {fmt.format(mean)}{unit} — plancher {fmt.format(floor)}{unit}"
    else:
        value = "Aucune mesure sur la période"
    return SignalEvidence(key=key, label=label, active=active, value=value, detail=detail)


def _build_signals(
    lr: Lr,
    metric_stats: dict[str, dict[str, float]],
    settings,
    *,
    value_word: str = "Moyenne",
    basis_phrase: str = "la moyenne 30 j",
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
        f"{value_word} {s_mean:.1f} dBm — seuil ≤ {signal_warn:.0f} dBm"
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
            f"si {basis_phrase} est ≤ {signal_warn:.0f} dBm."
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
            value_word=value_word,
            fmt="{:.0f}",
            detail=(
                f"Potentiel du lien (moyenne des linkScore DL/UL) — famille {family}. "
                f"Indicateur actif si {basis_phrase} est < {pot_floor:.0f} %."
            ),
        ),
        _level_signal(
            key="total_capacity_state",
            label="Capacité totale — état",
            stat=metric_stats.get(_M_TOTAL_CAP, {}),
            floor=cap_floor,
            unit=" Mbps",
            value_word=value_word,
            fmt="{:.0f}",
            detail=(
                "Capacité totale du lien (capacity.combined). "
                f"Indicateur actif si {basis_phrase} est < {cap_floor:.0f} Mbps."
            ),
        ),
        _level_signal(
            key="local_rate_state",
            label="Débit RX local — état",
            stat=metric_stats.get(_M_LOCAL_RATE, {}),
            floor=rate_floor,
            unit="×",
            value_word=value_word,
            fmt="{:.0f}",
            detail=(
                f"Multiplicateur de modulation RX local (mcs.txRate) — famille {family}. "
                f"Indicateur actif si {basis_phrase} est < ×{rate_floor:.0f}."
            ),
        ),
        _level_signal(
            key="remote_rate_state",
            label="Débit RX distant — état",
            stat=metric_stats.get(_M_REMOTE_RATE, {}),
            floor=rate_floor,
            unit="×",
            value_word=value_word,
            fmt="{:.0f}",
            detail=(
                f"Multiplicateur de modulation RX distant (mcs.rxRate) — famille {family}. "
                f"Indicateur actif si {basis_phrase} est < ×{rate_floor:.0f}."
            ),
        ),
    ]


def _verdict(active_count: int) -> str:
    if active_count >= _VERDICT_CRITICAL_AT:
        return "critical"
    if active_count >= _VERDICT_SUSPECT_AT:
        return "suspect"
    return "stable"
