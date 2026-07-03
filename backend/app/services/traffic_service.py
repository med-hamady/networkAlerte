"""Top client↔Internet operators/CDNs — reads traffic_dest_stats.

Two roll-ups over the directional aggregates written by the NetFlow collector
(:mod:`app.services.netflow_service`):

- :func:`get_top_destinations` — **volume** over a period (24h/7d/30d), download
  + upload bytes per operator, ranked by total. The "what consumed the most data"
  view.
- :func:`get_throughput` — **débit** (Gb/s) over the most recent bucket: how the
  current WAN bandwidth is shared between operators, download (RX) and upload
  (TX). The "where are my N Gb/s going right now" view used for cache decisions.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.traffic_dest_stat import TrafficDestStat

# Supported look-back windows for the volume view.
_PERIODS: dict[str, datetime.timedelta] = {
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
}

# Windows + resample step (seconds) for the throughput history chart. The stored
# buckets are 1 min; we re-bin to keep the point count reasonable per window.
_HISTORY_PERIODS: dict[str, tuple[datetime.timedelta, int]] = {
    "1h": (datetime.timedelta(hours=1), 60),      # 60 points
    "6h": (datetime.timedelta(hours=6), 300),     # 72 points
    "24h": (datetime.timedelta(hours=24), 600),   # 144 points
}

_UNKNOWN_LABEL = "Indéterminé"
_OTHERS_LABEL = "Autres"


def _operator_label(asn: int | None, as_org: str | None) -> str:
    return as_org or (f"AS{asn}" if asn is not None else _UNKNOWN_LABEL)


async def get_top_destinations(
    db: AsyncSession, period: str = "24h", limit: int = 50,
) -> dict:
    """Operators/CDNs ranked by traffic **volume** over ``period``.

    Returns ``{period, total_down_bytes, total_up_bytes, destinations: [{asn,
    operator, down_bytes, up_bytes, total_bytes, share_pct}]}`` sorted by total
    bytes descending. ``share_pct`` is the operator's share of total bytes.
    """
    window = _PERIODS.get(period, _PERIODS["24h"])
    cutoff = datetime.datetime.now(datetime.UTC) - window

    rows = (
        await db.execute(
            select(
                TrafficDestStat.asn,
                func.max(TrafficDestStat.as_org).label("as_org"),
                func.sum(TrafficDestStat.down_bytes).label("down_bytes"),
                func.sum(TrafficDestStat.up_bytes).label("up_bytes"),
            )
            .where(TrafficDestStat.bucket_start >= cutoff)
            .group_by(TrafficDestStat.asn)
            .order_by((func.sum(TrafficDestStat.down_bytes) + func.sum(TrafficDestStat.up_bytes)).desc())
            .limit(limit)
        )
    ).all()

    total_down = sum(int(r.down_bytes or 0) for r in rows)
    total_up = sum(int(r.up_bytes or 0) for r in rows)
    grand_total = total_down + total_up
    destinations = []
    for r in rows:
        down = int(r.down_bytes or 0)
        up = int(r.up_bytes or 0)
        total = down + up
        destinations.append(
            {
                "asn": r.asn,
                "operator": _operator_label(r.asn, r.as_org),
                "down_bytes": down,
                "up_bytes": up,
                "total_bytes": total,
                "share_pct": round(total / grand_total * 100, 1) if grand_total else 0.0,
            }
        )

    return {
        "period": period if period in _PERIODS else "24h",
        "total_down_bytes": total_down,
        "total_up_bytes": total_up,
        "destinations": destinations,
    }


async def get_throughput(db: AsyncSession, limit: int = 50) -> dict:
    """Per-operator **throughput** (Mbps) over the most recent bucket.

    Débit = bytes ÷ bucket seconds × 8. Answers "how is my WAN bandwidth shared
    right now": download (RX) and upload (TX) Mbps per operator + each one's
    share of the total download. Returns ``{bucket_start, window_seconds,
    total_down_mbps, total_up_mbps, operators: [...]}``.
    """
    window_seconds = max(1, get_settings().netflow_bucket_minutes * 60)

    latest = (
        await db.execute(select(func.max(TrafficDestStat.bucket_start)))
    ).scalar_one_or_none()
    if latest is None:
        return {
            "bucket_start": None,
            "window_seconds": window_seconds,
            "total_down_mbps": 0.0,
            "total_up_mbps": 0.0,
            "operators": [],
        }

    rows = (
        await db.execute(
            select(
                TrafficDestStat.asn,
                func.max(TrafficDestStat.as_org).label("as_org"),
                func.sum(TrafficDestStat.down_bytes).label("down_bytes"),
                func.sum(TrafficDestStat.up_bytes).label("up_bytes"),
            )
            .where(TrafficDestStat.bucket_start == latest)
            .group_by(TrafficDestStat.asn)
            .order_by(func.sum(TrafficDestStat.down_bytes).desc())
            .limit(limit)
        )
    ).all()

    def _mbps(nbytes: int) -> float:
        return round(nbytes * 8 / window_seconds / 1_000_000, 2)

    total_down = sum(int(r.down_bytes or 0) for r in rows)
    total_up = sum(int(r.up_bytes or 0) for r in rows)
    operators = [
        {
            "asn": r.asn,
            "operator": _operator_label(r.asn, r.as_org),
            "down_mbps": _mbps(int(r.down_bytes or 0)),
            "up_mbps": _mbps(int(r.up_bytes or 0)),
            "share_pct": round(int(r.down_bytes or 0) / total_down * 100, 1) if total_down else 0.0,
        }
        for r in rows
    ]

    return {
        "bucket_start": latest.isoformat(),
        "window_seconds": window_seconds,
        "total_down_mbps": _mbps(total_down),
        "total_up_mbps": _mbps(total_up),
        "operators": operators,
    }


async def get_throughput_history(
    db: AsyncSession, period: str = "24h", top: int = 12,
) -> dict:
    """Download throughput (Mbps) per operator **over time**, for a stacked chart.

    Re-bins the stored 1-min buckets into `step`-second slots over the window,
    keeps the top-N operators as their own series and folds the rest into
    "Autres". Débit per slot = bytes ÷ step × 8. Returns ``{period, step_seconds,
    times, series:[{asn, operator, down_mbps:[...]}], total_up_mbps:[...]}``
    (series ordered by total download desc, "Autres" last)."""
    window, step = _HISTORY_PERIODS.get(period, _HISTORY_PERIODS["24h"])
    cutoff = datetime.datetime.now(datetime.UTC) - window

    def _mbps(nbytes: int) -> float:
        return round(nbytes * 8 / step / 1_000_000, 2)

    # 1) Top-N operators (resolved ASNs only) by total download over the window.
    top_rows = (
        await db.execute(
            select(
                TrafficDestStat.asn,
                func.max(TrafficDestStat.as_org).label("as_org"),
            )
            .where(TrafficDestStat.bucket_start >= cutoff, TrafficDestStat.asn.isnot(None))
            .group_by(TrafficDestStat.asn)
            .order_by(func.sum(TrafficDestStat.down_bytes).desc())
            .limit(top)
        )
    ).all()
    top_asns = [r.asn for r in top_rows]
    labels = {r.asn: _operator_label(r.asn, r.as_org) for r in top_rows}

    # 2) Per-slot totals (all operators) — gives the timeline + the "Autres" base.
    # `step` is a trusted int from _HISTORY_PERIODS → safe to inline as an interval
    # literal (passing it as a bound param makes asyncpg expect a timedelta).
    bin_expr = f"date_bin('{step} seconds', bucket_start, TIMESTAMPTZ 'epoch')"
    total_rows = (
        await db.execute(
            text(
                f"SELECT {bin_expr} AS t, "
                "       SUM(down_bytes) AS d, SUM(up_bytes) AS u "
                "FROM traffic_dest_stats WHERE bucket_start >= :cutoff "
                "GROUP BY t ORDER BY t"
            ),
            {"cutoff": cutoff},
        )
    ).all()
    # Access text() columns by index — attribute access on Row is unreliable
    # (e.g. `.t` is reserved for the row tuple in SQLAlchemy 2.0). Columns: (t, d, u).
    times = [r[0] for r in total_rows]
    idx = {t: i for i, t in enumerate(times)}
    n = len(times)
    total_down = [int(r[1] or 0) for r in total_rows]
    total_up = [int(r[2] or 0) for r in total_rows]

    # 3) Per-slot download for the top-N operators.
    per_top: dict[int, list[int]] = {a: [0] * n for a in top_asns}
    if top_asns and n:
        rows = (
            await db.execute(
                text(
                    f"SELECT {bin_expr} AS t, asn, SUM(down_bytes) AS d "
                    "FROM traffic_dest_stats "
                    "WHERE bucket_start >= :cutoff AND asn = ANY(CAST(:asns AS integer[])) "
                    "GROUP BY t, asn"
                ),
                {"cutoff": cutoff, "asns": top_asns},
            )
        ).all()
        for r in rows:  # columns: (t, asn, d)
            i = idx.get(r[0])
            if i is not None:
                per_top[r[1]][i] = int(r[2] or 0)

    # 4) Unresolved-ASN (Indéterminé) download per slot — its own series so the
    # grey "Autres" band isn't inflated by IPs the ASN DB couldn't resolve.
    indet = [0] * n
    if n:
        rows = (
            await db.execute(
                text(
                    f"SELECT {bin_expr} AS t, SUM(down_bytes) AS d "
                    "FROM traffic_dest_stats "
                    "WHERE bucket_start >= :cutoff AND asn IS NULL "
                    "GROUP BY t"
                ),
                {"cutoff": cutoff},
            )
        ).all()
        for r in rows:  # columns: (t, d)
            i = idx.get(r[0])
            if i is not None:
                indet[i] = int(r[1] or 0)

    # "Autres" = per-slot total minus the top-N sum minus Indéterminé.
    others = [
        max(total_down[i] - sum(per_top[a][i] for a in top_asns) - indet[i], 0)
        for i in range(n)
    ]

    series = [
        {"asn": a, "operator": labels[a], "down_mbps": [_mbps(per_top[a][i]) for i in range(n)]}
        for a in top_asns
    ]
    if any(others):
        series.append(
            {"asn": None, "operator": _OTHERS_LABEL, "down_mbps": [_mbps(v) for v in others]}
        )
    if any(indet):
        series.append(
            {"asn": None, "operator": _UNKNOWN_LABEL, "down_mbps": [_mbps(v) for v in indet]}
        )

    return {
        "period": period if period in _HISTORY_PERIODS else "24h",
        "step_seconds": step,
        "times": [t.isoformat() for t in times],
        "series": series,
        "total_up_mbps": [_mbps(v) for v in total_up],
    }
