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

Le reste du réseau radio est dessiné en gris pâle : il situe les boucles dans
le parc sans leur disputer la lecture.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw  # noqa: E402

from app.services.site_map_service import (  # noqa: E402
    _ASSETS,
    _INK,
    _LABEL_BORDER,
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

# Le site par lequel Internet entre — fait d'exploitation : aucune table ne le
# dit (le lien amont n'est pas un data-link, c'est pourquoi la racine du graphe
# est un réglage). Même constante que la carte du parc, pour la même raison.
_SOURCE_SITE = "A2 HQ"

# ------------------------------------------------------------------- palette
# Identité A2 Connect pour le cadre et le texte ; couleurs FONCTIONNELLES
# choisies pour rester distinguables entre elles et sur de l'imagerie satellite
# (sable, toits gris, routes jaunes) — la marque tient l'identité, la sémantique
# tient la lecture.
_FIBER_EXISTING = (26, 95, 208)     # #1a5fd0 — bleu : fibre déjà en service
_FIBER_UPGRADE = (123, 47, 181)     # #7b2fb5 — violet : tracé radio à fibrer
_FIBER_NEW = (216, 27, 96)          # #d81b60 — magenta : segment neuf
_RADIO = (30, 107, 79)              # vert : backhaul radio conservé (vue CIBLE)
_IDLE_LINK = (150, 163, 154)        # réseau radio d'aujourd'hui, en retrait (vue PLAN)
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
        "source": "Arrivée Internet",
        "ring_pin": "Site sur la boucle",
        "other_pin": "Autre site du parc",
        "seg": "{n} segments",
        "seg1": "1 segment",
        "source_caption": ("ARRIVÉE INTERNET", "tête de réseau"),
        "rings": "BOUCLES",
        "banner_final": "ARCHITECTURE CIBLE — RÉSEAU APRÈS LES BOUCLES FIBRE",
        "sub_final": "Nouakchott · état visé une fois les boucles en service",
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
        "source": "Internet source",
        "ring_pin": "Site on the ring",
        "other_pin": "Other site",
        "seg": "{n} segments",
        "seg1": "1 segment",
        "source_caption": ("INTERNET SOURCE", "network head-end"),
        "rings": "RINGS",
        "banner_final": "TARGET ARCHITECTURE — NETWORK AFTER THE FIBRE RINGS",
        "sub_final": "Nouakchott · state once the rings are in service",
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
    t = trim / dist
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
              txt["sub"].format(year=year), font=f_sub, fill=_SAGE)


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
    """Rend la carte dans l'un des deux MODES.

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
    bounds = _load_bounds()["nouakchott"]
    base = Image.open(_ASSETS / "nouakchott.jpg").convert("RGBA")

    current: dict[frozenset, str] = {}
    for edge in topo.get("edges", []):
        a, b = _norm(edge.get("site_a", "")), _norm(edge.get("site_b", ""))
        current[frozenset((a, b))] = edge.get("medium")

    ring_segs = _ring_segments(current)
    ring_sites = {s for seg in ring_segs.values() for s in (seg["a"], seg["b"])}

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
        for s in (seg["a"], seg["b"]):
            if s not in positions and s not in missing:
                missing.append(s)

    scale = _SS
    r_ring, r_other = 32 * scale, 21 * scale
    overlay = Image.new("RGBA", (bounds["w"] * _SS, bounds["h"] * _SS),
                        (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # La pastille est posée AU-DESSUS du point, sa pointe sur les coordonnées :
    # c'est la pointe qui désigne le lieu, pas le centre du disque.
    anchors: dict[str, tuple[float, float]] = {}
    for name, (lat, lon) in positions.items():
        x, y = _project(bounds, lat, lon)
        r = r_ring if name in ring_sites else r_other
        anchors[name] = (x * _SS, y * _SS - 1.25 * r)

    final = mode == "final"

    # 1) Le radio d'abord, sous la fibre.
    #    ⚠️ Un tracé que la boucle reprend n'est JAMAIS redessiné en radio : en
    #    mode plan ce serait un doublon sous le trait de fibre, en mode cible ce
    #    serait un mensonge — ce backhaul n'existera plus.
    radio: list[tuple] = []
    for edge in topo.get("edges", []):
        a, b = _norm(edge.get("site_a", "")), _norm(edge.get("site_b", ""))
        if a not in anchors or b not in anchors or frozenset((a, b)) in ring_segs:
            continue
        _dashed(draw, anchors[a], anchors[b],
                _RADIO if final else _IDLE_LINK,
                (6.5 if final else 5.0) * scale,
                (r_ring if a in ring_sites else r_other) + 6)
        radio.append((anchors[a], anchors[b]))

    # 2) La fibre par-dessus. En mode plan, dans l'ordre du reste-à-faire
    #    croissant : le magenta (segment neuf) est ce qu'on vient chercher, il
    #    passe donc en dernier, au-dessus de tout. En mode cible il n'y a plus
    #    qu'UNE fibre — la distinction de chantier n'a plus d'objet.
    segments = list(radio)
    counts = dict.fromkeys(_STATUS_ORDER, 0)
    counts["radio"] = len(radio)
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
    ordered = sorted(anchors, key=lambda n: (n in ring_sites, -positions[n][0]))
    for name in ordered:
        cx, cy = anchors[name]
        on_ring = name in ring_sites
        if name == _SOURCE_SITE:
            _source_ring(draw, cx, cy, r_ring, scale)
        _pin(draw, cx, cy, r_ring if on_ring else r_other,
             _RING_PIN if on_ring else _IDLE_PIN)

    f_ring = _font(int(round(35 * scale)))
    f_other = _font(int(round(25 * scale)), bold=False)
    f_cap = _font(int(round(20 * scale)))
    items = []
    for name in sorted(anchors, key=lambda n: -positions[n][0]):
        on_ring = name in ring_sites
        caps = txt["source_caption"] if name == _SOURCE_SITE else ()
        rows, sizes, w, h = _measure_label(
            draw, name, caps, f_ring if on_ring else f_other, f_cap)
        cx, cy = anchors[name]
        items.append({
            "name": name, "x": cx, "y": cy,
            "r": r_ring if on_ring else r_other,
            "rows": rows, "sizes": sizes, "w": w, "h": h,
            "border": _LABEL_BORDER if on_ring else (214, 221, 215),
            "bw": 2 if on_ring else 1,
            "fg": _INK if on_ring else (110, 124, 114),
        })

    canvas = (bounds["w"] * _SS, bounds["h"] * _SS)
    for item in _place(items, draw, canvas, segments):
        _label(draw, item, scale)

    # La légende décrit ce que CETTE vue trace, et rien d'autre.
    if final:
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
            ("dash", _IDLE_LINK, txt["idle"], ""),
            ("pin", _RING_PIN, txt["ring_pin"], ""),
            ("pin", _IDLE_PIN, txt["other_pin"], ""),
            ("ring", _GOLD, txt["source"], ""),
        ]

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


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    p = argparse.ArgumentParser(description="Carte du projet de boucle fibre.")
    p.add_argument("--topo", default=str(root / "topo-prod.json"),
                   help="export /network-topology (positions + réseau actuel)")
    p.add_argument("--lang", choices=("fr", "en"), default="fr")
    p.add_argument("--mode", choices=("plan", "final"), default="plan",
                   help="plan = le chantier ; final = l'architecture cible")
    p.add_argument("--year", type=int, default=2027)
    p.add_argument("--out", default="fiber-ring-plan.jpg")
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
    if args.mode == "final":
        backbone = sum(counts[k] for k in _STATUS_ORDER)
        print(f"  {backbone:>2}  segments de dorsale fibre")
        print(f"  {counts['radio']:>2}  backhauls radio conserves")
    else:
        for status, label in (("existing", "fibre existante reutilisee"),
                              ("upgrade", "trace radio a fibrer"),
                              ("new", "segment neuf a creer")):
            print(f"  {counts[status]:>2}  {label}")
    if missing:
        print("  /!\\ sites non tracables : " + ", ".join(sorted(missing)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
