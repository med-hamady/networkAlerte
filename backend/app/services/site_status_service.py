"""État courant (up/down) de chaque site, pour un système tiers.

Règle = celle du Journal des coupures et des graphes « Pannes par site »
(`fn_site_outage_summary`, `night_outage_report_service`) : **une panne de site
est une panne de son SWITCH**. Reprise telle quelle pour que cet endpoint ne
puisse jamais contredire l'historique des coupures.

- Un site à plusieurs switches vaut le PIRE d'entre eux : un seul switch
  ``down`` met le site ``down``.
- On n'affirme une panne que sur constat : seul ``status == "down"`` (posé par
  `infra_ping_job` au seuil anti-flap) compte. ``unknown`` n'est pas une panne.
- Un site sans switch n'est PAS listé : la règle n'a rien pour le juger.
- Le nom de site est rendu tel qu'en base (`devices.site`), sans normalisation
  — le double espace de « A2  ARF1 » est voulu, cf. CLAUDE.md.

Lecture seule, une requête, aucun appel aux équipements.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.schemas.site_status import SiteStatus, SiteStatusResponse

_SWITCH_TYPE = "uisp_switch"


def site_statuses_from_switches(rows: list[tuple[str | None, str | None]]) -> list[SiteStatus]:
    """(site, status du switch) → un état par site, trié par nom. Logique pure."""
    down_by_site: dict[str, bool] = {}
    for site, status in rows:
        if not site or not site.strip():
            continue  # switch sans site : rien à rattacher
        down_by_site[site] = down_by_site.get(site, False) or status == "down"
    return [
        SiteStatus(site=site, status="down" if down else "up")
        for site, down in sorted(down_by_site.items())
    ]


async def get_site_statuses(db: AsyncSession) -> SiteStatusResponse:
    rows = (
        await db.execute(
            select(Device.site, Device.status).where(Device.device_type == _SWITCH_TYPE)
        )
    ).all()
    return SiteStatusResponse(sites=site_statuses_from_switches([tuple(r) for r in rows]))
