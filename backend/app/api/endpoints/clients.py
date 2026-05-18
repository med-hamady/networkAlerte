"""Per-client cumulative consumption over a sliding time window.

Each LR's traffic is captured by the parent Rocket's LTU HTTP API
(`/api/v1.0/statistics`), which exposes per-peer 64-bit cumulative byte
counters in `wireless.peers[i].common.counters.txBytes` / `rxBytes`.
The `ltu_api_poll_job` (60 s cadence) fans these counters out as
DeviceMetric rows on each child LR under the names `peer_tx_bytes` and
`peer_rx_bytes`.

This endpoint sums the positive deltas of those counters over the requested
window — gracefully skipping firmware reboots that reset the counter to 0 —
and returns one row per LR client.
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

Period = Literal["24h", "7d", "30d"]

_PERIOD_TO_TIMEDELTA: dict[Period, datetime.timedelta] = {
    "24h": datetime.timedelta(hours=24),
    "7d":  datetime.timedelta(days=7),
    "30d": datetime.timedelta(days=30),
}


class ClientConsumption(BaseModel):
    device_id: int
    name: str
    ip_address: str
    rocket_id: int | None
    rocket_name: str | None
    rx_bytes: int
    tx_bytes: int
    total_bytes: int
    samples: int
    has_data: bool


class ClientConsumptionResponse(BaseModel):
    period: Period
    period_start: datetime.datetime
    period_end: datetime.datetime
    items: list[ClientConsumption]


def _sum_positive_deltas(samples: list[float]) -> int:
    """Sum positive deltas between successive samples.

    The LTU API returns 64-bit counters that effectively never wrap, so the
    only legitimate negative delta is a firmware reset (CPE reboot) → we
    skip that step but resume accumulating on the next sample. Any positive
    delta larger than the plausible per-cycle budget is also rejected as a
    counter glitch.
    """
    if len(samples) < 2:
        return 0
    total = 0
    prev = samples[0]
    for curr in samples[1:]:
        delta = curr - prev
        if 0 <= delta <= _MAX_PLAUSIBLE_DELTA_BYTES:
            total += int(delta)
        # else: reset (delta < 0) or glitch (delta > budget) → skip
        prev = curr
    return total


@router.get("/consumption", response_model=ClientConsumptionResponse)
async def get_clients_consumption(
    period: Period = Query("24h", description="24h, 7d, or 30d"),
    db: AsyncSession = Depends(get_db),
) -> ClientConsumptionResponse:
    """Return cumulative RX/TX consumption per LR client over the window."""
    now = datetime.datetime.now(datetime.UTC)
    window = _PERIOD_TO_TIMEDELTA[period]
    cutoff = now - window

    lrs_result = await db.execute(
        select(Lr).options(selectinload(Lr.rocket))
    )
    lrs: list[Lr] = list(lrs_result.scalars().all())

    if not lrs:
        return ClientConsumptionResponse(
            period=period,
            period_start=cutoff,
            period_end=now,
            items=[],
        )

    lr_ids = [lr.id for lr in lrs]
    metric_names = ("peer_tx_bytes", "peer_rx_bytes")

    samples_result = await db.execute(
        select(
            DeviceMetric.device_id,
            DeviceMetric.metric_name,
            DeviceMetric.metric_value,
        )
        .where(
            DeviceMetric.device_id.in_(lr_ids),
            DeviceMetric.metric_name.in_(metric_names),
            DeviceMetric.collected_at >= cutoff,
        )
        .order_by(DeviceMetric.device_id, DeviceMetric.collected_at)
    )

    # device_id → metric_name → ordered list of values
    by_device: dict[int, dict[str, list[float]]] = {
        lr.id: {n: [] for n in metric_names} for lr in lrs
    }
    for device_id, metric_name, metric_value in samples_result.all():
        by_device[device_id][metric_name].append(metric_value)

    items: list[ClientConsumption] = []
    for lr in lrs:
        per_metric = by_device[lr.id]
        rx_samples = per_metric["peer_rx_bytes"]
        tx_samples = per_metric["peer_tx_bytes"]
        has_data = len(rx_samples) >= 2 or len(tx_samples) >= 2
        rx = _sum_positive_deltas(rx_samples) if has_data else 0
        tx = _sum_positive_deltas(tx_samples) if has_data else 0

        items.append(ClientConsumption(
            device_id=lr.id,
            name=lr.name,
            ip_address=lr.ip_address,
            rocket_id=lr.rocket_id,
            rocket_name=lr.rocket.name if lr.rocket else None,
            rx_bytes=rx,
            tx_bytes=tx,
            total_bytes=rx + tx,
            samples=len(rx_samples) + len(tx_samples),
            has_data=has_data,
        ))

    items.sort(key=lambda i: i.total_bytes, reverse=True)

    return ClientConsumptionResponse(
        period=period,
        period_start=cutoff,
        period_end=now,
        items=items,
    )
