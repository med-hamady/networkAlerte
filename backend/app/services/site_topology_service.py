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

Deux fraîcheurs, deux chemins
-----------------------------
:func:`sync_site_links` est le **seul** code ici qui parle au contrôleur, et il
tourne **1×/jour** : il rapatrie le câblage dans la table ``site_links``.
:func:`get_site_topology` sert la page depuis notre base, **sans aucun appel à
UISP**.

Ce découpage n'est pas une optimisation tardive, c'est la nature de la donnée.
Le câblage ne bouge que quand le terrain pose un backhaul — quelques fois par an.
La santé d'une liaison, elle, change en permanence : elle est donc relue à chaque
affichage depuis ``devices``/``device_metrics``, jamais figée dans la table
quotidienne (ce serait montrer l'état d'hier comme celui de maintenant).

Avant ce découpage, chaque ouverture de la page téléchargeait ~1300 équipements,
~1400 sites et ~1300 liens — et le rafraîchissement automatique de l'onglet le
refaisait toutes les 2 minutes.

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

Les routes vers Internet
------------------------
Puisque le graphe porte des boucles, un site a souvent **deux** chemins vers la
racine. :func:`internet_routes` les énumère, désigne le meilleur (celui dont le
maillon le plus chargé est le moins occupé) et nomme sur chacun le **goulot**.
⚠️ Ce sont les chemins que le **câblage permet** : ni OSPF ni la table de
routage ne sont lus, donc rien n'affirme par où le trafic passe réellement.

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

import datetime
import logging
from collections import defaultdict, deque

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Device, UispSwitch
from app.models.site_link import SiteLink
from app.models.site_location import SiteLocation
from app.services import uisp_service
from app.services.uisp_sync_service import classify_device

logger = logging.getLogger(__name__)

# Métriques relues en base pour qualifier une arête (cf. docstring du module).
# Les débits servent à distinguer une liaison qui ÉCOULE du trafic d'une liaison
# debout mais inerte — voir `edge_traffic`.
EDGE_METRICS: tuple[str, ...] = (
    "total_capacity_mbps", "link_potential_pct", "signal_dbm",
    "dl_throughput_mbps", "ul_throughput_mbps",
    # Débit du port fibre d'un SWITCH, dérivé de ses compteurs SNMP par
    # `snmp_poll_job` : c'est la seule mesure de trafic possible sur une liaison
    # inter-sites en fibre, dont les deux bouts sont des switches.
    "fiber_dl_throughput_mbps", "fiber_ul_throughput_mbps",
    # Occupation du lien (temps d'antenne, AF60 seulement — cf.
    # `af60_api_service._set_occupancy`). Distincte des débits ci-dessus : ceux-là
    # disent si ça PASSE, celle-ci si c'est PLEIN. Un backhaul de secours qui
    # encaisse le trafic d'une branche entière écoule beaucoup (donc « actif »,
    # vert) tout en étant à bout de souffle — seule l'occupation le montre.
    "link_occupancy_pct",
    # Les deux PARTS du total, par sens (relatives à l'équipement interrogé) :
    # elles disent par quel bout le lien se remplit. `edge_occupancy` les
    # renomme par site — sur une liaison, « descendant » ne veut rien dire tant
    # qu'on n'a pas choisi une extrémité.
    "link_occupancy_dl_pct", "link_occupancy_ul_pct",
)

# Types d'équipement qui font d'un site un site d'INFRA dans notre inventaire.
# Sert uniquement à faire apparaître un site dépourvu de backhaul provisionné :
# sans lui, un tel site serait absent de la carte au lieu d'y être signalé
# orphelin. Les LR (abonnés) en sont exclus — un site client n'est pas un nœud.
INFRA_SITE_DEVICE_TYPES: tuple[str, ...] = (
    "rocket", "airfiber", "ptp_litebeam", "uisp_switch", "uisp_power",
)


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

    out = {
        mac: {
            "id": d.id,
            "name": d.name,
            "device_type": d.device_type,
            "status": d.status,
            "site": d.site,
            "metrics": latest.get(d.id, {}),
            "uplink_switch_id": d.uplink_switch_id,
            "uplink_port": d.uplink_switch_port,
            "uplink_switch": None,
            "port_speed_mbps": None,
        }
        for mac, d in by_mac.items()
    }
    await _attach_uplink_port_speed(session, out)
    return out


async def _attach_uplink_port_speed(session: AsyncSession, ours: dict[str, dict]) -> None:
    """Renseigne la VITESSE NÉGOCIÉE du port de switch de chaque extrémité.

    Le port physique de chaque équipement est déjà connu (`uplink_switch_id` /
    `uplink_switch_port`, posés par `switch_port_mapping_job` d'après les
    data-links du contrôleur), et sa vitesse est relevée à chaque cycle SNMP
    dans `port_N_speed_mbps` sur le switch. Il ne reste qu'à croiser les deux.

    Ça vaut la requête : le parc porte de vrais backhauls **bloqués à 100 Mb/s
    alors qu'ils devraient être en gigabit** (relevé le 2026-07-30 sur
    `A2-AT2-SUD1`, `A2-NR1-NORD`, `A2-SM1-OUEST`, `F60 CT1-NR1`). C'est
    invisible sur une carte qui ne montre que l'état haut/bas.

    ⚠️ Beaucoup d'extrémités n'ont AUCUN port connu — au premier chef les
    liaisons fibre, dont les deux bouts sont des switches (les uplinks
    inter-switch sont volontairement non attribués : chaque bout est une
    affirmation valable, mais un équipement n'a qu'une colonne d'uplink). Elles
    restent à ``None`` : pas de port connu, pas de point affiché, aucune
    affirmation.
    """
    wanted: dict[int, set[int]] = defaultdict(set)
    for info in ours.values():
        if info["uplink_switch_id"] and info["uplink_port"]:
            wanted[info["uplink_switch_id"]].add(info["uplink_port"])
    if not wanted:
        return

    metric_names = sorted({
        f"port_{port}_speed_mbps" for ports in wanted.values() for port in ports
    })
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
        {"ids": sorted(wanted), "names": metric_names},
    )
    speeds = {(r.device_id, r.metric_name): float(r.metric_value) for r in result.all()}

    switch_names = dict(
        (await session.execute(
            select(Device.id, Device.name).where(Device.id.in_(sorted(wanted)))
        )).all()
    )

    for info in ours.values():
        sid, port = info["uplink_switch_id"], info["uplink_port"]
        if not sid or not port:
            continue
        info["uplink_switch"] = switch_names.get(sid)
        speed = speeds.get((sid, f"port_{port}_speed_mbps"))
        # `ifSpeed = 0` (cages SFP) : une vitesse INCONNUE n'est pas une vitesse
        # dégradée — même règle que la surveillance des ports.
        info["port_speed_mbps"] = speed if speed else None


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


def edge_traffic(end_a: dict | None, end_b: dict | None) -> dict:
    """Trafic écoulé par la liaison, **par direction**.

    Renvoie ``{state, total_mbps, a_to_b_mbps, b_to_a_mbps}`` où ``a``/``b``
    désignent les deux extrémités passées (donc site_a / site_b).

    ⚠️ **Sens.** Pour l'AF60 comme pour l'airMAX, ``dl_throughput_mbps`` est ce
    que l'équipement **interrogé REÇOIT** et ``ul`` ce qu'il **émet** (cf. les
    sections « DÉBIT vs CAPACITÉ » de CLAUDE.md). Un même flux est donc mesuré
    deux fois, sous deux noms opposés :

        A → B   =  ul de A   =  dl de B
        B → A   =  dl de A   =  ul de B

    On expose ces deux directions **nommées par les sites**, et non un
    « descendant/montant » global : ces mots n'ont de sens que vu d'un bout, et
    les inverser afficherait le trafic à l'envers sur la moitié des liaisons.
    C'est l'appelant, qui connaît la relation parent/enfant du graphe, qui peut
    ensuite les traduire en descendant/montant.

    Pour chaque direction on retient le relevé le **plus élevé** des deux bouts :
    ils décrivent le même flux, mais l'un peut n'avoir aucune valeur fraîche.

    ⚠️ **« Pas mesuré » n'est pas « pas de trafic »** — c'est ``unknown``, et le
    rendu doit alors s'abstenir (vert, pas jaune). Le cas est massif : les
    liaisons FIBRE ont des switches aux deux bouts, et un switch n'expose aucun
    débit en SNMP. Les peindre en jaune signalerait des pannes de trafic
    permanentes sur la dorsale du HQ, ce qui est faux.
    """
    settings = get_settings()

    def _m(end: dict | None, key: str) -> float | None:
        metrics = (end or {}).get("metrics") or {}
        # Un SWITCH n'a pas de débit propre : c'est celui de son port fibre qui
        # décrit la liaison. Même convention de sens (`dl` = ce qu'il reçoit),
        # donc un simple repli suffit — rien d'autre ne change en aval.
        value = metrics.get(key)
        return value if value is not None else metrics.get(f"fiber_{key}")

    def _best(*values: float | None) -> float | None:
        known = [v for v in values if v is not None]
        return max(known) if known else None

    a_to_b = _best(_m(end_a, "ul_throughput_mbps"), _m(end_b, "dl_throughput_mbps"))
    b_to_a = _best(_m(end_a, "dl_throughput_mbps"), _m(end_b, "ul_throughput_mbps"))

    if a_to_b is None and b_to_a is None:
        return {"state": "unknown", "total_mbps": None,
                "a_to_b_mbps": None, "b_to_a_mbps": None}

    total = (a_to_b or 0.0) + (b_to_a or 0.0)
    floor = float(settings.topology_traffic_min_mbps)
    return {
        "state": "active" if total >= floor else "idle",
        "total_mbps": total,
        "a_to_b_mbps": a_to_b,
        "b_to_a_mbps": b_to_a,
    }


def edge_occupancy(end_a: dict | None, end_b: dict | None) -> dict:
    """Occupation de la liaison, **par direction**, nommée par les sites.

    Renvoie ``{total_pct, a_to_b_pct, b_to_a_pct}``. Le **total** dit si le lien
    est plein (c'est lui qui alerte) ; les deux parts disent **par quel bout** il
    se remplit — la question qui rend l'information actionnable, un lien à 94 %
    dont 89 dans un seul sens n'appelant pas le même geste qu'un lien rempli
    symétriquement.

    ⚠️ **Même convention de sens que** :func:`edge_traffic`, et pour la même
    raison : ``link_occupancy_dl_pct`` est la part de temps d'antenne que
    l'équipement interrogé consomme à **recevoir**, ``ul`` à **émettre**. Un même
    flux est donc mesuré deux fois sous deux noms opposés ::

        A → B   =  ul de A   =  dl de B
        B → A   =  dl de A   =  ul de B

    On expose donc les directions **nommées par les sites**, jamais un
    « descendant/montant » global : ces mots n'ont de sens que vu d'un bout, et
    les inverser afficherait la charge à l'envers sur la moitié des liaisons.

    Pour chaque direction on retient le relevé le **plus élevé** des deux bouts
    (ils décrivent le même flux, mais l'un peut n'avoir aucune valeur fraîche).

    ⚠️ **Le total n'est PAS recalculé depuis les deux parts** : il vaut le plus
    haut des totaux **par équipement**, chacun étant un instantané cohérent de
    ses quatre valeurs. Quand les deux bouts sont d'accord — le cas normal — la
    somme des parts retombe sur le total ; quand ils divergent, mieux vaut un
    total tiré d'une seule lecture cohérente que la somme de deux moitiés prises
    à des instants différents.
    """
    def _share(end: dict | None, key: str) -> float | None:
        return ((end or {}).get("metrics") or {}).get(key)

    def _best(*values: float | None) -> float | None:
        known = [v for v in values if v is not None]
        return max(known) if known else None

    totals = [
        v for v in (_share(end_a, "link_occupancy_pct"), _share(end_b, "link_occupancy_pct"))
        if v is not None
    ]
    return {
        "total_pct": max(totals) if totals else None,
        "a_to_b_pct": _best(
            _share(end_a, "link_occupancy_ul_pct"), _share(end_b, "link_occupancy_dl_pct")
        ),
        "b_to_a_pct": _best(
            _share(end_a, "link_occupancy_dl_pct"), _share(end_b, "link_occupancy_ul_pct")
        ),
    }


def site_occupancy_map(edges: list[dict]) -> dict[str, float]:
    """Par site : l'occupation de sa liaison la PLUS CHARGÉE.

    L'occupation se **mesure** par liaison, mais ce qu'un opérateur cherche sur
    la carte, c'est quel **site** étrangle. Cette fonction fait le passage.

    ⚠️ **Attribuée aux DEUX extrémités**, délibérément : un tuyau plein gêne ses
    deux bouts — le site d'aval ne peut plus recevoir, celui d'amont ne peut
    plus émettre. Le réflexe serait de ne marquer que l'aval (« son uplink est
    plein »), mais ça supposerait que le trafic emprunte l'arête d'**arbre** :
    or `parent` vient d'un parcours en largeur du **câblage**, pas du routage
    réel. Après un basculement, le chemin qui porte le trafic est justement
    celui que l'arbre ne montre pas. On n'affirme donc que le mesuré — « ce site
    a une liaison pleine » — et le survol du trait dit laquelle.

    ⚠️ **Le MAX, jamais la moyenne** : un site à 5 liaisons dont une saturée est
    un site à problème ; moyenner le noierait sous les quatre saines.

    Une liaison sans occupation mesurée (fibre : deux switches aux bouts)
    n'entre pas dans le calcul — un site n'ayant que celles-là reste **absent**
    du dict, ce qui se rend en « non mesuré » et surtout pas en « fluide ».
    """
    out: dict[str, float] = {}
    for edge in edges:
        occupancy = (edge.get("health") or {}).get("occupancy_pct")
        if occupancy is None:
            continue
        for side in ("site_a", "site_b"):
            site = edge.get(side)
            if site is not None and occupancy > out.get(site, -1.0):
                out[site] = occupancy
    return out


# ---------------------------------------------------------------------------
# Routes vers Internet — la meilleure, et où ça sature
# ---------------------------------------------------------------------------
#
# ⚠️ CE QU'ON ÉNUMÈRE EST LE CÂBLAGE, PAS LE ROUTAGE. Le contrôleur nous dit
# quel site est relié à quel autre ; il ne dit pas par où le trafic passe, et on
# ne lit ni OSPF ni la table de routage. Une « route » ici est donc un chemin
# que le câblage PERMET vers la racine — pas une affirmation sur le chemin
# emprunté. C'est la même mise en garde que dans `site_occupancy_map`, et elle
# doit rester visible jusque dans l'UI : sans elle, l'écran se lit comme un
# diagnostic de routage, ce qu'il n'est pas.

# Bornes de l'énumération. Une recherche de chemins simples est exponentielle
# par nature : un parc futur plus maillé ne doit pas pouvoir faire exploser un
# affichage.
#
# ⚠️ 12 et non 8, et le raisonnement qui donnait 8 était FAUX : ce qui borne un
# chemin n'est pas la profondeur de l'arbre (4 sur ce parc) mais la longueur du
# plus long chemin SIMPLE, qui serpente à travers les boucles. Mesuré sur les
# 17 sites : le plus long fait **11 sauts**, et à 8 l'énumération se coupait sur
# **6 sites sur 17** — six bandeaux « liste incomplète » qui auraient usé la
# confiance dans l'écran. À 12 elle est complète, pour 44 chemins et moins de
# 2 ms (le graphe est creux : 19 arêtes pour 17 nœuds).
ROUTE_MAX_HOPS = 12
# ⚠️ Le budget compte les DÉPILAGES, pas les chemins complets — c'est la seule
# borne qui tienne quel que soit le facteur de branchement. Mesuré : 17
# dépilages au pire sur le parc réel, donc ~300× de marge avant qu'il ne morde.
ROUTE_MAX_EXPANSIONS = 5000
# Garde-fou de TAILLE de réponse, pas une politique d'affichage : on veut voir
# toutes les sorties d'un site, et le parc réel n'en produit que 3 ou 4.
ROUTE_KEEP = 12


# Sous ce taux d'occupation, la projection ci-dessous n'extrapole que du bruit.
# Vu sur les données réelles : `CT2↔PK1`, qui ne portait quasiment rien, était
# désigné « point de saturation » d'un chemin avec un « pic 0 Mb/s » — un lien
# VIDE présenté comme le maillon qui bride.
ROUTE_MIN_OCCUPANCY_FOR_PROJECTION = 5.0


async def fibre_cut_edges(
    session: AsyncSession, device_ids_by_edge: dict[frozenset, set[int]]
) -> set[frozenset]:
    """Les liaisons FIBRE dont le port SFP ne passe plus la lumière.

    ⚠️ **Sans ça, une fibre coupée passe pour saine.** L'état d'une arête vient
    du statut PING de ses deux bouts — or une fibre coupée ne rend pas ses
    switches injoignables : le site reste atteignable **par sa liaison radio de
    secours**, les deux switches répondent, et la dorsale morte continue de
    s'afficher comme la meilleure route. C'est le scénario exact où l'opérateur
    a besoin qu'on lui montre l'autre chemin — et c'était le seul où l'écran se
    taisait.

    Le signal existe déjà : le port désigné `fiber_port_index` est relevé à
    chaque cycle SNMP dans `port_N_up`, et c'est lui qui déclenche l'alerte
    `fiber_link_down`. On lit **la même métrique**, pour que la carte et
    l'alerte ne puissent pas se contredire.

    ⚠️ **On ne coupe que sur un DOWN constaté** (`port_N_up == 0`). Métrique
    absente = pas de relevé, pas un verdict : la liaison reste telle quelle.
    Côté HQ le switch est un UniFi hors inventaire, donc un seul bout porte
    l'information — un seul suffit.
    """
    ids = sorted({d for group in device_ids_by_edge.values() for d in group})
    if not ids:
        return set()

    ports = dict((await session.execute(
        select(UispSwitch.id, UispSwitch.fiber_port_index).where(
            UispSwitch.id.in_(ids), UispSwitch.fiber_port_index.isnot(None)
        )
    )).all())
    if not ports:
        return set()

    rows = (await session.execute(
        text("""
            SELECT DISTINCT ON (dm.device_id, dm.metric_name)
                   dm.device_id, dm.metric_name, dm.metric_value
            FROM device_metrics dm
            WHERE dm.device_id = ANY(CAST(:ids AS integer[]))
              AND dm.metric_name = ANY(CAST(:names AS text[]))
            ORDER BY dm.device_id, dm.metric_name, dm.collected_at DESC
        """),
        {
            "ids": sorted(ports),
            "names": sorted({f"port_{idx}_up" for idx in ports.values()}),
        },
    )).all()
    states = {(r.device_id, r.metric_name): float(r.metric_value) for r in rows}

    cut = set()
    for key, devices in device_ids_by_edge.items():
        for device_id in devices:
            index = ports.get(device_id)
            if index is None:
                continue
            if states.get((device_id, f"port_{index}_up")) == 0.0:
                cut.add(key)
                break
    return cut


def silence_dead_link(health: dict) -> None:
    """Un lien COUPÉ ne porte rien — ni descendant, ni montant.

    ⚠️ Sans ça, la dernière valeur relevée survit à la panne et se propage
    partout : marge, goulot, couleur de la liaison, infobulle. Le cas est vécu
    sur `ARF1↔HQ`, dont le compteur SNMP du port SFP annonçait **18 645 Mb/s**
    dans un sens et **0** dans l'autre alors que la fibre est coupée — un débit
    fantôme, d'un facteur 24 au-dessus de ce que la branche peut produire.

    C'est le pendant de la règle déjà tenue pour la capacité : la dernière valeur
    connue ne doit pas maquiller la panne. Ici elle doit valoir **zéro**, pas
    « inconnu » : on ne s'abstient pas faute de mesure, on SAIT que rien ne passe.

    ⚠️ `traffic` reste distinct de `idle` : une liaison coupée n'est pas une
    liaison debout et inerte, et seule la seconde mérite le signalement « dorsale
    sans trafic ».
    """
    health["traffic_mbps"] = 0.0
    health["traffic_a_to_b_mbps"] = 0.0
    health["traffic_b_to_a_mbps"] = 0.0
    health["occupancy_pct"] = None
    health["occupancy_a_to_b_pct"] = None
    health["occupancy_b_to_a_pct"] = None
    health["saturated"] = False


def peak_load(
    traffic_mbps: float | None, occupancy_pct: float | None
) -> dict | None:
    """Débit maximal d'un lien, et marge restante, à partir d'un point de charge.

    ⚠️ **Surtout pas `capacité − trafic`.** Sur un lien TDD (AF60),
    ``total_capacity_mbps`` est la MOYENNE des deux sens : la soustraire d'un
    ``dl + ul`` est faux dimensionnellement — exactement l'erreur que le projet
    a déjà écartée pour le calcul de l'occupation, où elle rendait 120 %.

    On passe donc par l'occupation, qui est un **temps d'antenne** et sature à
    100 % : à mélange descendant/montant constant, multiplier le trafic par
    ``100 / occupation`` remplit le lien.

        débit max = trafic ÷ (occupation / 100)
        marge     = débit max − trafic

    Ce que ça dit exactement : « au mélange de sens observé à cet instant, ce
    lien plafonne à X Mb/s ». Changer le mélange changerait le plafond — c'est
    une projection, pas une constante physique.

    ⚠️ **Rend ``None`` sur un lien quasi vide.** Diviser un trafic infime par une
    occupation infime amplifie le bruit de mesure : 0,4 Mb/s à 0,1 % projette
    400 Mb/s de plafond sur rien du tout. Pire, le lien se retrouvait désigné
    « point de saturation » du chemin alors qu'il ne portait aucun trafic. Un
    lien vide ne contraint personne : on ne se prononce pas, et il sort du calcul
    de la marge au lieu de la fausser.
    """
    if traffic_mbps is None or occupancy_pct is None:
        return None
    if occupancy_pct < ROUTE_MIN_OCCUPANCY_FOR_PROJECTION:
        return None
    max_rate = traffic_mbps * 100.0 / occupancy_pct
    return {
        "peak_traffic_mbps": round(traffic_mbps, 1),
        "peak_occupancy_pct": round(occupancy_pct, 1),
        "max_rate_mbps": round(max_rate, 1),
        "headroom_mbps": round(max_rate - traffic_mbps, 1),
    }


def _rank_routes(routes: list[dict]) -> list[dict]:
    """Classe les chemins d'un site — « la plus grande marge restante ».

    Décision opérateur : entre deux chemins **mesurés**, le meilleur est celui
    dont le maillon le PLUS CHARGÉ est le MOINS occupé. Le nombre de sauts ne
    départage qu'à égalité — un détour moins chargé vaut mieux qu'un raccourci
    saturé, et c'est bien ce qui est demandé.

    ⚠️ ``degraded`` ne pénalise pas. L'occupation étant débit/capacité, un lien
    dégradé (capacité sous son plancher) affiche déjà mécaniquement une
    occupation plus haute : le repénaliser compterait le même fait deux fois.
    Il est exposé (``degraded_hops``), pas déduit du classement.

    ⚠️ **Le cas qui piège : un chemin NON MESURÉ n'est comparable à rien.** Le
    laisser systématiquement derrière les mesurés produit une absurdité vécue —
    ARF1 est relié au HQ par une fibre directe qu'on ne sait pas mesurer, et le
    classement recommandait à sa place un **détour de 5 sauts chargé à 86 %**
    par tout le maillage. Un chemin non mesuré passe donc devant quand il est
    **strictement plus court que tous les mesurés** : c'est la référence
    évidente de l'opérateur. Aucune marge n'est inventée pour autant — il est
    seulement placé, jamais élu (cf. le retrait du badge dans
    :func:`internet_routes`).
    """
    usable = [r for r in routes if r["usable"]]
    with_margin = [r["hop_count"] for r in usable if r["headroom_mbps"] is not None]
    shortest_measured = min(with_margin) if with_margin else None

    def _group(route: dict) -> int:
        if not route["usable"]:
            return 4  # un chemin coupé reste listé, mais toujours en dernier
        if route["radio_hop_count"] == 0:
            # Tout en FIBRE : rien ne le bride côté radio, et on sait qu'il est
            # debout. C'est le meilleur chemin possible, sans discussion — c'est
            # le cas des trois dorsales du HQ.
            return 0
        if route["headroom_mbps"] is not None:
            return 2
        # Radio sans historique de charge : incomparable. Devant si strictement
        # plus court que tout chemin chiffré (le réflexe de l'opérateur), sinon
        # derrière. Jamais élu dans un cas comme dans l'autre.
        if shortest_measured is None or route["hop_count"] < shortest_measured:
            return 1
        return 3

    def _key(route: dict) -> tuple:
        return (
            _group(route),
            # LA marge, décroissante : le plus de place restante d'abord.
            -(route["headroom_mbps"] if route["headroom_mbps"] is not None else 0.0),
            route["radio_hop_count"] - route["measured_hops"],
            route["hop_count"],
            -(route["min_capacity_mbps"] or 0.0),
            route["id"],  # déterminisme à égalité parfaite
        )

    return sorted(routes, key=_key)


def _select_shown(routes: list[dict], keep: int) -> list[dict]:
    """TOUTES les routes du site, classées — on n'en cache aucune.

    ⚠️ Ce module a d'abord masqué des chemins : un seul par « point de rupture »,
    et une seule route coupée. L'intention était bonne (AT2 affichait trois
    chemins dont deux morts sur le MÊME lien), mais la décision n'appartient pas
    au backend : l'opérateur veut voir **toutes** les sorties possibles de son
    site, y compris les redondantes et les mortes. C'est le RENDU qui replie le
    surplus derrière un « voir les autres », ce qui se déplie ; une route absente
    de la réponse, non.

    Ce qui subsiste des règles écartées : le CLASSEMENT (les plus utiles en
    tête) et l'annotation `same_bottleneck_as`, qui dit qu'un chemin cède au même
    endroit qu'un autre au lieu de le supprimer pour cette raison.

    `keep` ne reste qu'un garde-fou de taille de réponse, jamais une politique
    d'affichage — et l'écart est rapporté par `found`/`kept`.
    """
    shown = routes[:keep]

    seen: dict[frozenset, str] = {}
    for route in shown:
        bottleneck = route["bottleneck"]
        route["same_bottleneck_as"] = None
        if bottleneck is None:
            continue
        key = frozenset((bottleneck["site_a"], bottleneck["site_b"]))
        if key in seen:
            # Pas une raison de cacher le chemin — une raison de dire qu'il ne
            # constitue pas une alternative au même point de rupture.
            route["same_bottleneck_as"] = seen[key]
        else:
            seen[key] = route["id"]
    return shown


def _describe_route(path: list[str], by_pair: dict[frozenset, dict]) -> dict:
    """Un chemin (liste de sites, du site vers la racine) → son verdict complet.

    ⚠️ **Un saut FIBRE ne se juge pas comme un saut radio.** Les trois dorsales
    du HQ (ARF1, AT1, CT1) sont en fibre : la seule question qu'elles posent est
    « le lien est-il debout et du trafic passe-t-il ». Elles n'ont ni taux
    d'occupation ni plafond utile à comparer aux AF60, et le contraire s'était
    vu à l'écran : comptées comme « non mesurées », elles faisaient afficher
    « départage impossible » sur les trois sites de la dorsale et dégradaient
    tous les badges en « MEILLEURE · 2/3 mesurés ». Un chemin se juge donc sur
    ses sauts RADIO — et le goulot est toujours un AF60, jamais la fibre.
    """
    hops: list[dict] = []
    for origin, target in zip(path, path[1:], strict=False):
        key = frozenset((origin, target))
        edge = by_pair[key]
        health = edge.get("health") or {}
        # ⚠️ La charge vient de la DERNIÈRE VALEUR EN BASE de l'équipement, pas
        # d'un historique : sur un lien radio, l'état de maintenant décrit mieux
        # le réseau qu'un pic d'hier. Aucun équipement n'est interrogé ici.
        load = peak_load(health.get("traffic_mbps"), health.get("occupancy_pct")) or {}
        fibre = edge.get("medium") == "wired"
        hops.append({
            # La fibre : up + trafic, rien d'autre. `traffic` vaut 'active',
            # 'idle' (mesuré à zéro — anormal sur une dorsale, donc signalé) ou
            # 'unknown' (un switch n'expose pas toujours son débit).
            "is_fibre": fibre,
            # Fibre dont le port SFP ne passe plus la lumière. Distinct d'un
            # simple « hors service » : ici l'équipement répond, c'est le VERRE
            # qui est coupé — et le geste terrain n'est pas le même.
            "fibre_cut": bool(health.get("fibre_cut")),
            "traffic": health.get("traffic"),
            # Débit brut courant de la liaison — sert au contrôle d'agrégat de
            # `_project_failover` (ce qui alimente un lien borne ce qu'il porte).
            "traffic_mbps": health.get("traffic_mbps"),
            # Charge courante et marge — vides sur la fibre, par construction.
            "peak_traffic_mbps": load.get("peak_traffic_mbps"),
            "peak_occupancy_pct": load.get("peak_occupancy_pct"),
            "max_rate_mbps": load.get("max_rate_mbps"),
            "headroom_mbps": load.get("headroom_mbps"),
            # ⚠️ Orientation reprise TELLE QUELLE de l'arête : c'est la clé par
            # laquelle le frontend rejoint `edges[]`. La réorienter dans le sens
            # de la marche ferait rater le lookup en silence.
            "site_a": edge["site_a"],
            "site_b": edge["site_b"],
            # Le sens de marche vers la racine, que la paire ne porte pas.
            "from": origin,
            "to": target,
            "occupancy_pct": health.get("occupancy_pct"),
            "saturated": bool(health.get("saturated")),
            "state": health.get("state"),
            "degraded": bool(health.get("degraded")),
            "capacity_mbps": health.get("capacity_mbps"),
            "redundant": bool(edge.get("redundant")),
            "links_count": len(edge.get("links") or []),
            "medium": edge.get("medium"),
            "is_bottleneck": False,
        })

    # ⚠️ Tout ce qui suit ne regarde QUE les sauts radio : la fibre n'a pas de
    # taux, et ne doit ni porter le goulot ni faire baisser la couverture.
    radio = [h for h in hops if not h["is_fibre"]]
    measured = [h for h in radio if h["occupancy_pct"] is not None]
    with_margin = [h for h in radio if h["headroom_mbps"] is not None]

    # LE verdict : ce que le chemin peut encore écouler, borné par son maillon
    # le plus juste. `None` = aucun saut radio ne rend de mesure de charge.
    headroom = min((h["headroom_mbps"] for h in with_margin), default=None)
    max_rate = min((h["max_rate_mbps"] for h in with_margin), default=None)
    # ⚠️ Le max des sauts MESURÉS, et `None` s'il n'y en a aucun. Ni 0 ni 100 —
    # une occupation absente n'est ni « fluide » ni « pleine ».
    max_occupancy = max((h["occupancy_pct"] for h in measured), default=None)

    # Le goulot est le maillon de plus petite MARGE : c'est lui qui plafonne le
    # chemin en Mb/s. ⚠️ Ce n'est pas forcément le plus occupé en % — un lien à
    # 90 % de 1951 Mb/s laisse 195 Mb/s, un lien à 50 % de 300 Mb/s n'en laisse
    # que 150 : c'est le second qui bride. Sans historique de charge, on retombe
    # sur l'occupation, qui reste une indication.
    bottleneck = None
    if with_margin:
        # À égalité, le saut le plus PROCHE DU SITE : celui sur lequel
        # l'opérateur peut agir localement, et le choix reste déterministe.
        pick = min(with_margin, key=lambda h: (h["headroom_mbps"], hops.index(h)))
    elif measured:
        pick = max(measured, key=lambda h: (h["occupancy_pct"], -hops.index(h)))
    else:
        pick = None
    if pick is not None:
        pick["is_bottleneck"] = True
        bottleneck = {
            "site_a": pick["site_a"],
            "site_b": pick["site_b"],
            "occupancy_pct": pick["occupancy_pct"],
            "headroom_mbps": pick["headroom_mbps"],
            "max_rate_mbps": pick["max_rate_mbps"],
            "peak_traffic_mbps": pick["peak_traffic_mbps"],
        }

    capacities = [h["capacity_mbps"] for h in hops if h["capacity_mbps"] is not None]
    down_hops = [
        {"site_a": h["site_a"], "site_b": h["site_b"], "fibre_cut": h["fibre_cut"]}
        for h in hops if h["state"] == "down"
    ]
    degraded_hops = [
        {"site_a": h["site_a"], "site_b": h["site_b"]} for h in hops if h["degraded"]
    ]

    # ⚠️ La couverture se compte sur les sauts RADIO seuls. Un chemin tout en
    # fibre n'est pas « non mesuré » : il n'a simplement rien à mesurer.
    if not radio:
        coverage = "full"
    elif not measured:
        coverage = "none"
    elif len(measured) == len(radio):
        coverage = "full"
    else:
        coverage = "partial"

    # ⚠️ **AUCUNE projection de bascule, et c'est physique.** Le réflexe est
    # d'annoncer « après bascule ce maillon portera X » en additionnant sa charge
    # actuelle et le trafic du chemin coupé. C'est un DOUBLE COMPTAGE : dès que
    # la dorsale d'ARF1 tombe, elle ne porte plus rien et le trafic d'ARF1 est
    # DÉJÀ reparti par PK1 et TS1 — donc la mesure courante de ces liaisons
    # contient déjà ce qu'on voulait y ajouter.
    #
    # Après une coupure réelle, il n'y a donc rien à projeter : il suffit de
    # LIRE. L'occupation du secours a monté, sa marge a fondu, et les deux se
    # voient directement. (Une version « et si ça coupait ? » avant la panne
    # serait une autre fonctionnalité, avec son propre modèle — pas une addition.)
    # Dorsale debout mais à l'arrêt : anormal sur ces trois liens, donc signalé.
    # ⚠️ `unknown` n'est PAS `idle` — un switch n'expose pas toujours son débit.
    fibre_idle_hops = [
        {"site_a": h["site_a"], "site_b": h["site_b"]}
        for h in hops
        if h["is_fibre"] and h["traffic"] == "idle" and h["state"] != "down"
    ]

    return {
        "id": ">".join(path),
        "sites": path,
        "hop_count": len(hops),
        # Le nombre de sauts qui portent réellement une contrainte de débit.
        # 0 = chemin tout en fibre : rien ne le bride côté radio.
        "radio_hop_count": len(radio),
        "hops": hops,
        # LE verdict, en Mb/s : ce que le chemin peut encore prendre, et son
        # plafond. `None` sur un chemin tout fibre (rien ne le borne) comme sur
        # un chemin radio sans historique — `radio_hop_count` sépare les deux.
        "headroom_mbps": headroom,
        "max_rate_mbps": max_rate,
        "max_occupancy_pct": max_occupancy,
        "bottleneck": bottleneck,
        "min_capacity_mbps": min(capacities) if capacities else None,
        "measured_hops": len(measured),
        "coverage": coverage,
        # Un saut `unmeasured` n'est PAS un saut `down` : il reste utilisable,
        # il baisse seulement la couverture.
        "usable": not down_hops,
        "down_hops": down_hops,
        "degraded_hops": degraded_hops,
        "fibre_idle_hops": fibre_idle_hops,
        # Renseigné par `_select_shown` : ce chemin cède au même endroit qu'un
        # autre, donc il n'est pas une alternative pour CE point de rupture.
        "same_bottleneck_as": None,
        "is_best": False,
    }


def internet_routes(
    merged_edges: list[dict],
    root: str | None,
    sites: set[str] | None = None,
    *,
    max_hops: int = ROUTE_MAX_HOPS,
    max_expansions: int = ROUTE_MAX_EXPANSIONS,
    keep: int = ROUTE_KEEP,
) -> dict[str, dict]:
    """Par site : ses chemins vers la racine, le meilleur, et le goulot de chacun.

    Prend les arêtes **LOGIQUES** (celles de ``edges[]``, déjà fusionnées par
    paire de sites), jamais les liens physiques : deux radios entre les deux
    mêmes sites sont **un seul saut** redondant. Les énumérer séparément
    afficherait « deux routes » comme quatre et distinguerait des chemins que la
    couche IP ne choisit pas — la redondance est un attribut du saut
    (``redundant``, ``links_count``), pas une route de plus.

    ``sites`` (optionnel) = l'ensemble des sites à couvrir, pour qu'un site
    ORPHELIN reçoive une entrée disant pourquoi il n'a aucune route, au lieu
    d'être simplement absent du dict — une absence se lirait comme un oubli.

    Les trois bornes sont **rapportées**, jamais silencieuses (``found``/
    ``kept``/``truncated``) : un rendu qui écarte un chemin sans le dire est
    exactement la faute que ce module refuse déjà pour les boucles du graphe.
    """
    if root is None:
        return {}

    by_pair: dict[frozenset, dict] = {}
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in merged_edges:
        site_a, site_b = edge["site_a"], edge["site_b"]
        by_pair[frozenset((site_a, site_b))] = edge
        adjacency[site_a].add(site_b)
        adjacency[site_b].add(site_a)

    neighbours = {site: sorted(peers) for site, peers in adjacency.items()}
    covered = set(sites) if sites else set()
    covered |= set(neighbours) | {root}

    out: dict[str, dict] = {}
    for site in sorted(covered):
        if site == root:
            out[site] = _empty_route_group(site, "racine")
            continue

        paths, truncated = _walk_to_root(site, root, neighbours, max_hops, max_expansions)
        if not paths:
            out[site] = _empty_route_group(site, "aucun chemin vers la racine", truncated)
            continue

        routes = _rank_routes([_describe_route(p, by_pair) for p in paths])
        kept_routes = _select_shown(routes, keep)

        # Le badge « meilleure » va en TÊTE DE LISTE, et seulement si ce chemin
        # est utilisable ET mesuré : élire un chemin dont on ne sait rien serait
        # recommander sur la foi de rien, et élire le second reviendrait à dire
        # que le premier est moins bon alors qu'on ne l'a pas mesuré. Quand on
        # ne tranche pas, on dit pourquoi — c'est en soi actionnable (« la
        # dorsale la plus courte n'est pas instrumentée »).
        leader = kept_routes[0]
        best_id, best_reason = None, None
        if not leader["usable"]:
            best_reason = "toutes les routes traversent un lien tombé"
        elif leader["radio_hop_count"] == 0 or leader["headroom_mbps"] is not None:
            # Élu : soit tout en fibre (rien ne le bride), soit sa marge est
            # chiffrée. Ce sont les deux seuls cas où l'on sait ce qu'on
            # recommande.
            leader["is_best"] = True
            best_id = leader["id"]
        else:
            best_reason = (
                "aucune route dont la charge soit mesurée"
                if all(r["headroom_mbps"] is None for r in routes if r["usable"])
                else "le chemin le plus court n'a pas d'historique de charge"
            )

        out[site] = {
            "site": site,
            "reason": None,
            "best_id": best_id,
            "best_reason": best_reason,
            "found": len(routes),
            "kept": len(kept_routes),
            "truncated": truncated,
            "paths": kept_routes,
            # Renseignés juste après, une fois TOUS les sites connus.
            "role": None,
            "exits": [],
            "decider": None,
        }

    _assign_roles(out, root)
    return out


def _assign_roles(groups: dict[str, dict], root: str) -> None:
    """Qui DÉCIDE de la direction du trafic, et qui ne fait que la subir.

    ⚠️ Distinction structurelle que le rendu ignorait, et qui change ce qu'on a
    le droit d'afficher. Un site à **plusieurs sorties** vers la racine arbitre
    réellement ; un site à **une seule** ne choisit rien — il remet son trafic au
    site du dessus. VEL1 n'a que TS1 : lui annoncer « 3 routes possibles » laisse
    croire à un choix qu'elle n'a pas. Ses trois chemins sont ceux de TS1, et
    c'est **TS1** qui arbitre entre ARF1 et SM1.

    Conséquence pratique : sur un site enfant, l'écran doit renvoyer vers son
    décideur — c'est là qu'on agit, pas sur l'enfant.

    ⚠️ Une **sortie** n'est pas une liaison voisine : c'est le premier saut d'un
    chemin qui MÈNE quelque part. TJN1 est voisin de DN1 mais c'est un
    cul-de-sac, donc pas une sortie de DN1 — les compter ferait passer des
    enfants pour des décideurs.
    """
    for site, group in groups.items():
        if group["reason"]:
            continue
        exits: list[str] = []
        for path in group["paths"]:
            first = path["sites"][1]
            if first not in exits:
                exits.append(first)
        group["exits"] = exits
        group["role"] = (
            "root" if site == root else "decider" if len(exits) > 1 else "child"
        )

    # Pour un enfant : le premier site EN AMONT qui a réellement un choix.
    # NR1 et SNDE n'en ont aucun — leur sortie unique va droit à la racine, donc
    # personne ne peut les rerouter. Le dire vaut mieux que laisser un blanc.
    for site, group in groups.items():
        if group["role"] != "child":
            continue
        seen, current = {site}, group["exits"][0]
        while True:
            upstream = groups.get(current)
            if upstream is None or current in seen or upstream["reason"]:
                break
            if upstream["role"] == "decider":
                group["decider"] = current
                break
            seen.add(current)
            if not upstream["exits"]:
                break
            current = upstream["exits"][0]


def _empty_route_group(site: str, reason: str, truncated: dict | None = None) -> dict:
    """Un site sans route — avec la raison en clair, jamais une liste vide muette."""
    return {
        "site": site,
        "reason": reason,
        "role": "root" if reason == "racine" else "isolated",
        "exits": [],
        "decider": None,
        "best_id": None,
        "best_reason": None,
        "found": 0,
        "kept": 0,
        "truncated": truncated or {"by_hops": False, "by_budget": False},
        "paths": [],
    }


def _walk_to_root(
    site: str,
    root: str,
    neighbours: dict[str, list[str]],
    max_hops: int,
    max_expansions: int,
) -> tuple[list[list[str]], dict]:
    """Tous les chemins SIMPLES de ``site`` à ``root``, bornés — et ce qu'on a coupé.

    Parcours en profondeur itératif (pile explicite) : la récursion sur un
    graphe dont on ne maîtrise pas la densité future est le meilleur moyen de
    faire tomber un worker sur un dépassement de pile.
    """
    stack: list[tuple[str, list[str], frozenset]] = [(site, [site], frozenset((site,)))]
    paths: list[list[str]] = []
    truncated = {"by_hops": False, "by_budget": False}
    expansions = 0

    while stack:
        if expansions >= max_expansions:
            truncated["by_budget"] = True
            break
        expansions += 1
        current, path, visited = stack.pop()

        if current == root:
            paths.append(path)
            continue

        unexplored = [n for n in neighbours.get(current, ()) if n not in visited]
        if len(path) - 1 >= max_hops:
            # On ne lève le drapeau que si on renonce vraiment à explorer :
            # une impasse atteinte à la longueur max n'est pas une troncature.
            if unexplored:
                truncated["by_hops"] = True
            continue

        for neighbour in unexplored:
            stack.append((neighbour, [*path, neighbour], visited | {neighbour}))

    return paths, truncated


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
    tr = edge_traffic(end_a, end_b)

    # OCCUPATION — le MAX des deux bouts, alors que la capacité prend le min :
    # dans les deux cas on retient l'extrémité la plus mal en point, mais ici
    # « plus mal » veut dire PLUS HAUT. Si un bout se dit à 50 % et l'autre à
    # 90 %, la liaison est à 90 %.
    #
    # ⚠️ On prend l'occupation DÉJÀ CALCULÉE par chaque équipement, on ne la
    # recalcule pas en croisant les bouts : chaque AF60 a une vue cohérente de
    # ses quatre valeurs au même instant, marier le débit de A avec la capacité
    # de B apparierait une valeur fraîche à une valeur périmée.
    #
    # ⚠️ Seuil lu dans les réglages d'ENV (`get_settings`), comme
    # `capacity_floor` juste au-dessus — donc une surcharge posée sur la page
    # Seuils s'applique à l'ALERTE mais pas encore à cette couleur. Limitation
    # connue et partagée avec le plancher de capacité ; à corriger pour les deux
    # ensemble en faisant descendre les réglages effectifs jusqu'ici.
    occ = edge_occupancy(end_a, end_b)
    occupancy = occ["total_pct"]
    sat_floor = float(get_settings().af60_occupancy_critical_pct)
    return {
        "state": state,
        "capacity_mbps": capacity,
        "link_potential_pct": min(pots) if pots else None,
        "measured_ends": len(caps),
        "floor_mbps": floor,
        # Occupation en temps d'antenne (AF60 seulement). None = non mesurée, ce
        # qui n'est PAS « au repos » : le rendu doit s'abstenir, jamais peindre
        # une liaison comme fluide faute de mesure.
        "occupancy_pct": occupancy,
        # PAR DIRECTION, nommée par les sites (a = site_a, b = site_b) — même
        # convention que `traffic_a_to_b_mbps` et pour la même raison : le
        # « descendant/montant » n'a de sens que vu d'un bout. Le total dit que
        # le lien est plein, ces deux-là par quel bout il se remplit.
        "occupancy_a_to_b_pct": occ["a_to_b_pct"],
        "occupancy_b_to_a_pct": occ["b_to_a_pct"],
        "occupancy_floor_pct": sat_floor,
        "saturated": bool(occupancy is not None and occupancy >= sat_floor),
        # "active" | "idle" | "unknown" — `unknown` ⇒ le rendu s'abstient.
        "traffic": tr["state"],
        "traffic_mbps": tr["total_mbps"],
        # Par direction, nommées par les sites (a = site_a, b = site_b) : le
        # « descendant/montant » n'a de sens que vu d'un bout.
        "traffic_a_to_b_mbps": tr["a_to_b_mbps"],
        "traffic_b_to_a_mbps": tr["b_to_a_mbps"],
        # Une capacité inconnue n'est PAS une capacité dégradée (même règle que
        # `ifSpeed = 0` sur les cages SFP) : sans mesure, on n'affirme rien.
        "degraded": bool(floor is not None and capacity is not None and capacity < floor),
    }


# ---------------------------------------------------------------------------
# SYNC quotidien — le seul chemin qui parle au contrôleur
# ---------------------------------------------------------------------------


async def sync_site_links(session: AsyncSession) -> dict:
    """Rapatrier le câblage inter-sites depuis UISP et le persister.

    Trois appels au contrôleur, **une fois par jour** : c'est une lecture du
    provisioning, jamais une écriture. Les data-links désignent leurs extrémités
    par l'id UISP de l'équipement — id que nous ne stockons pas — d'où le besoin
    de ``/devices`` pour traduire en MAC, notre identité partout ailleurs.

    ⚠️ **Remplacement intégral** de la table, et c'est volontaire : contrairement
    à la FDB d'un switch (où l'absence d'une MAC et la chute du port sont la même
    observation), UISP **ne retire pas** un lien qui tombe — il le rapporte
    ``state="disconnected"``, vérifié sur le lien CT1↔SK1 en panne. Une absence
    ici signifie donc « l'opérateur a dé-provisionné ce lien », pas « le lien est
    down ».

    ⚠️ **Garde-fou** : un fetch qui ne rend AUCUN lien ne vide pas la table. Un
    payload vide ou malformé serait sinon lu comme « plus aucun backhaul » et
    effacerait toute la topologie — même leçon que la passe de suppression du
    sync des stations.
    """
    settings = get_settings()
    if not settings.uisp_base_url:
        return {"ok": False, "reason": "UISP_BASE_URL non configuré"}

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
    edges, skipped = build_edges(links, device_index(raw_devices), infra)

    if not edges:
        logger.warning(
            "Sync topologie : aucune liaison inter-sites résolue sur %d data-links "
            "— table CONSERVÉE en l'état (un payload vide ne vaut pas un parc "
            "sans backhaul). Motifs de rejet : %s",
            len(links), skipped,
        )
        return {
            "ok": False,
            "reason": "aucune liaison résolue — table conservée",
            "data_links": len(links),
            "skipped": skipped,
        }

    now = datetime.datetime.now(datetime.UTC)
    await session.execute(delete(SiteLink))
    for edge in edges:
        session.add(
            SiteLink(
                site_a=edge["site_a"],
                site_b=edge["site_b"],
                link_type=edge["type"],
                state=edge["state"],
                mac_a=edge["device_a"]["mac"],
                mac_b=edge["device_b"]["mac"],
                name_a=edge["device_a"]["name"],
                name_b=edge["device_b"]["name"],
                synced_at=now,
            )
        )

    pairs = {frozenset((e["site_a"], e["site_b"])) for e in edges}
    logger.info(
        "Sync topologie : %d lien(s) physique(s) sur %d liaison(s), %d sites d'infra "
        "(%d data-links lus, rejets %s)",
        len(edges), len(pairs), len(infra), len(links), skipped,
    )
    return {
        "ok": True,
        "physical_links": len(edges),
        "edges": len(pairs),
        "infra_sites": len(infra),
        "data_links": len(links),
        "skipped": skipped,
        "synced_at": now,
    }


# ---------------------------------------------------------------------------
# LECTURE — base seule, aucun appel au contrôleur
# ---------------------------------------------------------------------------


async def get_site_topology(session: AsyncSession, root: str | None = None) -> dict:
    """Le graphe inter-sites, servi depuis notre base — zéro appel à UISP.

    Deux fraîcheurs différentes, assemblées ici :

    * le **câblage** vient de ``site_links``, synchronisé 1×/jour (il ne bouge
      que quand le terrain pose un backhaul) ;
    * la **santé** — statut, capacité, potentiel — est relue MAINTENANT depuis
      ``devices``/``device_metrics``. La figer dans la table quotidienne
      afficherait l'état d'hier comme s'il était celui de maintenant.
    """
    settings = get_settings()
    rows = (await session.execute(select(SiteLink))).scalars().all()
    if not rows:
        return {
            "available": False,
            "reason": "câblage jamais synchronisé — lancer le sync de topologie",
            # `routes` est présent même vide : la forme de la réponse doit rester
            # stable pour les consommateurs de `--json` et du frontend.
            "sites": [], "edges": [], "routes": {}, "layout": {}, "stats": {},
        }

    # Reconstruire la forme d'arête que le reste du module attend. Les sites
    # d'infra sont ceux que le câblage relie, plus ceux que notre inventaire
    # porte (un site sans backhaul doit rester visible comme orphelin).
    edges = [
        {
            "type": r.link_type,
            "state": r.state,
            "site_a": r.site_a,
            "site_b": r.site_b,
            "device_a": {"name": r.name_a, "mac": r.mac_a, "site_name": r.site_a},
            "device_b": {"name": r.name_b, "mac": r.mac_b, "site_name": r.site_b},
        }
        for r in rows
    ]
    synced_at = max(r.synced_at for r in rows)

    # Les sites de l'inventaire ET ceux que le câblage relie : un site d'infra
    # sans backhaul provisionné doit apparaître (c'est justement l'anomalie à
    # montrer), et un site nommé par un lien doit apparaître même si notre
    # inventaire ne le porte pas encore.
    health = await site_device_health(session)
    infra_sites = set(health) | {r.site_a for r in rows} | {r.site_b for r in rows}

    macs = {
        e[side]["mac"]
        for e in edges for side in ("device_a", "device_b")
        if e[side]["mac"]
    }
    ours = await supervised_by_mac(session, macs)

    layout = layered_graph(edges, infra_sites, root or settings.topology_root_site)

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
        # Support physique de la liaison, pour le style du trait. Une liaison
        # mixte compte comme filaire : le chemin cuivre/fibre est le plus
        # capable des deux, et c'est lui qui décrit le mieux la liaison.
        entry["medium"] = (
            "wireless"
            if all(link_["type"] == "wireless" for link_ in entry["links"])
            else "wired"
        )
        # Port de switch de chaque CÔTÉ de la liaison (a = site_a, b = site_b :
        # `build_edges` garantit que device_a est bien l'extrémité de site_a).
        # Sur une liaison redondante on retient le port le PLUS LENT : c'est
        # celui qui bride, et c'est lui qu'un opérateur doit aller voir.
        entry["port_a"] = _slowest_port([link_["device_a"] for link_ in entry["links"]])
        entry["port_b"] = _slowest_port([link_["device_b"] for link_ in entry["links"]])

    sat_floor = float(settings.af60_occupancy_critical_pct)
    site_occupancy = site_occupancy_map(list(merged.values()))

    # Les chemins vers la racine, par site. Une seule requête de plus : le pic
    # de charge des 24 h par liaison, qui donne la MARGE en Mb/s — le verdict
    # que l'opérateur lit, là où un pourcentage ne se traduit en rien.
    device_ids_by_edge = {
        frozenset((entry["site_a"], entry["site_b"])): {
            link_[side]["device_id"]
            for link_ in entry["links"] for side in ("device_a", "device_b")
            if link_[side]["device_id"] is not None
        }
        for entry in merged.values()
    }
    # ⚠️ Une fibre coupée ne rend pas ses switches injoignables : le site reste
    # atteignable par sa radio de secours, donc l'arête passerait pour saine et
    # la dorsale morte resterait « meilleure route ». On relit le port SFP.
    cut = await fibre_cut_edges(session, device_ids_by_edge)
    for key in cut:
        health = merged[key]["health"]
        health["state"] = "down"
        health["fibre_cut"] = True
        silence_dead_link(health)

    # La marge et le classement se calculent sur la DERNIÈRE VALEUR EN BASE de
    # chaque équipement (écrasée à chaque poll) — l'état de maintenant décrit
    # mieux le réseau qu'un pic d'hier, et aucun équipement n'est interrogé ici.
    # Aucun historique n'est lu : après une bascule, la charge reportée est déjà
    # DANS la mesure courante du chemin de secours.
    routes = internet_routes(list(merged.values()), layout["root"], infra_sites)

    # Position géographique du pylône, pour la vue CARTE de /topology. Jointure
    # sur la chaîne EXACTE (`site_locations.site` = `devices.site`) — les noms
    # portent des bizarreries voulues, dont le double espace de « A2  ARF1 » :
    # les normaliser ici ferait silencieusement disparaître ce site de la carte.
    # Un site sans ligne reste dans `sites[]` avec latitude/longitude à null :
    # l'UI le nomme comme non plaçable au lieu de l'escamoter — une carte qui
    # omet un site sans le dire se lit comme un réseau qui n'a pas ce site.
    coords = {
        row.site: row
        for row in (await session.execute(select(SiteLocation))).scalars().all()
    }

    sites = [
        {
            "site": name,
            "depth": layout["depth"].get(name),
            "parent": layout["parent"].get(name),
            "degree": len(layout["adjacency"].get(name, [])),
            "reachable": name in layout["depth"],
            # Compteur affiché sous le site, façon contrôleur : « 14/1 ».
            "device_count": health.get(name, {}).get("total", 0),
            "device_down_count": health.get(name, {}).get("down", 0),
            # Site ENTIÈREMENT tombé — le seul cas qui rougit ses liaisons.
            "is_down": health.get(name, {}).get("is_down", False),
            # Occupation de sa liaison la plus chargée, et le verdict.
            # ⚠️ ORTHOGONAL à `is_down`/`device_down_count` : un site en parfaite
            # santé peut être saturé — c'est même LE cas intéressant (un
            # backhaul de secours qui encaisse toute une branche). Le rendu doit
            # donc en faire un signal DISTINCT, jamais une nuance de la couleur
            # de disponibilité, sinon les deux se masqueraient l'un l'autre.
            # None = aucune liaison mesurée (fibre), et ce n'est PAS « fluide ».
            "occupancy_pct": site_occupancy.get(name),
            "saturated": name in site_occupancy and site_occupancy[name] >= sat_floor,
            "latitude": coords[name].latitude if name in coords else None,
            "longitude": coords[name].longitude if name in coords else None,
            # 'uisp' (semée depuis le contrôleur) ou 'manual' (corrigée à la
            # main) — une position retouchée sur le terrain reste reconnaissable.
            "position_source": coords[name].source if name in coords else None,
        }
        for name in sorted(infra_sites)
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
        # Date du dernier rapatriement du CÂBLAGE. La santé affichée, elle, est
        # de maintenant — l'UI doit dire lequel des deux elle date.
        "synced_at": synced_at.isoformat(),
        "sites": sites,
        "edges": sorted(merged.values(), key=lambda e: (e["site_a"], e["site_b"])),
        # Indexé par nom de site : un seul site à la fois consomme ses chemins,
        # alors que `sites[]` est parcouru en entier par le graphe ET la carte.
        "routes": routes,
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
            "infra_sites": len(infra_sites),
            "edges": len(merged),
            "physical_links": len(edges),
            "unsupervised_ends": unsupervised,
            # Les sites dont l'énumération a buté sur une borne : un coup d'œil
            # au JSON ou au CLI suffit à savoir que la liste n'est pas complète.
            "routes_truncated_sites": sorted(
                site
                for site, group in routes.items()
                if group["truncated"]["by_hops"] or group["truncated"]["by_budget"]
            ),
        },
    }


async def site_device_health(session: AsyncSession) -> dict[str, dict]:
    """Par site : combien d'équipements d'infra, et combien ne répondent plus.

    Deux usages, et la distinction est le cœur du rendu :

    * ``down`` alimente le compteur affiché sous chaque site (« 14/1 »), à la
      manière du contrôleur — une panne isolée reste visible ;
    * ``is_down`` ne vaut True que si **TOUS** les équipements du site sont
      tombés. C'est lui, et lui seul, qui rougit les liaisons du site : un
      équipement HS ne met pas un site à terre, et peindre en rouge les liaisons
      d'un site qui fonctionne enverrait chercher une panne de backhaul là où il
      n'y en a pas.

    ⚠️ Seuls les **types d'infra** sont comptés. Les LR portent le `site` de leur
    AP (le sync fait suivre le site au CPE), donc les inclure ferait afficher
    « 9 » comme « 87 » et un seul abonné éteint suffirait à fausser le compteur.
    """
    rows = await session.execute(
        select(Device.site, Device.status, func.count())
        .where(Device.device_type.in_(INFRA_SITE_DEVICE_TYPES), Device.site.is_not(None))
        .group_by(Device.site, Device.status)
    )
    out: dict[str, dict] = {}
    for site, status, count in rows.all():
        key = (site or "").strip()
        if not key:
            continue
        entry = out.setdefault(key, {"total": 0, "down": 0})
        entry["total"] += count
        if (status or "").lower() == "down":
            entry["down"] += count
    for entry in out.values():
        entry["is_down"] = entry["total"] > 0 and entry["down"] == entry["total"]
    return out


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
        # Port de switch sur lequel cette extrémité est câblée, et sa vitesse
        # négociée. `None` = câblage inconnu (cas des liaisons fibre) → le rendu
        # n'affiche alors aucun point.
        "uplink_switch": ours["uplink_switch"] if ours else None,
        "uplink_port": ours["uplink_port"] if ours else None,
        "port_speed_mbps": ours["port_speed_mbps"] if ours else None,
    }


def _slowest_port(ends: list[dict]) -> dict | None:
    """Le port le plus lent parmi les extrémités d'un même côté, ou ``None``.

    ``None`` quand AUCUNE extrémité n'a de port connu : le rendu n'affiche alors
    pas de point plutôt que d'en inventer un. Un port connu mais sans vitesse
    lue reste retourné (avec ``speed_mbps`` à ``None``) — savoir sur quel port
    l'équipement est câblé a de la valeur même sans sa vitesse.
    """
    known = [e for e in ends if e.get("uplink_port")]
    if not known:
        return None
    with_speed = [e for e in known if e.get("port_speed_mbps") is not None]
    pick = min(with_speed, key=lambda e: e["port_speed_mbps"]) if with_speed else known[0]
    return {
        "switch": pick.get("uplink_switch"),
        "port": pick.get("uplink_port"),
        "speed_mbps": pick.get("port_speed_mbps"),
    }


_HEALTH_RANK = {"down": 0, "unmeasured": 1, "measured": 2}
# `unknown` au-dessus d'`idle` : ne pas mesurer vaut mieux que mesurer zéro pour
# départager deux branches — on ne préfère pas une branche prouvée inerte.
_TRAFFIC_RANK = {"idle": 0, "unknown": 1, "active": 2}


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
                "measured_ends": 0, "floor_mbps": None, "degraded": False,
                "traffic": "unknown", "traffic_mbps": None,
                "traffic_a_to_b_mbps": None, "traffic_b_to_a_mbps": None,
                "occupancy_pct": None, "occupancy_a_to_b_pct": None,
                "occupancy_b_to_a_pct": None,
                "occupancy_floor_pct": None, "saturated": False}
    # Une branche saine l'emporte sur une branche dégradée : sans ce second
    # critère, `max` rendrait la première rencontrée et une liaison redondante
    # pourrait s'afficher dégradée alors qu'elle a une branche intacte. Même
    # raison pour le trafic : si UNE branche écoule, la liaison écoule.
    return max(
        healths,
        key=lambda h: (
            _HEALTH_RANK.get(h["state"], 1),
            _TRAFFIC_RANK.get(h.get("traffic", "unknown"), 1),
            not h.get("degraded", False),
        ),
    )
