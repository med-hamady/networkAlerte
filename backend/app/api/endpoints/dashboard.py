"""Dashboard KPI bar — thin wrapper over the fn_dashboard_summary() SQL function.

All counting (total / up / down / sites / pannes / clients / open incidents)
happens in the database; this route only forwards the ready-to-render JSON.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
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
