r"""
Montre le GRAPHE INTER-SITES du réseau (le squelette de la carte UISP).

À quoi ça sert
--------------
La topologie **intra-site** (switch → Rockets/AF60/Power) est déjà rendue par le
composant `SiteTopology`. Ce qui manque pour reproduire la carte du contrôleur
UISP, c'est le niveau au-dessus : quel site est raccordé à quel autre — le maillage
HQ → ARF1 / AT1 / CT1 … → PK1 / TS1 / AT2 … Cette information n'est stockée NULLE
PART chez nous : on connaît le `site` de chaque AF60 et de chaque PTP LiteBeam,
jamais quel AF60 parle à quel AF60.

Le contrôleur, lui, le sait — ses propres agents le rapportent — et le publie sur
`GET /nms/api/v2.1/data-links`. C'est déjà notre source de vérité pour le câblage
des ports de switch (`switch_port_service.detect_from_uisp`), qui n'en garde que
les liens `type == "ethernet"`. Les liens **radio** de la même réponse portent nos
backhauls : c'est eux qu'on lit ici.

Pourquoi un script AVANT l'UI
-----------------------------
Un rendu SVG dessine toujours quelque chose, même sur un graphe troué : des sites
orphelins passeraient pour des sites sans panne, et une boucle de redondance
serait rendue comme un arbre — deux mensonges silencieux. Ce script imprime le
graphe en texte et **nomme ce qui cloche** (sites sans lien, composantes séparées,
arêtes surnuméraires, bouts non supervisés) pour qu'on sache ce qu'on dessine.
Même intention que `detect_switch_ports.py` : voir la détection à blanc avant de
compter dessus.

Il est **lecture seule par construction** : aucune écriture, ni en base, ni sur le
contrôleur (pas de `--apply`, contrairement à detect_switch_ports).

Ce qui fait qu'une arête existe
-------------------------------
Un data-link dont les deux bouts se résolvent sur deux **sites d'infra
DIFFÉRENTS**. Deux précisions qui portent tout le résultat :

* **Les sites clients sont exclus.** UISP rattache chaque abonné à son propre site
  (~1400), tous porteurs de `ucrm.client` — sans ce filtre, chaque lien AP↔station
  deviendrait une « liaison inter-sites » et le graphe compterait des milliers
  d'arêtes au lieu d'une vingtaine. Le discriminant est la présence de la clé CRM
  sur le site, pas son nom (cf. `uisp_assignment_service` : le site est la
  plomberie qui porte `ucrm.client.id`).
* **Le type de lien n'est pas un filtre.** On n'impose pas `wireless` : une liaison
  fibre ou cuivre entre deux sites est une arête tout aussi réelle. Le type est
  affiché, pas présupposé.

L'identité est l'**id UISP de l'équipement** côté contrôleur, traduit en **MAC**
pour le rapprochement avec notre inventaire — jamais le nom, qui se ressemble et
s'édite (règle constante du projet).

Usage :

    dc exec backend python scripts/dump_site_topology.py
    dc exec backend python scripts/dump_site_topology.py --root "A2 HQ"
    dc exec backend python scripts/dump_site_topology.py --json > topo.json
    dc exec backend python scripts/dump_site_topology.py --include-client-sites
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict, deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, text  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402
from app.models.device import Device  # noqa: E402
from app.services import uisp_service  # noqa: E402

# Métriques relues en base pour qualifier une arête. Ce sont celles dont la carte
# UISP se sert pour colorer ses liens (« Link Potential »), à ceci près qu'ici
# elles viennent de NOTRE poll : la couleur d'une future arête serait donc notre
# propre mesure, cohérente avec l'alerting, et pas un chiffre relayé.
_EDGE_METRICS = ("total_capacity_mbps", "link_potential_pct", "signal_dbm")


# ---------------------------------------------------------------------------
# Lecture du contrôleur
# ---------------------------------------------------------------------------


def _client():
    settings = get_settings()
    return uisp_service.UISPClient(
        settings.uisp_base_url,
        username=settings.uisp_username,
        password=settings.uisp_password,
        api_token=settings.uisp_api_token,
        verify_tls=settings.uisp_verify_tls,
        timeout=settings.uisp_request_timeout,
    )


def _is_client_site(site: dict) -> bool:
    """Un site porteur d'un client CRM est un site d'ABONNÉ, pas un site d'infra.

    C'est le seul discriminant fiable : les noms de sites clients sont des noms de
    personnes, ceux d'infra suivent la convention « A2 <CODE> » — mais se fier à
    la convention casserait au premier site nommé hors norme.
    """
    return bool(((site.get("ucrm") or {}).get("client") or {}).get("id"))


def _device_index(raw_devices: list[dict]) -> dict[str, dict]:
    """id UISP → ce qu'on a besoin de savoir de l'équipement."""
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
            "type": ident.get("type"),
            "model": ident.get("model"),
            "site_id": site.get("id"),
            "site_name": (site.get("name") or "").strip() or None,
        }
    return out


# ---------------------------------------------------------------------------
# Construction du graphe
# ---------------------------------------------------------------------------


def build_edges(
    links: list[dict],
    devices: dict[str, dict],
    infra_site_ids: set[str],
    include_client_sites: bool,
) -> tuple[list[dict], dict[str, int]]:
    """Retenir les data-links qui joignent deux sites distincts.

    Renvoie ``(arêtes, compteurs de rejet)``. Chaque rejet est compté par motif :
    un graphe troué doit pouvoir s'expliquer, pas seulement se constater.
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
            # Un bout que /devices ne décrit pas : lien vers du matériel retiré de
            # l'inventaire, ou payload incomplet.
            skipped["bout inconnu du contrôleur"] += 1
            continue
        if not a["site_name"] or not b["site_name"]:
            skipped["bout sans site"] += 1
            continue
        if a["site_id"] == b["site_id"]:
            skipped["lien intra-site"] += 1
            continue
        if not include_client_sites and (
            a["site_id"] not in infra_site_ids or b["site_id"] not in infra_site_ids
        ):
            # Le cas de masse : tout lien AP↔abonné. Attendu, pas une anomalie.
            skipped["bout sur un site client"] += 1
            continue

        edges.append(
            {
                "type": (link.get("type") or "?").lower(),
                "state": link.get("state"),
                "site_a": a["site_name"],
                "site_b": b["site_name"],
                "device_a": a,
                "device_b": b,
            }
        )
    return edges, dict(skipped)


def layered_tree(
    nodes: set[str], adjacency: dict[str, set[str]], root: str
) -> tuple[dict[str, int], dict[str, str | None]]:
    """Parcours en LARGEUR depuis la racine → profondeur et parent de chaque site.

    Volontairement un parcours en couches et non un arbre : si le graphe porte une
    boucle (redondance), un arbre strict devrait en jeter une arête sans le dire.
    Ici la profondeur reste juste, et les arêtes surnuméraires sont listées à part.
    """
    depth = {root: 0}
    parent: dict[str, str | None] = {root: None}
    queue = deque([root])
    while queue:
        current = queue.popleft()
        for neighbour in sorted(adjacency.get(current, ())):
            if neighbour not in depth:
                depth[neighbour] = depth[current] + 1
                parent[neighbour] = current
                queue.append(neighbour)
    return depth, parent


def components(nodes: set[str], adjacency: dict[str, set[str]]) -> list[set[str]]:
    """Composantes connexes — plusieurs = le graphe se dessine en morceaux."""
    seen: set[str] = set()
    out: list[set[str]] = []
    for node in sorted(nodes):
        if node in seen:
            continue
        group: set[str] = set()
        queue = deque([node])
        seen.add(node)
        while queue:
            current = queue.popleft()
            group.add(current)
            for neighbour in adjacency.get(current, ()):
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        out.append(group)
    return out


# ---------------------------------------------------------------------------
# Rapprochement avec notre inventaire
# ---------------------------------------------------------------------------


async def supervised_by_mac(session, macs: set[str]) -> dict[str, dict]:
    """MAC → ce que NOUS savons de l'équipement (supervisé ? état ? mesures ?).

    Ce qu'un bout d'arête n'a pas ici, une future carte ne pourra ni colorer ni
    rendre cliquable : la mesure et le statut viennent de notre poll, pas d'UISP.
    """
    if not macs:
        return {}
    # Filtre en SQL sur la forme normalisée : les MAC sont stockées avec une casse
    # variable selon la source qui a créé la ligne, donc un `in_` brut en raterait.
    rows = (
        await session.execute(
            select(Device).where(
                func.lower(func.trim(Device.mac_address)).in_(sorted(macs))
            )
        )
    ).scalars().all()
    by_mac = {d.mac_address.strip().lower(): d for d in rows if d.mac_address}
    if not by_mac:
        return {}

    metrics = await session.execute(
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
        {"ids": [d.id for d in by_mac.values()], "names": list(_EDGE_METRICS)},
    )
    latest: dict[int, dict[str, float]] = defaultdict(dict)
    for row in metrics.all():
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


def _end_label(end: dict, ours: dict[str, dict]) -> str:
    """Décrire un bout d'arête : ce qu'UISP en dit, puis ce que nous en savons."""
    mine = ours.get(end["mac"]) if end["mac"] else None
    if mine is None:
        return f"{end['name']} [NON SUPERVISÉ]"
    caps = mine["metrics"].get("total_capacity_mbps")
    pot = mine["metrics"].get("link_potential_pct")
    bits = [f"{mine['device_type']}", mine["status"]]
    if caps is not None:
        bits.append(f"{caps:.0f} Mb/s")
    if pot is not None:
        bits.append(f"potentiel {pot:.0f}%")
    return f"{end['name']} ({', '.join(bits)})"


# ---------------------------------------------------------------------------
# Pilotage
# ---------------------------------------------------------------------------


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        help="Site racine du parcours (défaut : le site de plus haut degré, "
             "annoncé dans la sortie). Le lien Internet→HQ n'est pas un "
             "data-link : la racine ne peut pas être déduite du contrôleur.",
    )
    parser.add_argument(
        "--include-client-sites", action="store_true",
        help="Ne pas exclure les sites d'abonnés (débogage — attendez-vous à "
             "des milliers d'arêtes AP↔station).",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.uisp_base_url:
        print("UISP_BASE_URL non configuré — rien à interroger.")
        return 1

    client = _client()
    raw_devices = await client.fetch_devices()
    raw_sites = await client.fetch_sites()
    links = await client.fetch_data_links()

    devices = _device_index(raw_devices)
    infra_sites = {
        str((s.get("identification") or {}).get("id")): (
            (s.get("identification") or {}).get("name") or ""
        ).strip()
        for s in raw_sites
        if not _is_client_site(s) and (s.get("identification") or {}).get("id")
    }
    infra_site_ids = set(infra_sites)

    edges, skipped = build_edges(links, devices, infra_site_ids, args.include_client_sites)

    macs = {e[side]["mac"] for e in edges for side in ("device_a", "device_b") if e[side]["mac"]}
    async with async_session_factory() as session:
        ours = await supervised_by_mac(session, macs)

    # Graphe non orienté site↔site.
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        adjacency[edge["site_a"]].add(edge["site_b"])
        adjacency[edge["site_b"]].add(edge["site_a"])
    linked_sites = set(adjacency)
    all_infra_names = {n for n in infra_sites.values() if n}
    orphans = sorted(all_infra_names - linked_sites)

    root = args.root
    root_auto = False
    if root not in adjacency:
        if root:
            print(f"⚠ racine « {root} » absente du graphe — repli sur le degré maximal.\n")
        root_auto = True
        root = max(adjacency, key=lambda s: (len(adjacency[s]), s)) if adjacency else None

    groups = components(linked_sites, adjacency)
    depth, parent = ({}, {}) if root is None else layered_tree(linked_sites, adjacency, root)

    # Arêtes surnuméraires = celles qui ne sont pas une arête d'arbre du parcours.
    # Leur présence signifie « le graphe n'est pas un arbre » — un rendu en arbre
    # devrait en cacher une.
    tree_pairs = {frozenset((child, par)) for child, par in parent.items() if par}
    # Restreint aux sites ATTEINTS depuis la racine : une arête d'une composante
    # séparée n'est pas une boucle, elle est déjà signalée comme composante — la
    # compter ici la ferait apparaître deux fois sous deux diagnostics contraires.
    extra = [
        e for e in edges
        if e["site_a"] in depth and e["site_b"] in depth
        and frozenset((e["site_a"], e["site_b"])) not in tree_pairs
    ]
    seen_pairs: set[frozenset] = set()
    duplicates = []
    for edge in edges:
        pair = frozenset((edge["site_a"], edge["site_b"]))
        if pair in seen_pairs:
            duplicates.append(edge)
        seen_pairs.add(pair)

    if args.json:
        print(json.dumps(
            {
                "root": root,
                "root_auto_detected": root_auto,
                "infra_sites": sorted(all_infra_names),
                "orphan_sites": orphans,
                "components": [sorted(g) for g in groups],
                "edges": [
                    {
                        "type": e["type"],
                        "state": e["state"],
                        "site_a": e["site_a"],
                        "site_b": e["site_b"],
                        "device_a": e["device_a"]["name"],
                        "device_b": e["device_b"]["name"],
                        "mac_a": e["device_a"]["mac"],
                        "mac_b": e["device_b"]["mac"],
                        "supervised_a": e["device_a"]["mac"] in ours,
                        "supervised_b": e["device_b"]["mac"] in ours,
                        "ours_a": ours.get(e["device_a"]["mac"] or ""),
                        "ours_b": ours.get(e["device_b"]["mac"] or ""),
                    }
                    for e in edges
                ],
                "depth": depth,
                "parent": parent,
                "skipped_links": skipped,
            },
            indent=2, ensure_ascii=False, default=str,
        ))
        return 0

    # ── Rapport texte ────────────────────────────────────────────────────────
    print("=" * 74)
    print("GRAPHE INTER-SITES — source : GET /nms/api/v2.1/data-links (lecture seule)")
    print("=" * 74)
    print(f"{len(raw_devices)} équipements · {len(raw_sites)} sites "
          f"(dont {len(infra_sites)} d'infra) · {len(links)} data-links")
    by_type: dict[str, int] = defaultdict(int)
    for link in links:
        by_type[(link.get("type") or "?").lower()] += 1
    print("  types de liens : " + ", ".join(f"{t}={n}" for t, n in sorted(by_type.items())))
    print(f"  → {len(edges)} arête(s) inter-sites retenue(s)")
    for reason, count in sorted(skipped.items(), key=lambda kv: -kv[1]):
        print(f"     écarté : {reason} ×{count}")

    print(f"\n--- ARÊTES ({len(edges)}) " + "-" * 50)
    if not edges:
        print("  AUCUNE. Le contrôleur ne provisionne aucun lien entre deux sites")
        print("  d'infra — une carte inter-sites n'est pas constructible sur cette")
        print("  source, il faudra la saisir ou la déduire autrement.")
    for edge in sorted(edges, key=lambda e: (e["site_a"], e["site_b"])):
        flag = "" if edge["state"] == "active" else f"  [state={edge['state']}]"
        print(f"\n  {edge['site_a']}  ↔  {edge['site_b']}   ({edge['type']}){flag}")
        print(f"      {_end_label(edge['device_a'], ours)}")
        print(f"      {_end_label(edge['device_b'], ours)}")

    print("\n--- GRAPHE EN COUCHES " + "-" * 51)
    if root is None:
        print("  pas de racine (graphe vide).")
    else:
        origin = "degré maximal" if root_auto else "--root"
        print(f"  racine : {root}  ({origin})\n")
        children: dict[str, list[str]] = defaultdict(list)
        for site, par in parent.items():
            if par:
                children[par].append(site)
        _print_branch(root, children, adjacency)
        unreached = sorted(linked_sites - set(depth))
        if unreached:
            print(f"\n  {len(unreached)} site(s) reliés mais HORS de l'arbre de la "
                  f"racine : {', '.join(unreached)}")

    print("\n--- CE QUI CLOCHE " + "-" * 55)
    problems = 0
    if orphans:
        problems += 1
        print(f"  {len(orphans)} site(s) d'infra sans AUCUN lien — ils seraient")
        print("  dessinés flottants, ce qui se lit comme « pas de panne » :")
        for site in orphans:
            print(f"      {site}")
    if len(groups) > 1:
        problems += 1
        print(f"  {len(groups)} composantes séparées — le graphe ne se dessine pas d'un")
        print("  seul tenant ; seule celle de la racine serait rattachée :")
        for group in sorted(groups, key=len, reverse=True):
            mark = " (racine)" if root in group else ""
            print(f"      {len(group)} site(s){mark} : {', '.join(sorted(group))}")
    if extra:
        problems += 1
        print(f"  {len(extra)} arête(s) hors arbre — le graphe N'EST PAS un arbre.")
        print("  Un rendu arborescent en cacherait ; le layout doit rester en couches :")
        for edge in extra:
            print(f"      {edge['site_a']} ↔ {edge['site_b']} ({edge['type']})")
    if duplicates:
        problems += 1
        print(f"  {len(duplicates)} lien(s) redondant(s) entre deux mêmes sites "
              "(plusieurs radios sur la même liaison) — à agréger en une arête.")
    unsupervised = sorted({
        edge[side]["name"]
        for edge in edges for side in ("device_a", "device_b")
        if (edge[side]["mac"] or "") not in ours
    })
    if unsupervised:
        problems += 1
        print(f"  {len(unsupervised)} bout(s) d'arête non supervisé(s) chez nous — ni")
        print("  statut, ni mesure, donc une arête non colorable de ce côté :")
        for name in unsupervised:
            print(f"      {name}")
    if not problems:
        print("  rien : graphe connexe, tous les sites reliés, tous les bouts supervisés.")

    print("\n(lecture seule — aucune écriture en base ni sur le contrôleur)")
    return 0


def _print_branch(
    site: str,
    children: dict[str, list[str]],
    adjacency: dict[str, set[str]],
    prefix: str = "",
    last: bool = True,
    is_root: bool = True,
) -> None:
    """Rendu arborescent ASCII d'une branche du parcours en largeur.

    ``is_root`` est explicite et non déduit d'un préfixe vide : les enfants de la
    racine ont eux aussi un préfixe vide, et le dernier d'entre eux perdait son
    connecteur — il s'affichait détaché, comme s'il n'était rattaché à rien.
    """
    degree = len(adjacency.get(site, ()))
    plural = "s" if degree > 1 else ""
    connector = "" if is_root else ("└── " if last else "├── ")
    print(f"  {prefix}{connector}{site}  ({degree} lien{plural})")
    kids = sorted(children.get(site, []))
    extension = "" if is_root else ("    " if last else "│   ")
    for index, kid in enumerate(kids):
        _print_branch(
            kid, children, adjacency,
            prefix + extension, index == len(kids) - 1, is_root=False,
        )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
