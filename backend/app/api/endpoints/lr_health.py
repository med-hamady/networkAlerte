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

    Interroge chaque AF60 en direct (UDAPI locale) et n'expose que les liens avec
    ≥2/4 indicateurs actifs (signal, SNR, potentiel, capacité — seuils af60_*).
    Verdict suspect (≥2) ou critique (≥3). Les AF60 injoignables sont exclus.
    """
    return await lr_health_service.get_live_site_link_health(db)
