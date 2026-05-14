import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.network_uptime import DowntimeLogResponse
from app.services import network_uptime_service

router = APIRouter()


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
