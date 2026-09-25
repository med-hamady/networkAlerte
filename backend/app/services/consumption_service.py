"""Per-client cumulative consumption, rolled up site → rocket → client.

Two source paths depending on LR family:

  - LTU LRs: per-peer byte counters from the parent Rocket's HTTP API
    (`wireless.peers[i].common.counters.txBytes / rxBytes`), fanned out
    by `ltu_api_poll_job` as `peer_tx_bytes` / `peer_rx_bytes` on each
    child LR (64-bit, no wrap).

  - airMAX LRs (LiteBeam 5AC/M5): IF-MIB byte counters from the LR's
    own SNMP (`radio_rx_bytes` / `radio_tx_bytes` on ath0), polled by
    `snmp_poll_job`. The parent Rocket airMAX exposes peer identification
    only — no per-peer byte counters — so the LR has to be polled
    directly. These are 32-bit counters that wrap at ~4 GB; the wrap is
    absorbed by the sum-of-positive-deltas accounting (one cycle lost
    per wrap, bounded).

Direction mapping (customer-centric):
  download (AP → CPE)  ←  LTU: peer_tx_bytes    | airMAX: radio_rx_bytes
  upload   (CPE → AP)  ←  LTU: peer_rx_bytes    | airMAX: radio_tx_bytes

All views (24h / 7d / 30d / lifetime) sum the *positive deltas* between
successive samples — never the raw counter snapshot. The firmware counter
resets to 0 when the peer re-associates (AP reboot, CPE reboot, radio
link dropping out) and a fresh "lifetime" snapshot would lose every byte
seen before that reset. Sum-of-positive-deltas treats a reset as "delta
contributes nothing this cycle, then accumulation resumes" — so the
supervisor keeps the full history regardless of how many times the
hardware counter is wiped.

Performance:
  The delta accumulation runs in Postgres via LAG() + CASE (was Python
  before 2026-06-02; transferring millions of samples to Python made
  30d/lifetime take 30 s+ → user-visible "Chargement…" stall).

  Toutes les fenetres SAUF 24 h sont servies par le RESUME QUOTIDIEN
  (`client_consumption_daily`) : des totaux deja calcules, quelques
  milliers de lignes, plus aucune relecture des releves bruts. Seule la
  journee en cours repasse en live, et elle seule.

  Les deux matviews (`client_consumption_30d` / `_7d`) ont ete SUPPRIMEES
  le 2026-09-25 avec leurs deux REFRESH nocturnes : chacun relisait la
  fenetre entiere de `device_metrics` (>19 min d'E/S, cause de l'incident
  du 2026-07-20) et surtout exigeait de conserver 30 jours de releves
  bruts — c'est-a-dire exactement ce que la retention devait purger.

  24 h reste en SQL live sur les releves bruts : c'est une fenetre
  GLISSANTE a la seconde, que le resume — aligne sur les journees — ne
  peut pas rendre. C'est aussi ce qui fixe le plancher de la retention.

The site → rocket → client roll-up (`get_clients_consumption`) is a cheap
in-Python reshape over ~one row per LR — deliberately NOT pushed into SQL.
The heavy aggregate (deltas over millions of rows) is already done by the
matview/live query above; the grouping is a 68-row operation whose site key
(`Rocket.location`) is already loaded by `selectinload(Lr.rocket)`, so it
costs no extra query and no SQL join.
"""

from __future__ import annotations

import datetime
from typing import NamedTuple

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.client_consumption_daily import ClientConsumptionDaily
from app.models.device import Lr
from app.schemas.clients import (
    ClientConsumption,
    ClientConsumptionResponse,
    Period,
    RocketConsumption,
    SiteConsumption,
)

# Max plausible bytes per 60 s poll interval — 1 Gbps × 60 s ≈ 7.5 GB. Any
# computed delta exceeding this is rejected as a counter glitch.
_MAX_PLAUSIBLE_DELTA_BYTES = 8 * 1024**3

_AIRMAX_LR_VARIANTS = {"litebeam_5ac", "litebeam_m5"}

# Counter metrics queried, in stable order.
_COUNTER_METRICS = (
    "peer_tx_bytes",
    "peer_rx_bytes",
    "radio_rx_bytes",
    "radio_tx_bytes",
)

# Alias PUBLIC, lu par la retention sur `device_metrics`
# (`jobs.device_metrics_retention_job`).
#
# /!\ C'est la liste des seules metriques que la purge a le droit d'effacer.
# Toutes les autres sont ECRASEES EN PLACE (une ligne par (device, metric),
# cf. `persist_device_metrics`) : les purger sur une date ferait perdre a un
# equipement qui n'est plus interroge sa derniere valeur connue, donc le
# ferait disparaitre de `/lr-health`. Y ajouter une cle sans se poser cette
# question est le moyen le plus simple de perdre des donnees en silence.
COUNTER_METRICS = _COUNTER_METRICS

# Bucket label for LRs whose parent Rocket has no location, or no parent at all.
# Must match the frontend SITE_FALLBACK so deep-links stay consistent.
_SITE_FALLBACK = "Sans site"

# Fenetres GLISSANTES a la seconde — les trois le sont, comme avant le passage
# au resume quotidien. Ce qui a change, c'est d'ou vient le chiffre, pas la
# fenetre qu'il couvre.
_PERIOD_TO_TIMEDELTA: dict[str, datetime.timedelta] = {
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
}

# Fenetres servies par le RESUME QUOTIDIEN plutot que par une relecture des
# releves bruts. Elles restent glissantes : `_aggregate_via_daily` recoud les
# journees entieres (resume) avec les deux bords partiels (live).
#
# /!\ C'est CETTE profondeur qui borne la retention sur `device_metrics` : le
# bord le plus ancien de la fenetre 30 j se calcule sur des releves de 30 jours.
# Voir `deepest_raw_window_days`.
_DAILY_BACKED_PERIODS = frozenset({"7d", "30d"})

# Live SQL: window function + CASE replicates _sum_positive_deltas in Postgres.
# Keeps transfer at ~272 rows (68 LRs × 4 metrics) instead of millions.
# The CASE matches the Python semantics exactly: a negative delta (counter
# reset) and a delta > _MAX_PLAUSIBLE_DELTA_BYTES (counter glitch) both
# contribute 0 — never skip the sample, just don't add anything that cycle.
#
# device_id filter is mandatory: without it the planner can't use the
# (device_id, metric_name, collected_at) index → seq scan on 16 M rows
# even for a 24 h cutoff. Also excludes Rocket SNMP rows (which also have
# radio_rx/tx_bytes but aren't customer data).
_LIVE_AGGREGATE_SQL = text(
    """
    SELECT
        device_id,
        metric_name,
        SUM(CASE WHEN d IS NOT NULL AND d >= 0 AND d <= :max_delta
                 THEN d ELSE 0 END) AS bytes,
        COUNT(*)        AS samples,
        MIN(collected_at) AS first_sample_at
    FROM (
        SELECT
            device_id,
            metric_name,
            collected_at,
            metric_value - LAG(metric_value) OVER w AS d
        FROM device_metrics
        WHERE device_id = ANY(CAST(:lr_ids AS integer[]))
          AND metric_name = ANY(CAST(:metric_names AS text[]))
          AND collected_at >= :cutoff
        WINDOW w AS (
            PARTITION BY device_id, metric_name ORDER BY collected_at
        )
    ) deltas
    GROUP BY device_id, metric_name
    """
)

# Same as _LIVE_AGGREGATE_SQL but bounded on both ends — for an explicit
# [start, end) date range. The upper bound is exclusive (frontend passes the
# day *after* the last selected day so the last day is fully included). The
# custom range can't reuse the 7d/30d matviews (fixed windows) so it always
# runs live; the (device_id, metric_name, collected_at) index still applies.
_LIVE_AGGREGATE_RANGE_SQL = text(
    """
    SELECT
        device_id,
        metric_name,
        SUM(CASE WHEN d IS NOT NULL AND d >= 0 AND d <= :max_delta
                 THEN d ELSE 0 END) AS bytes,
        COUNT(*)        AS samples,
        MIN(collected_at) AS first_sample_at
    FROM (
        SELECT
            device_id,
            metric_name,
            collected_at,
            metric_value - LAG(metric_value) OVER w AS d
        FROM device_metrics
        WHERE device_id = ANY(CAST(:lr_ids AS integer[]))
          AND metric_name = ANY(CAST(:metric_names AS text[]))
          AND collected_at >= :cutoff
          AND collected_at < :upper
        WINDOW w AS (
            PARTITION BY device_id, metric_name ORDER BY collected_at
        )
    ) deltas
    GROUP BY device_id, metric_name
    """
)

# --- Résumé quotidien (client_consumption_daily) -----------------------------
# Une journée écoulée ne change plus : sa somme de deltas est calculée UNE fois
# puis relue telle quelle. C'est ce qui rend possible une rétention sur
# device_metrics (voir models/client_consumption_daily.py).

# Remonter avant minuit pour que le PREMIER relevé de la journée ait un
# prédécesseur : sans ça, `LAG` rend NULL et l'octet consommé entre le dernier
# relevé de la veille et le premier du jour serait perdu, chaque jour et pour
# chaque client. 6 h couvrent largement l'intervalle de poll (60 s, jusqu'à
# 3-8 min sur la sonde SSH) ; au-delà, un équipement était éteint, donc ne
# consommait rien.
_ROLLUP_LOOKBACK = datetime.timedelta(hours=6)

# ⚠️ La clause CASE doit rester identique à celle des matviews et de la requête
# live : delta négatif (compteur remis à zéro) et delta aberrant (> max_delta)
# comptent tous deux pour 0. Une divergence ferait que le total d'une journée
# archivée ne correspond plus à ce que la fenêtre 30 j affiche pour la même
# journée — un écart invisible et impossible à expliquer.
_DAILY_ROLLUP_SQL = text(
    """
    INSERT INTO client_consumption_daily
        (device_id, metric_name, day, bytes, samples, first_sample_at,
         created_at, updated_at)
    SELECT device_id, metric_name, CAST(:day AS date),
           SUM(CASE WHEN d IS NOT NULL AND d >= 0 AND d <= :max_delta
                    THEN d ELSE 0 END),
           COUNT(*),
           MIN(collected_at),
           now(), now()
    FROM (
        SELECT
            device_id,
            metric_name,
            collected_at,
            metric_value - LAG(metric_value) OVER w AS d
        FROM device_metrics
        WHERE metric_name = ANY(CAST(:metric_names AS text[]))
          AND collected_at >= :lookback_start
          AND collected_at <  :day_end
        WINDOW w AS (
            PARTITION BY device_id, metric_name ORDER BY collected_at
        )
    ) deltas
    WHERE collected_at >= :day_start
    GROUP BY device_id, metric_name
    ON CONFLICT (device_id, metric_name, day) DO UPDATE
        SET bytes           = EXCLUDED.bytes,
            samples         = EXCLUDED.samples,
            first_sample_at = EXCLUDED.first_sample_at,
            updated_at      = now()
    """
)

# Lecture : additionner les journées déjà totalisées. Bornes INCLUSIVES des
# deux côtés (on raisonne en jours, pas en instants).
_DAILY_RANGE_SQL = text(
    """
    SELECT device_id,
           metric_name,
           SUM(bytes)            AS bytes,
           SUM(samples)          AS samples,
           MIN(first_sample_at)  AS first_sample_at
    FROM client_consumption_daily
    WHERE day >= :from_day AND day <= :to_day
    GROUP BY device_id, metric_name
    """
)


class _AggRow(NamedTuple):
    """Même forme que les lignes rendues par les matviews et la requête live."""

    device_id: int
    metric_name: str
    bytes: int
    samples: int
    first_sample_at: datetime.datetime | None


async def rollup_day(db: AsyncSession, day: datetime.date) -> int:
    """Totalise la consommation de la journée `day` (UTC) et l'enregistre.

    Idempotent : rejouer un jour remplace ses lignes (upsert), il ne les
    double pas. Renvoie le nombre de lignes écrites.

    N'engage PAS la transaction : l'appelant décide quand valider.
    """
    day_start = datetime.datetime.combine(day, datetime.time.min, tzinfo=datetime.UTC)
    result = await db.execute(
        _DAILY_ROLLUP_SQL,
        {
            "day": day,
            "day_start": day_start,
            "day_end": day_start + datetime.timedelta(days=1),
            "lookback_start": day_start - _ROLLUP_LOOKBACK,
            "metric_names": list(_COUNTER_METRICS),
            "max_delta": _MAX_PLAUSIBLE_DELTA_BYTES,
        },
    )
    return result.rowcount or 0


async def last_rolled_day(db: AsyncSession) -> datetime.date | None:
    """Dernière journée déjà totalisée, ou None si le résumé est vide."""
    return await db.scalar(select(func.max(ClientConsumptionDaily.day)))


def _merge_rows(
    acc: dict[tuple[int, str], dict], rows
) -> None:
    """Additionne des lignes d'agrégat dans un accumulateur (par device+métrique).

    Sert à recoudre les deux moitiés d'une plage : les journées déjà
    totalisées, et la partie encore brute (aujourd'hui, ou les jours pas
    encore traités par le job de nuit).
    """
    for r in rows:
        key = (r.device_id, r.metric_name)
        slot = acc.setdefault(
            key, {"bytes": 0, "samples": 0, "first_sample_at": None}
        )
        slot["bytes"] += int(r.bytes or 0)
        slot["samples"] += int(r.samples or 0)
        first = r.first_sample_at
        if first is not None and (
            slot["first_sample_at"] is None or first < slot["first_sample_at"]
        ):
            slot["first_sample_at"] = first


async def _aggregate_via_daily(
    db: AsyncSession,
    lr_ids: list[int],
    lower: datetime.datetime,
    upper: datetime.datetime,
) -> list[_AggRow]:
    """Agrège [lower, upper) en s'appuyant sur le résumé quotidien.

    Découpe la fenêtre en TROIS, dont deux bords qui peuvent être vides :

        [lower ─── minuit)   [journées entières]   [minuit ─── upper)
             live                 résumé                  live

    Le résumé ne sait rendre que des journées ENTIÈRES. Une fenêtre glissante
    (« les 7 derniers jours » à 14 h 37) commence et finit en milieu de
    journée : ses deux bords sont donc calculés en direct sur les relevés
    bruts, et seul le milieu vient du résumé.

    ⚠️ **Le bord de TÊTE n'est pas un détail d'exactitude, c'est la fenêtre
    elle-même.** Sans lui, prendre la journée entière de `lower` ferait
    compter jusqu'à 24 h de trop, et la ramener au lendemain jusqu'à 24 h de
    trop peu — sur « 7 jours » consultés le matin, un écart de 14 %.

    ⚠️ Aucun double comptage à la jonction : le résumé d'une journée inclut
    déjà l'octet consommé de part et d'autre de minuit (son lookback de 6 h),
    et la requête de tête s'arrête à minuit exclu.

    ⚠️ **Résumé VIDE ⇒ tout repasse en live** (le comportement d'avant, jamais
    un total amputé). Idem si le résumé ne couvre aucune journée de la
    fenêtre.
    """
    acc: dict[tuple[int, str], dict] = {}
    rolled = await last_rolled_day(db)

    async def _live(from_ts: datetime.datetime, to_ts: datetime.datetime) -> None:
        rows = (
            await db.execute(
                _LIVE_AGGREGATE_RANGE_SQL,
                {
                    "lr_ids": lr_ids,
                    "metric_names": list(_COUNTER_METRICS),
                    "cutoff": from_ts,
                    "upper": to_ts,
                    "max_delta": _MAX_PLAUSIBLE_DELTA_BYTES,
                },
            )
        ).all()
        _merge_rows(acc, rows)

    live_lower = lower
    head: tuple[datetime.datetime, datetime.datetime] | None = None

    if rolled is not None:
        day_floor = datetime.datetime.combine(
            lower.date(), datetime.time.min, tzinfo=datetime.UTC
        )
        # `lower` tombe-t-il en MILIEU de journée ? Une plage de dates est
        # alignée sur minuit (rien à recoudre) ; une fenêtre glissante, non.
        partial_head = lower > day_floor
        first_full_day = (
            lower.date() + datetime.timedelta(days=1) if partial_head
            else lower.date()
        )
        # `upper` est exclusif : la dernière journée réellement demandée est
        # celle de l'instant qui précède.
        to_day = min((upper - datetime.timedelta(microseconds=1)).date(), rolled)

        if to_day >= first_full_day:
            rows = (
                await db.execute(
                    _DAILY_RANGE_SQL,
                    {"from_day": first_full_day, "to_day": to_day},
                )
            ).all()
            _merge_rows(acc, rows)
            if partial_head:
                head = (
                    lower,
                    min(
                        datetime.datetime.combine(
                            first_full_day, datetime.time.min, tzinfo=datetime.UTC
                        ),
                        upper,
                    ),
                )
            live_lower = max(
                lower,
                datetime.datetime.combine(
                    to_day + datetime.timedelta(days=1),
                    datetime.time.min,
                    tzinfo=datetime.UTC,
                ),
            )

    if head is not None and head[0] < head[1]:
        await _live(*head)
    if live_lower < upper:
        await _live(live_lower, upper)

    return [
        _AggRow(device_id, metric_name, v["bytes"], v["samples"], v["first_sample_at"])
        for (device_id, metric_name), v in acc.items()
    ]


def deepest_raw_window_days() -> int:
    """Profondeur, en jours, de la lecture la plus PROFONDE qui touche encore
    les releves bruts de `device_metrics`.

    Sert de plancher a la retention (`jobs.device_metrics_retention_job`) :
    purger plus court que ca ferait rendre a une fenetre un total AMPUTE, sans
    la moindre erreur pour le dire.

    /!\\ Derive de `_PERIOD_TO_TIMEDELTA` et pas ecrit en dur : le jour ou un
    onglet « 90 jours » est ajoute, la retention refusera d'elle-meme de
    descendre sous 91 jours. Une constante recopiee, elle, serait restee a 31.

    Meme une fenetre servie par le resume touche le brut a cette profondeur :
    sa journee la plus ancienne est PARTIELLE (la fenetre commence en milieu de
    journee), et ce bord-la se calcule en direct.
    """
    deepest = max(_PERIOD_TO_TIMEDELTA.values())
    return -(-int(deepest.total_seconds()) // 86400)  # arrondi au jour superieur


async def get_clients_consumption(
    db: AsyncSession,
    period: Period,
    *,
    start: datetime.date | None = None,
    end: datetime.date | None = None,
) -> ClientConsumptionResponse:
    """Cumulative download/upload per LR client, rolled up site → rocket → client.

    When both ``start`` and ``end`` are given, totals are computed over that
    exact date range (both days inclusive, UTC) and ``period`` is reported as
    ``"custom"``. Otherwise the named ``period`` window is used.
    """
    now = datetime.datetime.now(datetime.UTC)
    is_custom = start is not None and end is not None

    if is_custom:
        # [start 00:00, end+1day 00:00) so both selected days are fully covered.
        cutoff: datetime.datetime | None = datetime.datetime.combine(
            start, datetime.time.min, tzinfo=datetime.UTC
        )
        upper = datetime.datetime.combine(
            end, datetime.time.min, tzinfo=datetime.UTC
        ) + datetime.timedelta(days=1)
        query_lower_bound = cutoff
        period_end = upper
        effective_period: Period = "custom"
    elif period == "lifetime":
        cutoff = None
        query_lower_bound = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
        upper = None
        period_end = now
        effective_period = period
    else:
        cutoff = now - _PERIOD_TO_TIMEDELTA[period]
        query_lower_bound = cutoff
        upper = None
        period_end = now
        effective_period = period

    lrs_result = await db.execute(select(Lr).options(selectinload(Lr.rocket)))
    lrs: list[Lr] = list(lrs_result.scalars().all())
    if not lrs:
        return ClientConsumptionResponse(
            period=effective_period,
            period_start=cutoff,
            period_end=period_end,
            data_start=None,
            sites=[],
        )

    # TOUTES les fenêtres sauf 24 h passent par le RÉSUMÉ QUOTIDIEN : plage de
    # dates et « depuis toujours » depuis le 2026-09-24, puis 7 j et 30 j le
    # 2026-09-25, à la suppression de leurs matviews. Chacune relit des totaux
    # déjà calculés et ne repasse en live que sur la journée en cours.
    #
    # ⚠️ C'est ce qui BORNE la rétention sur `device_metrics` : tant qu'une vue
    # relit plusieurs semaines de relevés bruts, on ne peut pas les purger.
    # Ne pas rebrancher une fenêtre sur le brut sans revoir la rétention.
    #
    # 24 h reste en live : fenêtre glissante à la seconde (~2 s, acceptable
    # pour l'onglet par défaut). Voir models/client_consumption_daily.py.
    if is_custom:
        # Une plage de dates porte sur des JOURNÉES ENTIÈRES UTC : elle
        # s'aligne exactement sur le résumé quotidien, qui remplace ici une
        # relecture de plusieurs mois de relevés bruts.
        agg_rows = await _aggregate_via_daily(
            db, [lr.id for lr in lrs], query_lower_bound, upper,
        )
    elif period == "lifetime":
        # Idem, sans borne haute : le résumé porte tout l'historique, la
        # requête live ne couvre plus que la journée en cours.
        agg_rows = await _aggregate_via_daily(
            db, [lr.id for lr in lrs], query_lower_bound, now,
        )
    elif period in _DAILY_BACKED_PERIODS:
        # Journées entières depuis le résumé, les deux bords partiels en live
        # — découpage fait par `_aggregate_via_daily`. La fenêtre couverte est
        # exactement la même qu'avant les matviews.
        agg_rows = await _aggregate_via_daily(
            db, [lr.id for lr in lrs], query_lower_bound, now,
        )
    else:
        lr_ids = [lr.id for lr in lrs]
        agg_rows = (
            await db.execute(
                _LIVE_AGGREGATE_SQL,
                {
                    "lr_ids": lr_ids,
                    "metric_names": list(_COUNTER_METRICS),
                    "cutoff": query_lower_bound,
                    "max_delta": _MAX_PLAUSIBLE_DELTA_BYTES,
                },
            )
        ).all()

    by_pair: dict[tuple[int, str], dict] = {}
    earliest_global: datetime.datetime | None = None
    for r in agg_rows:
        by_pair[(r.device_id, r.metric_name)] = {
            "bytes": int(r.bytes or 0),
            "samples": int(r.samples or 0),
            "first_sample_at": r.first_sample_at,
        }
        if r.first_sample_at is not None and (
            earliest_global is None or r.first_sample_at < earliest_global
        ):
            earliest_global = r.first_sample_at

    clients = _build_client_rows(lrs, by_pair)
    sites = _group_by_site(clients)

    return ClientConsumptionResponse(
        period=effective_period,
        period_start=cutoff,
        period_end=period_end,
        data_start=earliest_global,
        sites=sites,
    )


def _build_client_rows(
    lrs: list[Lr],
    by_pair: dict[tuple[int, str], dict],
) -> list[tuple[str, ClientConsumption]]:
    """One (site, ClientConsumption) per LR, picking the right counter family."""
    rows: list[tuple[str, ClientConsumption]] = []
    for lr in lrs:
        if lr.model_variant in _AIRMAX_LR_VARIANTS:
            # LR-side IF-MIB counters — radio interface RX = customer download.
            download_key = (lr.id, "radio_rx_bytes")
            upload_key = (lr.id, "radio_tx_bytes")
        else:
            # AP-side per-peer counters — peer TX from AP = customer download.
            download_key = (lr.id, "peer_tx_bytes")
            upload_key = (lr.id, "peer_rx_bytes")

        dl = by_pair.get(download_key)
        ul = by_pair.get(upload_key)
        dl_samples = dl["samples"] if dl else 0
        ul_samples = ul["samples"] if ul else 0
        dl_bytes = dl["bytes"] if dl else 0
        ul_bytes = ul["bytes"] if ul else 0

        # has_data needs ≥2 samples in at least one direction (≥1 delta to sum).
        has_data = dl_samples >= 2 or ul_samples >= 2

        first_candidates = [
            t
            for t in (
                dl["first_sample_at"] if dl else None,
                ul["first_sample_at"] if ul else None,
            )
            if t is not None
        ]
        first_sample_at = min(first_candidates) if first_candidates else None

        # site = parent Rocket's location (already loaded via selectinload), so
        # this roll-up costs no extra query. LRs without a located Rocket fall
        # into the fallback bucket.
        site = (lr.rocket.location or "").strip() if lr.rocket else ""
        rows.append(
            (
                site or _SITE_FALLBACK,
                ClientConsumption(
                    device_id=lr.id,
                    name=lr.name,
                    ip_address=lr.ip_address,
                    rocket_id=lr.rocket_id,
                    rocket_name=lr.rocket.name if lr.rocket else None,
                    download_bytes=dl_bytes,
                    upload_bytes=ul_bytes,
                    total_bytes=dl_bytes + ul_bytes,
                    samples=dl_samples + ul_samples,
                    has_data=has_data,
                    first_sample_at=first_sample_at,
                    # Plan cached on the LR row by lr_plan_service (no extra query
                    # — lr is already loaded).
                    plan_download_mbps=lr.plan_download_mbps,
                    plan_upload_mbps=lr.plan_upload_mbps,
                ),
            )
        )
    return rows


def _group_by_site(
    clients: list[tuple[str, ClientConsumption]],
) -> list[SiteConsumption]:
    """Reshape flat (site, client) rows into site → rocket → client, sorted desc.

    Rockets are keyed by id (None = no parent); the synthetic no-parent bucket
    only ever lands in the fallback site.
    """
    sites_acc: dict[str, dict[int | None, list[ClientConsumption]]] = {}
    rocket_names: dict[int | None, str | None] = {}
    for site, client in clients:
        rockets = sites_acc.setdefault(site, {})
        rockets.setdefault(client.rocket_id, []).append(client)
        rocket_names[client.rocket_id] = client.rocket_name

    sites: list[SiteConsumption] = []
    for site, rockets in sites_acc.items():
        rocket_nodes: list[RocketConsumption] = []
        for rocket_id, members in rockets.items():
            members.sort(key=lambda c: c.total_bytes, reverse=True)
            rocket_nodes.append(
                RocketConsumption(
                    rocket_id=rocket_id,
                    rocket_name=rocket_names.get(rocket_id),
                    download_bytes=sum(c.download_bytes for c in members),
                    upload_bytes=sum(c.upload_bytes for c in members),
                    total_bytes=sum(c.total_bytes for c in members),
                    client_count=len(members),
                    clients=members,
                )
            )
        rocket_nodes.sort(key=lambda r: r.total_bytes, reverse=True)
        sites.append(
            SiteConsumption(
                site=site,
                download_bytes=sum(r.download_bytes for r in rocket_nodes),
                upload_bytes=sum(r.upload_bytes for r in rocket_nodes),
                total_bytes=sum(r.total_bytes for r in rocket_nodes),
                rocket_count=len(rocket_nodes),
                client_count=sum(r.client_count for r in rocket_nodes),
                rockets=rocket_nodes,
            )
        )
    sites.sort(key=lambda s: s.total_bytes, reverse=True)
    return sites
