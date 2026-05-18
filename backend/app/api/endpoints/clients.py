"""Per-client cumulative consumption.

Each LR's traffic is captured by the parent Rocket's LTU HTTP API
(`/api/v1.0/statistics`), which exposes per-peer 64-bit cumulative byte
counters in `wireless.peers[i].common.counters.txBytes` / `rxBytes`.
The `ltu_api_poll_job` (60 s cadence) fans these counters out as
DeviceMetric rows on each child LR under the names `peer_tx_bytes` and
`peer_rx_bytes`.

This endpoint reports one row per LR client. The `period` parameter selects:
  - "24h" / "7d" / "30d" : sum of positive deltas over a sliding window
  - "lifetime"           : latest counter value as-is (cumulative since the
                           peer last associated to the AP — resets on AP or
                           CPE reboot). Use `peer_uptime_s` as context.
"""

from __future__ import annotations

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.device import Lr
from app.models.device_metric import DeviceMetric

router = APIRouter()

# Max plausible bytes per 60 s poll interval — 1 Gbps × 60 s ≈ 7.5 GB. Any
# computed delta exceeding this is rejected as a counter glitch.
_MAX_PLAUSIBLE_DELTA_BYTES = 8 * 1024 ** 3

Period = Literal["24h", "7d", "30d", "lifetime"]

_PERIOD_TO_TIMEDELTA: dict[str, datetime.timedelta] = {
    "24h": datetime.timedelta(hours=24),
    "7d":  datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
}


class ClientConsumption(BaseModel):
    """Per-client consumption, customer-centric.

    download_bytes = traffic the customer received (AP→CPE), sourced from
                     the API's `txBytes` on `peer_tx_bytes`.
    upload_bytes   = traffic the customer sent (CPE→AP), sourced from
                     `rxBytes` on `peer_rx_bytes`.
    peer_uptime_s  = how long the peer has been associated with the AP.
                     For the "lifetime" view this is the meaningful
                     denominator ("X Go en Y jours").
    """
    device_id: int
    name: str
    ip_address: str
    rocket_id: int | None
    rocket_name: str | None
    download_bytes: int
    upload_bytes: int
    total_bytes: int
    samples: int
    has_data: bool
    peer_uptime_s: float | None


class ClientConsumptionResponse(BaseModel):
    period: Period
    period_start: datetime.datetime | None  # null for "lifetime"
    period_end: datetime.datetime
    # Earliest collected_at actually present in DB for this window. When this
    # is later than period_start, the displayed totals only cover the time
    # from data_start to now — useful after a fresh deployment.
    data_start: datetime.datetime | None
    items: list[ClientConsumption]


def _sum_positive_deltas(samples: list[float]) -> int:
    """Sum positive deltas between successive samples.

    LTU API exposes 64-bit counters that effectively never wrap, so the only
    legitimate negative delta is a firmware reset (CPE/AP reboot) — skip
    that step but resume on the next sample. Positive deltas beyond the
    plausible per-cycle budget are also rejected as counter glitches.
    """
    if len(samples) < 2:
        return 0
    total = 0
    prev = samples[0]
    for curr in samples[1:]:
        delta = curr - prev
        if 0 <= delta <= _MAX_PLAUSIBLE_DELTA_BYTES:
            total += int(delta)
        prev = curr
    return total


async def _fetch_latest_value_per_lr(
    db: AsyncSession,
    lr_ids: list[int],
    metric_names: tuple[str, ...],
) -> dict[int, dict[str, float]]:
    """Return `{device_id: {metric_name: latest_value}}` using row_number()."""
    rn = func.row_number().over(
        partition_by=(DeviceMetric.device_id, DeviceMetric.metric_name),
        order_by=DeviceMetric.collected_at.desc(),
    ).label("rn")
    subq = (
        select(
            DeviceMetric.device_id,
            DeviceMetric.metric_name,
            DeviceMetric.metric_value,
            rn,
        )
        .where(
            DeviceMetric.device_id.in_(lr_ids),
            DeviceMetric.metric_name.in_(metric_names),
        )
        .subquery()
    )
    rows = await db.execute(
        select(subq.c.device_id, subq.c.metric_name, subq.c.metric_value)
        .where(subq.c.rn == 1)
    )
    out: dict[int, dict[str, float]] = {}
    for device_id, metric_name, metric_value in rows.all():
        out.setdefault(device_id, {})[metric_name] = float(metric_value)
    return out


@router.get("/consumption", response_model=ClientConsumptionResponse)
async def get_clients_consumption(
    period: Period = Query("24h", description="24h, 7d, 30d, or lifetime"),
    db: AsyncSession = Depends(get_db),
) -> ClientConsumptionResponse:
    """Return cumulative download/upload per LR client."""
    now = datetime.datetime.now(datetime.UTC)

    lrs_result = await db.execute(
        select(Lr).options(selectinload(Lr.rocket))
    )
    lrs: list[Lr] = list(lrs_result.scalars().all())
    if not lrs:
        return ClientConsumptionResponse(
            period=period,
            period_start=None if period == "lifetime" else now,
            period_end=now,
            data_start=None,
            items=[],
        )

    lr_ids = [lr.id for lr in lrs]

    # Latest peer_uptime_s for every LR — used as context in all views.
    uptime_latest = await _fetch_latest_value_per_lr(
        db, lr_ids, ("peer_uptime_s",),
    )
    uptime_by_device: dict[int, float | None] = {
        did: vals.get("peer_uptime_s") for did, vals in uptime_latest.items()
    }

    if period == "lifetime":
        latest = await _fetch_latest_value_per_lr(
            db, lr_ids, ("peer_tx_bytes", "peer_rx_bytes"),
        )
        items: list[ClientConsumption] = []
        for lr in lrs:
            data = latest.get(lr.id, {})
            download = int(data.get("peer_tx_bytes", 0))
            upload   = int(data.get("peer_rx_bytes", 0))
            has_data = "peer_tx_bytes" in data or "peer_rx_bytes" in data
            items.append(ClientConsumption(
                device_id=lr.id,
                name=lr.name,
                ip_address=lr.ip_address,
                rocket_id=lr.rocket_id,
                rocket_name=lr.rocket.name if lr.rocket else None,
                download_bytes=download,
                upload_bytes=upload,
                total_bytes=download + upload,
                samples=1 if has_data else 0,
                has_data=has_data,
                peer_uptime_s=uptime_by_device.get(lr.id),
            ))
        items.sort(key=lambda i: i.total_bytes, reverse=True)
        return ClientConsumptionResponse(
            period=period,
            period_start=None,
            period_end=now,
            data_start=None,
            items=items,
        )

    # Sliding window — sum positive deltas
    window = _PERIOD_TO_TIMEDELTA[period]
    cutoff = now - window
    metric_names = ("peer_tx_bytes", "peer_rx_bytes")

    samples_result = await db.execute(
        select(
            DeviceMetric.device_id,
            DeviceMetric.metric_name,
            DeviceMetric.metric_value,
            DeviceMetric.collected_at,
        )
        .where(
            DeviceMetric.device_id.in_(lr_ids),
            DeviceMetric.metric_name.in_(metric_names),
            DeviceMetric.collected_at >= cutoff,
        )
        .order_by(DeviceMetric.device_id, DeviceMetric.collected_at)
    )

    by_device: dict[int, dict[str, list[float]]] = {
        lr.id: {n: [] for n in metric_names} for lr in lrs
    }
    earliest: datetime.datetime | None = None
    for device_id, metric_name, metric_value, collected_at in samples_result.all():
        by_device[device_id][metric_name].append(metric_value)
        if earliest is None or collected_at < earliest:
            earliest = collected_at

    items = []
    for lr in lrs:
        per_metric = by_device[lr.id]
        download_samples = per_metric["peer_tx_bytes"]
        upload_samples   = per_metric["peer_rx_bytes"]
        has_data = len(download_samples) >= 2 or len(upload_samples) >= 2
        download = _sum_positive_deltas(download_samples) if has_data else 0
        upload   = _sum_positive_deltas(upload_samples)   if has_data else 0
        items.append(ClientConsumption(
            device_id=lr.id,
            name=lr.name,
            ip_address=lr.ip_address,
            rocket_id=lr.rocket_id,
            rocket_name=lr.rocket.name if lr.rocket else None,
            download_bytes=download,
            upload_bytes=upload,
            total_bytes=download + upload,
            samples=len(download_samples) + len(upload_samples),
            has_data=has_data,
            peer_uptime_s=uptime_by_device.get(lr.id),
        ))
    items.sort(key=lambda i: i.total_bytes, reverse=True)

    return ClientConsumptionResponse(
        period=period,
        period_start=cutoff,
        period_end=now,
        data_start=earliest,
        items=items,
    )
