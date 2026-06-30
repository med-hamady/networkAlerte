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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.traffic_dest_stat import TrafficDestStat

# Supported look-back windows for the volume view.
_PERIODS: dict[str, datetime.timedelta] = {
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
}

_UNKNOWN_LABEL = "Indéterminé"


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
