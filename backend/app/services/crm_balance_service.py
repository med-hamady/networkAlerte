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
* **CRM injoignable ⇒ refus** — fermé par défaut. Même règle pour une réponse
  illisible, un client introuvable dans le CRM, ou une App Key non configurée :
  dans tous ces cas on ne SAIT pas que le client est à jour ;
* **abonné SANS id CRM ⇒ débloqué sans contrôle** (LR découvert par radio, non
  rattaché dans UISP) : il n'y a aucun compte à interroger ;
* **aucun moyen de forcer** le déblocage.

⚠️ Le solde du CRM est par **client**, pas par service : un client à plusieurs
services qui doit de l'argent sur l'un reste refusé sur tous.

⚠️ L'API CRM refuse le token NMS (401, constaté) : elle exige sa propre
**App Key**, envoyée en `X-Auth-App-Key`. Lecture seule suffit.

Ce module ne fait QU'UNE lecture HTTP et ne touche ni à la base ni au LR.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Literal

import httpx

from app.core.config import get_settings
from app.models.device import Lr

logger = logging.getLogger(__name__)

# `allowed`              → le déblocage peut avoir lieu
# `outstanding_balance`  → refus : dû au-delà du seuil
# `crm_unavailable`      → refus : solde impossible à établir (CRM injoignable,
#                          réponse illisible, client introuvable, clé absente)
Verdict = Literal["allowed", "outstanding_balance", "crm_unavailable"]


@dataclass(frozen=True)
class BalanceCheck:
    verdict: Verdict
    message: str
    crm_client_id: str | None = None
    outstanding: float | None = None
    threshold: float | None = None

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


async def fetch_outstanding(crm_client_id: str) -> float:
    """Dû du client dans le CRM. Lève sur toute impossibilité de l'établir.

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
            f"{base}/clients/{crm_client_id}",
            headers={"X-Auth-App-Key": settings.crm_api_key, "Accept": "application/json"},
        )
    if resp.status_code == 404:
        raise RuntimeError(f"client CRM {crm_client_id} introuvable dans le CRM")
    if resp.status_code in (401, 403):
        raise RuntimeError(f"le CRM refuse la clé API (HTTP {resp.status_code})")
    if resp.status_code != 200:
        raise RuntimeError(f"le CRM a répondu HTTP {resp.status_code}")
    try:
        raw = resp.json().get("accountOutstanding")
        outstanding = float(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("réponse du CRM illisible (accountOutstanding absent)") from exc
    if not math.isfinite(outstanding):
        raise RuntimeError("réponse du CRM illisible (accountOutstanding non fini)")
    return outstanding


async def check_unblock_allowed(lr: Lr) -> BalanceCheck:
    """Le déblocage de cet abonné est-il permis au vu de son solde CRM ?"""
    settings = get_settings()
    if not settings.unblock_balance_check_enabled:
        return BalanceCheck("allowed", "Contrôle du solde désactivé.")

    crm_id = (lr.uisp_crm_client_id or "").strip() or None
    if crm_id is None:
        return BalanceCheck("allowed", "Aucun client CRM rattaché : pas de solde à contrôler.")

    threshold = settings.unblock_max_outstanding
    try:
        outstanding = await fetch_outstanding(crm_id)
    except (RuntimeError, httpx.HTTPError) as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else (
            f"CRM injoignable ({type(exc).__name__})"
        )
        logger.warning(
            "UNBLOCK REFUSÉ — LR '%s' (id=%s, client CRM %s) : solde impossible à établir — %s",
            lr.name, lr.id, crm_id, detail,
        )
        return BalanceCheck(
            "crm_unavailable",
            f"Déblocage refusé : solde du client CRM {crm_id} impossible à vérifier "
            f"({detail}). Réessayer quand le CRM répond.",
            crm_client_id=crm_id, threshold=threshold,
        )

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
        )

    return BalanceCheck(
        "allowed",
        f"Solde vérifié : dû {outstanding:.2f} (seuil {threshold:.2f}).",
        crm_client_id=crm_id, outstanding=outstanding, threshold=threshold,
    )
