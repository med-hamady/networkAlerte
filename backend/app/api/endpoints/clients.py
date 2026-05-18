"""Per-client cumulative consumption.

Each LR's traffic is captured by the parent Rocket's LTU HTTP API
(`/api/v1.0/statistics`), which exposes per-peer 64-bit cumulative byte
counters in `wireless.peers[i].common.counters.txBytes` / `rxBytes`.
The `ltu_api_poll_job` (60 s cadence) fans these counters out as
DeviceMetric rows on each child LR under the names `peer_tx_bytes` and
`peer_rx_bytes`.

All views (24h / 7d / 30d / lifetime) sum the *positive deltas* between
successive samples — never the raw counter snapshot. The firmware counter
resets to 0 when the peer re-associates (AP reboot, CPE reboot, radio
link dropping out) and a fresh "lifetime" snapshot would lose every byte
seen before that reset. Sum-of-positive-deltas treats a reset as "delta
contributes nothing this cycle, then accumulation resumes" — so the
supervisor keeps the full history regardless of how many times the
hardware counter is wiped.
"""

from __future__ import annotations

import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
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
    first_sample_at = oldest sample we have for this LR in the queried
                      window. For the lifetime view this is the meaningful
                      "supervision started on" timestamp.
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
    first_sample_at: datetime.datetime | None


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


@router.get("/consumption", response_model=ClientConsumptionResponse)
async def get_clients_consumption(
    period: Period = Query("24h", description="24h, 7d, 30d, or lifetime"),
    db: AsyncSession = Depends(get_db),
) -> ClientConsumptionResponse:
    """Return cumulative download/upload per LR client."""
    now = datetime.datetime.now(datetime.UTC)

    # "lifetime" walks the entire DeviceMetric history — pick a cutoff well
    # before the project existed so a single code path handles every period.
    if period == "lifetime":
        cutoff: datetime.datetime | None = None
        query_lower_bound = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)
    else:
        cutoff = now - _PERIOD_TO_TIMEDELTA[period]
        query_lower_bound = cutoff

    lrs_result = await db.execute(
        select(Lr).options(selectinload(Lr.rocket))
    )
    lrs: list[Lr] = list(lrs_result.scalars().all())
    if not lrs:
        return ClientConsumptionResponse(
            period=period,
            period_start=cutoff,
            period_end=now,
            data_start=None,
            items=[],
        )

    lr_ids = [lr.id for lr in lrs]
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
            DeviceMetric.collected_at >= query_lower_bound,
        )
        .order_by(DeviceMetric.device_id, DeviceMetric.collected_at)
    )

    by_device: dict[int, dict[str, list[float]]] = {
        lr.id: {n: [] for n in metric_names} for lr in lrs
    }
    first_sample_by_device: dict[int, datetime.datetime] = {}
    earliest_global: datetime.datetime | None = None
    for device_id, metric_name, metric_value, collected_at in samples_result.all():
        by_device[device_id][metric_name].append(metric_value)
        if device_id not in first_sample_by_device:
            first_sample_by_device[device_id] = collected_at
        if earliest_global is None or collected_at < earliest_global:
            earliest_global = collected_at

    items: list[ClientConsumption] = []
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
            first_sample_at=first_sample_by_device.get(lr.id),
        ))
    items.sort(key=lambda i: i.total_bytes, reverse=True)

    return ClientConsumptionResponse(
        period=period,
        period_start=cutoff,
        period_end=now,
        data_start=earliest_global,
        items=items,
    )
