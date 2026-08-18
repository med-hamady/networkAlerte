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

Par défaut le script **ne contacte pas le contrôleur** : il lit le câblage déjà
rapatrié en base par le job quotidien. `--sync` force le rapatriement d'abord —
utile juste après une intervention terrain.

Usage :

    dc exec backend python scripts/dump_site_topology.py
    dc exec backend python scripts/dump_site_topology.py --sync
    dc exec backend python scripts/dump_site_topology.py --root "A2 HQ"
    dc exec backend python scripts/dump_site_topology.py --site "A2 CT2"
    dc exec backend python scripts/dump_site_topology.py --json > topo.json

⚠️ Les bornes de l'énumération des routes ne sont PAS réglables en ligne de
commande : le script doit appliquer exactement les mêmes que l'API, sinon les
deux se contrediraient — c'est toute la raison du « la logique vit dans le
service ».
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


def _route_line(route: dict) -> str:
    """Une route en une ligne : la chaîne, le goulot, et ce qu'on n'a pas mesuré."""
    chain = " → ".join(route["sites"])
    bits = [f"{route['hop_count']} saut(s)"]

    bottleneck = route["bottleneck"]
    if bottleneck is not None:
        bits.append(
            f"goulot {bottleneck['site_a']}↔{bottleneck['site_b']} "
            f"{bottleneck['occupancy_pct']:.0f} %"
        )
    if route["min_capacity_mbps"] is not None:
        bits.append(f"capacité min {route['min_capacity_mbps']:.0f} Mb/s")

    marks = []
    if route["is_best"]:
        # La couverture est dite AVEC le badge : « meilleure » sur 2 sauts
        # mesurés sur 3 n'est pas la même affirmation que sur 3 sur 3.
        marks.append(
            "MEILLEURE" if route["coverage"] == "full"
            else f"MEILLEURE · {route['measured_hops']}/{route['hop_count']} mesurés"
        )
    if not route["usable"]:
        cut = ", ".join(f"{h['site_a']}↔{h['site_b']}" for h in route["down_hops"])
        marks.append(f"COUPÉE : {cut} down")
    if route["coverage"] == "none":
        marks.append("NON MESURÉE")
    if route["degraded_hops"]:
        marks.append(f"{len(route['degraded_hops'])} saut(s) dégradé(s)")

    suffix = f"   [{' · '.join(marks)}]" if marks else ""
    return f"{chain}   ({' · '.join(bits)}){suffix}"


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
    parser.add_argument(
        "--sync", action="store_true",
        help="Rapatrie d'abord le câblage depuis le contrôleur (ce que fait le "
             "job quotidien). Sans ce drapeau, on lit la base — aucun appel UISP.",
    )
    parser.add_argument(
        "--site",
        help="Ne détailler les routes vers Internet que pour ce site "
             "(nom EXACT, double espace compris : « A2  ARF1 »).",
    )
    parser.add_argument("--json", action="store_true", help="Sortie JSON brute.")
    args = parser.parse_args()

    async with async_session_factory() as session:
        if args.sync:
            result = await site_topology_service.sync_site_links(session)
            if result.get("ok"):
                await session.commit()
                print(f"Câblage rapatrié : {result['physical_links']} lien(s) "
                      f"physique(s) sur {result['edges']} liaison(s).\n")
            else:
                print(f"Sync non appliqué : {result['reason']}\n")
        topo = await site_topology_service.get_site_topology(session, root=args.root)

    if not topo["available"]:
        print(f"Topologie indisponible : {topo['reason']}")
        return 1

    if args.json:
        print(json.dumps(topo, indent=2, ensure_ascii=False, default=str))
        return 0

    stats, layout = topo["stats"], topo["layout"]

    print("=" * 74)
    print("GRAPHE INTER-SITES — câblage en base (sync quotidien des data-links UISP)")
    print("=" * 74)
    print(f"câblage rapatrié le {topo['synced_at']}  "
          f"(la santé affichée, elle, est de maintenant)")
    print(f"{stats['infra_sites']} sites d'infra · {stats['edges']} liaison(s) "
          f"({stats['physical_links']} lien(s) physique(s))")

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

    routes = topo.get("routes") or {}
    print("\n--- ROUTES VERS LA RACINE " + "-" * 47)
    print("  ⚠️ Chemins permis par le CÂBLAGE. Ni OSPF ni la table de routage ne")
    print("     sont lus : rien ici n'affirme par où le trafic passe réellement.\n")
    shown = (
        [args.site] if args.site
        else [s for s in sorted(routes) if s != topo["root"]]
    )
    if args.site and args.site not in routes:
        print(f"  site inconnu : « {args.site} » (nom exact attendu)")
    for site in shown:
        group = routes.get(site)
        if group is None:
            continue
        if group["reason"]:
            print(f"  {site} : {group['reason']}")
            continue
        print(f"  {site}  —  {group['found']} chemin(s) trouvé(s), "
              f"{group['kept']} affiché(s)")
        if group["best_reason"]:
            print(f"      (pas de meilleure route : {group['best_reason']})")
        for route in group["paths"]:
            print(f"      {_route_line(route)}")
        if group["truncated"]["by_hops"] or group["truncated"]["by_budget"]:
            limit = "longueur max" if group["truncated"]["by_hops"] else "budget"
            print(f"      ⚠️ énumération tronquée ({limit}) — la liste n'est pas complète")
        print()

    print("\n--- CE QUI CLOCHE " + "-" * 55)
    problems = 0
    real = {s: g for s, g in routes.items() if not g["reason"]}
    single = sorted(s for s, g in real.items() if g["found"] == 1)
    if single:
        problems += 1
        print(f"  {len(single)} site(s) n'ont qu'UNE seule route vers la racine —")
        print("  aucune redondance : la liaison coupée, le site tombe :")
        for site in single:
            print(f"      {site}")
    saturated = sorted(
        site for site, group in real.items()
        for best in [next((p for p in group["paths"] if p["is_best"]), None)]
        if best is not None and any(h["saturated"] for h in best["hops"])
    )
    if saturated:
        problems += 1
        print(f"  {len(saturated)} site(s) dont la MEILLEURE route est déjà saturée —")
        print("  leur chemin le plus dégagé est au bout du souffle :")
        for site in saturated:
            print(f"      {site}")
    blind = sorted(s for s, g in real.items() if g["best_reason"])
    if blind:
        problems += 1
        print(f"  {len(blind)} site(s) sans meilleure route désignable —")
        print("  on ne recommande pas un chemin dont on ne sait rien :")
        for site in blind:
            print(f"      {site} : {real[site]['best_reason']}")
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

    if args.sync:
        print("\n(câblage rapatrié et écrit ; la santé vient de nos polls)")
    else:
        print("\n(lecture de la base — aucun appel au contrôleur ; --sync pour rapatrier)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
