r"""Carte du PROJET de boucle fibre optique — rendu image (planification papier).

À quoi ça sert
--------------
Deux boucles de fibre sont à l'étude :

    Boucle 1 : HQ → ARF1 → PK1 → SK1 → CT1 → HQ
    Boucle 2 : HQ → ARF1 → TS1 → DN1 → AT1 → HQ

Ce script en dessine le plan sur le fond satellite de Nouakchott, aux vraies
positions des pylônes. Il sert à discuter le tracé sur papier — il ne touche à
RIEN : ni base, ni contrôleur, ni supervision. Aucun code de l'application ne
l'importe.

⚠️ Ce que la carte affirme, et ce qu'elle n'affirme pas
-------------------------------------------------------
* Les **positions** des sites et le **réseau radio actuel** sont RÉELS : ils
  sortent d'un export de `/network-topology`, donc de la base de prod.
* Les **boucles** sont un PROJET. Elles n'existent nulle part en base — c'est
  leur état normal, elles ne sont pas construites. Elles vivent dans la
  constante `_RINGS` ci-dessous, et le bandeau du haut dit « PROJET » pour
  qu'aucune impression de cette carte ne puisse se lire comme l'état du réseau.
  Même règle que les sites programmés de `site_map_service` : une intention ne
  s'écrit jamais en base, sinon elle serait pinguée et alerterait.

⚠️ Trois couleurs, parce que les 9 segments ne coûtent PAS la même chose
------------------------------------------------------------------------
C'est l'information qu'on vient chercher quand on prépare le chantier :

* **Bleu** — fibre DÉJÀ posée et en service, réutilisée telle quelle.
* **Violet** — un backhaul RADIO existe déjà sur ce tracé : le chemin est
  connu et éprouvé, c'est la fibre qui reste à tirer.
* **Magenta** — segment NEUF : aucune liaison aujourd'hui entre ces deux
  sites, donc tracé entièrement à étudier. Ce sont les deux segments qui
  FERMENT les boucles, et les seuls dont le passage n'est pas déjà validé par
  une liaison qui fonctionne.

⚠️ Le statut d'un segment est DÉDUIT de l'existant relu dans l'export, jamais
écrit à la main : le jour où un backhaul est posé sur un de ces tracés, la
carte le reclasse toute seule au prochain export.

Le reste du réseau radio est dessiné en vert tireté, plus fin que la fibre :
il situe les boucles dans le parc sans leur disputer la lecture.

⚠️ L'export de topologie N'EST PAS versionné
---------------------------------------------
C'est une extraction de production (MAC des équipements, coordonnées des
pylônes) : elle n'a pas sa place dans le dépôt. Il faut donc la produire avant
le premier rendu :

    dc exec backend python scripts/dump_site_topology.py --json > topo-prod.json

Les cartes déjà rendues, elles, sont committées : le document circule sans
qu'on ait à rejouer l'export.

Usage
-----
    python scripts/render_fiber_ring_plan.py                  # mode plan
    python scripts/render_fiber_ring_plan.py --mode final     # architecture cible
    python scripts/render_fiber_ring_plan.py --lang en
    python scripts/render_fiber_ring_plan.py --topo autre.json --out plan.jpg
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app.services.site_map_service import (  # noqa: E402
    _ASSETS,
    _INK,
    _LABEL_BORDER,
    _PLANNED,
    _SS,
    _WHITE,
    _attribution,
    _compass,
    _font,
    _inside,
    _label_box,
    _load_bounds,
    _measure_label,
    _overlap,
    _pin,
    _project,
    _segment_samples,
    _source_ring,
)

# ------------------------------------------------------------------ le projet
# Les boucles telles qu'elles ont été décidées. Écrites comme des CHEMINS et pas
# comme des listes de segments : c'est la forme dans laquelle on les énonce, et
# la seule qui rende une erreur de saisie visible à l'œil nu.
_RINGS: tuple[tuple[str, list[str]], ...] = (
    ("Boucle 1", ["A2 HQ", "A2 ARF1", "A2 PK1", "A2 SK1", "A2 CT1", "A2 HQ"]),
    ("Boucle 2", ["A2 HQ", "A2 ARF1", "A2 TS1", "A2 DN1", "A2 AT1", "A2 HQ"]),
)

# ------------------------------------------------------- sites PROGRAMMÉS
# Les extensions de Nouakchott (`NKTT NEW 1` à côté d'AT2, `NKTT NEW 2` dans la
# zone laissée nue derrière VEL1) sont RÉUTILISÉES depuis `site_map_service`,
# jamais recopiées : c'est la même intention, et deux listes du même projet
# finiraient par se contredire — un site ajouté au plan mural n'apparaîtrait
# pas ici, ou pire, apparaîtrait ailleurs.
#
# Elles ne sont dans AUCUNE de nos tables — c'est leur état normal, elles ne
# sont pas construites — et ne doivent jamais y être écrites : elles seraient
# alors comptées dans la capacité du site, pinguées par `infra_ping_job`, et
# alerteraient en `device_unreachable` pour des mâts qui ne sont pas montés.
#
# ⚠️ AUCUNE LIAISON n'est tracée depuis ces sites, et ce n'est pas un oubli :
# la source déclare `edges: []` pour Nouakchott, parce que leur raccordement
# n'est pas arrêté. En inventer un ferait passer une hypothèse de câblage pour
# une décision.
_PLANNED_SITES: tuple[tuple[str, float, float], ...] = tuple(
    _PLANNED["nouakchott"]["sites"]
)

# ------------------------------------------------------- la source INTERNET
# ⚠️ Il n'y a qu'UNE source, et le secours porte sur la ROUTE qui y mène, pas
# sur la source. Aujourd'hui seul HQ l'atteint ; la seconde fibre partira de
# CT1 vers la MÊME source. Dessiner deux globes ferait croire à deux arrivées
# — donc à une redondance d'opérateur qui n'existe pas — alors que la panne
# qu'on couvre est la coupure d'un câble, pas la perte du fournisseur.
#
# L'amont est un GLOBE et non une pastille de site : ce n'est pas un de nos
# mâts, et la même icône le ferait compter comme tel.
#
# ⚠️ Ces coordonnées sont RELEVÉES, pas choisies : c'est l'emplacement réel du
# point de présence amont, fourni par l'exploitation. Elles ont remplacé une
# position de confort placée « pour que le dessin respire ».
#
# ⚠️ Conséquence assumée : la source est à ~660 m de CT1, donc le globe touche
# presque sa pastille et la fibre vers HQ passe au ras d'elle. C'est la
# réalité du terrain — et elle explique d'ailleurs pourquoi c'est CT1 qui
# porte la seconde route. On ne déplace PAS le globe pour aérer le dessin :
# une carte qui bouge un point pour être jolie ne peut plus servir à situer
# quoi que ce soit, et le KMZ exporterait alors une position fausse.
# `_check_feed_clearance` continue de signaler le frôlement — c'est un
# avertissement, pas une erreur à corriger en mentant.
_SOURCE_ICON: tuple[float, float] = (18.10505554748358, -16.017153176522687)
# ⚠️ Position d'AFFICHAGE, pour l'image seulement — jamais pour le KMZ.
# À ses vraies coordonnées, la source est à ~660 m de CT1 : sur une carte à
# l'échelle de la ville, le globe recouvre la pastille et la fibre de secours
# n'a plus la place d'exister (mesuré : 0 pixel tracé). Le globe est donc
# écarté du MINIMUM qui rende le trait visible — 811 m, trouvé en balayant le
# fond de carte sous les mêmes contraintes que le reste (terre ferme, hors
# cartouche, les deux routes dégagent les autres sites).
#
# ⚠️ Le KMZ, lui, exporte `_SOURCE_ICON` — la position RELEVÉE. C'est ce qui
# rend le décalage acceptable : il vit uniquement dans le dessin, là où il
# corrige une gêne d'échelle, et jamais dans la donnée qu'on réutilise pour
# situer quoi que ce soit sur le terrain.
_SOURCE_ICON_MAP: tuple[float, float] = (18.109475, -16.023246)
_SOURCE_ROUTE_MAIN = "A2 HQ"     # la route d'aujourd'hui
_SOURCE_ROUTE_NEW = "A2 CT1"     # la seconde, à construire

# ------------------------------------------------------------------- palette
# Identité A2 Connect pour le cadre et le texte ; couleurs FONCTIONNELLES
# choisies pour rester distinguables entre elles et sur de l'imagerie satellite
# (sable, toits gris, routes jaunes) — la marque tient l'identité, la sémantique
# tient la lecture.
_FIBER_EXISTING = (26, 95, 208)     # #1a5fd0 — bleu : fibre déjà en service
_FIBER_UPGRADE = (123, 47, 181)     # #7b2fb5 — violet : tracé radio à fibrer
_FIBER_NEW = (216, 27, 96)          # #d81b60 — magenta : segment neuf
_FIBER_BACKUP = (211, 47, 47)       # rouge : la 2e route vers la source
_PLANNED_PIN = (192, 39, 30)        # rouge brique : site programmé, pas encore posé
# ⚠️ Le radio est VERT dans TOUS les modes, et de la même épaisseur. Il a été
# gris pâle en mode plan, pour le mettre en retrait derrière les boucles :
# illisible sur du sable et des toits gris — un trait qu'on ne voit pas ne
# met rien en retrait, il retire l'information. La hiérarchie est portée par
# l'ÉPAISSEUR (fibre 13-15 px contre radio 6,5) et par le tireté, pas par un
# gris qui se fond dans le fond de carte.
_RADIO = (30, 107, 79)              # vert : backhaul radio
_IDLE_PIN = (129, 145, 134)
_RING_PIN = (30, 107, 79)           # vert : les sites de la boucle sont en service
_GOLD = (249, 181, 36)
_SAGE = (167, 185, 173)

_STATUS_ORDER = ("existing", "upgrade", "new")
_STATUS_COLOR = {
    "existing": _FIBER_EXISTING,
    "upgrade": _FIBER_UPGRADE,
    "new": _FIBER_NEW,
}

_TEXT = {
    "fr": {
        "banner": "PROJET — BOUCLE FIBRE OPTIQUE",
        "sub": "Nouakchott · préparation {year} · non déployé",
        "legend": "LÉGENDE",
        "existing": "Fibre existante, réutilisée",
        "upgrade": "Backhaul radio à remplacer par la fibre",
        "new": "Segment neuf — aucune liaison aujourd'hui",
        "idle": "Réseau radio actuel (inchangé)",
        "source": "Tête de réseau",
        "ring_pin": "Site sur la boucle",
        "other_pin": "Autre site du parc",
        "seg": "{n} segments",
        "seg1": "1 segment",
        "internet": "INTERNET",
        "feed": "Liaison vers la source Internet",
        "feed_backup": "2e liaison vers la source — à créer",
        "feed_backup_done": "2e liaison vers la source — secours",
        "planned_pin": "Site programmé — raccordement à définir",
        "rings": "BOUCLES",
        "banner_final": "ARCHITECTURE CIBLE — RÉSEAU APRÈS LES BOUCLES FIBRE",
        "sub_final": "Nouakchott · état visé une fois les boucles en service",
        "banner_current": "ARCHITECTURE ACTUELLE",
        "sub_current": "Nouakchott · réseau en service · relevé du {date}",
        "fibre_now": "Fibre optique",
        "radio_now": "Backhaul radio",
        "core_pin": "Site raccordé en fibre",
        "ring_fibre": "Boucle fibre optique — dorsale",
        "radio_kept": "Backhaul radio — collecte",
        "backbone_pin": "Site sur la dorsale fibre",
        "spur_pin": "Site raccordé en radio",
        "link": "{n} liaisons",
    },
    "en": {
        "banner": "PROJECT — FIBRE OPTIC RING",
        "sub": "Nouakchott · {year} planning · not deployed",
        "legend": "LEGEND",
        "existing": "Existing fibre, reused as is",
        "upgrade": "Radio backhaul to be replaced by fibre",
        "new": "New segment — no link today",
        "idle": "Current radio network (unchanged)",
        "source": "Network head-end",
        "ring_pin": "Site on the ring",
        "other_pin": "Other site",
        "seg": "{n} segments",
        "seg1": "1 segment",
        "internet": "INTERNET",
        "feed": "Link to the Internet source",
        "feed_backup": "2nd link to the source — to be built",
        "feed_backup_done": "2nd link to the source — backup",
        "planned_pin": "Planned site — uplink to be decided",
        "rings": "RINGS",
        "banner_final": "TARGET ARCHITECTURE — NETWORK AFTER THE FIBRE RINGS",
        "sub_final": "Nouakchott · state once the rings are in service",
        "banner_current": "CURRENT ARCHITECTURE",
        "sub_current": "Nouakchott · network in service · as of {date}",
        "fibre_now": "Fibre optic link",
        "radio_now": "Radio backhaul",
        "core_pin": "Site on fibre",
        "ring_fibre": "Fibre optic ring — backbone",
        "radio_kept": "Radio backhaul — collection",
        "backbone_pin": "Site on the fibre backbone",
        "spur_pin": "Site fed over radio",
        "link": "{n} links",
    },
}


def _norm(name: str) -> str:
    """Nom de site débarrassé de ses espaces multiples (« A2  ARF1 »).

    ⚠️ Uniquement pour APPARIER l'export avec `_RINGS` et pour l'affichage. La
    jointure côté application se fait sur la chaîne EXACTE — le double espace de
    « A2  ARF1 » est voulu en base et le normaliser là-bas ferait disparaître ce
    site.
    """
    return " ".join(str(name).split())


def _ring_segments(current: dict[frozenset, str]) -> dict[frozenset, dict]:
    """Les segments des boucles, chacun qualifié par ce qu'il reste à faire.

    Un segment porté par les DEUX boucles (HQ↔ARF1) n'apparaît qu'une fois :
    c'est un seul câble, le dessiner deux fois le ferait paraître doublé.
    """
    out: dict[frozenset, dict] = {}
    for ring_name, path in _RINGS:
        for a, b in zip(path, path[1:], strict=False):
            key = frozenset((a, b))
            medium = current.get(key)
            status = ("existing" if medium == "wired"
                      else "upgrade" if medium is not None
                      else "new")
            entry = out.setdefault(
                key, {"a": a, "b": b, "status": status, "rings": []})
            if ring_name not in entry["rings"]:
                entry["rings"].append(ring_name)
    return out


def _place(items, draw, canvas, segments):
    """Pose chaque nom autour de sa pastille — variante à rayon PAR SITE.

    Même notation que `site_map_service._place_labels` (recouvrement d'un autre
    nom > d'un trait de liaison > d'une pastille, les trois ramenés à la même
    échelle avant pondération), à ceci près que les sites hors boucle portent
    une pastille plus petite : leur nom doit pouvoir se serrer contre elle au
    lieu d'être repoussé du rayon des gros.
    """
    width, height = canvas
    pins = [(it["x"] - it["r"], it["y"] - it["r"],
             it["x"] + it["r"], it["y"] + it["r"] * 1.7) for it in items]
    samples = _segment_samples(segments, 12.0)
    placed: list[tuple] = []
    out = []

    # ⚠️ Les étiquettes les plus CONTRAINTES passent en premier, pas les plus
    # au nord. Un placement glouton donne la place à qui se sert d'abord : dans
    # l'ordre nord→sud, le petit « A2 NR1 » prenait le seul créneau libre, puis
    # « A2 HQ » — trois lignes, aucune position dégagée, cinq liaisons autour —
    # n'avait plus que des poses recouvrantes et se posait DESSUS. Résultat
    # mesuré : NR1 masqué à 100 %, donc un site absent de la carte sans que
    # rien ne le signale.
    #
    # En servant d'abord les gros pavés (les sites-sources et leurs légendes),
    # les petits noms gardent huit positions pour se glisser autour d'eux.
    # Tri STABLE : à contrainte égale l'ordre nord→sud d'origine est conservé,
    # donc deux exports des mêmes données rendent la même image.
    items = sorted(items, key=lambda it: (-len(it["rows"]), -it["r"]))

    for item in items:
        best, best_cost = None, None
        for side in ("e", "w", "s", "n", "ne", "nw", "se", "sw"):
            box = _label_box(item["x"], item["y"], item["r"], side,
                             item["w"], item["h"])
            area = max((box[2] - box[0]) * (box[3] - box[1]), 1.0)
            hits = sum(1 for px, py in samples
                       if box[0] <= px <= box[2] and box[1] <= py <= box[3])
            cost = (4.0 * sum(_overlap(box, other) for other in placed) / area
                    + 1.5 * sum(_overlap(box, pin) for pin in pins) / area
                    + 2.0 * min(1.0, hits / 8.0))
            if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
                cost += 1e6
            if best_cost is None or cost < best_cost:
                best, best_cost = box, cost
        placed.append(best)
        out.append({**item, "box": best})
    return out


def _label(draw, item, scale) -> None:
    box, rows, sizes = item["box"], item["rows"], item["sizes"]
    gap = sizes[0][1] * 0.42
    total = sum(h for _w, h in sizes) + gap * (len(rows) - 1)
    radius = min((box[3] - box[1]) * 0.30, sizes[0][1] * 0.8)
    draw.rounded_rectangle(box, radius=radius, fill=_WHITE,
                           outline=item.get("border", _LABEL_BORDER),
                           width=max(1, int(round(scale * item.get("bw", 1)))))
    cx = (box[0] + box[2]) / 2
    y = (box[1] + box[3]) / 2 - total / 2
    for (text, font, is_caption), (_w, h) in zip(rows, sizes, strict=True):
        draw.text((cx, y + h / 2), text, font=font,
                  fill=(176, 118, 8) if is_caption else item.get("fg", _INK),
                  anchor="mm")
        y += h + gap


def _fiber_line(draw, p1, p2, color, width, trim) -> None:
    """Segment de fibre : trait plein, posé sur un liseré blanc.

    Le liseré n'est pas décoratif — sur de l'imagerie satellite un trait de
    couleur seul se perd, et c'est précisément ce trait qu'on vient lire.
    """
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1) or 1.0
    # ⚠️ Le raccourci est PLAFONNÉ à 35 % de chaque côté. Sans ça, deux points
    # plus proches que 2×`trim` donnaient un segment de longueur négative : PIL
    # le trace à l'envers, un moignon posé de travers — ou rien du tout. Le cas
    # se produit pour de vrai (la source Internet est à 660 m de CT1), et il
    # doit rendre un trait court, pas un artefact.
    t = min(trim / dist, 0.35)
    a = (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
    b = (x2 - (x2 - x1) * t, y2 - (y2 - y1) * t)
    draw.line([a, b], fill=(255, 255, 255, 228), width=int(round(width * 1.75)))
    draw.line([a, b], fill=color, width=int(round(width)))


def _dashed(draw, p1, p2, color, width, trim) -> None:
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1) or 1.0
    t = trim / dist
    ax, ay = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
    bx, by = x2 - (x2 - x1) * t, y2 - (y2 - y1) * t
    total = math.hypot(bx - ax, by - ay)
    if total <= 0:
        return
    ux, uy = (bx - ax) / total, (by - ay) / total
    dash, gap, pos = width * 3.2, width * 2.6, 0.0
    while pos < total:
        end = min(pos + dash, total)
        draw.line([(ax + ux * pos, ay + uy * pos), (ax + ux * end, ay + uy * end)],
                  fill=color, width=int(round(width)))
        pos = end + gap


@dataclass(frozen=True)
class Layers:
    """Ce que la carte CONTIENT, avant tout choix de rendu.

    ⚠️ Produite par `_layers()` et consommée par le rendu IMAGE comme par
    l'export KMZ. C'est ce qui garantit que les deux ne peuvent pas diverger :
    un site programmé ajouté, une boucle modifiée, une règle de mode changée
    se répercutent sur les deux sorties sans qu'on y pense. Recopier ce calcul
    dans l'exportateur aurait produit deux cartes du même réseau qui se
    contredisent — le défaut le plus coûteux ici, parce qu'il ne se voit
    qu'en comparant les deux fichiers.
    """

    today: bool
    ring_segs: dict            # frozenset{a,b} -> {a, b, status, rings}
    ring_sites: set            # les sites portés par la fibre
    positions: dict            # nom -> (lat, lon), globe compris
    planned: set               # sites programmés (rouge)
    routes: list               # sites reliés à la source Internet
    icons: set                 # noms qui se dessinent en globe
    icon_label: str
    radio_edges: list          # [(a, b)] backhauls conservés
    wired_edges: list          # [(a, b)] fibre existante (mode `current`)
    missing: list              # ce qu'on n'a pas pu placer — jamais escamoté


def _layers(topo: dict, txt: dict, mode: str, bounds: dict) -> Layers:
    """Décide QUOI figure sur la carte ; ne décide rien de son apparence."""
    current: dict[frozenset, str] = {}
    for edge in topo.get("edges", []):
        a, b = _norm(edge.get("site_a", "")), _norm(edge.get("site_b", ""))
        current[frozenset((a, b))] = edge.get("medium")

    today = mode == "current"
    # ⚠️ En mode `current` il n'y a AUCUN segment de boucle : elle n'existe pas
    # encore. Le « cœur » du réseau y est simplement l'ensemble des sites déjà
    # raccordés en fibre — ce que la donnée dit, pas ce que le projet prévoit.
    ring_segs = {} if today else _ring_segments(current)
    if today:
        ring_sites = {s for pair, medium in current.items() if medium == "wired"
                      for s in pair}
    else:
        ring_sites = {s for seg in ring_segs.values()
                      for s in (seg["a"], seg["b"])}

    positions: dict[str, tuple[float, float]] = {}
    missing: list[str] = []
    for site in topo.get("sites", []):
        name = _norm(site.get("site", ""))
        lat, lon = site.get("latitude"), site.get("longitude")
        if lat is None or lon is None or not _inside(bounds, lat, lon):
            missing.append(name)
            continue
        positions[name] = (lat, lon)

    # Un site de la boucle qu'on ne sait pas placer est NOMMÉ, jamais escamoté :
    # une carte qui omet un site sans le dire se lit comme un réseau sans ce
    # site — et ici ce serait une boucle qui ne boucle pas.
    for seg in ring_segs.values():
        for site_name in (seg["a"], seg["b"]):
            if site_name not in positions and site_name not in missing:
                missing.append(site_name)

    # Les sites PROGRAMMÉS viennent après l'existant, jamais par-dessus lui : un
    # site déjà en base est déjà dessiné en vert, le redoubler en rouge le ferait
    # passer pour deux mâts. C'est ce qui rend le retrait de sa ligne de
    # `_PLANNED_SITES` facultatif le jour de sa mise en service.
    planned: set[str] = set()
    for name, lat, lon in () if today else _PLANNED_SITES:
        if name in positions:
            continue
        if not _inside(bounds, lat, lon):
            missing.append(f"{name} (hors cadre)")
            continue
        positions[name] = (lat, lon)
        planned.add(name)

    # Les ROUTES vers l'unique source : celle d'aujourd'hui toujours, la seconde
    # seulement sur les cartes de projet (elle n'existe pas encore).
    routes = [_SOURCE_ROUTE_MAIN] + ([] if today else [_SOURCE_ROUTE_NEW])
    routes = [r for r in routes if r in positions]

    icons: set[str] = set()
    icon_label = txt["internet"]
    if routes and _inside(bounds, *_SOURCE_ICON):
        positions[icon_label] = _SOURCE_ICON
        icons.add(icon_label)
    elif routes:
        missing.append(f"{icon_label} (hors cadre)")
        routes = []

    # ⚠️ Un tracé que la boucle reprend n'est JAMAIS conservé en radio : en mode
    # plan ce serait un doublon sous le trait de fibre, en mode cible ce serait
    # un mensonge — ce backhaul n'existera plus.
    radio_edges: list[tuple[str, str]] = []
    wired_edges: list[tuple[str, str]] = []
    for edge in topo.get("edges", []):
        a, b = _norm(edge.get("site_a", "")), _norm(edge.get("site_b", ""))
        if a not in positions or b not in positions or frozenset((a, b)) in ring_segs:
            continue
        if today and edge.get("medium") == "wired":
            wired_edges.append((a, b))
        else:
            radio_edges.append((a, b))

    return Layers(today, ring_segs, ring_sites, positions, planned, routes,
                  icons, icon_label, radio_edges, wired_edges, missing)


def _internet_icon(draw, cx, cy, r, scale) -> None:
    """Le globe de l'arrivée Internet.

    ⚠️ Volontairement AUTRE CHOSE qu'une pastille de site : c'est l'amont, pas
    un de nos mâts. Même forme que les pastilles et il serait compté comme un
    site de plus sur la carte comme dans la tête du lecteur.
    """
    ring = max(2.0, r * 0.14)
    draw.ellipse([cx - r - ring * 2, cy - r - ring * 2,
                  cx + r + ring * 2, cy + r + ring * 2],
                 fill=(255, 255, 255, 235))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_GOLD,
                 outline=_WHITE, width=int(round(ring)))

    lw = max(1, int(round(r * 0.10)))
    # Équateur + deux parallèles, chacun coupé à la largeur réelle du disque à
    # sa hauteur : des traits de longueur constante ne feraient pas une sphère.
    for frac in (0.0, 0.46, -0.46):
        dy = r * frac
        half = math.sqrt(max(r * r - dy * dy, 0.0)) * 0.82
        draw.line([(cx - half, cy + dy), (cx + half, cy + dy)],
                  fill=_WHITE, width=lw)
    # Méridiens : le contour du disque, puis une ellipse aplatie au centre.
    for half_w in (r * 0.82, r * 0.34):
        draw.ellipse([cx - half_w, cy - r * 0.82, cx + half_w, cy + r * 0.82],
                     outline=_WHITE, width=lw)


def _check_feed_clearance(anchors, icon_label, routes, r_other, scale) -> None:
    """Vérifie qu'aucune route vers la source ne passe SUR un autre site.

    ⚠️ Ce contrôle existe parce que le défaut s'est produit : le globe posé au
    nord-ouest de HQ mettait la fibre HQ↔source pile sur la pastille de NR1 et
    sur son backhaul — un site et une liaison effacés par un trait, sans que
    rien ne le signale. C'est le même échec que l'étiquette recouverte : la
    carte reste belle et ment.

    Ces routes sont les seules à traverser la carte de part en part (le globe
    est au large, ses sites sont au centre), donc les seules à pouvoir masquer
    un site qui n'a rien à voir avec elles. On journalise plutôt qu'on ne lève :
    une carte imparfaite reste plus utile qu'une absence de carte, mais le
    problème doit être DIT.
    """
    a = anchors.get(icon_label)
    if a is None:
        return
    # Demi-largeur du trait plus le rayon d'une petite pastille : en deçà, le
    # trait mord sur l'icône du site.
    clearance = 15.0 * scale / 2 + r_other + 4 * scale
    for site in routes:
        b = anchors.get(site)
        if b is None:
            continue
        for name, p in anchors.items():
            # ⚠️ On n'exclut que les DEUX extrémités de CETTE route. Exclure
            # tous les sites-routes était un trou : la fibre vers CT1 frôlait
            # alors la pastille de HQ sans que rien ne le dise, et se lisait
            # comme si elle s'y raccordait.
            if name in (icon_label, site):
                continue
            # Distance du point au SEGMENT (pas à la droite) : au-delà des
            # extrémités, la route ne passe pas là.
            vx, vy = b[0] - a[0], b[1] - a[1]
            wx, wy = p[0] - a[0], p[1] - a[1]
            span = vx * vx + vy * vy
            t = 0.0 if span == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / span))
            dist = math.hypot(a[0] + vx * t - p[0], a[1] + vy * t - p[1])
            if dist < clearance:
                print(f"  /!\\ la fibre {icon_label}-{site} passe sur « {name} » "
                      f"({dist:.0f} px, il en faut {clearance:.0f}) — "
                      f"deplacer _SOURCE_ICON", file=sys.stderr)


def _banner_height(draw, scale, txt) -> float:
    """Hauteur du bandeau, calculee a part : le cartouche des boucles se pose
    juste dessous, et le bandeau lui-meme est dessine EN DERNIER (il doit
    couvrir les etiquettes qui remonteraient trop haut)."""
    pad = 22 * scale
    _l, t1, _r, b1 = draw.textbbox((0, 0), txt["banner"], font=_font(int(round(46 * scale))))
    _l, t2, _r, b2 = draw.textbbox((0, 0), txt["sub"], font=_font(int(round(24 * scale)), bold=False))
    return (b1 - t1) + (b2 - t2) + pad * 2.6


def _banner(draw, bounds, scale, txt, year) -> None:
    """Bandeau du haut.

    Il porte le mot PROJET, et c'est sa raison d'être : imprimée puis détachée
    de tout contexte, cette carte ne doit pas pouvoir se lire comme l'état
    actuel du réseau.
    """
    w = bounds["w"] * _SS
    pad = 22 * scale
    f_title = _font(int(round(46 * scale)))
    f_sub = _font(int(round(24 * scale)), bold=False)
    _l, t1, _r, b1 = draw.textbbox((0, 0), txt["banner"], font=f_title)
    _l, t2, _r, b2 = draw.textbbox((0, 0), txt["sub"], font=f_sub)
    h = (b1 - t1) + (b2 - t2) + pad * 2.6
    draw.rectangle([0, 0, w, h], fill=(*_INK, 236))
    draw.rectangle([0, h - 5 * scale, w, h], fill=_GOLD)
    draw.text((pad * 1.5, pad), txt["banner"], font=f_title, fill=_WHITE)
    draw.text((pad * 1.5, pad + (b1 - t1) + pad * 0.5),
              txt["sub"], font=f_sub, fill=_SAGE)


def _ring_card(draw, bounds, scale, txt, top) -> None:
    """Rappel des deux boucles en toutes lettres, en haut à droite.

    La carte montre le tracé ; ce cartouche dit l'INTENTION dans les mots où
    elle a été décidée. Les deux se contrôlent l'un l'autre : une boucle mal
    saisie se voit en comparant le texte au dessin.
    """
    f_head = _font(int(round(21 * scale)))
    f = _font(int(round(24 * scale)))
    rows = [(name, " → ".join(s.replace("A2 ", "") for s in path))
            for name, path in _RINGS]

    pad = 20 * scale
    row_h = 40 * scale
    name_w = max(draw.textbbox((0, 0), n, font=f_head)[2] for n, _p in rows)
    path_w = max(draw.textbbox((0, 0), p, font=f)[2] for _n, p in rows)
    head_h = draw.textbbox((0, 0), txt["rings"], font=f_head)[3] + 12 * scale
    box_w = pad * 2 + name_w + 16 * scale + path_w
    box_h = pad * 2 + head_h + row_h * len(rows)

    # ⚠️ En haut à GAUCHE, au-dessus de l'océan. À droite, le cartouche
    # recouvrait la pastille de AT1 — c.-à-d. masquait un site de la boucle
    # derrière le texte qui décrit la boucle.
    x1 = 26 * scale + box_w
    y0 = top + 22 * scale
    draw.rounded_rectangle([x1 - box_w, y0, x1, y0 + box_h], radius=12 * scale,
                           fill=(255, 255, 255, 243), outline=_LABEL_BORDER,
                           width=max(1, int(round(2 * scale))))
    draw.text((x1 - box_w + pad, y0 + pad), txt["rings"], font=f_head,
              fill=(120, 134, 124))
    y = y0 + pad + head_h
    for name, path in rows:
        cy = y + row_h / 2
        draw.text((x1 - box_w + pad, cy), name, font=f_head,
                  fill=(120, 134, 124), anchor="lm")
        draw.text((x1 - box_w + pad + name_w + 16 * scale, cy), path, font=f,
                  fill=_INK, anchor="lm")
        y += row_h


def _legend(draw, bounds, scale, txt, rows) -> None:
    """Légende dessinée DANS l'image.

    Cette carte circule seule, sans document autour d'elle pour porter le code
    couleur — et le témoin montre la FORME réellement tracée (trait plein pour
    la fibre, tirets pour la radio), pas un aplat de couleur.

    ⚠️ Les lignes sont FOURNIES par l'appelant, jamais reconstruites ici : les
    deux vues ne montrent pas les mêmes objets, et une légende qui se
    devinerait toute seule finirait par annoncer une couleur que la vue
    courante ne trace pas.
    """
    f_head = _font(int(round(21 * scale)))
    f = _font(int(round(23 * scale)), bold=False)
    f_small = _font(int(round(19 * scale)), bold=False)

    pad = 20 * scale
    swatch = 62 * scale
    row_h = 36 * scale
    text_w = max(draw.textbbox((0, 0), r[2], font=f)[2] for r in rows)
    tail_w = max((draw.textbbox((0, 0), r[3], font=f_small)[2]
                  for r in rows if r[3]), default=0)
    box_w = (pad * 2 + swatch + 14 * scale + text_w
             + (tail_w + 18 * scale if tail_w else 0))
    head_h = draw.textbbox((0, 0), txt["legend"], font=f_head)[3] + 12 * scale
    box_h = pad * 2 + head_h + row_h * len(rows)

    x0 = 26 * scale
    y0 = bounds["h"] * _SS - box_h - 64 * scale
    draw.rounded_rectangle([x0, y0, x0 + box_w, y0 + box_h], radius=12 * scale,
                           fill=(255, 255, 255, 243), outline=_LABEL_BORDER,
                           width=max(1, int(round(2 * scale))))
    draw.text((x0 + pad, y0 + pad), txt["legend"], font=f_head,
              fill=(120, 134, 124))

    y = y0 + pad + head_h
    for kind, color, label, tail in rows:
        cy = y + row_h / 2
        sx = x0 + pad
        if kind == "line":
            draw.line([(sx, cy), (sx + swatch, cy)], fill=color,
                      width=int(round(9 * scale)))
        elif kind == "dash":
            _dashed(draw, (sx, cy), (sx + swatch, cy), color, 5 * scale, 0)
        elif kind == "pin":
            draw.ellipse([sx + swatch / 2 - 11 * scale, cy - 11 * scale,
                          sx + swatch / 2 + 11 * scale, cy + 11 * scale],
                         fill=color, outline=_WHITE, width=int(round(3 * scale)))
        else:
            draw.ellipse([sx + swatch / 2 - 13 * scale, cy - 13 * scale,
                          sx + swatch / 2 + 13 * scale, cy + 13 * scale],
                         outline=color, width=int(round(5 * scale)))
        draw.text((sx + swatch + 14 * scale, cy), label, font=f, fill=_INK,
                  anchor="lm")
        if tail:
            draw.text((x0 + box_w - pad, cy), tail, font=f_small,
                      fill=(130, 143, 133), anchor="rm")
        y += row_h


def render(topo: dict, lang: str, year: int, mode: str = "plan",
           ) -> tuple[bytes, list[str], dict]:
    """Rend la carte dans l'un des trois MODES.

    * `current` — l'architecture ACTUELLE : uniquement ce qui existe, relu
      dans l'export. Aucune boucle, aucun site programmé, aucun HQ de
      secours. Répond à « de quoi disposons-nous aujourd'hui ? ».

    ⚠️ `current` ne dessine RIEN de projeté, et c'est tout son intérêt : c'est
    la carte qu'on met en regard des deux autres pour voir ce qui change. Y
    laisser entrer un site programmé la rendrait impossible à opposer aux
    autres — on ne saurait plus ce qui existe.

    * `plan`  — le chantier : chaque segment coloré par ce qu'il reste à faire,
      le réseau radio d'aujourd'hui en gris derrière. Répond à « que
      construit-on ? ».
    * `final` — l'architecture CIBLE : les 9 segments ne sont plus qu'une seule
      dorsale fibre, les 4 backhauls radio qu'elle remplace ont DISPARU, et le
      radio restant se lit pour ce qu'il devient — le niveau de collecte.
      Répond à « à quoi ressemble le réseau une fois fini ? ».

    ⚠️ En mode `final`, un backhaul repris par la boucle n'est pas redessiné en
    radio : il n'existera plus. C'est toute la différence entre les deux vues —
    l'une ajoute la fibre au réseau d'aujourd'hui, l'autre montre celui de
    demain.
    """
    txt = dict(_TEXT[lang])
    if mode == "final":
        txt["banner"], txt["sub"] = txt["banner_final"], txt["sub_final"]
    elif mode == "current":
        txt["banner"], txt["sub"] = txt["banner_current"], txt["sub_current"]
    # La date du relevé vient de l'export lui-même : elle rend la mention
    # « actuelle » vérifiable au lieu d'être une affirmation du dessin.
    stamp = str(topo.get("synced_at") or "")[:10]
    txt["sub"] = txt["sub"].format(year=year, date=stamp or "?")
    bounds = _load_bounds()["nouakchott"]
    base = Image.open(_ASSETS / "nouakchott.jpg").convert("RGBA")

    layers = _layers(topo, txt, mode, bounds)
    today = layers.today
    ring_segs, ring_sites = layers.ring_segs, layers.ring_sites
    positions, planned = dict(layers.positions), layers.planned
    routes, icons, icon_label = layers.routes, layers.icons, layers.icon_label
    missing = list(layers.missing)

    # Le décalage d'affichage (cf. `_SOURCE_ICON_MAP`). `positions` est une
    # COPIE : `layers` garde la position relevée pour l'export KMZ.
    if icon_label in positions:
        positions[icon_label] = _SOURCE_ICON_MAP

    scale = _SS
    r_ring, r_other, r_icon = 32 * scale, 21 * scale, 25 * scale
    overlay = Image.new("RGBA", (bounds["w"] * _SS, bounds["h"] * _SS),
                        (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # La pastille est posée AU-DESSUS du point, sa pointe sur les coordonnées :
    # c'est la pointe qui désigne le lieu, pas le centre du disque.
    anchors: dict[str, tuple[float, float]] = {}
    for name, (lat, lon) in positions.items():
        x, y = _project(bounds, lat, lon)
        if name in icons:
            # Un globe est CENTRÉ sur son point ; une pastille est posée
            # au-dessus, sa pointe sur le point. Décaler le globe le ferait
            # flotter au-dessus de rien.
            anchors[name] = (x * _SS, y * _SS)
            continue
        r = r_ring if name in ring_sites or name in planned else r_other
        anchors[name] = (x * _SS, y * _SS - 1.25 * r)

    final = mode == "final"

    # 1) Le radio d'abord, sous la fibre.
    #    ⚠️ Un tracé que la boucle reprend n'est JAMAIS redessiné en radio : en
    #    mode plan ce serait un doublon sous le trait de fibre, en mode cible ce
    #    serait un mensonge — ce backhaul n'existera plus.
    radio: list[tuple] = []
    for a, b in layers.radio_edges:
        _dashed(draw, anchors[a], anchors[b],
                _RADIO, 6.5 * scale,
                (r_ring if a in ring_sites else r_other) + 6)
        radio.append((anchors[a], anchors[b]))

    # La fibre EXISTANTE du mode `current` : la seule vue où elle n'est ni un
    # projet ni une cible, juste un fait.
    wired: list[tuple] = [(anchors[a], anchors[b]) for a, b in layers.wired_edges]
    for pa, pb in wired:
        _fiber_line(draw, pa, pb, _FIBER_EXISTING, 15.0 * scale, r_ring + 10)

    # 2) La fibre par-dessus. En mode plan, dans l'ordre du reste-à-faire
    #    croissant : le magenta (segment neuf) est ce qu'on vient chercher, il
    #    passe donc en dernier, au-dessus de tout. En mode cible il n'y a plus
    #    qu'UNE fibre — la distinction de chantier n'a plus d'objet.
    segments = list(radio) + list(wired)
    counts = dict.fromkeys(_STATUS_ORDER, 0)
    counts["radio"] = len(radio)
    counts["wired"] = len(wired)
    for status in _STATUS_ORDER:
        for seg in ring_segs.values():
            if seg["status"] != status:
                continue
            counts[status] += 1
            a, b = seg["a"], seg["b"]
            if a not in anchors or b not in anchors:
                continue
            _fiber_line(draw, anchors[a], anchors[b],
                        _FIBER_EXISTING if final else _STATUS_COLOR[status],
                        (15.0 if final else 13.0) * scale, r_ring + 10)
            segments.append((anchors[a], anchors[b]))

    # Les sites de la boucle passent en dernier : leur pastille doit couvrir le
    # bout des traits, pas l'inverse. Ordre nord→sud figé à l'intérieur de
    # chaque groupe pour que deux exports rendent exactement la même image.
    # La fibre vers l'arrivée de SECOURS. Même trait que la dorsale — c'est la
    # même fibre — mais comptée à part : elle ne ferme aucune boucle, elle
    # amène une seconde source. En mode plan elle rejoint les « segments
    # neufs » (rien n'existe aujourd'hui sur ce tracé), en mode cible elle a sa
    # propre ligne de légende, la dorsale y étant comptée par boucle.
    # La fibre entre le globe et le site qu'il alimente.
    #
    # ⚠️ JAMAIS comptée dans les totaux de segments : ce n'est pas une de nos
    # liaisons inter-sites, c'est l'amont. La gonfler dans « fibre existante »
    # ou « segment neuf » fausserait le chiffre sur lequel on dimensionne le
    # chantier. Elle a sa propre ligne de légende.
    #
    # ⚠️ Une route dont les deux extrémités se CHEVAUCHENT ne produit aucun
    # trait : le globe et la pastille se recouvrent, il n'y a pas de place
    # entre eux. Le cas est réel — la source est à ~660 m de CT1. On le relève
    # pour que la légende n'annonce pas une couleur que le dessin ne porte pas.
    hidden_feeds: set[str] = set()
    for site in routes:
        gap = (math.dist(anchors[icon_label], anchors[site])
               - r_icon - (r_ring if site in ring_sites else r_other))
        if gap < 6 * scale:
            hidden_feeds.add(site)
            print(f"  [!] fibre {icon_label}-{site} INVISIBLE : les deux icones "
                  f"se chevauchent ({gap / scale:+.0f} px)", file=sys.stderr)

    for site in routes:
        # ⚠️ La 2e route est ROUGE dans TOUS les modes. Elle a été magenta
        # (« segment neuf ») sur la carte chantier : ça ne disait que son état
        # d'avancement, et devenait faux sur la carte cible où elle est en
        # service. Le rouge dit son RÔLE — c'est le chemin de secours — et ce
        # rôle ne change pas d'une carte à l'autre.
        color = (_FIBER_BACKUP if site == _SOURCE_ROUTE_NEW
                 else _FIBER_EXISTING)
        _fiber_line(draw, anchors[icon_label], anchors[site], color,
                    (15.0 if final else 13.0) * scale, r_ring + 10)
        segments.append((anchors[icon_label], anchors[site]))

    # ⚠️ Contrôle : une route vers la source ne doit RECOUVRIR aucun autre site.
    # Elle traverse la carte de part en part et masquait NR1 et son backhaul —
    # un site effacé par un trait est un site absent, sans que rien ne le dise.
    _check_feed_clearance(anchors, icon_label, routes, r_other, scale)

    # Les sites de la boucle et les programmes passent en dernier : leur
    # pastille doit couvrir le bout des traits, pas l'inverse.
    ordered = sorted(anchors,
                     key=lambda n: (n in ring_sites or n in planned,
                                    -positions[n][0]))
    for name in ordered:
        cx, cy = anchors[name]
        if name in icons:
            _internet_icon(draw, cx, cy, r_icon, scale)
            continue
        on_ring, is_planned = name in ring_sites, name in planned
        # L'anneau ne marque que la TÊTE DE RÉSEAU. CT1 aura une fibre vers
        # la source, mais ce n'est pas une seconde arrivée : le lui poser
        # ferait relire la carte comme deux sources.
        if name == _SOURCE_ROUTE_MAIN and routes:
            _source_ring(draw, cx, cy, r_ring, scale)
        _pin(draw, cx, cy,
             r_ring if on_ring or is_planned else r_other,
             _PLANNED_PIN if is_planned else (_RING_PIN if on_ring else _IDLE_PIN))

    f_ring = _font(int(round(35 * scale)))
    f_other = _font(int(round(25 * scale)), bold=False)
    f_cap = _font(int(round(20 * scale)))
    items = []
    for name in sorted(anchors, key=lambda n: -positions[n][0]):
        on_ring, is_planned = name in ring_sites, name in planned
        is_icon = name in icons
        big = on_ring or is_planned or is_icon
        # Aucune légende sous un nom de site : le globe et son trait disent
        # déjà d'où vient Internet, et le répéter en toutes lettres sur HQ
        # ajoutait un pavé de trois lignes au point le plus encombré de la
        # carte — c'est lui qui poussait « A2 NR1 » sous son voisin.
        caps = ()
        rows, sizes, w, h = _measure_label(
            draw, name, caps, f_ring if big else f_other, f_cap)
        cx, cy = anchors[name]
        # Le liseré rouge de l'étiquette rattache le nom à sa pastille : sans
        # lui, un site programmé se lit comme un site en service dont la
        # pastille aurait juste une autre couleur.
        items.append({
            "name": name, "x": cx, "y": cy,
            "r": r_icon if is_icon else (r_ring if big else r_other),
            "rows": rows, "sizes": sizes, "w": w, "h": h,
            "border": (_GOLD if is_icon else _PLANNED_PIN if is_planned
                       else _LABEL_BORDER if on_ring else (214, 221, 215)),
            "bw": 2 if big else 1,
            "fg": ((176, 118, 8) if is_icon else _PLANNED_PIN if is_planned
                   else _INK if on_ring else (110, 124, 114)),
        })

    canvas = (bounds["w"] * _SS, bounds["h"] * _SS)
    for item in _place(items, draw, canvas, segments):
        _label(draw, item, scale)

    # La légende décrit ce que CETTE vue trace, et rien d'autre.
    if today:
        rows = [
            ("line", _FIBER_EXISTING, txt["fibre_now"],
             txt["seg1"] if len(wired) == 1 else txt["seg"].format(n=len(wired))),
            ("dash", _RADIO, txt["radio_now"], txt["link"].format(n=len(radio))),
            ("pin", _RING_PIN, txt["core_pin"], ""),
            ("pin", _IDLE_PIN, txt["spur_pin"], ""),
            ("ring", _GOLD, txt["source"], ""),
        ]
    elif final:
        rows = [
            ("line", _FIBER_EXISTING, txt["ring_fibre"],
             txt["seg"].format(n=sum(counts[k] for k in _STATUS_ORDER))),
            ("dash", _RADIO, txt["radio_kept"],
             txt["link"].format(n=counts["radio"])),
            ("pin", _RING_PIN, txt["backbone_pin"], ""),
            ("pin", _IDLE_PIN, txt["spur_pin"], ""),
            ("ring", _GOLD, txt["source"], ""),
        ]
    else:
        rows = [
            ("line", _STATUS_COLOR[k], txt[k],
             txt["seg1"] if counts[k] == 1 else txt["seg"].format(n=counts[k]))
            for k in _STATUS_ORDER
        ] + [
            ("dash", _RADIO, txt["idle"], ""),
            ("pin", _RING_PIN, txt["ring_pin"], ""),
            ("pin", _IDLE_PIN, txt["other_pin"], ""),
            ("ring", _GOLD, txt["source"], ""),
        ]

    # ⚠️ Annoncé SEULEMENT si un site programmé est réellement dessiné : une
    # légende qui décrit une couleur absente du dessin fait chercher au lecteur
    # quelque chose qui n'y est pas.
    # L'arrivée Internet a sa propre ligne : elle est en fibre, mais elle n'est
    # pas une de nos liaisons inter-sites et n'entre dans aucun total.
    if icons:
        rows.insert(1, ("line", _FIBER_EXISTING, txt["feed"], ""))
        # Le libellé suit l'état, la COULEUR suit le rôle : « à créer » sur la
        # carte chantier, « secours » une fois en service, rouge dans les deux.
        # Annoncée seulement si elle est réellement tracée.
        if _SOURCE_ROUTE_NEW in routes and _SOURCE_ROUTE_NEW not in hidden_feeds:
            rows.insert(2, ("line", _FIBER_BACKUP,
                            txt["feed_backup_done"] if final else txt["feed_backup"],
                            ""))
    if planned:
        rows.insert(-1, ("pin", _PLANNED_PIN, txt["planned_pin"], ""))

    # ⚠️ Pas de cartouche des boucles en mode `current` : il décrit un projet,
    # et cette carte ne montre que l'existant.
    if not today:
        _ring_card(draw, bounds, scale, txt, _banner_height(draw, scale, txt))
    _banner(draw, bounds, scale, txt, year)
    _legend(draw, bounds, scale, txt, rows)
    _compass(draw, bounds, scale)
    _attribution(draw, bounds, scale)

    overlay = overlay.resize((bounds["w"], bounds["h"]), Image.LANCZOS)
    composed = Image.alpha_composite(base, overlay).convert("RGB")

    buf = io.BytesIO()
    composed.save(buf, format="JPEG", quality=92, optimize=True, progressive=True)
    return buf.getvalue(), missing, counts


# ------------------------------------------------------------------- KMZ
def _kml_color(rgb: tuple[int, int, int], alpha: int = 255) -> str:
    """RGB → la notation KML, qui est **aabbggrr** et non aarrggbb.

    ⚠️ L'ordre des octets est inversé par rapport à tout le reste du projet.
    Une couleur écrite naïvement en aarrggbb « marche » — elle produit juste
    une autre couleur, plausible et fausse : le bleu de la fibre ressort rouge.
    """
    r, g, b = rgb
    return f"{alpha:02x}{b:02x}{g:02x}{r:02x}"


def _dash_segments(a: tuple[float, float], b: tuple[float, float],
                   dash_m: float = 250.0, gap_m: float = 150.0
                   ) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Découpe une liaison en tirets GÉOGRAPHIQUES.

    ⚠️ KML ne sait pas tracer en pointillé : `<LineStyle>` ne porte que `color`
    et `width`, il n'existe aucune propriété de tireté (ni dans l'extension
    `gx:`). Le pointillé de nos cartes est un effet de DESSIN, sans équivalent
    exportable — Google Earth rendait donc les backhauls radio en trait plein,
    indiscernables de la fibre à la forme près.

    On fabrique donc les tirets en géométrie : un `<LineString>` par tiret,
    regroupés dans un `<MultiGeometry>` pour que la liaison reste UN objet
    cliquable et non quinze.

    ⚠️ La longueur des tirets est en MÈTRES, pas en degrés : un pas en degrés
    donnerait des tirets plus courts en longitude qu'en latitude, donc une
    liaison est-ouest et une liaison nord-sud qui ne se ressemblent pas.
    """
    (la, lna), (lb, lnb) = a, b
    mid = math.radians((la + lb) / 2)
    m_lat, m_lon = 110_900.0, 111_320.0 * math.cos(mid)
    dy, dx = (lb - la) * m_lat, (lnb - lna) * m_lon
    total = math.hypot(dx, dy)
    if total <= 0:
        return []
    out = []
    pos, step = 0.0, dash_m + gap_m
    while pos < total:
        end = min(pos + dash_m, total)
        t0, t1 = pos / total, end / total
        out.append((
            (la + (lb - la) * t0, lna + (lnb - lna) * t0),
            (la + (lb - la) * t1, lna + (lnb - lna) * t1),
        ))
        pos += step
    return out


def _xml(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _icon_png(kind: str) -> bytes:
    """Fabrique l'icône du KMZ avec les MÊMES primitives que la carte.

    Réutiliser `_pin` et `_internet_icon` garantit qu'un site vert dans Google
    Earth est le même objet qu'un site vert sur le JPEG. Dessiner des icônes
    « équivalentes » à la main les aurait fait dériver au premier ajustement.
    """
    size, r = 128, 44
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if kind == "internet":
        _internet_icon(d, size / 2, size / 2, r, 2)
    else:
        color = _PLANNED_PIN if kind == "planned" else (
            _RING_PIN if kind == "site" else _IDLE_PIN)
        _pin(d, size / 2, size / 2 - r * 0.3, r, color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def export_kmz(topo: dict, lang: str, year: int, mode: str, out_path: Path,
               overlay_jpeg: bytes | None = None) -> tuple[Path, list[str]]:
    """Écrit le KMZ correspondant EXACTEMENT à la carte du même mode.

    Sites et liaisons deviennent des objets cliquables, rangés en dossiers que
    Google Earth sait allumer et éteindre. Le KMZ n'hérite pas du cadrage figé
    du fond de carte embarqué : Google Earth fournit sa propre imagerie, donc
    on peut zoomer sans limite.

    ⚠️ Les couches viennent de `_layers()`, comme le JPEG. Les deux sorties ne
    peuvent donc pas se contredire.

    `overlay_jpeg` ajoute la carte imprimée en calque au sol, **éteint par
    défaut** : allumé, il recouvrirait l'imagerie de Google Earth avec la
    nôtre, et les objets vectoriels se superposeraient à leur propre dessin.
    """
    txt = dict(_TEXT[lang])
    if mode == "final":
        txt["banner"], txt["sub"] = txt["banner_final"], txt["sub_final"]
    elif mode == "current":
        txt["banner"], txt["sub"] = txt["banner_current"], txt["sub_current"]
    stamp = str(topo.get("synced_at") or "")[:10]
    txt["sub"] = txt["sub"].format(year=year, date=stamp or "?")

    bounds = _load_bounds()["nouakchott"]
    layers = _layers(topo, txt, mode, bounds)
    final = mode == "final"

    styles: list[str] = []
    for name, rgb, width in (
        ("fibre", _FIBER_EXISTING, 6), ("upgrade", _FIBER_UPGRADE, 6),
        ("new", _FIBER_NEW, 6), ("backup", _FIBER_BACKUP, 6),
        ("radio", _RADIO, 4),
    ):
        styles.append(
            f'<Style id="l_{name}"><LineStyle>'
            f"<color>{_kml_color(rgb)}</color><width>{width}</width>"
            f"</LineStyle></Style>")
    for name in ("site", "spur", "planned", "internet"):
        styles.append(
            f'<Style id="p_{name}"><IconStyle><scale>1.1</scale>'
            f"<Icon><href>files/{name}.png</href></Icon>"
            f"<hotSpot x=\"0.5\" y=\"0.1\" xunits=\"fraction\" "
            f"yunits=\"fraction\"/></IconStyle></Style>")

    def point(name: str, style: str, desc: str) -> str:
        lat, lon = layers.positions[name]
        return (f"<Placemark><name>{_xml(name)}</name>"
                f"<description>{_xml(desc)}</description>"
                f"<styleUrl>#{style}</styleUrl>"
                f"<Point><coordinates>{lon:.6f},{lat:.6f},0</coordinates></Point>"
                f"</Placemark>")

    def line(a: str, b: str, style: str, label: str) -> str:
        (la, lna), (lb, lnb) = layers.positions[a], layers.positions[b]
        # tessellate=1 : le trait épouse le sol au lieu de traverser le relief.
        return (f"<Placemark><name>{_xml(label)}</name>"
                f"<styleUrl>#{style}</styleUrl><LineString><tessellate>1</tessellate>"
                f"<altitudeMode>clampToGround</altitudeMode><coordinates>"
                f"{lna:.6f},{la:.6f},0 {lnb:.6f},{lb:.6f},0"
                f"</coordinates></LineString></Placemark>")

    def dashed(a: str, b: str, style: str, label: str) -> str:
        """Liaison radio : un seul Placemark, plusieurs tirets à l'intérieur."""
        parts = _dash_segments(layers.positions[a], layers.positions[b])
        if not parts:
            return ""
        geo = "".join(
            f"<LineString><tessellate>1</tessellate>"
            f"<altitudeMode>clampToGround</altitudeMode><coordinates>"
            f"{p0[1]:.6f},{p0[0]:.6f},0 {p1[1]:.6f},{p1[0]:.6f},0"
            f"</coordinates></LineString>"
            for p0, p1 in parts)
        return (f"<Placemark><name>{_xml(label)}</name>"
                f"<styleUrl>#{style}</styleUrl>"
                f"<MultiGeometry>{geo}</MultiGeometry></Placemark>")

    def folder(name: str, items: list[str], visible: bool = True) -> str:
        if not items:
            return ""
        return (f"<Folder><name>{_xml(name)}</name>"
                f"<visibility>{1 if visible else 0}</visibility>"
                + "".join(items) + "</Folder>")

    folders: list[str] = []

    if layers.today:
        folders.append(folder(txt["fibre_now"], [
            line(a, b, "l_fibre", f"{a} ↔ {b}") for a, b in layers.wired_edges]))
    else:
        # Un dossier par nature de segment : c'est le découpage sur lequel on
        # décide du chantier, donc celui qu'on veut pouvoir isoler à l'écran.
        by_status = {k: [] for k in _STATUS_ORDER}
        for seg in layers.ring_segs.values():
            rings = " + ".join(seg["rings"])
            by_status[seg["status"]].append(
                line(seg["a"], seg["b"],
                     "l_fibre" if final else {"existing": "l_fibre",
                                              "upgrade": "l_upgrade",
                                              "new": "l_new"}[seg["status"]],
                     f"{seg['a']} ↔ {seg['b']} · {rings}"))
        if final:
            folders.append(folder(txt["ring_fibre"],
                                  [x for k in _STATUS_ORDER for x in by_status[k]]))
        else:
            for k in _STATUS_ORDER:
                folders.append(folder(txt[k], by_status[k]))

    folders.append(folder(txt["radio_now"] if layers.today else txt["radio_kept"], [
        dashed(a, b, "l_radio", f"{a} ↔ {b}") for a, b in layers.radio_edges]))

    feeds = []
    for site in layers.routes:
        backup = site == _SOURCE_ROUTE_NEW
        feeds.append(line(layers.icon_label, site,
                          "l_backup" if backup else "l_fibre",
                          txt["feed_backup_done"] if (backup and final)
                          else txt["feed_backup"] if backup else txt["feed"]))
    if layers.icons:
        feeds.append(point(layers.icon_label, "p_internet", txt["feed"]))
    folders.append(folder(txt["internet"], feeds))

    core = [point(n, "p_site", txt["core_pin"] if layers.today else txt["backbone_pin"])
            for n in sorted(layers.ring_sites) if n in layers.positions]
    folders.append(folder(txt["core_pin"] if layers.today else txt["backbone_pin"], core))

    spurs = [point(n, "p_spur", txt["spur_pin"]) for n in sorted(layers.positions)
             if n not in layers.ring_sites and n not in layers.planned and n not in layers.icons]
    folders.append(folder(txt["spur_pin"], spurs))

    folders.append(folder(txt["planned_pin"],
                          [point(n, "p_planned", txt["planned_pin"])
                           for n in sorted(layers.planned)]))

    if overlay_jpeg is not None:
        folders.append(
            "<Folder><name>" + _xml(txt["banner"]) + "</name><visibility>0</visibility>"
            "<GroundOverlay><name>" + _xml(txt["banner"]) + "</name>"
            "<visibility>0</visibility><Icon><href>files/carte.jpg</href></Icon>"
            f"<LatLonBox><north>{bounds['north']}</north><south>{bounds['south']}</south>"
            f"<east>{bounds['east']}</east><west>{bounds['west']}</west></LatLonBox>"
            "</GroundOverlay></Folder>")

    doc = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>{_xml(txt['banner'])}</name>"
           f"<description>{_xml(txt['sub'])}</description>"
           + "".join(styles) + "".join(f for f in folders if f)
           + "</Document></kml>")

    out_path = Path(out_path)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", doc)
        for kind in ("site", "spur", "planned", "internet"):
            z.writestr(f"files/{kind}.png", _icon_png(kind))
        if overlay_jpeg is not None:
            z.writestr("files/carte.jpg", overlay_jpeg)
    return out_path, layers.missing


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    p = argparse.ArgumentParser(description="Carte du projet de boucle fibre.")
    p.add_argument("--topo", default=str(root / "topo-prod.json"),
                   help="export /network-topology (positions + réseau actuel)")
    p.add_argument("--lang", choices=("fr", "en"), default="fr")
    p.add_argument("--mode", choices=("plan", "final", "current"),
                   default="plan",
                   help="plan = le chantier ; final = l'architecture cible ; "
                        "current = l'architecture actuelle")
    p.add_argument("--year", type=int, default=2027)
    p.add_argument("--out", default="fiber-ring-plan.jpg")
    p.add_argument("--kmz", metavar="FICHIER",
                   help="ecrire aussi un KMZ (Google Earth) des memes couches")
    p.add_argument("--kmz-overlay", action="store_true",
                   help="embarquer la carte imprimee comme calque au sol "
                        "(eteint par defaut dans Google Earth)")
    args = p.parse_args()

    # ⚠️ L'export N'EST PAS versionné (c'est une extraction de prod : MAC des
    # équipements, coordonnées des pylônes). Il faut donc le produire avant le
    # premier rendu, et le message doit dire comment — un FileNotFoundError nu
    # laisserait croire à un défaut d'installation.
    topo_path = Path(args.topo)
    if not topo_path.is_file():
        print(f"Export de topologie introuvable : {topo_path}\n"
              "Le produire d'abord, au choix :\n"
              "  dc exec backend python scripts/dump_site_topology.py --json > topo-prod.json\n"
              "  curl -sk -H \"X-API-Key: $API_KEY\" \\\n"
              "       https://10.135.3.25/api/v1/network-topology > topo-prod.json\n"
              "puis relancer avec --topo <chemin>.", file=sys.stderr)
        return 2

    topo = json.loads(topo_path.read_text(encoding="utf-8"))
    data, missing, counts = render(topo, args.lang, args.year, args.mode)
    Path(args.out).write_bytes(data)

    print(f"Carte ecrite : {args.out}  ({len(data) / 1024:.0f} Ko)")
    if args.mode == "current":
        print(f"  {counts['wired']:>2}  liaisons fibre")
        print(f"  {counts['radio']:>2}  backhauls radio")
    elif args.mode == "final":
        backbone = sum(counts[k] for k in _STATUS_ORDER)
        print(f"  {backbone:>2}  segments de dorsale fibre")
        print(f"  {counts['radio']:>2}  backhauls radio conserves")
    else:
        for status, label in (("existing", "fibre existante reutilisee"),
                              ("upgrade", "trace radio a fibrer"),
                              ("new", "segment neuf a creer")):
            print(f"  {counts[status]:>2}  {label}")
    if args.kmz:
        kmz, _ = export_kmz(topo, args.lang, args.year, args.mode, Path(args.kmz),
                            overlay_jpeg=data if args.kmz_overlay else None)
        print(f"KMZ ecrit    : {kmz}  ({kmz.stat().st_size/1024:.0f} Ko)")
    if missing:
        print("  /!\\ sites non tracables : " + ", ".join(sorted(missing)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
