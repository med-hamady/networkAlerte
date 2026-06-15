"""Per-site overview cards — thin wrapper over fn_site_overview().

Grouping devices by site, counting infra / online / blocked clients, finding the
oldest down_since, and assembling the per-site down-device and power-device lists
all happen in SQL. The frontend only renders the returned array.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rpc import scalar_json
from app.db.session import get_db

router = APIRouter()


@router.get("/overview")
async def get_sites_overview(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    """Site cards (counts + down/power device lists), sorted by name in SQL."""
    result = await db.execute(text("SELECT fn_site_overview()"))
    return scalar_json(result)
