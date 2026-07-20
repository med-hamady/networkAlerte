"""Client map — the LR coordinates, split into plottable points and bad data.

Coordinates come from the LR itself (`devices.latitude/longitude`, read over SSH
from its airOS `system.cfg` by `lr_plan_service`). They are stored VERBATIM: the
device is the source of truth and we never rewrite what it says.

That honesty stops at the map. The provisioned data is dirty — UISP pushed its
own mistakes into `system.cfg`, so the fleet carries clients pinned to Yemen
(14.66, 49.38), Gaza (31.52, 34.43) and Saudi Arabia, plus longitude sign flips
(18.08, **+**16.01 → Chad instead of Nouakchott). Field-measured 2026-07-17.

So this service splits, it does not filter: everything inside the Mauritania
bounding box is a map `point`; everything outside is an `outlier` — kept, named,
and handed back so the UI can list it for field correction. A map is believed;
an outlier list is actionable. Dropping the bad rows silently would hide a
provisioning bug, and plotting them would make the map useless (auto-zoom to
world scale).

Only LRs are covered: `lr_plan_service` is the sole reader of `system.cfg`, and
it only walks LRs. Devices with no position (never provisioned — every M5 so
far) are simply absent; they are counted, not invented.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Lr
from app.models.site_location import SiteLocation

logger = logging.getLogger(__name__)

# Generous bounding box around Mauritania (lat 14.5..27.5 N, lon -17.5..-4.5 W).
# Deliberately loose: it must reject Yemen/Gaza/Trinidad, not adjudicate whether
# a client sits on the right street.
MR_LAT_MIN, MR_LAT_MAX = 14.5, 27.5
MR_LON_MIN, MR_LON_MAX = -17.5, -4.5


def is_plausible(lat: float | None, lon: float | None) -> bool:
    """True when the point falls inside the Mauritania bounding box."""
    if lat is None or lon is None:
        return False
    return MR_LAT_MIN <= lat <= MR_LAT_MAX and MR_LON_MIN <= lon <= MR_LON_MAX


def _reason(lat: float, lon: float) -> str:
    """Explain, in operator terms, why a point was rejected.

    The sign-flip case is called out by name because it is the cheapest to fix:
    the latitude is already right, only the longitude lost its minus sign.
    """
    if MR_LAT_MIN <= lat <= MR_LAT_MAX and -MR_LON_MAX <= lon <= -MR_LON_MIN:
        return "longitude probablement inversée (signe +, devrait être négatif)"
    if lat == 0 and lon == 0:
        return "position à 0,0 (jamais renseignée réellement)"
    return "hors Mauritanie"


async def get_client_map(session: AsyncSession) -> dict:
    """Return the sites, the plottable client points, the outliers and the stats.

    Shape::

        {
          "sites":    [ {site, latitude, longitude, client_count, ...}, ... ],
          "points":   [ {id, name, latitude, longitude, status, site, ...}, ... ],
          "outliers": [ {id, name, latitude, longitude, reason, ...}, ... ],
          "stats":    {total, with_position, plotted, outliers, without_position,
                       sites, linked},
          "bbox":     {lat_min, lat_max, lon_min, lon_max},
        }

    Each plotted client carries the ``site`` it belongs to; the UI draws the link
    from the client to that site's marker. The link is by SITE, not by sector
    Rocket: every sector of a mast shares one position, so which sector serves
    the client changes nothing on the map.
    """
    rows = (await session.execute(select(Lr))).scalars().all()
    site_rows = (await session.execute(select(SiteLocation))).scalars().all()
    # Exact-string join on `devices.site` — site names carry oddities such as
    # the double space in "A2  ARF1", so they are never normalised here.
    site_coords = {s.site: s for s in site_rows}

    points: list[dict] = []
    outliers: list[dict] = []
    without_position = 0
    per_site_clients: dict[str, int] = {}

    for lr in rows:
        if lr.latitude is None or lr.longitude is None:
            without_position += 1
            continue
        entry = {
            "id": lr.id,
            "name": lr.name,
            "ip_address": lr.ip_address,
            "status": lr.status,
            "site": lr.site,
            "model_variant": lr.model_variant,
            "ap_name": lr.uisp_ap_name,
            "client_blocked": lr.client_blocked,
            "plan_download_mbps": lr.plan_download_mbps,
            "plan_upload_mbps": lr.plan_upload_mbps,
            "latitude": lr.latitude,
            "longitude": lr.longitude,
        }
        if is_plausible(lr.latitude, lr.longitude):
            # A link can be drawn only when we also know where the site is.
            entry["linked"] = lr.site in site_coords
            points.append(entry)
            if entry["linked"]:
                per_site_clients[lr.site] = per_site_clients.get(lr.site, 0) + 1
        else:
            outliers.append({**entry, "reason": _reason(lr.latitude, lr.longitude)})

    sites = [
        {
            "site": s.site,
            "latitude": s.latitude,
            "longitude": s.longitude,
            "source": s.source,
            # Clients actually drawn for this site — not the full roster: a
            # client with no position of its own cannot be linked to anything.
            "client_count": per_site_clients.get(s.site, 0),
        }
        for s in sorted(site_rows, key=lambda x: x.site)
    ]

    stats = {
        "total": len(rows),
        "with_position": len(points) + len(outliers),
        "plotted": len(points),
        "outliers": len(outliers),
        "without_position": without_position,
        "sites": len(sites),
        "linked": sum(1 for p in points if p["linked"]),
    }
    logger.debug("client map: %s", stats)
    return {
        "sites": sites,
        "points": points,
        "outliers": outliers,
        "stats": stats,
        "bbox": {
            "lat_min": MR_LAT_MIN, "lat_max": MR_LAT_MAX,
            "lon_min": MR_LON_MIN, "lon_max": MR_LON_MAX,
        },
    }
