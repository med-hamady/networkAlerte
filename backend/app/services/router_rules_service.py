"""Lecture des règles de coupure client posées sur le routeur de cœur.

Pourquoi cette page existe
--------------------------
Le routeur est le **filet de sécurité** du blocage client (cf.
``mikrotik_service``) : il coupe les abonnés que leur propre LR n'a pas pu
couper. Personne ne voit ce qu'il porte réellement — ni le journal FAI (qui dit
ce qui s'est *passé*), ni la base (qui dit ce que **nous croyons** avoir posé).
Un opérateur qui doit répondre à « ce client dit qu'il est coupé alors qu'il a
payé » n'a aujourd'hui aucun moyen de lire la vérité, qui est sur le routeur.

Ce module la lit **en direct**, à la demande — jamais en tâche de fond : chaque
consultation ouvre une session API, ce qui est acceptable sur un clic d'opérateur
et ne le serait pas sur un rafraîchissement automatique (cf. la leçon de
`site_topology_service`, où une page rejouait un gros fetch toutes les 2 min sur
un onglet oublié).

⚠️ Une MAC seule est illisible
------------------------------
Le routeur ne connaît que des adresses MAC. Les rendre telles quelles obligerait
l'opérateur à faire le croisement à la main, client par client. On rapproche donc
chaque règle de notre inventaire (nom, site, IP) et surtout de l'**intention**
enregistrée en base — c'est ce rapprochement, pas la liste brute, qui fait
apparaître les deux désaccords qui coûtent de l'argent :

  * **règle en trop** (``unexpected``) : le routeur coupe un client que la base
    ne veut plus couper. Le client a payé et reste hors ligne — il appellera.
  * **règle manquante** (``missing``) : la base croit le client coupé par le
    routeur, le routeur n'a rien. Le client est impayé et navigue.

Les deux sont invisibles autrement : le job de renforcement ne parle au routeur
que sur *transition* (cf. ``client_block_service._reconcile_router``), donc il ne
détecte jamais un écart apparu ailleurs — règle retirée à la main, base restaurée,
règle du système historique.
"""

from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Lr
from app.services import mikrotik_service


def classify(rule: dict, lr: Lr | None) -> str:
    """État d'une règle vis-à-vis de ce que la base veut.

    - ``unexpected`` : abonné connu que **personne ne veut couper** → il est
      hors ligne à tort. Le seul état qui appelle une action immédiate.
    - ``unknown`` : MAC absente de notre inventaire. Ni bonne ni mauvaise
      nouvelle — règle du système historique, ou LR supprimé de la base
      (déprovisionné dans UISP) dont la coupure est restée sur le routeur.
    - ``expected`` : la base veut couper ce client, le routeur le coupe.
    """
    if lr is None:
        return "unknown"
    return "expected" if lr.client_blocked else "unexpected"


async def get_router_client_blocks(session: AsyncSession) -> dict:
    """Règles du routeur + croisement avec l'inventaire. Ne lève jamais.

    ``available`` = le repli routeur est configuré. ``error`` = on n'a pas pu
    demander ; dans ce cas ``rules`` est vide et **n'affirme rien**.
    """
    rules, error = await mikrotik_service.list_client_block_rules()

    result = await session.execute(select(Lr).where(Lr.mac_address.is_not(None)))
    by_mac: dict[str, Lr] = {
        lr.mac_address.lower(): lr for lr in result.scalars().all()
    }

    rows: list[dict] = []
    seen: set[str] = set()
    for rule in rules:
        mac_key = rule["mac"].lower()
        seen.add(mac_key)
        lr = by_mac.get(mac_key)
        rows.append({
            "rule_id": rule["id"],
            "mac": rule["mac"],
            "comment": rule["comment"],
            "disabled": rule["disabled"],
            "dynamic": rule["dynamic"],
            "packets": rule["packets"],
            "bytes": rule["bytes"],
            "origin": (
                "supervisor"
                if mikrotik_service.is_supervisor_comment(rule["comment"])
                else "legacy"
            ),
            "state": classify(rule, lr),
            "lr_id": lr.id if lr else None,
            "name": lr.name if lr else None,
            "site": lr.site if lr else None,
            "ip_address": lr.ip_address if lr else None,
            "client_blocked": lr.client_blocked if lr else None,
            # POURQUOI ce client est coupé. Deux décisions opposées produisent des
            # règles identiques sur le routeur : l'impayé (système de paiement) et
            # le balayage « hors supervision » (scripts/block_out_of_supervision,
            # qui coupe des abonnés qu'on a perdus de vue — pas des mauvais
            # payeurs). Sans le motif, l'opérateur ne peut pas les distinguer et
            # lirait tout le lot comme des impayés.
            "blocked_reason": lr.client_blocked_reason if lr else None,
            # Le routeur devait-il porter cette règle selon NOUS ? Un `false` sur
            # une règle `expected` n'est pas une anomalie : la coupure a pu être
            # posée par le système historique, ou notre base restaurée depuis.
            "router_blocked": lr.router_blocked if lr else None,
            # Coupé sur son propre équipement en plus du routeur : la règle du
            # routeur est alors redondante et sera retirée au prochain cycle.
            "enforced_on_lr": bool(lr.client_block_enforced_at) if lr else None,
        })

    # L'écart INVERSE : notre base affirme une coupure routeur que le routeur ne
    # porte pas. Sans cette liste, la page ne montrerait que les excès de
    # coupure ; or c'est le manque qui laisse un impayé en ligne.
    missing = [
        {
            "lr_id": lr.id,
            "name": lr.name,
            "mac": lr.mac_address,
            "site": lr.site,
            "ip_address": lr.ip_address,
            "enforced_on_lr": bool(lr.client_block_enforced_at),
            "blocked_reason": lr.client_blocked_reason,
        }
        for mac, lr in by_mac.items()
        if lr.router_blocked and mac not in seen
    ]
    missing.sort(key=lambda row: (row["name"] or ""))

    # Ce qui appelle une action d'abord : les clients coupés à tort, puis les MAC
    # qu'on n'explique pas. Le reste (coupures légitimes) par nom.
    order = {"unexpected": 0, "unknown": 1, "expected": 2}
    rows.sort(key=lambda row: (order.get(row["state"], 3), row["name"] or "", row["mac"]))

    return {
        "available": mikrotik_service.is_enabled(),
        "error": error,
        "fetched_at": datetime.datetime.now(datetime.UTC),
        "host": mikrotik_service.get_settings().mikrotik_host,
        "rules": rows,
        "missing": missing,
        "stats": {
            "total": len(rows),
            "supervisor": sum(1 for r in rows if r["origin"] == "supervisor"),
            "legacy": sum(1 for r in rows if r["origin"] == "legacy"),
            "unexpected": sum(1 for r in rows if r["state"] == "unexpected"),
            "unknown": sum(1 for r in rows if r["state"] == "unknown"),
            "disabled": sum(1 for r in rows if r["disabled"]),
            "missing": len(missing),
        },
    }
