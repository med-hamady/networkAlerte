"""Règles de coupure client portées par le routeur de cœur — lecture seule.

Auth NORMALE (session dashboard / clé maître), comme le journal FAI : la clé du
système de paiement ne lit pas l'état du routeur, elle ne sert qu'à bloquer et
débloquer.

⚠️ **Lecture en direct, à la demande.** Chaque appel ouvre une session API
RouterOS. C'est le prix d'une réponse vraie *maintenant* — et c'est pour ça que
la page ne se rafraîchit pas toute seule : l'opérateur demande, on va voir.

Aucune écriture ici, délibérément : retirer une règle depuis cette page
court-circuiterait la réconciliation de ``client_block_service`` (qui la
reposerait au cycle suivant si la base veut toujours couper). Le bon geste sur
un client coupé à tort reste le déblocage métier — la règle du routeur part
alors d'elle-même.
"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.services import mikrotik_service, router_rules_service

router = APIRouter()


class RouterRuleRow(BaseModel):
    rule_id: str | None
    mac: str
    comment: str
    # Une règle désactivée existe sans couper personne : elle explique un client
    # « bloqué » toujours en ligne.
    disabled: bool
    dynamic: bool
    packets: int | None
    bytes: int | None
    # "supervisor" = posée par nous (marque dans le commentaire) ; "legacy" =
    # tout le reste, dont le système historique add_rules.php. Indice d'origine,
    # pas une preuve — un commentaire s'édite.
    origin: str
    # "expected" | "unexpected" (client à ne plus couper) | "unknown" (MAC hors
    # inventaire). Cf. router_rules_service.classify.
    state: str
    lr_id: int | None
    name: str | None
    site: str | None
    ip_address: str | None
    client_blocked: bool | None
    router_blocked: bool | None
    enforced_on_lr: bool | None


class MissingRuleRow(BaseModel):
    """Base : « coupé par le routeur ». Routeur : rien. Le client navigue."""

    lr_id: int
    name: str
    mac: str | None
    site: str | None
    ip_address: str | None
    enforced_on_lr: bool


class RouterRulesStats(BaseModel):
    total: int
    supervisor: int
    legacy: int
    unexpected: int
    unknown: int
    disabled: int
    missing: int


class RouterRulesResponse(BaseModel):
    # false = repli routeur non configuré (MIKROTIK_ENABLED / mot de passe). La
    # page affiche alors une explication, pas une liste vide — « aucune règle »
    # et « je n'ai pas pu demander » ne doivent jamais se lire pareil.
    available: bool
    error: str | None = None
    # Instant de la LECTURE : tout ce qui suit est l'état du routeur à cette
    # seconde, pas un cache.
    fetched_at: datetime.datetime
    host: str
    rules: list[RouterRuleRow]
    missing: list[MissingRuleRow]
    stats: RouterRulesStats


@router.get("", response_model=RouterRulesResponse)
async def get_router_rules(db: AsyncSession = Depends(get_db)) -> RouterRulesResponse:
    """Toutes les règles ``chain=forward action=drop`` ciblant une MAC.

    502 si le routeur est configuré mais injoignable : une liste vide serait lue
    comme « aucun client bloqué », le contresens le plus coûteux de cette page.
    """
    data = await router_rules_service.get_router_client_blocks(db)
    if data["error"] and mikrotik_service.is_enabled():
        raise HTTPException(status_code=502, detail=data["error"])
    return RouterRulesResponse(**data)
