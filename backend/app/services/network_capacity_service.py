"""Network client-capacity overview — consumed vs available client slots.

For every base-station Rocket we know two numbers:

  - **max clients** : the ceiling that opens the ``rocket_client_overload``
    incident, computed by :func:`alert_rules._rocket_overload_threshold`
    (per-family base at 10 MHz + step per +10 MHz of channel width).
  - **current clients** : the live connected-peer count (``peer_count`` —
    ``len(all_peers)`` for LTU, SNMP station count for airMAX).

Both are read from ``device_metrics`` (latest-only collapse, persisted by the
LTU/airMAX poll jobs) via a single ``DISTINCT ON`` query. The service rolls them
up by radio **family** (LTU / airMAX) for the whole network and by **site**
(``Rocket.location``), and lists each site's Rockets for the drill-down.

A Rocket whose channel width is unknown (no airOS creds → no ``chanbw``) has no
computable ceiling: it is **excluded** from the consumed/capacity totals and
counted separately under ``unknown`` so the page can surface it.
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Rocket
from app.services import threshold_service
from app.services.alert_rules import _rocket_overload_threshold

_SITE_FALLBACK = "Sans site"
_CAPACITY_METRICS = ("peer_count", "channel_width_mhz")


async def _fetch_latest_capacity_metrics(
    db: AsyncSession, device_ids: list[int],
) -> dict[int, dict[str, float]]:
    """Latest ``peer_count`` + ``channel_width_mhz`` per Rocket, read from
    ``device_metrics`` — ``{device_id: {metric_name: value}}``.

    One ``DISTINCT ON`` query served by ``ix_device_metrics_lookup``; both
    metrics are collapse-only (one fresh row per ``(device_id, metric_name)``)
    so this is the latest poll value, freshness ≤ poll interval (30-60 s)."""
    if not device_ids:
        return {}
    sql = text(
        """
        SELECT DISTINCT ON (device_id, metric_name)
               device_id, metric_name, metric_value
        FROM device_metrics
        WHERE device_id = ANY(CAST(:ids AS integer[]))
          AND metric_name = ANY(CAST(:names AS text[]))
        ORDER BY device_id, metric_name, collected_at DESC
        """
    )
    rows = (
        await db.execute(sql, {"ids": device_ids, "names": list(_CAPACITY_METRICS)})
    ).all()
    out: dict[int, dict[str, float]] = defaultdict(dict)
    for r in rows:
        out[r.device_id][r.metric_name] = float(r.metric_value)
    return out


def _empty_bucket() -> dict[str, int]:
    return {"consumed": 0, "capacity": 0, "available": 0, "rockets": 0, "unknown": 0}


def _finalize(bucket: dict[str, int]) -> dict[str, int]:
    """available = capacity − consumed, never negative (over-subscribed AP)."""
    bucket["available"] = max(bucket["capacity"] - bucket["consumed"], 0)
    return bucket


async def get_network_capacity(db: AsyncSession) -> dict:
    """Whole-network + per-site client-capacity roll-up (see module docstring)."""
    settings = await threshold_service.get_effective_settings(db, get_settings())

    # Column-only select (NOT `select(Rocket)`) : loading full Rocket ORM
    # objects would eager-load the `lrs` relationship (lazy="selectin") for every
    # Rocket — materialising the whole ~600-LR subscriber tree just to read 4
    # scalar columns per AP. Rows carry no relationships → no extra query.
    # PTP LiteBeams (inter-site links) are their own device_type, not Rockets, so
    # they're naturally excluded here — only base-station Rockets serve clients.
    rockets = (
        await db.execute(
            select(
                Rocket.id,
                Rocket.name,
                Rocket.location,
                Rocket.radio_tech,
                Rocket.max_clients_override,
            )
        )
    ).all()
    latest = await _fetch_latest_capacity_metrics(db, [r.id for r in rockets])

    families = {"ltu": _empty_bucket(), "airmax": _empty_bucket()}
    sites: dict[str, dict] = {}

    for rocket in rockets:
        airmax = rocket.radio_tech == "airmax"
        family = "airmax" if airmax else "ltu"
        metrics = latest.get(rocket.id, {})
        width = metrics.get("channel_width_mhz")
        current = int(metrics.get("peer_count") or 0)
        override = rocket.max_clients_override
        # Auto formula value (None if width unknown) — surfaced separately so the
        # UI can show it as the "automatic" reference even when an override is set.
        max_clients_auto = _rocket_overload_threshold(settings, airmax, width)
        # Effective ceiling: a manual override replaces the formula entirely and
        # applies even without a known width.
        max_clients = _rocket_overload_threshold(settings, airmax, width, override)

        site_name = (rocket.location or "").strip() or _SITE_FALLBACK
        site = sites.setdefault(
            site_name,
            {
                "site": site_name,
                "ltu": _empty_bucket(),
                "airmax": _empty_bucket(),
                "rockets": [],
            },
        )
        site["rockets"].append(
            {
                "id": rocket.id,
                "name": rocket.name,
                "family": family,
                "current_clients": current,
                "max_clients": max_clients,
                "max_clients_auto": max_clients_auto,
                "max_clients_override": override,
                "channel_width_mhz": width,
            }
        )

        fam_bucket = families[family]
        site_bucket = site[family]
        fam_bucket["rockets"] += 1
        site_bucket["rockets"] += 1

        if max_clients is None:
            # Unknown channel width → no computable ceiling → excluded from totals.
            fam_bucket["unknown"] += 1
            site_bucket["unknown"] += 1
            continue

        for bucket in (fam_bucket, site_bucket):
            bucket["consumed"] += current
            bucket["capacity"] += max_clients

    for bucket in families.values():
        _finalize(bucket)

    site_list = []
    for site in sites.values():
        _finalize(site["ltu"])
        _finalize(site["airmax"])
        # Total des Rockets à capacité indéterminée du site (toutes familles) —
        # calculé ici pour que le front l'affiche tel quel sans re-sommer.
        site["unknown"] = site["ltu"]["unknown"] + site["airmax"]["unknown"]
        site["rockets"].sort(key=lambda r: r["name"].lower())
        site_list.append(site)
    site_list.sort(key=lambda s: s["site"].lower())

    return {"families": families, "sites": site_list}
