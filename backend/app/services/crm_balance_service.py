"""Contrôle du solde CRM avant le déblocage d'un abonné.

S'applique aux deux chemins de déblocage — `POST /fai/unblock` (système de
paiement) et le bouton « Débloquer » du dashboard — et à aucun autre : le rejeu
automatique d'un déblocage déjà accordé (`unblock_pending`, job d'enforcement)
ne repasse PAS par ici, la décision a été prise au moment de la demande.

La règle (décision opérateur du 2026-10-20) — **le dû doit être couvert par les
abonnements qui RESTENT coupés** :

    on débloque si   solde dû − prix des autres abonnements du client
                     encore coupés chez nous   ≤   unblock_max_outstanding (150)

Un client peut donc avoir autant d'abonnements actifs qu'il en a payé. Exemple,
3 abonnements à 1000 MRU, tous coupés :

* dû 3000 (rien payé) → 3000 − 2000 = 1000 → refus ;
* dû 2000 (un payé)   → 2000 − 2000 = 0    → le 1er est débloqué ;
* dû 2000, 1 actif, on demande le 2e → 2000 − 1000 = 1000 → refus ;
* client à un seul abonnement : rien ne reste coupé, c'est le dû seul qui compte.

Plusieurs mois de retard grossissent le dû : il faut alors davantage
d'abonnements coupés pour le couvrir — le sens prudent.

Pourquoi pas les factures (version abandonnée) : le CRM regroupe souvent les
abonnements d'un client sur UNE facture et n'enregistre le paiement que par
facture — impossible d'y lire quel abonnement a été payé.

Détails voulus :

* **solde ≤ 150 ⇒ débloqué tout de suite**, sans lire les abonnements ;
* « encore coupés » = les AUTRES LR du même client CRM avec `client_blocked`
  en base (l'intention) ; un abonnement du CRM sans antenne chez nous n'est
  PAS compté comme coupé (il ne couvre rien : sens prudent) ;
* prix d'un abonnement = son `totalPrice` CRM. Prix tous égaux ⇒ pas besoin de
  savoir quelle antenne est quel abonnement ; sinon chaque antenne coupée est
  rattachée à son abonnement par son site UISP (`unmsClientSiteId`), lu en
  direct dans UISP par sa MAC. **Rattachement impossible ⇒ refus** ;
* le nombre d'abonnements comptés comme coupés ne dépasse jamais le nombre
  d'abonnements du client moins celui qu'on débloque (une fiche périmée ne
  doit pas couvrir un dû qui n'existe pas) ;
* **CRM ou UISP injoignable ⇒ refus** — fermé par défaut ;
* **abonné SANS id CRM ⇒ débloqué sans contrôle** ;
* **aucun moyen de forcer** le déblocage.

⚠️ L'API CRM refuse le token NMS (401, constaté) : elle exige sa propre
**App Key**, envoyée en `X-Auth-App-Key`. Lecture seule suffit.

Ce module ne fait que des lectures (HTTP + une requête en base) et ne modifie
ni la base ni le LR.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Lr
from app.schemas.device import normalize_mac
from app.services import uisp_service

logger = logging.getLogger(__name__)

# Liste des stations UISP gardée en mémoire : une série de déblocages (le lot du
# matin après une nuit de paiements) ne doit pas retélécharger tout le parc à
# chaque appel. Le site d'une antenne ne change qu'à une réinstallation.
_STATIONS_TTL_S = 120
_stations_cache: tuple[float, dict[str, str]] | None = None
_stations_lock = asyncio.Lock()

# `allowed`              → le déblocage peut avoir lieu
# `outstanding_balance`  → refus : dû non couvert par les abonnements coupés
# `crm_unavailable`      → refus : impossible de décider (CRM ou UISP
#                          injoignable, réponse illisible, client introuvable,
#                          abonnement d'une antenne introuvable, clé absente)
Verdict = Literal["allowed", "outstanding_balance", "crm_unavailable"]


@dataclass(frozen=True)
class BalanceCheck:
    verdict: Verdict
    message: str
    crm_client_id: str | None = None
    outstanding: float | None = None       # solde dû du client (CRM)
    threshold: float | None = None
    covered: float | None = None           # prix des abonnements qui restent coupés
    blocked_services: int | None = None    # combien d'abonnements restent coupés

    @property
    def allowed(self) -> bool:
        return self.verdict == "allowed"


# ── Lectures CRM ────────────────────────────────────────────────────────────

def _crm_base_url() -> str:
    settings = get_settings()
    if settings.crm_api_url:
        return settings.crm_api_url.rstrip("/")
    if settings.uisp_base_url:
        return f"{settings.uisp_base_url.rstrip('/')}/crm/api/v1.0"
    return ""


async def _crm_get(path: str, crm_client_id: str, params: Any = None) -> Any:
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


async def fetch_outstanding(crm_client_id: str) -> float:
    """Solde dû du client dans le CRM (`accountOutstanding`)."""
    client = await _crm_get(f"/clients/{crm_client_id}", crm_client_id)
    try:
        outstanding = float(client.get("accountOutstanding"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("réponse du CRM illisible (accountOutstanding absent)") from exc
    if not math.isfinite(outstanding):
        raise RuntimeError("réponse du CRM illisible (accountOutstanding non fini)")
    return outstanding


async def fetch_services(crm_client_id: str) -> list[dict]:
    """Tous les abonnements du client, filtrés sur CE client."""
    services = await _crm_get(
        "/clients/services", crm_client_id, params={"clientId": crm_client_id},
    )
    if not isinstance(services, list):
        raise RuntimeError("réponse du CRM illisible (liste des abonnements attendue)")
    return [s for s in services if isinstance(s, dict) and str(s.get("clientId")) == crm_client_id]


def service_price(service: dict) -> float:
    """Prix mensuel d'un abonnement. Lève si illisible : un prix deviné
    pourrait couvrir un dû qui n'est pas couvert."""
    raw = service.get("totalPrice")
    if raw is None:
        raw = service.get("price")
    try:
        price = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"prix illisible pour l'abonnement {service.get('id')}") from exc
    if not math.isfinite(price) or price < 0:
        raise RuntimeError(f"prix illisible pour l'abonnement {service.get('id')}")
    return price


# ── Rattachement antenne → abonnement (uniquement si les prix diffèrent) ────

def _uisp_client() -> uisp_service.UISPClient:
    s = get_settings()
    return uisp_service.UISPClient(
        s.uisp_base_url,
        username=s.uisp_username,
        password=s.uisp_password,
        api_token=s.uisp_api_token,  # lecture seule : on ne fait que lire
        verify_tls=s.uisp_verify_tls,
        timeout=s.uisp_request_timeout,
    )


async def _station_sites() -> dict[str, str]:
    """MAC normalisée → id du site UISP, pour toutes les stations (cache court)."""
    global _stations_cache
    async with _stations_lock:
        now = time.monotonic()
        if _stations_cache is None or now - _stations_cache[0] > _STATIONS_TTL_S:
            mapping: dict[str, str] = {}
            for dev in await _uisp_client().fetch_devices(role="station"):
                ident = dev.get("identification") or {}
                site_id = (ident.get("site") or {}).get("id")
                try:
                    mac = normalize_mac(ident.get("mac") or "")
                except ValueError:
                    continue
                if site_id:
                    mapping[mac] = str(site_id)
            _stations_cache = (now, mapping)
        return _stations_cache[1]


def covered_amount(
    services: list[dict],
    blocked_macs: list[str | None],
    sites_by_mac: dict[str, str] | None,
) -> tuple[float, int]:
    """(prix total, nombre) des abonnements qui restent coupés — logique pure.

    `blocked_macs` = MAC des AUTRES antennes coupées du client. Leur nombre est
    plafonné à `len(services) − 1` (celui qu'on débloque ne reste pas coupé).
    Prix tous égaux ⇒ simple multiplication, `sites_by_mac` inutile (None).
    Sinon chaque antenne est rattachée à son abonnement par son site UISP ;
    une antenne non rattachable lève `RuntimeError` (refus).
    """
    limit = max(0, len(services) - 1)
    if not blocked_macs or limit == 0:
        return 0.0, 0
    prices = {round(service_price(s), 2) for s in services}
    if len(prices) == 1:
        count = min(len(blocked_macs), limit)
        return round(prices.pop() * count, 2), count

    if sites_by_mac is None:
        raise RuntimeError("prix différents : rattachement des antennes nécessaire")
    by_site = {str(s.get("unmsClientSiteId")): s for s in services if s.get("unmsClientSiteId")}
    seen: set[str] = set()
    total = 0.0
    for mac in blocked_macs:
        try:
            site = sites_by_mac.get(normalize_mac(mac or ""))
        except ValueError:
            site = None
        service = by_site.get(site) if site else None
        if service is None:
            raise RuntimeError(f"abonnement de l'antenne coupée {mac} introuvable")
        sid = str(service.get("id"))
        if sid in seen:
            continue  # deux fiches pour le même abonnement : compté une fois
        seen.add(sid)
        total += service_price(service)
        if len(seen) == limit:
            break
    return round(total, 2), len(seen)


def needs_site_mapping(services: list[dict]) -> bool:
    """Faut-il lire UISP ? Seulement si les prix des abonnements diffèrent."""
    return len({round(service_price(s), 2) for s in services}) > 1


# ── Base : les autres antennes coupées du même client ───────────────────────

async def other_blocked_macs(db: AsyncSession, lr: Lr, crm_client_id: str) -> list[str | None]:
    rows = await db.execute(
        select(Lr.mac_address)
        .where(
            Lr.uisp_crm_client_id == crm_client_id,
            Lr.client_blocked.is_(True),
            Lr.id != lr.id,
        )
        .order_by(Lr.id)
    )
    return list(rows.scalars().all())


# ── La décision ─────────────────────────────────────────────────────────────

def _unavailable(lr: Lr, crm_id: str, threshold: float, detail: str) -> BalanceCheck:
    logger.warning(
        "UNBLOCK REFUSÉ — LR '%s' (id=%s, client CRM %s) : solde impossible à vérifier — %s",
        lr.name, lr.id, crm_id, detail,
    )
    return BalanceCheck(
        "crm_unavailable",
        f"Déblocage refusé : solde du client CRM {crm_id} impossible à vérifier "
        f"({detail}). Réessayer quand le CRM répond.",
        crm_client_id=crm_id, threshold=threshold,
    )


def _describe(exc: Exception, source: str) -> str:
    if isinstance(exc, RuntimeError):
        return str(exc)
    return f"{source} injoignable ({type(exc).__name__})"


async def check_unblock_allowed(db: AsyncSession, lr: Lr) -> BalanceCheck:
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
        return _unavailable(lr, crm_id, threshold, _describe(exc, "CRM"))

    if outstanding <= threshold:
        return BalanceCheck(
            "allowed",
            f"Solde vérifié : dû {outstanding:.2f} (seuil {threshold:.2f}).",
            crm_client_id=crm_id, outstanding=outstanding, threshold=threshold,
            covered=0.0, blocked_services=0,
        )

    blocked = await other_blocked_macs(db, lr, crm_id)
    covered, count = 0.0, 0
    if blocked:
        try:
            services = await fetch_services(crm_id)
            sites = await _station_sites() if needs_site_mapping(services) else None
            covered, count = covered_amount(services, blocked, sites)
        except (RuntimeError, httpx.HTTPError, uisp_service.UISPAuthError) as exc:
            return _unavailable(lr, crm_id, threshold, _describe(exc, "CRM/UISP"))

    remaining = round(outstanding - covered, 2)
    if remaining > threshold:
        logger.warning(
            "UNBLOCK REFUSÉ — LR '%s' (id=%s, client CRM %s) : dû %.2f − %d abonnement(s) "
            "coupé(s) %.2f = %.2f > seuil %.2f",
            lr.name, lr.id, crm_id, outstanding, count, covered, remaining, threshold,
        )
        return BalanceCheck(
            "outstanding_balance",
            f"Déblocage refusé : le client CRM {crm_id} doit {outstanding:.2f}, dont "
            f"{remaining:.2f} non couverts par ses {count} abonnement(s) encore coupé(s) "
            f"(seuil {threshold:.2f}).",
            crm_client_id=crm_id, outstanding=outstanding, threshold=threshold,
            covered=covered, blocked_services=count,
        )

    return BalanceCheck(
        "allowed",
        f"Solde vérifié : dû {outstanding:.2f}, couvert par {count} abonnement(s) "
        f"encore coupé(s) ({covered:.2f}) — reste {remaining:.2f} (seuil {threshold:.2f}).",
        crm_client_id=crm_id, outstanding=outstanding, threshold=threshold,
        covered=covered, blocked_services=count,
    )
