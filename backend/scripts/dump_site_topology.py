r"""
Montre le GRAPHE INTER-SITES du réseau (le squelette de la carte UISP), en texte.

À quoi ça sert
--------------
Le maillage site→site (HQ → ARF1/AT1/CT1… → PK1/TS1…) n'est stocké nulle part
chez nous : on connaît le `site` de chaque AF60 et PTP LiteBeam, jamais quel AF60
parle à quel AF60. Le contrôleur le sait et le publie sur
`GET /nms/api/v2.1/data-links`.

Un rendu graphique dessine toujours quelque chose, même sur un graphe troué : des
sites orphelins passeraient pour des sites sans panne, et une boucle de
redondance serait rendue comme un arbre. Ce script imprime le même graphe que la
page `/topology`, en nommant **ce qui cloche** — pour diagnostiquer sans passer
par le navigateur, et pour vérifier la donnée après une intervention terrain.

Toute la logique vit dans `app/services/site_topology_service.py` : l'API et ce
script appliquent EXACTEMENT la même règle (un service ne peut pas importer
depuis `scripts/`). Ici il ne reste que le pilotage et l'affichage.

Lecture seule par construction : pas de `--apply`, aucune écriture en base ni sur
le contrôleur.

Usage :

    dc exec backend python scripts/dump_site_topology.py
    dc exec backend python scripts/dump_site_topology.py --root "A2 HQ"
    dc exec backend python scripts/dump_site_topology.py --json > topo.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import async_session_factory  # noqa: E402
from app.services import site_topology_service  # noqa: E402


def _end_label(end: dict) -> str:
    """Décrire un bout d'arête : nom, puis ce que NOUS en savons (ou rien)."""
    if not end["supervised"]:
        return f"{end['uisp_name']} [NON SUPERVISÉ]"
    bits = [end["device_type"] or "?", end["status"] or "?"]
    if end["capacity_mbps"] is not None:
        bits.append(f"{end['capacity_mbps']:.0f} Mb/s")
    if end["link_potential_pct"] is not None:
        bits.append(f"potentiel {end['link_potential_pct']:.0f}%")
    return f"{end['name']} ({', '.join(bits)})"


def _print_branch(
    site: str,
    children: dict[str, list[str]],
    degrees: dict[str, int],
    prefix: str = "",
    last: bool = True,
    is_root: bool = True,
) -> None:
    """Rendu arborescent ASCII d'une branche du parcours en largeur.

    ``is_root`` est explicite et non déduit d'un préfixe vide : les enfants de la
    racine ont eux aussi un préfixe vide, et le dernier d'entre eux perdait son
    connecteur — il s'affichait détaché, comme rattaché à rien.
    """
    degree = degrees.get(site, 0)
    plural = "s" if degree > 1 else ""
    connector = "" if is_root else ("└── " if last else "├── ")
    print(f"  {prefix}{connector}{site}  ({degree} lien{plural})")
    kids = sorted(children.get(site, []))
    extension = "" if is_root else ("    " if last else "│   ")
    for index, kid in enumerate(kids):
        _print_branch(
            kid, children, degrees,
            prefix + extension, index == len(kids) - 1, is_root=False,
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        help="Site racine du parcours (défaut : TOPOLOGY_ROOT_SITE, puis repli "
             "sur le site de plus haut degré — le lien Internet→HQ n'étant pas "
             "un data-link, la racine ne se déduit pas du contrôleur).",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    args = parser.parse_args()

    async with async_session_factory() as session:
        topo = await site_topology_service.get_site_topology(session, root=args.root)

    if not topo["available"]:
        print(f"Topologie indisponible : {topo['reason']}")
        return 1

    if args.json:
        print(json.dumps(topo, indent=2, ensure_ascii=False, default=str))
        return 0

    stats, layout = topo["stats"], topo["layout"]

    print("=" * 74)
    print("GRAPHE INTER-SITES — source : GET /nms/api/v2.1/data-links (lecture seule)")
    print("=" * 74)
    print(f"{stats['uisp_devices']} équipements · {stats['uisp_sites']} sites "
          f"(dont {stats['infra_sites']} d'infra) · {stats['data_links']} data-links")
    print(f"  → {stats['edges']} liaison(s) inter-sites "
          f"({stats['physical_links']} lien(s) physique(s))")
    for reason, count in sorted(stats["skipped_links"].items(), key=lambda kv: -kv[1]):
        print(f"     écarté : {reason} ×{count}")

    print(f"\n--- LIAISONS ({stats['edges']}) " + "-" * 48)
    if not topo["edges"]:
        print("  AUCUNE. Le contrôleur ne provisionne aucun lien entre deux sites")
        print("  d'infra — une carte inter-sites n'est pas constructible ainsi.")
    for edge in topo["edges"]:
        health = edge["health"]
        marks = []
        if edge["redundant"]:
            marks.append(f"REDONDANTE ×{len(edge['links'])}")
        if not edge["is_tree_edge"]:
            marks.append("hors arbre")
        if health["state"] == "unmeasured":
            marks.append("NON MESURÉE")
        if health["state"] == "down":
            marks.append("DOWN")
        suffix = f"   [{' · '.join(marks)}]" if marks else ""
        summary = ""
        if health["capacity_mbps"] is not None:
            summary = f"  {health['capacity_mbps']:.0f} Mb/s"
            if health["link_potential_pct"] is not None:
                summary += f", potentiel {health['link_potential_pct']:.0f}%"
        print(f"\n  {edge['site_a']}  ↔  {edge['site_b']}{summary}{suffix}")
        for link in edge["links"]:
            state = "" if link["state"] == "active" else f"  [state={link['state']}]"
            print(f"      ({link['type']}){state}")
            print(f"        {_end_label(link['device_a'])}")
            print(f"        {_end_label(link['device_b'])}")

    print("\n--- GRAPHE EN COUCHES " + "-" * 51)
    if topo["root"] is None:
        print("  pas de racine (graphe vide).")
    else:
        print(f"  racine : {topo['root']}  ({topo['root_source']})\n")
        children: dict[str, list[str]] = defaultdict(list)
        degrees = {s["site"]: s["degree"] for s in topo["sites"]}
        for site in topo["sites"]:
            if site["parent"]:
                children[site["parent"]].append(site["site"])
        _print_branch(topo["root"], children, degrees)

    print("\n--- CE QUI CLOCHE " + "-" * 55)
    problems = 0
    if layout["orphan_sites"]:
        problems += 1
        print(f"  {len(layout['orphan_sites'])} site(s) d'infra sans AUCUNE liaison —")
        print("  ils seraient dessinés flottants, ce qui se lit comme « pas de panne » :")
        for site in layout["orphan_sites"]:
            print(f"      {site}")
    if len(layout["components"]) > 1:
        problems += 1
        print(f"  {len(layout['components'])} composantes séparées — le graphe ne se")
        print("  dessine pas d'un seul tenant :")
        for group in sorted(layout["components"], key=len, reverse=True):
            mark = " (racine)" if topo["root"] in group else ""
            print(f"      {len(group)} site(s){mark} : {', '.join(group)}")
    if layout["extra_edges"]:
        problems += 1
        print(f"  {len(layout['extra_edges'])} liaison(s) hors arbre — le graphe N'EST")
        print("  PAS un arbre. Un rendu arborescent en cacherait ; layout en couches :")
        for edge in layout["extra_edges"]:
            print(f"      {edge['site_a']} ↔ {edge['site_b']} ({edge['type']})")
    unmeasured = [e for e in topo["edges"] if e["health"]["state"] == "unmeasured"]
    if unmeasured:
        problems += 1
        print(f"  {len(unmeasured)} liaison(s) sans AUCUNE mesure de notre côté — elles")
        print("  seront rendues neutres, jamais vertes (un lien non mesuré n'est pas")
        print("  un lien sain) :")
        for edge in unmeasured:
            print(f"      {edge['site_a']} ↔ {edge['site_b']}")
    if stats["unsupervised_ends"]:
        problems += 1
        print(f"  {len(stats['unsupervised_ends'])} bout(s) de liaison non supervisé(s) —")
        print("  ni statut ni mesure de ce côté :")
        for name in stats["unsupervised_ends"]:
            print(f"      {name}")
    if not problems:
        print("  rien : graphe connexe, tous les sites reliés et mesurés.")

    print("\n(lecture seule — aucune écriture en base ni sur le contrôleur)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
