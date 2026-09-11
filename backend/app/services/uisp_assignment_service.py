"""Association d'un équipement à un client CRM.

Le contrat est volontairement minimal : **une MAC** (l'équipement) et **un id
CRM** (le client). Rien d'autre. C'est la transposition exacte du geste manuel —
chercher la MAC dans UISP, la voir en « unknown », cliquer dessus et choisir le
client dans le formulaire.

Si l'équipement est absent de UISP, on lui **pose d'abord la clé du contrôleur**
(`uisp_enrollment_service`), on **attend qu'il se déclare** au contrôleur, puis
on poursuit dans le MÊME appel : sans clé il ne se déclare jamais, et il n'y
aurait rien à associer.

⚠️ **Le nom d'hôte de l'équipement n'est JAMAIS utilisé** — ni comme critère, ni
en sortie. Un CPE s'annonce sous un nom qu'il s'est donné lui-même (souvent
« <contrat>-<nom du client> ») : s'en servir pour rapprocher un abonné revient à
identifier par un nom, et les noms se ressemblent (« Keida, Mariem Oumar » vs
« Sall, Mariem oumar » — deux clients CRM distincts). Le seul identifiant du
client est l'**id CRM** fourni par l'appelant ; le seul identifiant de
l'équipement est sa **MAC**. (Le NOM CRM du détenteur, lui, vient du CRM et peut
être rendu : c'est une donnée de référence, pas une auto-déclaration.)

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

Attente de l'enregistrement (2026-09-11)
----------------------------------------
Demandé par l'équipe qui consomme la route : le 10/09, le contrôleur a adopté un
équipement en **9 s**, mais l'API avait déjà rendu `pending_registration` sans
tenter le rattachement — il fallait un second appel pour finir un travail qui
tenait dans le premier. On attend donc que l'équipement apparaisse au
contrôleur (`REGISTRATION_WAIT_S`), puis on associe.

⚠️ L'attente est bornée par un **budget GLOBAL** (`CALL_BUDGET_S`), pas seulement
par sa propre durée : la pose de clé attend déjà jusqu'à 45 s l'adoption (plus
la poignée de main SSH), et le proxy coupe à 120 s. Sans budget global, 45 + 60
dépassait le proxy et l'appelant recevait un **504 sur une association réussie**
— le piège déjà corrigé sur `/fai`, `/uisp/assign` puis `/content-filter`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Lr
from app.schemas.device import normalize_mac
from app.services import uisp_enrollment_service, uisp_service

logger = logging.getLogger(__name__)

# ── Attente de l'enregistrement après la pose de clé ─────────────────────────
# Durée max d'attente demandée par l'appelant.
REGISTRATION_WAIT_S = 60
# Cadence d'interrogation du contrôleur pendant l'attente. `enroll_lr` ne rend la
# main qu'une fois l'adoption CONSTATÉE sur l'équipement : la première lecture,
# faite sans pause, trouve donc l'équipement dans la grande majorité des cas.
REGISTRATION_POLL_S = 5
# ⚠️ Budget TOTAL d'un appel, mesuré depuis son début. Doit rester sous le
# `proxy_read_timeout` de la location /api/v1/uisp/assign (120 s, nginx.conf),
# avec de quoi faire l'écriture finale : 20 s de marge. Verrouillé par un test
# qui relit nginx.conf — les deux fichiers ne peuvent pas diverger en silence.
CALL_BUDGET_S = 100
# Délai conseillé à l'appelant avant de rejouer, quand l'enregistrement n'a pas
# été constaté à temps. Le rejeu est sans danger : il reprend là où on s'est
# arrêté (la clé est posée, il ne reste que l'association).
RETRY_AFTER_S = 30


class AssignmentError(RuntimeError):
    """Échec métier (client ou équipement introuvable, choix impossible).

    `error_code` est le code **STABLE** exposé à l'appelant — c'est lui, et non
    le message, qu'une intégration doit tester. Il est OBLIGATOIRE à la
    construction : un code oublié serait une erreur sans code, exactement ce que
    le contrat promet de ne jamais renvoyer.
    """

    def __init__(self, message: str, error_code: str):
        super().__init__(message)
        self.error_code = error_code


class AmbiguousClientError(AssignmentError):
    """Le client CRM possède plusieurs sites — l'appelant doit trancher."""

    def __init__(self, message: str, candidates: list[dict[str, Any]]):
        super().__init__(message, "multiple_services")
        self.candidates = candidates


class AlreadyAssignedError(AssignmentError):
    """L'équipement appartient DÉJÀ à un autre client — refus sans `force`.

    Déplacer un CPE d'un abonné vers un autre est une opération légitime (le
    matériel est récupéré et réinstallé ailleurs) mais lourde de conséquences :
    l'ancien abonné perd le rattachement de son équipement. Une MAC saisie de
    travers produirait ce dégât en silence. On exige donc un geste explicite.
    """

    def __init__(
        self,
        message: str,
        current_crm_client_id: str | None,
        current_client_name: str | None = None,
    ):
        super().__init__(message, "device_already_assigned")
        self.current_crm_client_id = current_crm_client_id
        self.current_client_name = current_client_name


class DeviceUnreachableError(AssignmentError):
    """La clé n'a pas pu être posée (SSH/équipement) — ce n'est PAS un « introuvable ».

    Distinguée pour que l'appelant ne reçoive pas un 404 sur un échec technique :
    un 404 l'enverrait vérifier ses identifiants alors que le problème est sur
    l'équipement.
    """

    def __init__(self, message: str):
        super().__init__(message, "device_unreachable")


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


def _crm_owner_of_site(sites: list[dict], site_id: str | None) -> dict:
    """Client CRM propriétaire de ce site (`{id, name}`), ou `{}` s'il n'en a pas.

    Sert à nommer l'actuel détenteur dans un refus — par son id (ce qui
    l'identifie) ET son nom CRM (ce qui permet à un agent de le reconnaître).
    """
    if not site_id:
        return {}
    for s in sites:
        if (s.get("identification") or {}).get("id") == site_id:
            return _crm_client(s)
    return {}


def registration_deadline(started: float, now: float) -> float:
    """Instant (horloge monotone) auquel l'attente d'enregistrement s'arrête.

    Le plus tôt de : `REGISTRATION_WAIT_S` après le début de l'attente, et la fin
    du budget total de l'appel (`CALL_BUDGET_S` après son début). Si la pose de
    clé a déjà mangé le budget, l'attente est courte ou nulle — et l'appelant
    reçoit `retry_after_seconds` plutôt qu'un 504.
    """
    return min(now + REGISTRATION_WAIT_S, started + CALL_BUDGET_S)


async def _await_registration(
    uisp: uisp_service.UISPClient, mac: str, deadline: float,
) -> tuple[dict | None, float]:
    """Attend que `mac` apparaisse au contrôleur, au plus tard à `deadline`.

    Retourne ``(équipement UISP ou None, secondes attendues)``. La première
    lecture est immédiate (cf. `REGISTRATION_POLL_S`).

    ⚠️ Une erreur du contrôleur PENDANT l'attente n'est pas un échec de l'appel :
    la clé est posée, l'équipement est adopté, seul le constat manque. Un hoquet
    réseau transformerait sinon une adoption réussie en 502. On continue de
    sonder jusqu'à l'échéance ; au pire l'appelant rejoue (`retry_after_seconds`).
    Chaque lecture est elle-même bornée par le temps restant : une requête lente
    (timeout UISP 30 s) ne peut pas pousser l'appel au-delà du budget.
    """
    begin = time.monotonic()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, time.monotonic() - begin
        try:
            devices = await asyncio.wait_for(uisp.fetch_devices(), timeout=remaining)
        except uisp_service.UISPAuthError:
            raise  # un refus d'authentification ne se résout pas en attendant
        except Exception as exc:
            logger.info(
                "uisp_assign: lecture du contrôleur échouée pendant l'attente de %s "
                "(%s) — on continue jusqu'à l'échéance.",
                mac, type(exc).__name__,
            )
            devices = []
        device = next((d for d in devices if _mac_of(d) == mac), None)
        if device is not None:
            return device, time.monotonic() - begin
        pause = min(REGISTRATION_POLL_S, deadline - time.monotonic())
        if pause <= 0:
            return None, time.monotonic() - begin
        await asyncio.sleep(pause)


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

    `reassign` (exposé `force` dans l'API) autorise à déplacer un équipement déjà
    rattaché à un AUTRE client — refusé sinon, cf. `AlreadyAssignedError`.

    Retourne un rapport étape par étape, pour qu'un échec soit toujours
    attribuable à un point précis plutôt qu'à « ça n'a pas marché ».

    Un équipement absent de UISP reçoit d'abord la clé du contrôleur, puis on
    attend qu'il se déclare et on l'associe dans le même appel. S'il ne s'est
    pas déclaré dans le délai, la réponse porte ``pending_registration`` et
    ``retry_after_seconds`` — ce n'est pas une erreur, c'est une attente.
    """
    started = time.monotonic()
    normalized = normalize_mac(mac)  # ValueError -> 400 chez l'appelant
    # Une chaîne vide vaut « non fourni » : les intégrations sérialisent souvent
    # un champ absent en "". Sans ça, `""` partait dans la branche « service
    # imposé » et renvoyait un 404 incompréhensible sur un service sans id.
    wanted = str(crm_service_id).strip() if crm_service_id is not None else ""

    report: dict[str, Any] = {
        "mac": normalized,
        "crm_client_id": str(crm_client_id),
        "crm_service_id": wanted or None,
        # ⚠️ Les clés du contrat sont TOUJOURS présentes, quel que soit le
        # chemin. `pending_registration` n'apparaissait autrefois QUE lorsqu'il
        # valait vrai : un appelant qui le testait trouvait une clé absente sur
        # tous les autres chemins.
        "assigned": False,
        "pending_registration": False,
        "retry_after_seconds": None,
        "key_injected": False,
        "steps": [],
    }
    uisp = _client()

    # ── 1. Résolution de la cible ─────────────────────────────────────────────
    # Faite AVANT toute action sur l'équipement : inutile de poser une clé pour
    # un client qui n'existe pas, et un échec ici ne laisse aucune trace.
    # Un seul fetch des sites, réutilisé ensuite pour identifier l'éventuel
    # détenteur actuel de l'équipement (≈1400 sites : on ne les relit pas deux fois).
    all_sites = await uisp.fetch_sites()
    sites = _sites_of_crm_client(all_sites, crm_client_id)
    if not sites:
        raise AssignmentError(
            f"Aucun client CRM d'id {crm_client_id} dans UISP. Vérifier l'id côté "
            f"CRM — l'association n'a pas été tentée.",
            "crm_client_not_found",
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
                f"L'association n'a pas été tentée.",
                "crm_service_mismatch",
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
    devices = await uisp.fetch_devices()
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
                f"notre base : aucun moyen de le joindre pour lui poser la clé.",
                "device_not_found",
            )
        # `enroll_lr` COMMITTE avant de rendre la main : l'attente qui suit ne
        # tient donc aucune connexion à la base, même pendant 60 s.
        ok, msg = await uisp_enrollment_service.enroll_lr(session, lr)
        report["key_injected"] = ok
        report["steps"].append({"step": "inject_key", "ok": ok, "message": msg})
        if not ok:
            raise DeviceUnreachableError(
                f"La clé UISP n'a pas pu être posée sur {normalized} : {msg}"
            )

        # ── 2 bis. Attente de l'enregistrement ───────────────────────────────
        deadline = registration_deadline(started, time.monotonic())
        device, waited = await _await_registration(uisp, normalized, deadline)
        if device is None:
            report["pending_registration"] = True
            report["retry_after_seconds"] = RETRY_AFTER_S
            report["steps"].append({
                "step": "await_registration", "ok": False,
                "message": (
                    f"Pas encore déclaré au contrôleur après {waited:.0f} s d'attente."
                ),
            })
            report["message"] = (
                f"Clé UISP posée sur {normalized}, mais l'équipement ne s'est pas "
                f"encore déclaré au contrôleur après {waited:.0f} s. Rejouer l'appel "
                f"dans {RETRY_AFTER_S} s : il terminera le rattachement au client "
                f"CRM {crm_client_id}."
            )
            logger.info(
                "uisp_assign: %s — clé posée, enregistrement non constaté après %.0f s "
                "(retry_after=%d s).", normalized, waited, RETRY_AFTER_S,
            )
            return report
        report["steps"].append({
            "step": "await_registration", "ok": True,
            "message": f"Déclaré au contrôleur après {waited:.0f} s.",
        })
        logger.info(
            "uisp_assign: %s déclaré au contrôleur après %.0f s — association dans "
            "le même appel.", normalized, waited,
        )
    else:
        report["steps"].append({
            "step": "inject_key", "ok": True,
            "message": "Équipement déjà connu du contrôleur — clé en place.",
        })

    return await _associate(
        uisp, report, device, site_id, all_sites, normalized, crm_client_id, reassign,
    )


async def _associate(
    uisp: uisp_service.UISPClient,
    report: dict[str, Any],
    device: dict,
    site_id: str | None,
    all_sites: list[dict],
    mac: str,
    crm_client_id: str,
    reassign: bool,
) -> dict[str, Any]:
    """Étape 3 — rattache `device` au site du client, avec les mêmes règles que
    l'équipement ait été trouvé d'emblée ou juste après son enregistrement."""
    ident = _ident(device)
    current_site = (ident.get("site") or {}).get("id")
    if current_site == site_id:
        # Déjà chez le bon client : aucune écriture. Rejouable sans effet.
        report["assigned"] = True
        report["steps"].append({
            "step": "assign", "ok": True,
            "message": "Déjà associé à ce client — rien à faire.",
        })
        report["message"] = f"{mac} est déjà associé au client CRM {crm_client_id}."
        return report

    # ⚠️ L'équipement appartient à un AUTRE client. Le déplacer est légitime
    # (matériel récupéré et réinstallé ailleurs) mais retire son rattachement à
    # l'abonné actuel — et une MAC saisie de travers ferait ce dégât en silence.
    # Refus par défaut : on exige un geste explicite, comme pour toute action
    # qui peut toucher le mauvais abonné.
    if current_site and not reassign:
        owner = _crm_owner_of_site(all_sites, current_site)
        owner_id = owner.get("id")
        raise AlreadyAssignedError(
            f"L'équipement {mac} est déjà rattaché au client CRM "
            f"{owner_id or '(inconnu)'}. Le déplacer vers le client {crm_client_id} "
            f"retirerait son équipement à l'abonné actuel — relancer avec "
            f"force=true si c'est bien l'intention.",
            owner_id,
            owner.get("name"),
        )

    await uisp.assign_device_to_site(ident.get("id"), site_id)
    report["assigned"] = True
    report["steps"].append({
        "step": "assign", "ok": True,
        "message": f"Associé au client CRM {crm_client_id}.",
    })
    logger.warning(
        "UISP ASSIGN — %s associé au client CRM %s", mac, crm_client_id,
    )
    report["message"] = f"{mac} associé au client CRM {crm_client_id}."
    return report
