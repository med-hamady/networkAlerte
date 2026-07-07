"""Behavior-based classification of client LR installations.

Each LR is evaluated against **5 level indicators** and gets a verdict
(stable / suspect / critical) from its **état actuel** (live values):

  • :func:`get_live_link_health` — chaque équipement est interrogé en direct à
    l'ouverture de la page « Liaisons clients » (LTU via le Rocket parent,
    airMAX via airOS). Un LR injoignable en live est exclu.

Note : l'ancienne entrée ``get_bad_installations`` (moyenne glissante 30 j via
la matview ``lr_health_metric_stats_30d``) a été retirée — les métriques radio
ne sont plus historisées (collapse latest-only), donc le scoring 30 j n'a plus
de données. Seul le live subsiste.

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
from app.models.device import AirFiber, Lr, PtpLiteBeam, Rocket
from app.schemas.device import normalize_mac
from app.schemas.lr_health import (
    BadInstallationRow,
    HighLatencyResponse,
    HighLatencyRow,
    LiveLinkHealthResponse,
    SignalEvidence,
    SiteLinkHealthResponse,
    SiteLinkRow,
)
from app.services import airos_api_service, ltu_api_service, threshold_service

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

# Métrique propre aux liens AF60 (clé de af60_api_service) : le SNR 60 GHz,
# affiché seul à côté de la capacité (hors filtre).
_M_SNR = "snr_db"

# Floors live in Settings (single source shared with the lr_link_substandard
# alert rule). Read at request time so a config change applies to both the
# page and the alerting without code edits. Family-banded (LTU vs airMAX)
# since 2026-05-21 — voir _link_potential_floor / _rx_rate_floor.

# Verdict thresholds (out of 5 active indicators).
# 3 → suspect (à inspecter), 4-5 → critical. Below 3 → stable (not surfaced).
_VERDICT_SUSPECT_AT = 3
_VERDICT_CRITICAL_AT = 4

_VERDICT_ORDER = {"critical": 0, "suspect": 1}


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
    """Classer les LR clients sur leur **dernière mesure connue** (device_metrics).

    Lit les dernières valeurs radio collectées en continu par les polls de fond
    (``ltu_api_poll_job`` / ``airos_api_poll_job``, collapse latest-only dans
    ``device_metrics``) — **au lieu de re-contacter EN DIRECT chaque LR** à
    l'ouverture. L'ancien fetch live contactait 600+ devices, tapait le deadline
    de 12 s et excluait ~60 LR au hasard du timing (page lente ET inexacte). Ici :
    une requête SQL → page quasi instantanée, **exhaustive** (tous les LR up
    évalués), fraîcheur ≤ intervalle des polls (60-180 s).

    Un LR ``down`` (ping) ou sans métrique radio récente est exclu (non
    évaluable). Les 5 indicateurs et le verdict (≥3/5) sont identiques — seules
    les valeurs viennent de la DB plutôt que d'un fetch live.
    """
    now = datetime.datetime.now(datetime.UTC)

    lrs = (await db.execute(select(Lr).options(selectinload(Lr.rocket)))).scalars().all()
    if not lrs:
        return LiveLinkHealthResponse(generated_at=now, unreachable_count=0, items=[])

    settings = get_settings()
    latest = await _fetch_latest_link_metrics(db, [lr.id for lr in lrs])

    items: list[BadInstallationRow] = []
    unreachable = 0
    for lr in lrs:
        metrics = latest.get(lr.id) or {}
        # Down (ping) ou aucune métrique radio en base → non évaluable, exclu.
        if lr.status != "up" or not any(
            metrics.get(m) is not None for m in _TRACKED_METRICS
        ):
            unreachable += 1
            continue

        # Même logique d'indicateurs : chaque valeur enveloppée en {mean, samples}.
        stats = {
            name: {"mean": metrics[name], "samples": 1}
            for name in _TRACKED_METRICS
            if metrics.get(name) is not None
        }
        signals = _build_signals(
            lr=lr,
            metric_stats=stats,
            settings=settings,
            value_word="Dernier",
            basis_phrase="la dernière mesure",
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
                latency_ms=metrics.get(_M_LATENCY),
            )
        )

    items.sort(
        key=lambda r: (
            _VERDICT_ORDER[r.verdict],
            -r.active_signals_count,
            r.lr_name,
        )
    )
    return LiveLinkHealthResponse(
        generated_at=now, unreachable_count=unreachable, items=items
    )


async def get_high_latency_clients(db: AsyncSession) -> HighLatencyResponse:
    """Lister les LR clients dont la latence LR → Internet dépasse le seuil.

    Critère unique : dernier ``lr_latency_ms`` (RTT relevé par
    ``lr_internet_probe_job``, collapse latest-only en base) ≥
    ``lr_latency_critical_ms`` (seuil effectif : env + overrides runtime). Seuls
    les LR ``up`` sont considérés (un LR down n'a pas de latence fraîche → on
    n'affiche pas une vieille valeur). Pas d'interrogation live."""
    now = datetime.datetime.now(datetime.UTC)

    settings = await threshold_service.get_effective_settings(db, get_settings())
    threshold = float(settings.lr_latency_critical_ms)

    lrs = (await db.execute(select(Lr).options(selectinload(Lr.rocket)))).scalars().all()
    if not lrs:
        return HighLatencyResponse(
            generated_at=now, latency_threshold_ms=threshold, items=[]
        )

    latency = await _fetch_latest_latency(db, [lr.id for lr in lrs if lr.status == "up"])

    items: list[HighLatencyRow] = []
    for lr in lrs:
        ms = latency.get(lr.id)
        if ms is None or ms < threshold:
            continue
        items.append(
            HighLatencyRow(
                lr_id=lr.id,
                lr_name=lr.name,
                lr_ip=lr.ip_address,
                lr_mac=lr.mac_address,
                model_variant=lr.model_variant,
                distance_m=lr.distance_m,
                rocket_id=lr.rocket.id if lr.rocket else None,
                rocket_name=lr.rocket.name if lr.rocket else None,
                latency_ms=ms,
                latency_threshold_ms=threshold,
            )
        )

    items.sort(key=lambda r: r.latency_ms, reverse=True)  # pires d'abord
    return HighLatencyResponse(
        generated_at=now, latency_threshold_ms=threshold, items=items
    )


async def network_latency_summary(db: AsyncSession) -> dict[str, float | int]:
    """Network-wide high-latency share across all UP client LRs.

    Counts UP LRs that have a fresh ``lr_latency_ms`` reading (``total``) and how
    many of those are at or above ``lr_latency_critical_ms`` (``high``), then the
    percentage. Reuses :func:`_fetch_latest_latency` (LATERAL, no live probing).

    Returns ``{total, high, pct, threshold_ms}``. ``pct`` is 0.0 when no LR has a
    reading. The caller decides whether ``total`` is a large enough sample to act
    on (``network_latency_min_sample``).
    """
    settings = await threshold_service.get_effective_settings(db, get_settings())
    threshold = float(settings.lr_latency_critical_ms)

    up_ids = (
        await db.execute(select(Lr.id).where(Lr.status == "up"))
    ).scalars().all()
    latency = await _fetch_latest_latency(db, list(up_ids))

    total = len(latency)
    high = sum(1 for ms in latency.values() if ms >= threshold)
    pct = (high / total * 100.0) if total else 0.0
    return {"total": total, "high": high, "pct": pct, "threshold_ms": threshold}


async def _fetch_latest_latency(
    db: AsyncSession,
    lr_ids: list[int],
) -> dict[int, float]:
    """Dernier ``lr_latency_ms`` (RTT LR → Internet) par LR, via LATERAL.

    Un ``ORDER BY collected_at DESC LIMIT 1`` par LR sur
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


async def _fetch_latest_link_metrics(
    db: AsyncSession,
    lr_ids: list[int],
) -> dict[int, dict[str, float]]:
    """Dernières valeurs des métriques de lien (scoring + latence) par LR, lues
    depuis ``device_metrics`` — ``{lr_id: {metric_name: value}}``.

    Ces métriques sont collectées en continu par ``ltu_api_poll_job`` /
    ``airos_api_poll_job`` et stockées en collapse latest-only (1 ligne fraîche
    par ``(device_id, metric_name)``). Lire ici remplace le fetch LIVE de tous
    les LR à l'ouverture de la page (qui contactait 600+ devices, tapait le
    deadline de 12 s et excluait ~60 LR au hasard du timing). Une seule requête
    ``DISTINCT ON`` (latest par paire) servie par ``ix_device_metrics_lookup`` ;
    fraîcheur ≤ intervalle des polls (60-180 s)."""
    if not lr_ids:
        return {}
    names = [*_TRACKED_METRICS, _M_LATENCY]
    sql = text(
        """
        SELECT DISTINCT ON (device_id, metric_name)
               device_id, metric_name, metric_value
        FROM device_metrics
        WHERE device_id = ANY(CAST(:lr_ids AS integer[]))
          AND metric_name = ANY(CAST(:metric_names AS text[]))
        ORDER BY device_id, metric_name, collected_at DESC
        """
    )
    rows = (await db.execute(sql, {"lr_ids": lr_ids, "metric_names": names})).all()
    out: dict[int, dict[str, float]] = defaultdict(dict)
    for r in rows:
        out[r.device_id][r.metric_name] = float(r.metric_value)
    return out


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


# ---------------------------------------------------------------------------
# Liaisons entre sites (Point-à-Point) — backhaul airFiber 60 (AF60-LR)
# ---------------------------------------------------------------------------
#
# Un AF60 est un équipement d'infra autonome (pas de Rocket parent), un seul peer.
# Critère d'affichage UNIQUE : la **dernière capacité totale** lue en base est sous
# le plancher ``af60_capacity_display_min_mbps`` (1.95 Gb/s). Pas de fetch live (trop
# coûteux) — on relit la dernière valeur de ``device_metrics`` (collapsée latest-only).
# Signal/SNR sont joints uniquement pour l'affichage, jamais pour le filtre.

# Métriques relues en base pour la section P2P (capacité = filtre ; signal/SNR = info).
_AF60_DISPLAY_METRICS: tuple[str, ...] = (_M_TOTAL_CAP, _M_SIGNAL, _M_SNR)


async def get_site_link_health(db: AsyncSession) -> SiteLinkHealthResponse:
    """Lister les liens P2P inter-sites dont la dernière capacité est sous le plancher.

    Deux technos, même critère (dernière ``total_capacity_mbps`` en base, pas de
    fetch live) mais plancher distinct :
      - AF60 (airFiber 60) ........ ``af60_capacity_display_min_mbps`` (1.95 Gb/s)
      - PTP LiteBeam (ptp_litebeam) ``airmax_backhaul_capacity_min_mbps`` (150 Mbps)

    Seuls les équipements ``up`` (ping) sont évalués : un lien down est déjà
    couvert par les alertes de disponibilité, et sa dernière capacité en base
    est stale — l'afficher ici serait un faux « dégradé ».
    """
    now = datetime.datetime.now(datetime.UTC)
    settings = get_settings()
    items: list[SiteLinkRow] = []
    no_data = 0

    # ── AF60 (airFiber 60) ──
    afs = (
        await db.execute(select(AirFiber).where(AirFiber.status == "up"))
    ).scalars().all()
    if afs:
        af_floor = float(settings.af60_capacity_display_min_mbps)
        latest = await _fetch_latest_af60_metrics(db, [af.id for af in afs])
        for af in afs:
            metrics = latest.get(af.id, {})
            cap = metrics.get(_M_TOTAL_CAP)
            if cap is None:
                no_data += 1
                continue  # aucun relevé de capacité → pas évaluable
            if cap >= af_floor:
                continue  # lien sain → non surfacé
            items.append(
                SiteLinkRow(
                    device_id=af.id,
                    name=af.name,
                    ip=af.ip_address,
                    distance_m=af.distance_m,
                    link_type="af60",
                    latest_total_capacity_mbps=cap,
                    capacity_floor_mbps=af_floor,
                    latest_signal_dbm=metrics.get(_M_SIGNAL),
                    latest_snr_db=metrics.get(_M_SNR),
                )
            )

    # ── PTP LiteBeams (airMAX point-à-point inter-sites) ──
    # cinr_db sert d'équivalent SNR à l'affichage (latest_snr_db laissé None).
    backhauls = (
        await db.execute(select(PtpLiteBeam).where(PtpLiteBeam.status == "up"))
    ).scalars().all()
    if backhauls:
        bh_floor = float(settings.airmax_backhaul_capacity_min_mbps)
        bh_latest = await _fetch_latest_af60_metrics(db, [r.id for r in backhauls])
        for r in backhauls:
            metrics = bh_latest.get(r.id, {})
            cap = metrics.get(_M_TOTAL_CAP)
            if cap is None:
                no_data += 1
                continue
            if cap >= bh_floor:
                continue
            items.append(
                SiteLinkRow(
                    device_id=r.id,
                    name=r.name,
                    ip=r.ip_address,
                    distance_m=r.distance_m,
                    link_type="airmax",
                    latest_total_capacity_mbps=cap,
                    capacity_floor_mbps=bh_floor,
                    latest_signal_dbm=metrics.get(_M_SIGNAL),
                    latest_snr_db=None,
                )
            )

    items.sort(key=lambda r: r.latest_total_capacity_mbps or 0.0)  # pires d'abord
    return SiteLinkHealthResponse(
        generated_at=now, no_data_count=no_data, items=items
    )


async def _fetch_latest_af60_metrics(
    db: AsyncSession,
    ids: list[int],
) -> dict[int, dict[str, float]]:
    """Dernière valeur de chaque métrique d'affichage AF60, par device.

    ``DISTINCT ON (device_id, metric_name) … ORDER BY collected_at DESC`` — la table
    est collapsée latest-only pour ces métriques, donc c'est une lecture triviale."""
    if not ids:
        return {}
    sql = text(
        """
        SELECT DISTINCT ON (dm.device_id, dm.metric_name)
               dm.device_id, dm.metric_name, dm.metric_value
        FROM device_metrics dm
        WHERE dm.device_id = ANY(CAST(:ids AS integer[]))
          AND dm.metric_name = ANY(CAST(:names AS text[]))
        ORDER BY dm.device_id, dm.metric_name, dm.collected_at DESC
        """
    )
    rows = (
        await db.execute(
            sql, {"ids": ids, "names": list(_AF60_DISPLAY_METRICS)}
        )
    ).all()
    out: dict[int, dict[str, float]] = defaultdict(dict)
    for r in rows:
        out[r.device_id][r.metric_name] = float(r.metric_value)
    return out
