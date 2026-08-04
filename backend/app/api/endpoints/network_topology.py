"""Inter-site topology endpoint — thin wrapper over site_topology_service.

The graph (which site is linked to which, its layered layout, and each link's
health read from OUR poll) lives in ``app.services.site_topology_service``; this
module only wires the HTTP route.

⚠️ **Live read of the controller.** Three calls to UISP per request (devices,
sites, data-links) — the wiring is not stored anywhere on our side, so there is
nothing in the database to serve from. The graph changes only when the field team
installs a backhaul, so this is deliberately NOT on a poll: paying three API
calls on the rare page view beats keeping a table in sync with something that
moves a few times a year. If the page ever gets hot, the fix is a scheduled job
writing the edges down — not a cache with a made-up TTL.
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
    """Graphe inter-sites : sites, arêtes, couches et santé de chaque liaison.

    Le graphe n'est **pas un arbre** (boucles de redondance mesurées sur le
    parc) : `sites[].depth` donne la couche, et `layout.extra_edges` les arêtes
    hors arbre, à tracer autrement — jamais à masquer.

    Une erreur de transport vers le contrôleur remonte en **502** plutôt que de
    rendre un graphe partiel : une carte amputée serait lue comme une carte
    complète, donc comme des sites sans liaison.
    """
    try:
        return await site_topology_service.get_site_topology(db, root=root)
    except uisp_service.UISPAuthError as exc:
        logger.warning("Topologie : authentification UISP refusée (%s)", exc)
        raise HTTPException(
            status_code=502,
            detail="Le contrôleur UISP a refusé l'authentification.",
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("Topologie : contrôleur UISP injoignable (%s)", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Contrôleur UISP injoignable : {exc}",
        ) from exc
