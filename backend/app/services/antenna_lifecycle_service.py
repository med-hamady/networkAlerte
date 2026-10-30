"""Suppression et remplacement d'une antenne abonnée — demandés par S-Ticketing.

Deux gestes de fin de vie d'un CPE, désigné par sa **MAC** (l'identité de
l'antenne partout dans le projet, côté UISP comme chez nous) :

* **Suppression** — l'antenne est retirée de UISP Network puis de notre base.
* **Remplacement** — la nouvelle antenne est rattachée au client (dans UISP ET
  chez nous, par le même chemin que `POST /uisp/assign`), puis l'ancienne est
  retirée de UISP et de notre base.

⚠️ **Le remplacement RATTACHE D'ABORD, SUPPRIME ENSUITE** — l'inverse de l'ordre
de la spécification, et c'est voulu. Supprimer d'abord laisserait le client sans
aucune antenne si le rattachement échoue (nouvelle antenne injoignable, pas
encore déclarée au contrôleur) : un état qu'aucun rejeu ne répare, puisque
l'ancienne n'existe plus nulle part. Dans cet ordre, un échec laisse l'ancienne
en place, et rejouer l'appel termine le travail (le rattachement déjà fait est
un no-op, il ne reste que la suppression). Le résultat rendu à S-Ticketing est
le même.

Garde-fous — une suppression ne se rattrape pas
-----------------------------------------------
* **Une antenne d'INFRA n'est jamais supprimée ici** (Rocket, AF60, switch…) :
  ni par notre base (`device_type != "lr"`), ni par UISP (`classify_device`, le
  classificateur unique du projet). Une MAC de Rocket saisie dans un ticket
  arracherait un secteur entier de l'inventaire.
* **Une antenne vue EN LIGNE est refusée sauf `force`** (notre ping la voit
  `up`, ou UISP la voit `active`). Une antenne démontée ne répond plus : en
  ligne, c'est le plus souvent une MAC erronée — et la découverte radio la
  recréerait d'ailleurs dans la minute.
* **Un client BLOQUÉ est refusé, sans dérogation** (décision opérateur du
  2026-10-26) : l'intention de coupure (impayé) et le filtre de contenu vivent
  sur la ligne de l'antenne, la supprimer les effacerait. Il faut d'abord faire
  débloquer — c'est alors un geste tracé dans le journal FAI.
* **Remplacement : l'ancienne antenne doit APPARTENIR au client annoncé**
  (client CRM de son site UISP, sinon celui de notre fiche). Propriétaire
  différent ou impossible à établir ⇒ refus : supprimer l'antenne d'un autre
  abonné est exactement l'accident que ce contrôle empêche.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device, Lr
from app.schemas.device import normalize_mac
from app.services import uisp_assignment_service, uisp_service
from app.services.uisp_sync_service import classify_device

logger = logging.getLogger(__name__)

# Temps laissé à la suppression de l'ancienne antenne APRÈS le rattachement de
# la nouvelle, qui consomme au plus `uisp_assignment_service.CALL_BUDGET_S`
# (100 s). 100 + 15 reste sous le `proxy_read_timeout` de 120 s de la location
# nginx `/api/v1/antennas/` — verrouillé par un test qui relit nginx.conf.
DELETE_STEP_BUDGET_S = 15


class AntennaError(RuntimeError):
    """Refus ou échec métier. `error_code` est le code STABLE exposé à l'appelant."""

    def __init__(self, message: str, error_code: str, **extra: Any):
        super().__init__(message)
        self.error_code = error_code
        self.extra = extra


def _ident(device: dict) -> dict:
    return device.get("identification") or {}


def _uisp_device_by_mac(devices: list[dict], mac: str) -> dict | None:
    return next((d for d in devices if uisp_assignment_service._mac_of(d) == mac), None)


async def _local_device_by_mac(session: AsyncSession, mac: str) -> Device | None:
    """Ligne `devices` portant cette MAC, QUEL QUE SOIT son type (requête polymorphe) :
    c'est ce qui permet de reconnaître une MAC d'infra et de la refuser."""
    return (
        await session.execute(select(Device).where(Device.mac_address == mac))
    ).scalar_one_or_none()


def _assert_client_antenna(mac: str, local: Device | None, remote: dict | None) -> None:
    if local is not None and not isinstance(local, Lr):
        raise AntennaError(
            f"{mac} n'est pas une antenne abonnée ({local.device_type} « {local.name} ») : "
            f"un équipement d'infrastructure ne se supprime pas par cette API.",
            "not_a_client_antenna",
        )
    if remote is not None:
        ident = _ident(remote)
        overview = remote.get("overview") or {}
        if classify_device(
            ident.get("type"), ident.get("role"), ident.get("model"),
            overview.get("wirelessMode"),
        ) is not None:
            raise AntennaError(
                f"{mac} est un équipement d'infrastructure dans UISP "
                f"({ident.get('model') or ident.get('type')}) : il ne se supprime pas "
                f"par cette API.",
                "not_a_client_antenna",
            )


def _is_online(local: Device | None, remote: dict | None) -> bool:
    if local is not None and local.status == "up":
        return True
    return ((remote or {}).get("overview") or {}).get("status") == "active"


def _assert_offline(mac: str, local: Device | None, remote: dict | None, force: bool) -> None:
    if _is_online(local, remote) and not force:
        raise AntennaError(
            f"L'antenne {mac} est vue EN LIGNE : une antenne démontée ne répond plus, "
            f"la MAC est peut-être erronée. Relancer avec force=true si la suppression "
            f"est bien l'intention.",
            "device_online",
        )


def _assert_not_blocked(mac: str, local: Device | None, *, include_content_filter: bool) -> None:
    if not isinstance(local, Lr):
        return
    if local.client_blocked:
        raise AntennaError(
            f"Le client de l'antenne {mac} est BLOQUÉ (motif : "
            f"{local.client_blocked_reason or 'non précisé'}). La supprimer effacerait "
            f"le blocage : faire débloquer le client d'abord.",
            "client_blocked",
        )
    if include_content_filter and (local.blocked_categories or local.block_adult_content):
        raise AntennaError(
            f"L'antenne {mac} porte un filtre de contenu actif : il serait perdu avec "
            f"elle. Retirer le filtre d'abord.",
            "content_filter_active",
        )


async def _remove(
    session: AsyncSession,
    uisp: uisp_service.UISPClient,
    mac: str,
    local: Device | None,
    remote: dict | None,
    steps: list[dict],
    timeout: float | None = None,
) -> dict[str, bool]:
    """UISP d'abord, notre base ensuite : si le contrôleur refuse, rien n'a bougé.

    L'inverse laisserait une antenne supprimée chez nous mais toujours dans UISP,
    que le sync des stations recréerait le lendemain matin.
    """
    removed = {"removed_from_uisp": False, "removed_from_database": False}
    if remote is not None:
        call = uisp.delete_device(_ident(remote).get("id"))
        removed["removed_from_uisp"] = await (
            asyncio.wait_for(call, timeout=timeout) if timeout else call
        )
        steps.append({
            "step": "delete_uisp", "ok": True,
            "message": "Supprimée de UISP." if removed["removed_from_uisp"]
            else "Déjà absente de UISP.",
        })
    else:
        steps.append({"step": "delete_uisp", "ok": True, "message": "Absente de UISP — rien à supprimer."})

    if local is not None:
        logger.warning(
            "ANTENNA DELETE — %s « %s » supprimée de la base (cascade)", mac, local.name,
        )
        await session.delete(local)
        await session.flush()
        removed["removed_from_database"] = True
        steps.append({"step": "delete_database", "ok": True, "message": "Supprimée de la base A2 Supervision."})
    else:
        steps.append({"step": "delete_database", "ok": True, "message": "Absente de la base — rien à supprimer."})
    return removed


async def delete_antenna(session: AsyncSession, mac: str, *, force: bool = False) -> dict[str, Any]:
    """Supprime l'antenne `mac` de UISP puis de notre base.

    Rejouable : une antenne déjà absente des deux côtés rend `device_not_found`
    (404), jamais un succès inventé — S-Ticketing doit pouvoir distinguer « c'est
    fait » de « la MAC ne désigne rien ».
    """
    normalized = normalize_mac(mac)  # ValueError -> 400
    uisp = uisp_assignment_service._client()
    remote = _uisp_device_by_mac(await uisp.fetch_devices(), normalized)
    local = await _local_device_by_mac(session, normalized)
    if remote is None and local is None:
        raise AntennaError(
            f"Aucune antenne {normalized} ni dans UISP ni dans A2 Supervision.",
            "device_not_found",
        )
    _assert_client_antenna(normalized, local, remote)
    _assert_not_blocked(normalized, local, include_content_filter=False)
    _assert_offline(normalized, local, remote, force)

    steps: list[dict] = []
    removed = await _remove(session, uisp, normalized, local, remote, steps)
    return {
        "mac": normalized,
        "deleted": True,
        **removed,
        "steps": steps,
        "message": f"Antenne {normalized} supprimée.",
    }


async def replace_antenna(
    session: AsyncSession,
    crm_client_id: str,
    old_mac: str,
    new_mac: str,
    *,
    crm_service_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Remplace l'antenne `old_mac` du client `crm_client_id` par `new_mac`.

    `force` ne lève QUE le refus « ancienne antenne encore en ligne ». Il ne
    passe ni sur un client bloqué, ni sur une ancienne antenne qui appartient à
    un autre client, ni sur une nouvelle antenne déjà rattachée à un autre
    client (celle-ci se déplace explicitement par `POST /uisp/assign` + `force`).
    """
    started = time.monotonic()
    old = normalize_mac(old_mac)
    new = normalize_mac(new_mac)
    client_id = str(crm_client_id).strip()
    if old == new:
        raise AntennaError("L'ancienne et la nouvelle antenne ont la même MAC.", "same_device")

    uisp = uisp_assignment_service._client()
    devices = await uisp.fetch_devices()
    sites = await uisp.fetch_sites()
    old_remote = _uisp_device_by_mac(devices, old)
    old_local = await _local_device_by_mac(session, old)
    new_local = await _local_device_by_mac(session, new)
    _assert_client_antenna(new, new_local, _uisp_device_by_mac(devices, new))

    report: dict[str, Any] = {
        "crm_client_id": client_id,
        "old_mac": old,
        "new_mac": new,
        "replaced": False,
        "new_assigned": False,
        "pending_registration": False,
        "retry_after_seconds": None,
        "removed_from_uisp": False,
        "removed_from_database": False,
        "steps": [],
    }

    if old_remote is None and old_local is None:
        # Rejeu d'un remplacement déjà terminé : l'ancienne a disparu ET la
        # nouvelle est déjà chez ce client. Sinon la MAC ne désigne rien.
        new_remote = _uisp_device_by_mac(devices, new)
        new_site = (_ident(new_remote or {}).get("site") or {}).get("id")
        owner = uisp_assignment_service._crm_owner_of_site(sites, new_site)
        if str(owner.get("id") or "") != client_id:
            raise AntennaError(
                f"Aucune antenne {old} ni dans UISP ni dans A2 Supervision.",
                "old_device_not_found",
            )
        report["steps"].append({
            "step": "resolve_old", "ok": True,
            "message": "Ancienne antenne déjà supprimée — remplacement déjà effectué.",
        })
    else:
        _assert_client_antenna(old, old_local, old_remote)
        crm_service_id = _assert_owned_by(
            old, client_id, old_local, old_remote, sites, crm_service_id,
        )
        _assert_not_blocked(old, old_local, include_content_filter=True)
        _assert_offline(old, old_local, old_remote, force)
        report["steps"].append({
            "step": "resolve_old", "ok": True,
            "message": f"Ancienne antenne {old} appartient au client CRM {client_id}.",
        })

    assignment = await uisp_assignment_service.assign_device_to_crm_client(
        session, new, client_id, crm_service_id, started=started,
    )
    report["steps"].extend(assignment["steps"])
    report["client_name"] = assignment.get("client_name")
    report["crm_service_id"] = assignment.get("crm_service_id")
    if not assignment["assigned"]:
        report["pending_registration"] = assignment["pending_registration"]
        report["retry_after_seconds"] = assignment["retry_after_seconds"]
        report["message"] = (
            f"Nouvelle antenne {new} pas encore déclarée au contrôleur : l'ancienne "
            f"{old} est CONSERVÉE. Rejouer l'appel dans "
            f"{assignment['retry_after_seconds']} s pour terminer."
        )
        return report
    report["new_assigned"] = True
    if new_local is None:
        # Le rattachement UISP est fait ; notre fiche n'existe pas encore. Elle
        # sera créée par la découverte radio dès qu'un AP la rapporte, ou par le
        # sync des stations — avec ce client CRM, lu dans UISP. Rien à inventer ici.
        report["steps"].append({
            "step": "database_link", "ok": True,
            "message": "Nouvelle antenne pas encore en base : elle y entrera avec ce "
                       "client au prochain relevé radio ou sync UISP.",
        })

    if old_remote is not None or old_local is not None:
        remaining = max(1.0, started + uisp_assignment_service.CALL_BUDGET_S
                        + DELETE_STEP_BUDGET_S - time.monotonic())
        try:
            report.update(await _remove(
                session, uisp, old, old_local, old_remote, report["steps"], timeout=remaining,
            ))
        except uisp_service.UISPAuthError:
            raise
        except Exception as exc:
            # La nouvelle antenne EST rattachée (UISP l'a déjà enregistré) : seule
            # l'ancienne reste. Le dire, pour que le rejeu soit le geste évident.
            raise AntennaError(
                f"Nouvelle antenne {new} rattachée au client, mais l'ancienne {old} "
                f"n'a pas pu être supprimée ({type(exc).__name__}: {exc}). Rejouer "
                f"l'appel pour terminer.",
                "old_device_delete_failed",
                new_assigned=True,
            ) from exc

    report["replaced"] = True
    report["message"] = f"Antenne {old} remplacée par {new} pour le client CRM {client_id}."
    logger.warning("ANTENNA REPLACE — client CRM %s : %s -> %s", client_id, old, new)
    return report


def _assert_owned_by(
    mac: str,
    client_id: str,
    local: Device | None,
    remote: dict | None,
    sites: list[dict],
    crm_service_id: str | None,
) -> str | None:
    """Vérifie que l'ancienne antenne appartient au client ; rend le service à viser.

    Source de vérité : le client CRM de son site UISP (relu à l'instant), sinon
    celui de notre fiche (posé par le sync). Quand le site UISP est connu, son
    service devient la cible de la nouvelle antenne — c'est ce qui évite de
    demander `crm_service_id` aux clients à plusieurs services : la nouvelle
    antenne prend la place de l'ancienne, sur le même service.
    """
    site_id = (_ident(remote or {}).get("site") or {}).get("id")
    site = next((s for s in sites if _ident(s).get("id") == site_id), None) if site_id else None
    owner = uisp_assignment_service._crm_client(site) if site else {}
    owner_id = str(owner.get("id") or "").strip() or (
        (local.uisp_crm_client_id or "").strip() if isinstance(local, Lr) else ""
    )
    if not owner_id:
        raise AntennaError(
            f"Impossible d'établir le client de l'antenne {mac} (sans client CRM ni dans "
            f"UISP ni dans A2 Supervision) : le remplacement est refusé plutôt que de "
            f"supprimer l'antenne d'un autre abonné.",
            "old_device_owner_unknown",
        )
    if owner_id != client_id:
        raise AntennaError(
            f"L'antenne {mac} appartient au client CRM {owner_id}, pas au client "
            f"{client_id} : remplacement refusé.",
            "old_device_not_owned",
            current_crm_client_id=owner_id,
        )
    if crm_service_id is None and site is not None:
        svc_id = uisp_assignment_service._crm_service(site).get("id")
        return str(svc_id) if svc_id is not None else None
    return crm_service_id
