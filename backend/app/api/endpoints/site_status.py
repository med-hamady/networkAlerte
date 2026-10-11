"""GET /site-status — état up/down de chaque site, pour un système tiers.

⚠️ Router gardé par sa clé dédiée `SITE_STATUS_API_KEY` (cf. `api/router.py`).
Une dépendance de router étant ADDITIVE, toute route ajoutée ici s'ouvrirait
à cette clé en silence : la liste permise est verrouillée par
`tests/test_site_status.py` (`_ROUTES_OPEN_TO_THE_KEY`).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.site_status import SiteStatusResponse
from app.services import site_status_service

router = APIRouter()


@router.get("", response_model=SiteStatusResponse)
async def get_site_status(db: AsyncSession = Depends(get_db)) -> SiteStatusResponse:
    """État courant de chaque site : ``up`` ou ``down``.

    Un site est ``down`` quand son switch ne répond plus au ping (la règle du
    Journal des coupures) ; avec plusieurs switches, un seul tombé suffit. Les
    sites sans switch ne sont pas listés. Lecture en base, appel rapide.
    """
    return await site_status_service.get_site_statuses(db)
