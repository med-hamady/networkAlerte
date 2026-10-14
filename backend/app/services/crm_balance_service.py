"""Contrôle du SOLDE CRM avant le déblocage d'un abonné.

Avant de rendre l'accès internet à un client, on lit dans le CRM UISP ce qu'il
doit encore (`accountOutstanding`). Au-delà de `unblock_max_outstanding` (150),
le déblocage est REFUSÉ : le client reste coupé. S'applique aux deux chemins de
déblocage — `POST /fai/unblock` (système de paiement) et le bouton « Débloquer »
du dashboard — et à aucun autre : le rejeu automatique d'un déblocage déjà
accordé (`unblock_pending`, job d'enforcement) ne repasse PAS par ici, la
décision a été prise au moment de la demande.

Décisions opérateur (2026-10-14) :

* **dû > 150 ⇒ refus** (strictement : 150 tout rond passe) ;
* **seulement pour un client à UN SEUL abonnement** — un client qui en a
  plusieurs est débloqué sans contrôle du solde. On compte ses services CRM
  **hors terminés** (`_ENDED_SERVICE_STATUSES`) : un vieil abonnement clôturé
  ne doit pas le faire passer pour un client « à plusieurs abonnements ».
  Zéro abonnement non terminé n'est pas « un seul » : pas de contrôle non plus ;
* **CRM injoignable ⇒ refus** — fermé par défaut, pour le comptage des
  abonnements comme pour le solde. Même règle pour une réponse illisible, un
  client introuvable, ou une App Key non configurée : dans tous ces cas on ne
  SAIT pas que le déblocage est permis ;
* **abonné SANS id CRM ⇒ débloqué sans contrôle** (LR découvert par radio, non
  rattaché dans UISP) : il n'y a aucun compte à interroger ;
* **aucun moyen de forcer** le déblocage.

⚠️ L'API CRM refuse le token NMS (401, constaté) : elle exige sa propre
**App Key**, envoyée en `X-Auth-App-Key`. Lecture seule suffit.

Ce module ne fait que des lectures HTTP et ne touche ni à la base ni au LR.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.config import get_settings
from app.models.device import Lr

logger = logging.getLogger(__name__)

# Statuts de service UCRM qui ne comptent PAS comme un abonnement : 2 = terminé,
# 5 = obsolète. Tout le reste compte (1 actif, 3 suspendu — l'état normal d'un
# client coupé —, 0/4 préparé, 6 différé, 7 devis, 8 inactif).
_ENDED_SERVICE_STATUSES = frozenset({2, 5})

# `allowed`              → le déblocage peut avoir lieu
# `outstanding_balance`  → refus : dû au-delà du seuil
# `crm_unavailable`      → refus : abonnements ou solde impossibles à établir
#                          (CRM injoignable, réponse illisible, client
#                          introuvable, clé absente)
Verdict = Literal["allowed", "outstanding_balance", "crm_unavailable"]


@dataclass(frozen=True)
class BalanceCheck:
    verdict: Verdict
    message: str
    crm_client_id: str | None = None
    outstanding: float | None = None
    threshold: float | None = None
    service_count: int | None = None

    @property
    def allowed(self) -> bool:
        return self.verdict == "allowed"


def _crm_base_url() -> str:
    settings = get_settings()
    if settings.crm_api_url:
        return settings.crm_api_url.rstrip("/")
    if settings.uisp_base_url:
        return f"{settings.uisp_base_url.rstrip('/')}/crm/api/v1.0"
    return ""


async def _crm_get(path: str, crm_client_id: str, params: dict | None = None) -> Any:
    """GET sur l'API CRM. Lève sur tout ce qui n'est pas un 200 JSON.

    `RuntimeError` porte un message lisible par l'opérateur ; les erreurs de
    transport httpx sont laissées remonter et traduites par l'appelant.
    """
    settings = get_settings()
    base = _crm_base_url()
    if not base or not settings.crm_api_key:
        raise RuntimeError(
            "vérification du solde non configurée (CRM_API_KEY / URL du CRM absente)"
        )
    async with httpx.AsyncClient(
        verify=settings.uisp_verify_tls, timeout=settings.crm_request_timeout,
    ) as client:
        resp = await client.get(
            f"{base}{path}", params=params,
            headers={"X-Auth-App-Key": settings.crm_api_key, "Accept": "application/json"},
        )
    if resp.status_code == 404:
        raise RuntimeError(f"client CRM {crm_client_id} introuvable dans le CRM")
    if resp.status_code in (401, 403):
        raise RuntimeError(f"le CRM refuse la clé API (HTTP {resp.status_code})")
    if resp.status_code != 200:
        raise RuntimeError(f"le CRM a répondu HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise RuntimeError("réponse du CRM illisible (pas du JSON)") from exc


async def fetch_service_count(crm_client_id: str) -> int:
    """Nombre d'abonnements NON TERMINÉS du client dans le CRM."""
    services = await _crm_get(
        "/clients/services", crm_client_id, params={"clientId": crm_client_id},
    )
    if not isinstance(services, list):
        raise RuntimeError("réponse du CRM illisible (liste des abonnements attendue)")
    try:
        return sum(
            1 for s in services
            if str(s["clientId"]) == str(crm_client_id)
            and int(s["status"]) not in _ENDED_SERVICE_STATUSES
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("réponse du CRM illisible (abonnement sans statut)") from exc


async def fetch_outstanding(crm_client_id: str) -> float:
    """Dû du client dans le CRM."""
    client = await _crm_get(f"/clients/{crm_client_id}", crm_client_id)
    try:
        outstanding = float(client.get("accountOutstanding"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("réponse du CRM illisible (accountOutstanding absent)") from exc
    if not math.isfinite(outstanding):
        raise RuntimeError("réponse du CRM illisible (accountOutstanding non fini)")
    return outstanding


def _unavailable(lr: Lr, crm_id: str, threshold: float, what: str, exc: Exception) -> BalanceCheck:
    detail = str(exc) if isinstance(exc, RuntimeError) else (
        f"CRM injoignable ({type(exc).__name__})"
    )
    logger.warning(
        "UNBLOCK REFUSÉ — LR '%s' (id=%s, client CRM %s) : %s impossible à établir — %s",
        lr.name, lr.id, crm_id, what, detail,
    )
    return BalanceCheck(
        "crm_unavailable",
        f"Déblocage refusé : {what} du client CRM {crm_id} impossible à vérifier "
        f"({detail}). Réessayer quand le CRM répond.",
        crm_client_id=crm_id, threshold=threshold,
    )


async def check_unblock_allowed(lr: Lr) -> BalanceCheck:
    """Le déblocage de cet abonné est-il permis au vu de son solde CRM ?"""
    settings = get_settings()
    if not settings.unblock_balance_check_enabled:
        return BalanceCheck("allowed", "Contrôle du solde désactivé.")

    crm_id = (lr.uisp_crm_client_id or "").strip() or None
    if crm_id is None:
        return BalanceCheck("allowed", "Aucun client CRM rattaché : pas de solde à contrôler.")

    threshold = settings.unblock_max_outstanding

    # Le comptage d'abord : un client à plusieurs abonnements n'est pas contrôlé,
    # inutile alors de lire son solde.
    try:
        service_count = await fetch_service_count(crm_id)
    except (RuntimeError, httpx.HTTPError) as exc:
        return _unavailable(lr, crm_id, threshold, "nombre d'abonnements", exc)
    if service_count != 1:
        return BalanceCheck(
            "allowed",
            f"Client CRM {crm_id} à {service_count} abonnement(s) : solde non contrôlé "
            f"(contrôle réservé aux clients à un seul abonnement).",
            crm_client_id=crm_id, threshold=threshold, service_count=service_count,
        )

    try:
        outstanding = await fetch_outstanding(crm_id)
    except (RuntimeError, httpx.HTTPError) as exc:
        return _unavailable(lr, crm_id, threshold, "solde", exc)

    if outstanding > threshold:
        logger.warning(
            "UNBLOCK REFUSÉ — LR '%s' (id=%s, client CRM %s) : dû %.2f > seuil %.2f",
            lr.name, lr.id, crm_id, outstanding, threshold,
        )
        return BalanceCheck(
            "outstanding_balance",
            f"Déblocage refusé : le client CRM {crm_id} doit encore {outstanding:.2f} "
            f"(seuil {threshold:.2f}).",
            crm_client_id=crm_id, outstanding=outstanding, threshold=threshold,
            service_count=1,
        )

    return BalanceCheck(
        "allowed",
        f"Solde vérifié : dû {outstanding:.2f} (seuil {threshold:.2f}).",
        crm_client_id=crm_id, outstanding=outstanding, threshold=threshold,
        service_count=1,
    )
