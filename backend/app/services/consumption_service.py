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

  For the 30d window — the most common page view — a materialized view
  (`client_consumption_30d`, refreshed every 15 min) pre-computes the
  aggregate, dropping 30d latency from ~36 s to <100 ms. 24h and 7d
  windows run the live query (sub-2 s, acceptable). Lifetime also runs
  live for correctness once retention >30 d is enabled.

The site → rocket → client roll-up (`get_clients_consumption`) is a cheap
in-Python reshape over ~one row per LR — deliberately NOT pushed into SQL.
The heavy aggregate (deltas over millions of rows) is already done by the
matview/live query above; the grouping is a 68-row operation whose site key
(`Rocket.location`) is already loaded by `selectinload(Lr.rocket)`, so it
costs no extra query and no SQL join.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

# Counter metrics queried, in stable order. Must match the matview definition
# in migration p7b8c9d0e1f2 — a divergence makes 30d/lifetime under-report.
_COUNTER_METRICS = (
    "peer_tx_bytes",
    "peer_rx_bytes",
    "radio_rx_bytes",
    "radio_tx_bytes",
)

# Bucket label for LRs whose parent Rocket has no location, or no parent at all.
# Must match the frontend SITE_FALLBACK so deep-links stay consistent.
_SITE_FALLBACK = "Sans site"

_PERIOD_TO_TIMEDELTA: dict[str, datetime.timedelta] = {
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
}

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

# Matview lookup: same schema as the live query above, but pre-computed.
# Keep the column list aligned with the matview definition. One matview per
# pre-computed window — the SUM in each can't be subtracted to a narrower
# range, so 7d and 30d need separate objects.
_MATVIEW_30D_SQL = text(
    """
    SELECT device_id, metric_name, bytes, samples, first_sample_at
    FROM client_consumption_30d
    """
)
_MATVIEW_7D_SQL = text(
    """
    SELECT device_id, metric_name, bytes, samples, first_sample_at
    FROM client_consumption_7d
    """
)


async def get_clients_consumption(
    db: AsyncSession,
    period: Period,
) -> ClientConsumptionResponse:
    """Cumulative download/upload per LR client, rolled up site → rocket → client."""
    now = datetime.datetime.now(datetime.UTC)

    if period == "lifetime":
        cutoff: datetime.datetime | None = None
        query_lower_bound = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    else:
        cutoff = now - _PERIOD_TO_TIMEDELTA[period]
        query_lower_bound = cutoff

    lrs_result = await db.execute(select(Lr).options(selectinload(Lr.rocket)))
    lrs: list[Lr] = list(lrs_result.scalars().all())
    if not lrs:
        return ClientConsumptionResponse(
            period=period,
            period_start=cutoff,
            period_end=now,
            data_start=None,
            sites=[],
        )

    # 7d and 30d are served from per-window matviews (<100 ms). 24h runs
    # live SQL (~2 s — acceptable for the default tab, and gives a true
    # rolling 24 h window). Lifetime also runs live so it stays correct
    # once retention pushes data past 30 d.
    if period == "30d":
        agg_rows = (await db.execute(_MATVIEW_30D_SQL)).all()
    elif period == "7d":
        agg_rows = (await db.execute(_MATVIEW_7D_SQL)).all()
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
        period=period,
        period_start=cutoff,
        period_end=now,
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
