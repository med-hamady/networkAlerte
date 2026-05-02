import datetime
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.report import SupervisionReport
from app.services import report_service

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_RANGE_DAYS = 366


@router.get("/generate", response_model=SupervisionReport)
async def generate_report(
    date_from: datetime.date = Query(..., description="Date de début (YYYY-MM-DD)"),
    date_to: datetime.date = Query(..., description="Date de fin incluse (YYYY-MM-DD)"),
    db: AsyncSession = Depends(get_db),
) -> SupervisionReport:
    """Génère un rapport de supervision sur la période donnée."""
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to doit être >= date_from")
    if (date_to - date_from).days > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Plage maximale : {MAX_RANGE_DAYS} jours",
        )
    logger.info("Generating supervision report from %s to %s", date_from, date_to)
    return await report_service.generate_report(db, date_from, date_to)
