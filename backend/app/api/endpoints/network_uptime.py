import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rpc import scalar_json
from app.db.session import get_db
from app.schemas.network_uptime import DowntimeLogResponse
from app.services import network_uptime_service

router = APIRouter()


@router.get("/site-summary")
async def get_site_outage_summary(
    start: datetime.datetime = Query(..., description="ISO-8601 window start (inclusive)"),
    end: datetime.datetime = Query(..., description="ISO-8601 window end (inclusive)"),
    merge_gap_seconds: int = Query(
        300,
        ge=0,
        le=3_600,
        description="Fuse consecutive outages separated by less than this (default 300 s).",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Per-site outage rollup for the "pannes par site" charts.

    Merges availability incidents per device (gaps-and-islands), clips to the
    window, and aggregates episode counts + cumulated downtime by site — all in
    SQL via fn_site_outage_summary(). Returns `{by_pannes, by_downtime}`, each a
    list of sites already sorted descending with their affected-device breakdown.
    """
    if end <= start:
        raise HTTPException(status_code=400, detail="`end` must be strictly after `start`")
    if start.tzinfo is None:
        start = start.replace(tzinfo=datetime.UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=datetime.UTC)
    result = await db.execute(
        text("SELECT fn_site_outage_summary(:start, :end, :gap)"),
        {"start": start, "end": end, "gap": merge_gap_seconds},
    )
    return scalar_json(result)


@router.get("/downtime-log", response_model=DowntimeLogResponse)
async def get_downtime_log(
    start: datetime.datetime = Query(..., description="ISO-8601 window start (inclusive)"),
    end: datetime.datetime = Query(..., description="ISO-8601 window end (inclusive)"),
    merge_gap_seconds: int = Query(
        300,
        ge=0,
        le=3_600,
        description=(
            "Fuse two consecutive incidents into a single episode if separated"
            " by less than this many seconds. Default 300 (5 min) — typical"
            " flapping signature. Set to 0 to disable merging."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> DowntimeLogResponse:
    """List every infrastructure device (Rocket / Switch / UISP Power) that was
    down at least once during [start, end], with each individual outage episode.

    Client LR devices are excluded — see /api/v1/lr-health for those.
    """
    if end <= start:
        raise HTTPException(status_code=400, detail="`end` must be strictly after `start`")
    if start.tzinfo is None:
        start = start.replace(tzinfo=datetime.UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=datetime.UTC)
    return await network_uptime_service.get_downtime_log(
        db,
        start=start,
        end=end,
        merge_gap_seconds=merge_gap_seconds,
    )
