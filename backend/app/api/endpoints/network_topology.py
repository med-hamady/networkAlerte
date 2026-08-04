"""Inter-site topology endpoint — thin wrapper over site_topology_service.

``GET`` is served **entirely from our own database** and never talks to the UISP
controller. The wiring lives in ``site_links``, refreshed once a day by
``site_topology_sync_job``; the health of each link is read live from
``devices``/``device_metrics``, which our pollers already keep current.

That split is the point. The wiring only changes when the field team installs a
backhaul — a few times a year — whereas link health changes constantly. Serving
the page straight from the controller meant downloading ~1300 devices, ~1400
sites and ~1300 links on every tab open, and again every two minutes through the
page's auto-refresh.

``POST /sync`` forces a refresh for the case the daily cadence cannot cover: a
backhaul installed this morning, which the operator wants on the map now.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import site_topology_service, uisp_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def get_network_topology(
    root: str | None = Query(
        None,
        description="Site racine du parcours. Par défaut TOPOLOGY_ROOT_SITE ; "
                    "repli sur le site de plus haut degré, signalé par root_source.",
    ),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Graphe inter-sites : sites, liaisons, couches et santé de chaque liaison.

    Lecture de notre base uniquement. `synced_at` date le **câblage** ; la santé
    affichée, elle, est de maintenant.

    Le graphe n'est **pas un arbre** (boucles de redondance mesurées sur le
    parc) : `sites[].depth` donne la couche, et `layout.extra_edges` les arêtes
    hors arbre, à tracer autrement — jamais à masquer.

    `available: false` tant que le câblage n'a jamais été synchronisé : la carte
    est alors absente plutôt que vide, une carte vide se lisant comme un réseau
    sans liaisons.
    """
    return await site_topology_service.get_site_topology(db, root=root)


@router.post("/sync")
async def sync_network_topology(db: AsyncSession = Depends(get_db)) -> dict:
    """Rapatrie le câblage depuis le contrôleur maintenant, sans attendre le job.

    Le seul chemin de ce module qui parle à UISP. À utiliser après une
    intervention terrain — sinon le job quotidien suffit.

    Une erreur de transport remonte en **502** : mieux vaut dire que le
    rapatriement a échoué que laisser croire à une topologie à jour. La table
    reste intacte dans ce cas, comme lorsque le contrôleur ne rend aucun lien.
    """
    try:
        result = await site_topology_service.sync_site_links(db)
    except uisp_service.UISPAuthError as exc:
        logger.warning("Sync topologie : authentification UISP refusée (%s)", exc)
        raise HTTPException(
            status_code=502,
            detail="Le contrôleur UISP a refusé l'authentification.",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Sync topologie : contrôleur UISP injoignable (%s)", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Contrôleur UISP injoignable : {exc}",
        ) from exc

    if result.get("ok"):
        await db.commit()
    return result
