"""Per-client cumulative consumption over a sliding time window.

Each LR's `ath0` radio interface is polled every 60 s by the SNMP job, which
writes Counter64 (`radio_rx_bytes64` / `radio_tx_bytes64`) and Counter32
(`radio_rx_bytes` / `radio_tx_bytes`) byte counters into `device_metrics`.

This endpoint sums the positive deltas of those counters over the requested
window — handling Counter32 wrap-around and firmware-restart counter resets
— and returns one row per LR client.
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

# ifInOctets / ifOutOctets are Counter32 — wraps at 2**32. A negative delta of
# magnitude < this implies "wrap"; larger implies "reset/restart" (skip).
_COUNTER32_MOD = 2 ** 32

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
    counter_source: Literal["counter64", "counter32", "none"]


class ClientConsumptionResponse(BaseModel):
    period: Period
    period_start: datetime.datetime
    period_end: datetime.datetime
    items: list[ClientConsumption]


def _sum_positive_deltas(
    samples: list[float],
    *,
    is_counter32: bool,
) -> int:
    """Sum positive deltas between successive samples.

    Counter32 wrap is detected when the next sample is *less* than the previous
    one AND the wrap-corrected delta `(2**32 - prev) + curr` stays within the
    plausible per-cycle budget. Anything beyond that (oversized delta in either
    direction) is treated as a counter reset and skipped — the reset itself
    contributes nothing, but the next positive delta resumes accumulation.
    """
    if len(samples) < 2:
        return 0
    total = 0
    prev = samples[0]
    for curr in samples[1:]:
        delta = curr - prev
        if delta >= 0:
            if delta <= _MAX_PLAUSIBLE_DELTA_BYTES:
                total += int(delta)
            # else: glitch / spurious huge jump → skip
        elif is_counter32:
            wrapped = (_COUNTER32_MOD - prev) + curr
            if 0 < wrapped <= _MAX_PLAUSIBLE_DELTA_BYTES:
                total += int(wrapped)
            # else: real reset → skip
        # else (Counter64 negative): real reset → skip
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
    metric_names = (
        "radio_rx_bytes64", "radio_tx_bytes64",
        "radio_rx_bytes",   "radio_tx_bytes",
    )

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
    by_device: dict[int, dict[str, list[float]]] = {lr.id: {n: [] for n in metric_names} for lr in lrs}
    for device_id, metric_name, metric_value in samples_result.all():
        by_device[device_id][metric_name].append(metric_value)

    items: list[ClientConsumption] = []
    for lr in lrs:
        per_metric = by_device[lr.id]
        # Prefer Counter64 series when we have enough samples (≥2 needed for a delta).
        if len(per_metric["radio_rx_bytes64"]) >= 2 or len(per_metric["radio_tx_bytes64"]) >= 2:
            source: Literal["counter64", "counter32", "none"] = "counter64"
            rx = _sum_positive_deltas(per_metric["radio_rx_bytes64"], is_counter32=False)
            tx = _sum_positive_deltas(per_metric["radio_tx_bytes64"], is_counter32=False)
            samples_used = len(per_metric["radio_rx_bytes64"]) + len(per_metric["radio_tx_bytes64"])
        elif len(per_metric["radio_rx_bytes"]) >= 2 or len(per_metric["radio_tx_bytes"]) >= 2:
            source = "counter32"
            rx = _sum_positive_deltas(per_metric["radio_rx_bytes"], is_counter32=True)
            tx = _sum_positive_deltas(per_metric["radio_tx_bytes"], is_counter32=True)
            samples_used = len(per_metric["radio_rx_bytes"]) + len(per_metric["radio_tx_bytes"])
        else:
            source = "none"
            rx = 0
            tx = 0
            samples_used = 0

        items.append(ClientConsumption(
            device_id=lr.id,
            name=lr.name,
            ip_address=lr.ip_address,
            rocket_id=lr.rocket_id,
            rocket_name=lr.rocket.name if lr.rocket else None,
            rx_bytes=rx,
            tx_bytes=tx,
            total_bytes=rx + tx,
            samples=samples_used,
            counter_source=source,
        ))

    items.sort(key=lambda i: i.total_bytes, reverse=True)

    return ClientConsumptionResponse(
        period=period,
        period_start=cutoff,
        period_end=now,
        items=items,
    )
