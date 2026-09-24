import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rpc import scalar_json
from app.db.session import get_db
from app.schemas.network_uptime import DowntimeLogResponse
from app.services import network_uptime_service, night_outage_report_service

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


@router.get("/window-report/pdf")
async def get_window_outage_report_pdf(
    start: datetime.date = Query(..., description="Premier jour (YYYY-MM-DD, UTC)"),
    end: datetime.date = Query(..., description="Dernier jour, INCLUS (YYYY-MM-DD, UTC)"),
    from_hour: int = Query(0, ge=0, le=23, description="Ouverture de la tranche (heure UTC)"),
    to_hour: int = Query(8, ge=0, le=23, description="Fermeture de la tranche (heure UTC)"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Rapport PDF des coupures de TOUS les sites, limité à une tranche horaire
    répétée chaque jour de la période (défaut 00:00 → 08:00).

    `to_hour <= from_hour` = la tranche enjambe minuit (22 → 6). Seules les
    coupures qui COMMENCENT dans la tranche sont comptées ; voir
    `night_outage_report_service` pour la règle complète.
    """
    if end < start:
        raise HTTPException(status_code=422, detail="`end` doit être postérieur ou égal à `start`")
    if from_hour == to_hour:
        raise HTTPException(
            status_code=422, detail="La tranche horaire ne peut pas être vide (début = fin)"
        )
    days = (end - start).days + 1
    if days > night_outage_report_service.MAX_REPORT_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Période trop longue : {night_outage_report_service.MAX_REPORT_DAYS} jours au plus",
        )
    report = await night_outage_report_service.build_night_report(
        db, start, end, from_hour, to_hour
    )
    pdf = night_outage_report_service.render_pdf(report)
    filename = f"coupures-{from_hour:02d}h-{to_hour:02d}h-{start:%Y%m%d}-{end:%Y%m%d}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
