"""Association d'un équipement à un client CRM.

Le contrat est volontairement minimal : **une MAC** (l'équipement) et **un id
CRM** (le client). Rien d'autre. C'est la transposition exacte du geste manuel —
chercher la MAC dans UISP, la voir en « unknown », cliquer dessus et choisir le
client dans le formulaire.

Si l'équipement est absent de UISP, on lui **pose d'abord la clé du contrôleur**
(`uisp_enrollment_service`) puis on poursuit : sans clé il ne se déclare jamais,
et il n'y aurait rien à associer.

⚠️ **Le nom d'hôte de l'équipement n'est JAMAIS utilisé** — ni comme critère, ni
en sortie. Un CPE s'annonce sous un nom qu'il s'est donné lui-même (souvent
« <contrat>-<nom du client> ») : s'en servir pour rapprocher un abonné revient à
identifier par un nom, et les noms se ressemblent (« Keida, Mariem Oumar » vs
« Sall, Mariem oumar » — deux clients CRM distincts). Le seul identifiant du
client est l'**id CRM** fourni par l'appelant ; le seul identifiant de
l'équipement est sa **MAC**.

Plomberie interne — jamais exposée dans le contrat
--------------------------------------------------
UISP ne rattache pas un équipement à un client, il le rattache à un **site**, et
c'est le site qui porte le lien CRM (`ucrm.client.id`). Traduire l'id CRM en site
est donc notre travail, pas celui de l'appelant. Le mot « site » n'apparaît ni
dans l'entrée ni dans la sortie de l'API.

**6 clients sur 1402 possèdent plusieurs services**, donc plusieurs sites. Pour
eux, l'id du client ne suffit pas : l'appelant précise alors `crm_service_id`.
Sans ce paramètre, l'association est refusée avec la liste des services
candidats — jamais choisie au hasard, ce qui rattacherait l'abonné au mauvais
service en silence et durablement.

⚠️ **Le service se désigne par son ID, jamais par son nom.** Les services d'un
même client portent régulièrement des noms IDENTIQUES (le client 11 en a trois
nommés « 20Mb TEST », le client 1005 trois « AirFiber 15Mb Familial ») : le nom
ne distingue rien. L'id, lui, est unique sur tout le contrôleur — 1410 ids pour
1410 sites, zéro doublon — donc il détermine le site à lui seul.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Lr
from app.schemas.device import normalize_mac
from app.services import uisp_enrollment_service, uisp_service

logger = logging.getLogger(__name__)


class AssignmentError(RuntimeError):
    """Échec métier (client ou équipement introuvable, choix impossible)."""


class AmbiguousClientError(AssignmentError):
    """Le client CRM possède plusieurs sites — l'appelant doit trancher."""

    def __init__(self, message: str, candidates: list[dict[str, Any]]):
        super().__init__(message)
        self.candidates = candidates


class AlreadyAssignedError(AssignmentError):
    """L'équipement appartient DÉJÀ à un autre client — refus sans `reassign`.

    Déplacer un CPE d'un abonné vers un autre est une opération légitime (le
    matériel est récupéré et réinstallé ailleurs) mais lourde de conséquences :
    l'ancien abonné perd le rattachement de son équipement. Une MAC saisie de
    travers produirait ce dégât en silence. On exige donc un geste explicite.
    """

    def __init__(self, message: str, current_crm_client_id: str | None):
        super().__init__(message)
        self.current_crm_client_id = current_crm_client_id


class DeviceUnreachableError(AssignmentError):
    """La clé n'a pas pu être posée (SSH/équipement) — ce n'est PAS un « introuvable ».

    Distinguée pour que l'appelant ne reçoive pas un 404 sur un échec technique :
    un 404 l'enverrait vérifier ses identifiants alors que le problème est sur
    l'équipement.
    """


def _client() -> uisp_service.UISPClient:
    """Client UISP de CE service — avec le token d'ÉCRITURE quand il existe.

    L'assignation est la seule opération du projet qui modifie le contrôleur.
    Elle utilise donc un token dédié (`UISP_WRITE_API_TOKEN`) : tout le reste,
    dont le sync quotidien qui parcourt 1300 équipements sans surveillance,
    continue de tourner avec un token en lecture seule, physiquement incapable
    d'écrire. Repli sur `uisp_api_token` si le token dédié n'est pas configuré,
    pour qu'un déploiement à une seule clé reste fonctionnel.
    """
    s = get_settings()
    return uisp_service.UISPClient(
        s.uisp_base_url,
        username=s.uisp_username,
        password=s.uisp_password,
        api_token=s.uisp_write_api_token or s.uisp_api_token,
        verify_tls=s.uisp_verify_tls,
        timeout=s.uisp_request_timeout,
    )


def is_configured() -> bool:
    """Le contrôleur est-il joignable côté configuration ? (URL + une auth)"""
    s = get_settings()
    return bool(s.uisp_base_url and (s.uisp_api_token or (s.uisp_username and s.uisp_password)))


def _ident(device: dict) -> dict:
    return device.get("identification") or {}


def _mac_of(device: dict) -> str | None:
    """MAC normalisée d'un équipement UISP, ou None si absente/illisible."""
    raw = _ident(device).get("mac")
    if not raw:
        return None
    try:
        return normalize_mac(raw)
    except ValueError:
        return None


def _crm_client(site: dict) -> dict:
    return (site.get("ucrm") or {}).get("client") or {}


def _crm_service(site: dict) -> dict:
    return (site.get("ucrm") or {}).get("service") or {}


def _sites_of_crm_client(sites: list[dict], crm_client_id: str) -> list[dict]:
    """Sites rattachés à ce client CRM. Comparaison en CHAÎNE.

    UISP rend l'id CRM sous forme de chaîne ("2064") ; un appelant l'enverra
    volontiers en entier. On normalise des deux côtés plutôt que de supposer un
    type — une comparaison 2064 == "2064" échouerait silencieusement et rendrait
    « client introuvable » pour un client qui existe.
    """
    needle = str(crm_client_id).strip()
    return [s for s in sites if str(_crm_client(s).get("id") or "").strip() == needle]


def _crm_client_of_site(sites: list[dict], site_id: str | None) -> str | None:
    """Id du client CRM propriétaire de ce site — pour nommer l'actuel détenteur."""
    if not site_id:
        return None
    for s in sites:
        if (s.get("identification") or {}).get("id") == site_id:
            return _crm_client(s).get("id")
    return None


async def assign_device_to_crm_client(
    session: AsyncSession,
    mac: str,
    crm_client_id: str,
    crm_service_id: str | None = None,
    reassign: bool = False,
) -> dict[str, Any]:
    """Associe l'équipement `mac` au client CRM `crm_client_id`.

    `crm_service_id` n'est nécessaire que pour les clients à **plusieurs
    services** ; il est alors le seul moyen de désigner lequel (leurs noms sont
    souvent identiques). Fourni, il détermine la cible à lui seul — mais on
    vérifie quand même qu'il appartient bien au client annoncé : un appelant qui
    intervertit deux ids rattacherait sinon l'équipement à un tout autre abonné,
    sans que rien ne le signale.

    Retourne un rapport étape par étape, pour qu'un échec soit toujours
    attribuable à un point précis plutôt qu'à « ça n'a pas marché ».

    Un équipement absent de UISP reçoit d'abord la clé du contrôleur ; son
    enregistrement n'étant pas instantané, la réponse porte alors
    ``pending_registration`` et l'appel se rejoue dans la minute — ce n'est pas
    une erreur, c'est une attente.
    """
    normalized = normalize_mac(mac)  # ValueError -> 400 chez l'appelant
    # Une chaîne vide vaut « non fourni » : les intégrations sérialisent souvent
    # un champ absent en "". Sans ça, `""` partait dans la branche « service
    # imposé » et renvoyait un 404 incompréhensible sur un service sans id.
    wanted = str(crm_service_id).strip() if crm_service_id is not None else ""

    report: dict[str, Any] = {
        "mac": normalized,
        "crm_client_id": str(crm_client_id),
        "crm_service_id": wanted or None,
        "assigned": False,
        "key_injected": False,
        "steps": [],
    }

    # ── 1. Résolution de la cible ─────────────────────────────────────────────
    # Faite AVANT toute action sur l'équipement : inutile de poser une clé pour
    # un client qui n'existe pas, et un échec ici ne laisse aucune trace.
    # Un seul fetch des sites, réutilisé ensuite pour identifier l'éventuel
    # détenteur actuel de l'équipement (≈1400 sites : on ne les relit pas deux fois).
    all_sites = await _client().fetch_sites()
    sites = _sites_of_crm_client(all_sites, crm_client_id)
    if not sites:
        raise AssignmentError(
            f"Aucun client CRM d'id {crm_client_id} dans UISP. Vérifier l'id côté "
            f"CRM — l'association n'a pas été tentée."
        )

    if wanted:
        site = next(
            (s for s in sites if str(_crm_service(s).get("id") or "").strip() == wanted),
            None,
        )
        if site is None:
            # Le service existe peut-être, mais chez QUELQU'UN D'AUTRE — le dire
            # explicitement, c'est la faute d'inversion d'ids qu'on veut attraper.
            raise AssignmentError(
                f"Le service CRM {crm_service_id} n'appartient pas au client "
                f"{crm_client_id} (ses services : "
                f"{', '.join(str(_crm_service(s).get('id')) for s in sites)}). "
                f"L'association n'a pas été tentée."
            )
    elif len(sites) > 1:
        candidates = [
            {
                "crm_service_id": _crm_service(s).get("id"),
                "service_name": _crm_service(s).get("name"),
                "address": (s.get("description") or {}).get("address"),
            }
            for s in sites
        ]
        raise AmbiguousClientError(
            f"Le client CRM {crm_client_id} possède {len(sites)} services : "
            f"préciser crm_service_id. Leurs noms peuvent être identiques — "
            f"c'est l'id qui les distingue.",
            candidates,
        )
    else:
        site = sites[0]

    crm = _crm_client(site)
    site_id = (site.get("identification") or {}).get("id")
    report["client_name"] = crm.get("name")
    report["crm_service_id"] = _crm_service(site).get("id")
    report["steps"].append({
        "step": "resolve_target", "ok": True,
        "message": (
            f"Client CRM {crm_client_id} — {crm.get('name')}, "
            f"service {_crm_service(site).get('id')}."
        ),
    })

    # ── 2. L'équipement est-il connu du contrôleur ? ──────────────────────────
    devices = await _client().fetch_devices()
    device = next((d for d in devices if _mac_of(d) == normalized), None)

    if device is None:
        # Absent de UISP : il n'a pas la clé du contrôleur, donc il ne s'est
        # jamais déclaré. On la lui pose — sans elle il n'y a rien à associer.
        lr = (
            await session.execute(select(Lr).where(Lr.mac_address == normalized))
        ).scalar_one_or_none()
        if lr is None:
            raise AssignmentError(
                f"L'équipement {normalized} n'est ni connu de UISP ni présent dans "
                f"notre base : aucun moyen de le joindre pour lui poser la clé."
            )
        ok, msg = await uisp_enrollment_service.enroll_lr(session, lr)
        report["key_injected"] = ok
        report["steps"].append({"step": "inject_key", "ok": ok, "message": msg})
        if not ok:
            raise DeviceUnreachableError(
                f"La clé UISP n'a pas pu être posée sur {normalized} : {msg}"
            )
        report["pending_registration"] = True
        report["message"] = (
            f"Clé UISP posée sur {normalized}. L'équipement doit maintenant se "
            f"déclarer au contrôleur — rejouer l'association d'ici une minute "
            f"pour l'attacher au client CRM {crm_client_id}."
        )
        return report

    ident = _ident(device)
    report["steps"].append({
        "step": "inject_key", "ok": True,
        "message": "Équipement déjà connu du contrôleur — clé en place.",
    })

    # ── 3. Association ────────────────────────────────────────────────────────
    current_site = (ident.get("site") or {}).get("id")
    if current_site == site_id:
        # Déjà chez le bon client : aucune écriture. Rejouable sans effet.
        report.update({"assigned": True})
        report["steps"].append({
            "step": "assign", "ok": True,
            "message": "Déjà associé à ce client — rien à faire.",
        })
        report["message"] = (
            f"{normalized} est déjà associé au client CRM {crm_client_id}."
        )
        return report

    # ⚠️ L'équipement appartient à un AUTRE client. Le déplacer est légitime
    # (matériel récupéré et réinstallé ailleurs) mais retire son rattachement à
    # l'abonné actuel — et une MAC saisie de travers ferait ce dégât en silence.
    # Refus par défaut : on exige un geste explicite, comme pour toute action
    # qui peut toucher le mauvais abonné.
    if current_site and not reassign:
        owner = _crm_client_of_site(all_sites, current_site)
        raise AlreadyAssignedError(
            f"L'équipement {normalized} est déjà rattaché au client CRM "
            f"{owner or '(inconnu)'}. Le déplacer vers le client {crm_client_id} "
            f"retirerait son équipement à l'abonné actuel — relancer avec "
            f"reassign=true si c'est bien l'intention.",
            owner,
        )

    await _client().assign_device_to_site(ident.get("id"), site_id)
    report["assigned"] = True
    report["steps"].append({
        "step": "assign", "ok": True,
        "message": f"Associé au client CRM {crm_client_id}.",
    })
    logger.warning(
        "UISP ASSIGN — %s associé au client CRM %s",
        normalized, crm_client_id,
    )
    report["message"] = f"{normalized} associé au client CRM {crm_client_id}."
    return report
