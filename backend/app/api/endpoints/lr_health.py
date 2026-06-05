from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.lr_health import LiveLinkHealthResponse
from app.services import lr_health_service

router = APIRouter()


@router.get("/bad-installations", response_model=LiveLinkHealthResponse)
async def list_bad_installations(
    db: AsyncSession = Depends(get_db),
) -> LiveLinkHealthResponse:
    """LR clients dont l'**état actuel** trahit une mauvaise liaison, pires d'abord.

    Interroge chaque équipement en direct (LTU via le Rocket parent, airMAX via
    airOS) et classe sur les valeurs courantes : 5 indicateurs de niveau, verdict
    suspect (≥3/5) ou critique (≥4/5). Les LR injoignables en live sont exclus.

    NB : le rapport `/reports` reste sur la moyenne glissante 30 j via
    `lr_health_service.get_bad_installations` (matview) — deux lectures
    volontairement distinctes : la page = maintenant, le rapport = étude 30 j.
    """
    return await lr_health_service.get_live_link_health(db)
