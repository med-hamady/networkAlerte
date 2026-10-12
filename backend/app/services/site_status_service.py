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

``devices_up`` / ``devices_down`` comptent les équipements d'INFRA du site, sur
le même périmètre que le compteur « 14/1 » de `/topology`
(`INFRA_SITE_DEVICE_TYPES`, importé — jamais recopié). Les LR abonnés en sont
exclus : ils portent le site de leur AP, un abonné éteint fausserait le compte.
Un équipement ``unknown`` n'entre dans aucun des deux.

Lecture seule, une requête, aucun appel aux équipements.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.schemas.site_status import SiteStatus, SiteStatusResponse
from app.services.site_topology_service import INFRA_SITE_DEVICE_TYPES

_SWITCH_TYPE = "uisp_switch"


def site_statuses_from_devices(
    rows: list[tuple[str | None, str | None, str | None]],
) -> list[SiteStatus]:
    """(site, device_type, status) → un état par site, trié par nom. Logique pure."""
    sites: dict[str, dict] = {}
    for site, device_type, status in rows:
        if not site or not site.strip():
            continue  # équipement sans site : rien à rattacher
        entry = sites.setdefault(
            site, {"has_switch": False, "switch_down": False, "up": 0, "down": 0},
        )
        if device_type == _SWITCH_TYPE:
            entry["has_switch"] = True
            entry["switch_down"] = entry["switch_down"] or status == "down"
        if status == "up":
            entry["up"] += 1
        elif status == "down":
            entry["down"] += 1
    return [
        SiteStatus(
            site=site,
            status="down" if e["switch_down"] else "up",
            devices_up=e["up"],
            devices_down=e["down"],
        )
        for site, e in sorted(sites.items())
        if e["has_switch"]  # règle A : sans switch, rien pour juger le site
    ]


async def get_site_statuses(db: AsyncSession) -> SiteStatusResponse:
    rows = (
        await db.execute(
            select(Device.site, Device.device_type, Device.status).where(
                Device.device_type.in_(INFRA_SITE_DEVICE_TYPES)
            )
        )
    ).all()
    return SiteStatusResponse(sites=site_statuses_from_devices([tuple(r) for r in rows]))
