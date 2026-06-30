"""Top client→Internet destinations by operator/CDN — reads traffic_dest_stats.

Thin roll-up over the aggregates written by the NetFlow collector
(:mod:`app.services.netflow_service`): sum bytes/packets/flows over a period,
group by ASN/operator, rank by volume and add each one's share of the total.
This is the signal the team uses to decide which cache (GGC/FNA/OCA) to request.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.traffic_dest_stat import TrafficDestStat

# Supported look-back windows for the /traffic page.
_PERIODS: dict[str, datetime.timedelta] = {
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
}

_UNKNOWN_LABEL = "Indéterminé"


async def get_top_destinations(
    db: AsyncSession, period: str = "24h", limit: int = 50,
) -> dict:
    """Operators/CDNs ranked by traffic volume over ``period``.

    Returns ``{period, total_bytes, destinations: [{asn, operator, bytes,
    packets, flows, share_pct}]}`` sorted by bytes descending.
    """
    window = _PERIODS.get(period, _PERIODS["24h"])
    cutoff = datetime.datetime.now(datetime.UTC) - window

    rows = (
        await db.execute(
            select(
                TrafficDestStat.asn,
                func.max(TrafficDestStat.as_org).label("as_org"),
                func.sum(TrafficDestStat.bytes).label("bytes"),
                func.sum(TrafficDestStat.packets).label("packets"),
                func.sum(TrafficDestStat.flows).label("flows"),
            )
            .where(TrafficDestStat.bucket_start >= cutoff)
            .group_by(TrafficDestStat.asn)
            .order_by(func.sum(TrafficDestStat.bytes).desc())
            .limit(limit)
        )
    ).all()

    total_bytes = sum(int(r.bytes or 0) for r in rows)
    destinations = [
        {
            "asn": r.asn,
            "operator": r.as_org or (f"AS{r.asn}" if r.asn is not None else _UNKNOWN_LABEL),
            "bytes": int(r.bytes or 0),
            "packets": int(r.packets or 0),
            "flows": int(r.flows or 0),
            "share_pct": round(int(r.bytes or 0) / total_bytes * 100, 1) if total_bytes else 0.0,
        }
        for r in rows
    ]

    return {
        "period": period if period in _PERIODS else "24h",
        "total_bytes": total_bytes,
        "destinations": destinations,
    }
