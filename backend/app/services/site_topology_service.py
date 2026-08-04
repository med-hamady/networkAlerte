"""Graphe INTER-SITES du réseau — le maillage que dessine la carte du contrôleur.

Ce que ce module résout
-----------------------
La topologie **intra-site** (switch → Rockets/AF60/Power) est déjà rendue par le
composant `SiteTopology` du frontend, à partir de notre inventaire. Le niveau
au-dessus — quel site est raccordé à quel autre, HQ → ARF1/AT1/CT1… → PK1/TS1… —
n'est stocké **nulle part** chez nous : on connaît le `site` de chaque AF60 et de
chaque PTP LiteBeam, jamais quel AF60 parle à quel AF60.

Le contrôleur, lui, le sait (ses agents le rapportent) et le publie sur
``GET /nms/api/v2.1/data-links``. C'est déjà notre source de vérité pour le
câblage des ports de switch (:mod:`switch_port_service`), qui n'en retient que
les liens ``ethernet`` ; les liens **radio** de la même réponse portent nos
backhauls.

Ce qui fait qu'une arête existe
-------------------------------
Un data-link dont les deux bouts se résolvent sur deux **sites d'infra
DIFFÉRENTS**. Trois précisions portent tout le résultat :

* **Un site d'infra est un site qui PORTE de l'infra** — au moins un équipement
  que :func:`uisp_sync_service.classify_device` reconnaît comme supervisé
  (Rocket, AF60, PTP LiteBeam, switch, UISP Power). Voir
  :func:`infra_site_ids` pour pourquoi la clé CRM ne suffit pas.
* **Le type de lien n'est pas un filtre.** On n'impose pas ``wireless`` : trois
  des cinq liaisons partant du HQ sont en ``ethernet`` (fibre vers ARF1, AT1,
  CT1). Filtrer sur la radio amputerait le graphe de sa racine.
* **L'identité est la MAC**, jamais le nom — les noms d'équipements se
  ressemblent et s'éditent (règle constante du projet).

Le graphe N'EST PAS un arbre
----------------------------
Mesuré sur le parc le 2026-08-04 : 17 sites, 19 arêtes, dont **2 hors arbre**
(``SK1↔CT2`` et ``KS1↔SM1``). Ce sont de vraies boucles de redondance — CT2 est
joignable par PK1 *et* par SK1. Un rendu arborescent devrait en cacher une sans
le dire, donc :func:`layered_graph` produit des **couches** (parcours en largeur)
et rend les arêtes surnuméraires à part, jamais supprimées.

La racine ne se déduit pas
--------------------------
Le lien Internet→HQ n'est pas un data-link : le contrôleur ne le connaît pas. La
racine est donc un **paramètre** (``TOPOLOGY_ROOT_SITE``), avec repli sur le site
de plus haut degré — et la sortie dit toujours laquelle des deux a servi, pour
qu'un repli silencieux ne passe pas pour une déduction.

Colorer une arête
-----------------
Les mesures viennent de NOTRE poll (``total_capacity_mbps``, ``link_potential_pct``),
pas du chiffre relayé par UISP : la couleur d'un lien est ainsi cohérente avec ce
qui déclenche l'alerting. ⚠️ Une arête a **deux** bouts et ils ne répondent pas
toujours tous les deux (mesuré : 6 arêtes radio sur 15 mesurées des deux côtés,
6 d'un seul, 3 d'aucun). La règle est donc :

* deux bouts mesurés → on retient le **pire** des deux (un lien vaut son extrémité
  la plus dégradée) ;
* un seul → celui-là ;
* aucun → ``None``, et l'arête est rendue **neutre, jamais verte**. Un lien qu'on
  ne mesure pas ne doit pas se lire comme un lien sain.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Device
from app.services import uisp_service
from app.services.uisp_sync_service import classify_device

logger = logging.getLogger(__name__)

# Métriques relues en base pour qualifier une arête (cf. docstring du module).
EDGE_METRICS: tuple[str, ...] = ("total_capacity_mbps", "link_potential_pct", "signal_dbm")


# ---------------------------------------------------------------------------
# Résolution des sites d'infra
# ---------------------------------------------------------------------------


def infra_site_ids(raw_sites: list[dict], raw_devices: list[dict]) -> dict[str, str]:
    """Sites d'infra → nom, décidés par **ce que le site porte**.

    Deux critères, et le second est celui qui tranche :

    1. Le site ne porte pas de client CRM (``ucrm.client``) — les ~1400 sites
       d'abonnés en portent un, et sans ce filtre chaque lien AP↔station
       deviendrait une « liaison inter-sites ».
    2. Le site porte **au moins un équipement d'infra**, au sens de
       :func:`uisp_sync_service.classify_device` — le classificateur unique du
       projet, réutilisé et jamais recopié.

    Le critère 1 seul ne suffit pas, mesuré sur le parc le 2026-08-04 : deux
    sites d'abonnés (« Haydara, Ousmane », « El id, Mohamed fall ») ont été créés
    à la main dans UISP **sans rattachement CRM**. Ils passaient donc pour de
    l'infra, et le premier apparaissait comme un site enfant de SK1 — alors que
    le lien en question est un banal AP↔abonné. (Ces deux lignes sont par
    ailleurs une anomalie de gestion : un abonné sans lien CRM est un abonné
    potentiellement non facturé, même famille que la section « absents de UISP »
    de /access-diagnostics.)

    Le nom n'est **jamais** un critère : se fier à la convention « A2 <CODE> »
    casserait au premier site nommé hors norme.
    """
    infra_by_site: set[str] = set()
    for raw in raw_devices:
        ident = raw.get("identification") or {}
        site_id = (ident.get("site") or {}).get("id")
        if not site_id:
            continue
        mapping = classify_device(
            ident.get("type"),
            ident.get("role"),
            ident.get("model"),
            ((raw.get("overview") or {}).get("wirelessMode")),
        )
        if mapping is not None:
            infra_by_site.add(str(site_id))

    out: dict[str, str] = {}
    for site in raw_sites:
        ident = site.get("identification") or {}
        site_id = ident.get("id")
        if not site_id or str(site_id) not in infra_by_site:
            continue
        if ((site.get("ucrm") or {}).get("client") or {}).get("id"):
            continue
        name = (ident.get("name") or "").strip()
        if name:
            # Les noms sont pris VERBATIM : le parc porte « A2  ARF1 » avec un
            # double espace, et `devices.site` le stocke tel quel — normaliser
            # ici casserait la jointure avec notre inventaire.
            out[str(site_id)] = name
    return out


def device_index(raw_devices: list[dict]) -> dict[str, dict]:
    """id UISP → ce qu'on a besoin de savoir de l'équipement pour le graphe."""
    out: dict[str, dict] = {}
    for raw in raw_devices:
        ident = raw.get("identification") or {}
        uisp_id = ident.get("id")
        if not uisp_id:
            continue
        site = ident.get("site") or {}
        out[str(uisp_id)] = {
            "id": str(uisp_id),
            "name": (ident.get("name") or "").strip() or "(sans nom)",
            "mac": (ident.get("mac") or "").strip().lower() or None,
            "role": ident.get("role"),
            "model": ident.get("model"),
            "site_id": str(site["id"]) if site.get("id") else None,
            "site_name": (site.get("name") or "").strip() or None,
        }
    return out


# ---------------------------------------------------------------------------
# Construction du graphe
# ---------------------------------------------------------------------------


def build_edges(
    links: list[dict],
    devices: dict[str, dict],
    infra_sites: dict[str, str],
) -> tuple[list[dict], dict[str, int]]:
    """Retenir les data-links joignant deux sites d'infra distincts.

    Renvoie ``(arêtes, compteurs de rejet par motif)`` : un graphe troué doit
    pouvoir s'expliquer, pas seulement se constater.

    Les liaisons redondantes (deux radios entre les deux mêmes sites) sont
    **conservées comme arêtes distinctes** ici ; c'est :func:`layered_graph` qui
    les agrège pour le rendu. Les compter pour une seule dès la construction
    ferait disparaître une redondance réelle du décompte.
    """
    edges: list[dict] = []
    skipped: dict[str, int] = defaultdict(int)

    for link in links:
        ends = []
        for side in ("from", "to"):
            end = link.get(side) or {}
            uisp_id = ((end.get("device") or {}).get("identification") or {}).get("id")
            ends.append(devices.get(str(uisp_id)) if uisp_id else None)

        a, b = ends
        if a is None or b is None:
            skipped["bout inconnu du contrôleur"] += 1
            continue
        if not a["site_id"] or not b["site_id"]:
            skipped["bout sans site"] += 1
            continue
        if a["site_id"] == b["site_id"]:
            skipped["lien intra-site"] += 1
            continue
        if a["site_id"] not in infra_sites or b["site_id"] not in infra_sites:
            # Le cas de masse : tout lien AP↔abonné. Attendu, pas une anomalie.
            skipped["bout hors site d'infra"] += 1
            continue

        # Orientation stable (ordre alphabétique) : une arête ne doit pas changer
        # d'identité selon le sens dans lequel UISP a provisionné le lien.
        first, second = sorted((a, b), key=lambda d: infra_sites[d["site_id"]])
        edges.append(
            {
                "type": (link.get("type") or "?").lower(),
                "state": link.get("state"),
                "site_a": infra_sites[first["site_id"]],
                "site_b": infra_sites[second["site_id"]],
                "device_a": first,
                "device_b": second,
            }
        )
    return edges, dict(skipped)


def layered_graph(
    edges: list[dict], all_infra_sites: set[str], root: str | None
) -> dict:
    """Profondeurs, parents, arêtes hors arbre et anomalies — le layout complet.

    Parcours en **largeur** et non construction d'arbre : le graphe porte des
    boucles de redondance (mesuré), et un arbre strict devrait en jeter une
    arête sans le dire. Ici la profondeur reste juste et les arêtes surnuméraires
    sont rendues à part, à charge du rendu de les tracer autrement.
    """
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge["site_a"]].add(edge["site_b"])
        adjacency[edge["site_b"]].add(edge["site_a"])
    linked = set(adjacency)

    chosen_root, root_source = root, "paramètre"
    if chosen_root not in adjacency:
        if chosen_root:
            logger.warning(
                "Racine de topologie « %s » absente du graphe — repli sur le degré maximal",
                chosen_root,
            )
        chosen_root = max(adjacency, key=lambda s: (len(adjacency[s]), s)) if adjacency else None
        root_source = "degré maximal"

    depth: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    if chosen_root is not None:
        depth[chosen_root] = 0
        parent[chosen_root] = None
        queue = deque([chosen_root])
        while queue:
            current = queue.popleft()
            for neighbour in sorted(adjacency[current]):
                if neighbour not in depth:
                    depth[neighbour] = depth[current] + 1
                    parent[neighbour] = current
                    queue.append(neighbour)

    tree_pairs = {frozenset((child, par)) for child, par in parent.items() if par}
    # Restreint aux sites ATTEINTS : une arête d'une composante séparée n'est pas
    # une boucle, elle est déjà signalée comme composante — la compter ici la
    # ferait apparaître sous deux diagnostics contraires.
    extra = [
        e for e in edges
        if e["site_a"] in depth and e["site_b"] in depth
        and frozenset((e["site_a"], e["site_b"])) not in tree_pairs
    ]

    return {
        "root": chosen_root,
        "root_source": root_source,
        "depth": depth,
        "parent": parent,
        "adjacency": {k: sorted(v) for k, v in adjacency.items()},
        "extra_edges": extra,
        "components": _components(linked, adjacency),
        "orphan_sites": sorted(all_infra_sites - linked),
        "unreached_sites": sorted(linked - set(depth)),
    }


def _components(nodes: set[str], adjacency: dict[str, set[str]]) -> list[list[str]]:
    """Composantes connexes — plusieurs = le graphe se dessine en morceaux."""
    seen: set[str] = set()
    out: list[list[str]] = []
    for node in sorted(nodes):
        if node in seen:
            continue
        group: list[str] = []
        queue = deque([node])
        seen.add(node)
        while queue:
            current = queue.popleft()
            group.append(current)
            for neighbour in adjacency[current]:
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        out.append(sorted(group))
    return out


# ---------------------------------------------------------------------------
# Rapprochement avec notre inventaire
# ---------------------------------------------------------------------------


async def supervised_by_mac(session: AsyncSession, macs: set[str]) -> dict[str, dict]:
    """MAC → ce que NOUS savons de l'équipement (supervisé ? état ? mesures ?).

    Ce qu'un bout d'arête n'a pas ici, la carte ne pourra ni colorer ni rendre
    cliquable : le statut et la mesure viennent de notre poll, pas d'UISP.
    """
    if not macs:
        return {}
    # Filtre en SQL sur la forme normalisée : les MAC sont stockées avec une
    # casse variable selon la source qui a créé la ligne, donc un `in_` brut en
    # raterait.
    rows = (
        await session.execute(
            select(Device).where(func.lower(func.trim(Device.mac_address)).in_(sorted(macs)))
        )
    ).scalars().all()
    by_mac = {d.mac_address.strip().lower(): d for d in rows if d.mac_address}
    if not by_mac:
        return {}

    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (dm.device_id, dm.metric_name)
                   dm.device_id, dm.metric_name, dm.metric_value
            FROM device_metrics dm
            WHERE dm.device_id = ANY(CAST(:ids AS integer[]))
              AND dm.metric_name = ANY(CAST(:names AS text[]))
            ORDER BY dm.device_id, dm.metric_name, dm.collected_at DESC
            """
        ),
        {"ids": [d.id for d in by_mac.values()], "names": list(EDGE_METRICS)},
    )
    latest: dict[int, dict[str, float]] = defaultdict(dict)
    for row in result.all():
        latest[row.device_id][row.metric_name] = float(row.metric_value)

    return {
        mac: {
            "id": d.id,
            "name": d.name,
            "device_type": d.device_type,
            "status": d.status,
            "site": d.site,
            "metrics": latest.get(d.id, {}),
        }
        for mac, d in by_mac.items()
    }


def capacity_floor(end_a: dict | None, end_b: dict | None) -> float | None:
    """Plancher de capacité applicable à la liaison, par famille de matériel.

    **Repris de** :func:`lr_health_service.get_site_link_health`, qui alimente
    déjà la section « Liaisons entre sites » : une liaison doit être rendue
    dégradée sur la carte **exactement quand** cette section la listerait. Deux
    vues du même fait qui se contrediraient rendraient les deux inutilisables.

    Un lien ethernet (switch↔switch) n'a pas de plancher : sa capacité n'est pas
    mesurée par nos polls, et un port cuivre a déjà ses propres règles.
    """
    settings = get_settings()
    types = {e.get("device_type") for e in (end_a, end_b) if e}
    if "airfiber" in types:
        return float(settings.af60_capacity_display_min_mbps)
    if "ptp_litebeam" in types:
        return float(settings.airmax_backhaul_capacity_min_mbps)
    return None


def edge_health(end_a: dict | None, end_b: dict | None) -> dict:
    """Santé d'une arête à partir de ses deux bouts — le **pire** des deux.

    Une arête a deux extrémités et elles ne répondent pas toujours toutes les
    deux. Aucune mesure ⇒ ``state="unmeasured"`` et pas de valeur : le rendu doit
    alors rester neutre. Rendre vert un lien qu'on ne mesure pas serait le
    mensonge le plus coûteux de la carte — c'est exactement l'inverse de ce
    qu'une supervision doit dire.

    ``degraded`` est calculé ICI, contre le plancher réel des réglages, et jamais
    côté rendu : la couleur d'un trait doit être celle qui déclenche l'alerte,
    pas un barème recopié dans le frontend qui divergerait au premier ajustement
    de seuil.
    """
    caps, pots = [], []
    for end in (end_a, end_b):
        if not end:
            continue
        metrics = end.get("metrics") or {}
        if metrics.get("total_capacity_mbps") is not None:
            caps.append(metrics["total_capacity_mbps"])
        if metrics.get("link_potential_pct") is not None:
            pots.append(metrics["link_potential_pct"])

    down = any(e and e.get("status") == "down" for e in (end_a, end_b))
    if down:
        state = "down"
    elif not caps and not pots:
        state = "unmeasured"
    else:
        state = "measured"

    # Un lien vaut son extrémité la plus dégradée.
    capacity = min(caps) if caps else None
    floor = capacity_floor(end_a, end_b)
    return {
        "state": state,
        "capacity_mbps": capacity,
        "link_potential_pct": min(pots) if pots else None,
        "measured_ends": len(caps),
        "floor_mbps": floor,
        # Une capacité inconnue n'est PAS une capacité dégradée (même règle que
        # `ifSpeed = 0` sur les cages SFP) : sans mesure, on n'affirme rien.
        "degraded": bool(floor is not None and capacity is not None and capacity < floor),
    }


# ---------------------------------------------------------------------------
# Point d'entrée unique — le script ET l'API passent par là
# ---------------------------------------------------------------------------


async def get_site_topology(session: AsyncSession, root: str | None = None) -> dict:
    """Le graphe inter-sites complet, prêt à rendre ou à imprimer.

    Trois appels au contrôleur (devices, sites, data-links) : c'est une lecture
    du provisioning, jamais une écriture. Les erreurs de transport remontent —
    une carte vide vaut mieux qu'une carte partielle qu'on croirait complète.
    """
    settings = get_settings()
    if not settings.uisp_base_url:
        return {
            "available": False,
            "reason": "UISP_BASE_URL non configuré",
            "sites": [], "edges": [], "layout": {}, "stats": {},
        }

    client = uisp_service.UISPClient(
        settings.uisp_base_url,
        username=settings.uisp_username,
        password=settings.uisp_password,
        api_token=settings.uisp_api_token,
        verify_tls=settings.uisp_verify_tls,
        timeout=settings.uisp_request_timeout,
    )
    raw_devices = await client.fetch_devices()
    raw_sites = await client.fetch_sites()
    links = await client.fetch_data_links()

    infra = infra_site_ids(raw_sites, raw_devices)
    devices = device_index(raw_devices)
    edges, skipped = build_edges(links, devices, infra)

    macs = {
        e[side]["mac"]
        for e in edges for side in ("device_a", "device_b")
        if e[side]["mac"]
    }
    ours = await supervised_by_mac(session, macs)

    layout = layered_graph(edges, set(infra.values()), root or settings.topology_root_site)

    # Agrégation pour le rendu : une seule arête par paire de sites, portant ses
    # liaisons physiques. Deux radios entre deux sites sont UNE liaison logique
    # redondante, pas deux traits superposés illisibles.
    merged: dict[frozenset, dict] = {}
    for edge in edges:
        key = frozenset((edge["site_a"], edge["site_b"]))
        ours_a = ours.get(edge["device_a"]["mac"] or "")
        ours_b = ours.get(edge["device_b"]["mac"] or "")
        member = {
            "type": edge["type"],
            "state": edge["state"],
            "device_a": {**_end_payload(edge["device_a"], ours_a)},
            "device_b": {**_end_payload(edge["device_b"], ours_b)},
            "health": edge_health(ours_a, ours_b),
        }
        if key not in merged:
            merged[key] = {
                "site_a": edge["site_a"],
                "site_b": edge["site_b"],
                "is_tree_edge": layout["parent"].get(edge["site_b"]) == edge["site_a"]
                or layout["parent"].get(edge["site_a"]) == edge["site_b"],
                "links": [],
            }
        merged[key]["links"].append(member)

    for entry in merged.values():
        healths = [link_["health"] for link_ in entry["links"]]
        entry["health"] = _worst(healths)
        entry["redundant"] = len(entry["links"]) > 1

    sites = [
        {
            "site": name,
            "depth": layout["depth"].get(name),
            "parent": layout["parent"].get(name),
            "degree": len(layout["adjacency"].get(name, [])),
            "reachable": name in layout["depth"],
        }
        for name in sorted(infra.values())
    ]

    unsupervised = sorted({
        e[side]["name"]
        for e in edges for side in ("device_a", "device_b")
        if (e[side]["mac"] or "") not in ours
    })

    return {
        "available": True,
        "root": layout["root"],
        "root_source": layout["root_source"],
        "sites": sites,
        "edges": sorted(merged.values(), key=lambda e: (e["site_a"], e["site_b"])),
        "layout": {
            "components": layout["components"],
            "orphan_sites": layout["orphan_sites"],
            "unreached_sites": layout["unreached_sites"],
            "extra_edges": [
                {"site_a": e["site_a"], "site_b": e["site_b"], "type": e["type"]}
                for e in layout["extra_edges"]
            ],
        },
        "stats": {
            "uisp_devices": len(raw_devices),
            "uisp_sites": len(raw_sites),
            "infra_sites": len(infra),
            "data_links": len(links),
            "edges": len(merged),
            "physical_links": len(edges),
            "skipped_links": skipped,
            "unsupervised_ends": unsupervised,
        },
    }


def _end_payload(end: dict, ours: dict | None) -> dict:
    """Un bout d'arête : ce qu'UISP en dit + ce que nous en savons (ou rien)."""
    return {
        "uisp_name": end["name"],
        "mac": end["mac"],
        "site": end["site_name"],
        "supervised": ours is not None,
        "device_id": ours["id"] if ours else None,
        "name": ours["name"] if ours else end["name"],
        "device_type": ours["device_type"] if ours else None,
        "status": ours["status"] if ours else None,
        "capacity_mbps": (ours["metrics"].get("total_capacity_mbps") if ours else None),
        "link_potential_pct": (ours["metrics"].get("link_potential_pct") if ours else None),
    }


_HEALTH_RANK = {"down": 0, "unmeasured": 1, "measured": 2}


def _worst(healths: list[dict]) -> dict:
    """La santé d'une liaison redondante est celle de sa meilleure branche.

    Nuance volontaire : entre deux radios reliant les mêmes sites, le trafic
    passe par celle qui marche — une branche morte ne coupe pas la liaison. On
    remonte donc la MEILLEURE, à l'inverse de la règle qui vaut *à l'intérieur*
    d'une liaison (le pire des deux bouts), où les deux extrémités décrivent le
    même lien physique.
    """
    if not healths:
        return {"state": "unmeasured", "capacity_mbps": None, "link_potential_pct": None,
                "measured_ends": 0, "floor_mbps": None, "degraded": False}
    # Une branche saine l'emporte sur une branche dégradée : sans ce second
    # critère, `max` rendrait la première rencontrée et une liaison redondante
    # pourrait s'afficher dégradée alors qu'elle a une branche intacte.
    return max(
        healths,
        key=lambda h: (_HEALTH_RANK.get(h["state"], 1), not h.get("degraded", False)),
    )
