from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.lr_health import LiveLinkHealthResponse, SiteLinkHealthResponse
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

    Seule source du scoring liens clients : le rapport `/reports` ne fait plus de
    moyenne 30 j (métriques radio non historisées depuis le collapse latest-only).
    """
    return await lr_health_service.get_live_link_health(db)


@router.get("/site-links", response_model=SiteLinkHealthResponse)
async def list_site_links(
    db: AsyncSession = Depends(get_db),
) -> SiteLinkHealthResponse:
    """Liaisons backhaul site-à-site (airFiber 60) dégradées, pires d'abord.

    Critère unique : la **dernière capacité totale** lue en base est sous le
    plancher d'affichage (``af60_capacity_display_min_mbps``, 1.95 Gb/s). Lecture
    de la dernière valeur de ``device_metrics`` — pas d'interrogation live.
    """
    return await lr_health_service.get_site_link_health(db)
