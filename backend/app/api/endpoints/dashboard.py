"""Dashboard KPI bar — thin wrapper over the fn_dashboard_summary() SQL function.

All counting (total / up / down / sites / pannes / clients / open incidents)
happens in the database; this route only forwards the ready-to-render JSON.
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rpc import scalar_json
from app.db.session import get_db

router = APIRouter()


@router.get("/summary")
async def get_dashboard_summary(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """KPI counts for the dashboard header (computed entirely in SQL)."""
    result = await db.execute(text("SELECT fn_dashboard_summary()"))
    return scalar_json(result)


@router.get("/network-health")
async def get_network_health(
    start: datetime.datetime = Query(..., description="ISO-8601 window start (inclusive)"),
    end: datetime.datetime = Query(..., description="ISO-8601 window end (inclusive)"),
    merge_gap_seconds: int = Query(
        300,
        ge=0,
        le=3_600,
        description="Fusion des coupures séparées de moins de X s (anti-flapping).",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """« Santé du réseau » sur [start, end] : disponibilité moyenne des sites.

    Par équipement infra, dispo = 100 × (1 − downtime / fenêtre) ; par site =
    moyenne de ses équipements ; santé réseau = moyenne des sites. Même fenêtre
    que les graphes « Pannes par site » (cf. fn_network_health).
    """
    if end <= start:
        raise HTTPException(status_code=400, detail="`end` must be strictly after `start`")
    if start.tzinfo is None:
        start = start.replace(tzinfo=datetime.UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=datetime.UTC)
    result = await db.execute(
        text("SELECT fn_network_health(:start, :end, :gap)"),
        {"start": start, "end": end, "gap": merge_gap_seconds},
    )
    return scalar_json(result)
