"""Carte cartographique des sites — rendu en image, puis export Word.

Ce module dessine la **cartographie du parc** : les sites posés à leur vraie
place sur un fond de carte, et les backhauls tracés entre eux. C'est la vue
« plan mural » du réseau, celle qu'on imprime et qu'on fait circuler — à ne pas
confondre avec la page `/topology`, qui sert le même maillage sous forme de
graphe en couches et de carte Google interactive.

⚠️ **Le fond de carte est EMBARQUÉ, pas téléchargé.** Trois tuiles composées
(`data/maps/*.jpg` + `bounds.json`) sont livrées avec le code. Rien n'est
demandé à un serveur de tuiles au moment de l'export : le serveur de prod est
derrière un FortiGate et n'a pas d'accès Internet sortant garanti, et un export
qui dépend d'un CDN échouerait le jour où on en a besoin. Le prix à payer est
que le **cadrage est figé** — un site posé hors de la fenêtre d'une ville ne
peut pas être dessiné, et il est alors **nommé** dans le document plutôt
qu'escamoté (même règle que la vue carte : une carte qui omet un site sans le
dire se lit comme un réseau qui n'a pas ce site).

⚠️ **Deux populations, deux couleurs, deux sources.**

* **VERT — l'existant.** Les 17 sites de Nouakchott, lus dans la topologie à
  chaque export : coordonnées relevées (`site_locations`), liaisons mesurées.
  Un backhaul posé hier apparaît sans rien toucher ici.
* **ROUGE — les extensions PROGRAMMÉES**, pas encore installées : deux à
  Nouakchott, trois à Nouadhibou, deux à Rosso. Elles ne sont dans aucune de
  nos tables — c'est leur état NORMAL, elles n'existent pas encore — et vivent
  dans la constante `_PLANNED` ci-dessous. Leurs positions sont des
  **intentions**, pas des relevés, et ne sont **jamais écrites en base** : les y
  inscrire les ferait passer pour du parc installé, donc les ferait compter dans
  la capacité, pinguer, et alerter comme injoignables.

Le jour où un site programmé est posé et enrôlé dans UISP, il remonte tout seul
par le chemin normal, en vert — il ne reste plus qu'à retirer sa ligne de
`_PLANNED`.
"""

from __future__ import annotations

import io
import json
import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_ASSETS = Path(__file__).resolve().parents[2] / "data" / "maps"

# ----------------------------------------------------------------- palette
# Les couleurs de trait viennent de la NATURE de la liaison, pas de son état :
# ce document est un plan de câblage, pas un tableau de supervision. L'état
# vivant se lit sur /topology, où il change toutes les minutes.
# Identité A2 Connect, relevée sur a2connect.mr : le charbon-vert du sigle et
# le vert-de-gris du mot « Connect ».
_INK = (44, 60, 52)         # #2c3c34 — texte, cadres, fonds sombres
_SAGE = (167, 185, 173)     # #a7b9ad — le vert-de-gris de la marque
_GOLD = (249, 181, 36)      # #f9b524 — l'accent de la marque

# ⚠️ Les couleurs FONCTIONNELLES de la carte ne suivent pas la marque : elles
# doivent rester distinguables entre elles ET sur de l'imagerie satellite
# (sable, toits gris, eau). La marque tient l'identité — cadres, titres, texte
# des étiquettes — la sémantique tient la lecture.
_GREEN = (30, 107, 79)      # backhaul radio actif + site en service
_BLUE = (26, 95, 208)       # fibre / cuivre
_AMBER = (224, 138, 30)     # backhaul radio hors arbre = boucle de secours
_RED = (192, 39, 30)        # site programmé
_WHITE = (255, 255, 255)
_LABEL_BORDER = (196, 207, 199)
_GOLD_TEXT = (176, 118, 8)  # l'or de marque assombri : #f9b524 sur blanc ne se lit pas

# Supersampling du calque de dessin. PIL ne lisse pas les formes : tracer les
# pastilles à 1× donne des cercles crénelés sur un fond de carte net. On dessine
# à 2× puis on réduit en LANCZOS — le coût est une image transitoire de ~80 Mo
# sur la plus grande planche, le temps d'une requête.
_SS = 2

_FONT_CANDIDATES = {
    True: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
    False: (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
}


class MapAssetsError(RuntimeError):
    """Les fonds de carte livrés avec le code sont absents ou illisibles."""


@dataclass(frozen=True)
class Plate:
    """Une planche : une ville, sa fenêtre géographique, son fond de carte."""

    key: str
    title: str
    scale: float


_PLATES: tuple[Plate, ...] = (
    Plate("nouakchott", "Nouakchott", 1.0),
    Plate("nouadhibou", "Nouadhibou", 1.55),
    Plate("rosso", "Rosso", 1.2),
)

# La couleur d'une pastille dit son ÉTAT, jamais sa ville : vert = installé,
# rouge = programmé. Un code couleur par ville obligerait à lire une légende
# pour comprendre le message principal de la carte.
_PIN_INSTALLED = _GREEN
_PIN_PLANNED = _RED

# Extensions PROGRAMMÉES — pas encore installées (cf. en-tête du module).
# Positions approchées : elles situent l'intention dans la ville, ce ne sont pas
# des relevés. Rendues en ROUGE partout.
#
# ⚠️ Nouakchott en a aussi : la planche « vivante » porte donc les deux
# populations. C'est tout l'intérêt de la carte — voir d'un coup ce qui est
# debout et ce qui manque, sur le même plan.
#
# ⚠️ Aucune liaison n'est tracée depuis un site programmé de Nouakchott : son
# raccordement n'est pas arrêté. En dessiner une inventerait de la topologie,
# ce que ce module ne fait nulle part. Les liaisons de Nouadhibou et Rosso, en
# revanche, font partie du plan lui-même — ce sont des réseaux à créer d'un
# bloc, pas des ajouts à un maillage existant.
_PLANNED: dict[str, dict] = {
    "nouakchott": {
        "sites": [
            ("NKTT NEW 1", 18.1456, -15.9593),  # à côté d'AT2
            ("NKTT NEW 2", 18.0270, -15.9210),  # la zone laissée nue derrière VEL1
        ],
        "edges": [],
    },
    "nouadhibou": {
        "sites": [
            ("NDB-NORD", 20.9782, -17.0285),
            ("NDB-CENTRE", 20.9500, -17.0375),
            ("NDB-SUD", 20.9155, -17.0395),
        ],
        "edges": [
            ("NDB-NORD", "NDB-CENTRE"),
            ("NDB-CENTRE", "NDB-SUD"),
        ],
    },
    "rosso": {
        # Alignés à la MÊME latitude : les deux mâts se répondent d'ouest en
        # est le long du fleuve, pas du nord au sud.
        "sites": [
            ("RSO-NORD", 16.5155, -15.8100),
            ("RSO-SUD", 16.5155, -15.7950),
        ],
        "edges": [("RSO-NORD", "RSO-SUD")],
    },
}

# Le site par lequel Internet entre dans chaque ville, et ce qu'on en dit.
#
# ⚠️ C'est la seule chose que la carte affirme SANS la tenir d'une mesure :
# aucune de nos tables ne dit qu'un site est la tête de réseau (le lien amont
# n'est pas un data-link — le contrôleur ignore lui aussi quel site fait face à
# l'amont, c'est exactement pourquoi `TOPOLOGY_ROOT_SITE` est un réglage). Fait
# d'exploitation, écrit ici.
#
# ⚠️ Écrit SUR le site, pas au bout d'un câble vers un cartouche : l'arrivée
# amont n'est pas une liaison de notre réseau, et lui donner un trait la
# faisait ressembler à un de nos backhauls. C'est une PROPRIÉTÉ du site.
_SOURCES: dict[str, dict[str, tuple[str, ...]]] = {
    "nouakchott": {"A2 HQ": ("SOURCE INTERNET", "arrivée nationale")},
    "nouadhibou": {"NDB-CENTRE": ("SOURCE INTERNET", "fibre optique depuis Nouakchott")},
    "rosso": {"RSO-NORD": ("SOURCE INTERNET", "fibre optique depuis Nouakchott")},
}

# La légende, partagée par la page et le document Word — jamais recopiée dans
# l'un des deux : deux légendes du même dessin finiraient par se contredire.
LEGEND: tuple[dict, ...] = (
    {"color": _BLUE, "shape": "solid", "label": "Liaison fibre / cuivre"},
    {"color": _GREEN, "shape": "dashed", "label": "Backhaul radio — active"},
    {"color": _AMBER, "shape": "dashed", "label": "Backhaul radio — boucle de secours"},
    {"color": _GREEN, "shape": "pin", "label": "Site en service"},
    {"color": _RED, "shape": "pin", "label": "Site programmé (extension)"},
    {"color": _GOLD, "shape": "ring", "label": "Site source Internet"},
)


# Glyphes de la légende pour le document Word. Choisis parmi les caractères que
# les polices bureautiques courantes portent toutes : un trait de type
# semi-graphique s'afficherait en carré vide chez le lecteur, sur le seul bloc
# qui explique le dessin.
_LEGEND_GLYPH = {"solid": "▬▬", "dashed": "▬ ▬",
                 "pin": "●", "ring": "◉"}


def legend_entries() -> list[dict]:
    """La légende sous une forme prête à rendre (hex + forme + libellé)."""
    return [
        {"hex": "#{:02x}{:02x}{:02x}".format(*entry["color"]), "shape": entry["shape"],
         "glyph": _LEGEND_GLYPH[entry["shape"]], "color": entry["color"],
         "label": entry["label"]}
        for entry in LEGEND
    ]


# ------------------------------------------------------------- géométrie
def _mercator_y(lat: float) -> float:
    s = math.sin(math.radians(lat))
    return math.log((1 + s) / (1 - s)) / 2


def _load_bounds() -> dict[str, dict]:
    path = _ASSETS / "bounds.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # pragma: no cover - défaut d'install
        raise MapAssetsError(f"bounds.json illisible ({path}) : {exc}") from exc
    return {entry["name"]: entry for entry in raw}


def _project(bounds: dict, lat: float, lon: float) -> tuple[float, float]:
    """(lat, lon) → pixel dans le fond de carte, en projection Web Mercator."""
    y_north = _mercator_y(bounds["north"])
    y_south = _mercator_y(bounds["south"])
    x = (lon - bounds["west"]) / (bounds["east"] - bounds["west"]) * bounds["w"]
    y = (y_north - _mercator_y(lat)) / (y_north - y_south) * bounds["h"]
    return x, y


def _inside(bounds: dict, lat: float, lon: float) -> bool:
    return (bounds["south"] <= lat <= bounds["north"]
            and bounds["west"] <= lon <= bounds["east"])


# ------------------------------------------------------------- typographie
def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES[bold]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    # Aucune police vectorielle : plutôt qu'un rendu illisible en bitmap, on le
    # dit — l'image de la carte est le produit, une carte sans noms ne sert à
    # rien. L'image Docker installe `fonts-dejavu-core` pour cette raison.
    raise MapAssetsError(
        "Aucune police TrueType trouvée (DejaVu/Liberation/Arial). "
        "Installer fonts-dejavu-core dans l'image backend."
    )


# ------------------------------------------------------------- primitives
def _dashed_line(draw: ImageDraw.ImageDraw, p1, p2, color, width: float,
                 dash: float, gap: float) -> None:
    x1, y1 = p1
    x2, y2 = p2
    total = math.hypot(x2 - x1, y2 - y1)
    if total <= 0:
        return
    ux, uy = (x2 - x1) / total, (y2 - y1) / total
    pos = 0.0
    while pos < total:
        end = min(pos + dash, total)
        draw.line(
            [(x1 + ux * pos, y1 + uy * pos), (x1 + ux * end, y1 + uy * end)],
            fill=color, width=int(round(width)),
        )
        pos = end + gap


def _link(draw: ImageDraw.ImageDraw, p1, p2, color, width: float, dashed: bool,
          trim: float) -> None:
    """Trait entre deux pastilles, raccourci de `trim` à chaque bout.

    Doublé d'un liseré blanc dessous : sur un fond de carte clair chargé de
    routes jaunes, un trait de couleur seul se perd. Le liseré est plein même
    quand le trait est tireté — c'est lui qui porte la continuité du lien.
    """
    x1, y1 = p1
    x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1) or 1.0
    t = trim / dist
    a = (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
    b = (x2 - (x2 - x1) * t, y2 - (y2 - y1) * t)
    draw.line([a, b], fill=(255, 255, 255, 190), width=int(round(width + 3.4)))
    if dashed:
        _dashed_line(draw, a, b, color, width, width * 3.1, width * 2.3)
    else:
        draw.line([a, b], fill=color, width=int(round(width)))


def _pin(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
         color: tuple[int, int, int]) -> None:
    """Pastille ronde + pointe + pictogramme de pylône, façon marqueur de carte."""
    ring = max(2.0, r * 0.16)
    draw.polygon(
        [(cx - r * 0.34, cy + r * 0.80), (cx, cy + r * 1.62), (cx + r * 0.34, cy + r * 0.80)],
        fill=color, outline=_WHITE, width=int(round(ring * 0.8)),
    )
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color,
                 outline=_WHITE, width=int(round(ring)))

    g = r / 20.0          # le pictogramme est dessiné dans une boîte de 40 unités
    w = max(1.0, 2.4 * g)
    lw = int(round(w))
    draw.line([(cx - 5.4 * g, cy + 12 * g), (cx - 1.6 * g, cy - 8 * g)], fill=_WHITE, width=lw)
    draw.line([(cx + 5.4 * g, cy + 12 * g), (cx + 1.6 * g, cy - 8 * g)], fill=_WHITE, width=lw)
    draw.line([(cx - 3.8 * g, cy + 2 * g), (cx + 3.8 * g, cy + 2 * g)], fill=_WHITE, width=lw)
    draw.line([(cx - 4.9 * g, cy + 7.2 * g), (cx + 4.9 * g, cy + 7.2 * g)], fill=_WHITE, width=lw)
    for radius, span in ((8.0, 55), (13.0, 50)):
        box = [cx - radius * g, cy - (radius + 11) * g,
               cx + radius * g, cy + (radius - 11) * g]
        draw.arc(box, 180 + span, 360 - span, fill=_WHITE, width=lw)
        draw.arc(box, span, 180 - span, fill=_WHITE, width=lw)
    dot = 1.1 * g
    draw.ellipse([cx - dot, cy - 11 * g - dot, cx + dot, cy - 11 * g + dot], fill=_WHITE)


# Positions candidates d'un nom autour de sa pastille, dans l'ordre d'essai.
# Les quatre diagonales ne sont pas du luxe : un site très maillé (le siège en
# porte cinq liaisons plus l'arrivée Internet) n'a AUCUN des quatre côtés droits
# de libre, et le solveur choisissait alors le moins mauvais — en pratique, par-
# dessus le nom du voisin.
_LABEL_SIDES = ("e", "w", "s", "n", "ne", "nw", "se", "sw")


def _label_box(cx: float, cy: float, r: float, side: str, w: float,
               h: float) -> tuple[float, float, float, float]:
    gap = r * 0.55
    left, right = cx - r - gap - w, cx + r + gap
    above, below = cy - r - gap - h, cy + r * 1.7
    positions = {
        "e": (right, cy - h / 2),
        "w": (left, cy - h / 2),
        "s": (cx - w / 2, below),
        "n": (cx - w / 2, above),
        "ne": (cx + r * 0.45, above),
        "nw": (cx - r * 0.45 - w, above),
        "se": (cx + r * 0.45, below),
        "sw": (cx - r * 0.45 - w, below),
    }
    x, y = positions[side]
    return x, y, x + w, y + h


def _measure_label(draw: ImageDraw.ImageDraw, name: str, captions: Sequence[str],
                   font: ImageFont.FreeTypeFont, caption_font: ImageFont.FreeTypeFont,
                   ) -> tuple[list[tuple[str, ImageFont.FreeTypeFont, bool]],
                              list[tuple[float, float]], float, float]:
    """Mesure une étiquette, éventuellement suivie de lignes de légende.

    Les lignes de légende ne servent qu'à un site : celui par lequel Internet
    entre. Elles vivent DANS l'étiquette plutôt que dans un cartouche à part —
    c'est une propriété du site, pas un objet du réseau.
    """
    rows = [(name, font, False)] + [(text, caption_font, True) for text in captions]
    sizes = []
    for text, row_font, _is_caption in rows:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=row_font)
        sizes.append((right - left, bottom - top))
    line_gap = sizes[0][1] * 0.42
    pad_x, pad_y = sizes[0][1] * 0.55, sizes[0][1] * 0.38
    width = max(w for w, _h in sizes) + pad_x * 2
    height = sum(h for _w, h in sizes) + line_gap * (len(rows) - 1) + pad_y * 2
    return rows, sizes, width, height


def _overlap(a, b) -> float:
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0 and dy > 0 else 0.0


def _segment_samples(segments: Iterable[tuple[tuple[float, float], tuple[float, float]]],
                     step: float) -> list[tuple[float, float]]:
    """Échantillonne les traits de liaison en points, pour le test de recouvrement.

    Un test segment/rectangle exact serait plus élégant ; l'échantillonnage est
    suffisant ici (on cherche à savoir si un nom se pose SUR un trait, pas à
    mesurer de combien) et ne peut pas se tromper de signe.
    """
    points: list[tuple[float, float]] = []
    for (x1, y1), (x2, y2) in segments:
        length = math.hypot(x2 - x1, y2 - y1)
        count = max(2, int(length / max(step, 1.0)))
        for i in range(count + 1):
            t = i / count
            points.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
    return points


def _place_labels(items: Sequence[dict], r: float, draw: ImageDraw.ImageDraw,
                  canvas: tuple[float, float],
                  segments: Sequence[tuple[tuple[float, float], tuple[float, float]]],
                  ) -> list[dict]:
    """Choisit de quel côté de sa pastille chaque étiquette se pose.

    Placement glouton sur **huit** positions (quatre côtés, quatre diagonales),
    noté par ce que l'étiquette recouvrirait. Volontairement AUTOMATIQUE et non
    une table de réglages par site : un backhaul posé sur un nouveau site doit
    apparaître à l'export suivant sans qu'on touche au code.

    ⚠️ Les diagonales ne sont pas du luxe : un site très maillé (le siège en
    porte cinq liaisons) n'a AUCUN des quatre côtés droits de libre, et le
    solveur choisissait alors le moins mauvais — en pratique, par-dessus le nom
    du voisin.

    ⚠️ Les trois gênes sont ramenées à la MÊME ÉCHELLE (une fraction de la
    surface de l'étiquette) avant d'être pondérées, sans quoi les poids ne sont
    pas comparables et le terme brut le plus grand décide seul. Le recouvrement
    d'une autre étiquette pèse le plus lourd : deux noms superposés sont
    illisibles, alors qu'un nom posé sur un trait laisse deviner le trait.

    L'ordre de parcours est figé (nord → sud) pour que deux exports des mêmes
    données rendent exactement la même image.
    """
    width, height = canvas
    pins = [(it["x"] - r, it["y"] - r, it["x"] + r, it["y"] + r * 1.7) for it in items]
    samples = _segment_samples(segments, r * 0.35)
    placed: list[tuple] = []
    out: list[dict] = []
    for item in items:
        best, best_cost = None, None
        for side in _LABEL_SIDES:
            box = _label_box(item["x"], item["y"], r, side, item["w"], item["h"])
            area = max((box[2] - box[0]) * (box[3] - box[1]), 1.0)
            hits = sum(
                1 for px, py in samples
                if box[0] <= px <= box[2] and box[1] <= py <= box[3]
            )
            cost = (
                4.0 * sum(_overlap(box, other) for other in placed) / area
                + 1.5 * sum(_overlap(box, pin) for pin in pins) / area
                + 2.0 * min(1.0, hits / 8.0)
            )
            if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
                cost += 1e6
            if best_cost is None or cost < best_cost:
                best, best_cost = box, cost
        placed.append(best)
        out.append({**item, "box": best})
    return out


def _draw_label(draw: ImageDraw.ImageDraw, item: dict, scale: float) -> None:
    box = item["box"]
    rows, sizes = item["rows"], item["sizes"]
    line_gap = sizes[0][1] * 0.42
    total = sum(h for _w, h in sizes) + line_gap * (len(rows) - 1)
    radius = min((box[3] - box[1]) * 0.30, sizes[0][1] * 0.8)
    draw.rounded_rectangle(box, radius=radius, fill=_WHITE,
                           outline=_LABEL_BORDER, width=max(1, int(round(scale))))
    cx = (box[0] + box[2]) / 2
    y = (box[1] + box[3]) / 2 - total / 2
    for (text, font, is_caption), (_w, h) in zip(rows, sizes, strict=True):
        draw.text((cx, y + h / 2), text, font=font,
                  fill=_GOLD_TEXT if is_caption else _INK, anchor="mm")
        y += h + line_gap


def _source_ring(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
                 scale: float) -> None:
    """Anneau doré autour de la pastille du site par lequel Internet entre.

    Un canal visuel SÉPARÉ de la couleur de la pastille : « en service » et
    « source du réseau » sont deux faits indépendants, et fondre les deux ferait
    que chacun masque l'autre. Même raison que l'anneau de saturation sur
    `/topology`.
    """
    ring = r + 9 * scale
    draw.ellipse([cx - ring, cy - ring, cx + ring, cy + ring],
                 outline=_WHITE, width=max(3, int(round(9 * scale))))
    draw.ellipse([cx - ring, cy - ring, cx + ring, cy + ring],
                 outline=_GOLD, width=max(2, int(round(5.5 * scale))))


def _compass(draw: ImageDraw.ImageDraw, bounds: dict, scale: float) -> None:
    r = 34 * scale
    cx = bounds["w"] * _SS - (r + 26 * scale)
    cy = bounds["h"] * _SS - (r + 26 * scale)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 235),
                 outline=_LABEL_BORDER, width=int(round(2 * scale)))
    draw.polygon(
        [(cx, cy - r * 0.34), (cx + r * 0.26, cy + r * 0.58),
         (cx, cy + r * 0.33), (cx - r * 0.26, cy + r * 0.58)],
        fill=_INK,
    )
    # Le « N » tient DANS le disque : posé plus haut, il dépasse du cercle et la
    # rose se lit comme un glyphe cassé.
    font = _font(int(round(15 * scale)))
    draw.text((cx, cy - r * 0.42), "N", font=font, fill=_INK, anchor="ms")


def _attribution(draw: ImageDraw.ImageDraw, bounds: dict, scale: float) -> None:
    """Mention de source de l'imagerie — exigée par la licence du fournisseur.

    Elle est dessinée DANS l'image : le document Word n'a pas d'autre endroit
    où elle survivrait à un copier-coller de la carte. Le texte vient de
    `bounds.json`, donc du script qui a téléchargé les tuiles — un changement
    de fournisseur d'imagerie ne peut pas oublier de changer la mention.
    """
    text = bounds.get("attribution") or "© Esri"
    font = _font(int(round(19 * scale)), bold=False)
    x, y = 14 * scale, bounds["h"] * _SS - 14 * scale
    left, top, right, bottom = draw.textbbox((x, y), text, font=font, anchor="ls")
    pad = 6 * scale
    draw.rounded_rectangle(
        [left - pad, top - pad, right + pad, bottom + pad],
        radius=5 * scale, fill=(255, 255, 255, 225),
    )
    draw.text((x, y), text, font=font, fill=(91, 106, 125), anchor="ls")


# ------------------------------------------------------------- rendu
def _plate_data(plate: Plate, topo: dict, bounds: dict
                ) -> tuple[list[tuple[str, float, float, bool]],
                           list[tuple[str, str, bool, bool]], list[str]]:
    """Sites et liaisons d'une planche, plus les sites non traçables.

    Un site est rendu `(nom, lat, lon, programmé)`. L'installé de Nouakchott
    sort de la topologie — donc de la base — et les extensions programmées de
    `_PLANNED` ; les deux se retrouvent sur la même planche.
    """
    sites: list[tuple[str, float, float, bool]] = []
    missing: list[str] = []

    for site in topo.get("sites", []) if plate.key == "nouakchott" else []:
        lat, lon = site.get("latitude"), site.get("longitude")
        name = site.get("site", "")
        if lat is None or lon is None:
            missing.append(f"{name} (position inconnue)")
            continue
        if not _inside(bounds, lat, lon):
            missing.append(f"{name} (hors du cadrage de la carte)")
            continue
        sites.append((" ".join(name.split()), lat, lon, False))

    installed = {name for name, _lat, _lon, _p in sites}
    planned = _PLANNED.get(plate.key, {"sites": [], "edges": []})
    for name, lat, lon in planned["sites"]:
        # Un site programmé qui existe désormais en base est déjà dessiné en
        # vert : on ne le redouble pas en rouge. C'est ce qui rend le retrait de
        # sa ligne de `_PLANNED` facultatif le jour de sa mise en service.
        if name in installed:
            continue
        sites.append((name, lat, lon, True))

    drawable = {name for name, _lat, _lon, _p in sites}
    edges: list[tuple[str, str, bool, bool]] = []
    for edge in topo.get("edges", []) if plate.key == "nouakchott" else []:
        a = " ".join(str(edge.get("site_a", "")).split())
        b = " ".join(str(edge.get("site_b", "")).split())
        if a not in drawable or b not in drawable:
            continue
        wired = edge.get("medium") == "wired"
        edges.append((a, b, not wired, bool(edge.get("is_tree_edge", True))))

    for a, b in planned["edges"]:
        if a in drawable and b in drawable:
            edges.append((a, b, True, True))

    return sites, edges, missing


def render_plate(plate: Plate, topo: dict, bounds_by_key: dict[str, dict]
                 ) -> tuple[bytes, list[str]]:
    """Dessine une planche et rend le JPEG plus la liste des sites non traçables."""
    bounds = bounds_by_key.get(plate.key)
    if bounds is None:
        raise MapAssetsError(f"Fenêtre géographique absente pour « {plate.key} »")
    try:
        base = Image.open(_ASSETS / f"{plate.key}.jpg").convert("RGBA")
    except OSError as exc:  # pragma: no cover - défaut d'installation
        raise MapAssetsError(f"Fond de carte « {plate.key} » illisible : {exc}") from exc

    sites, edges, missing = _plate_data(plate, topo, bounds)
    sources = _SOURCES.get(plate.key, {})

    scale = plate.scale * _SS
    r = 32 * scale
    width = 7.2 * scale
    font = _font(int(round(35 * scale)))
    caption_font = _font(int(round(23 * scale)))

    overlay = Image.new("RGBA", (bounds["w"] * _SS, bounds["h"] * _SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # La pastille est posée AU-DESSUS du point, sa pointe sur les coordonnées :
    # c'est la pointe qui désigne le lieu, pas le centre du disque.
    anchors: dict[str, tuple[float, float]] = {}
    for name, lat, lon, _planned in sites:
        x, y = _project(bounds, lat, lon)
        anchors[name] = (x * _SS, y * _SS - 1.25 * r)

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for a, b, dashed, tree in edges:
        color = _AMBER if (dashed and not tree) else (_GREEN if dashed else _BLUE)
        _link(draw, anchors[a], anchors[b], color, width, dashed, r + width)
        segments.append((anchors[a], anchors[b]))

    ordered = sorted(sites, key=lambda s: -s[1])
    for name, _lat, _lon, planned in ordered:
        cx, cy = anchors[name]
        if name in sources:
            _source_ring(draw, cx, cy, r, scale)
        _pin(draw, cx, cy, r, _PIN_PLANNED if planned else _PIN_INSTALLED)

    items = []
    for name, _lat, _lon, _planned in ordered:
        rows, sizes, w, h = _measure_label(
            draw, name, sources.get(name, ()), font, caption_font
        )
        cx, cy = anchors[name]
        items.append({"name": name, "x": cx, "y": cy, "rows": rows,
                      "sizes": sizes, "w": w, "h": h})

    canvas = (bounds["w"] * _SS, bounds["h"] * _SS)
    for item in _place_labels(items, r, draw, canvas, segments):
        _draw_label(draw, item, scale)

    _compass(draw, bounds, scale)
    _attribution(draw, bounds, scale)

    overlay = overlay.resize((bounds["w"], bounds["h"]), Image.LANCZOS)
    composed = Image.alpha_composite(base, overlay).convert("RGB")

    # JPEG et pas PNG : la planche finie est une PHOTO (imagerie satellite) que
    # le sans-perte fait peser dix fois plus — 9,9 Mo contre 1,6 Mo mesurés sur
    # Nouakchott, pour un document Word qui dépassait alors 13 Mo. La qualité 88
    # garde les étiquettes nettes ; ce sont elles, pas les toits, qu'on lit.
    buffer = io.BytesIO()
    composed.save(buffer, format="JPEG", quality=88, optimize=True, progressive=True)
    return buffer.getvalue(), missing


def render_plates(topo: dict) -> tuple[list[tuple[Plate, bytes, int, int]], list[str]]:
    """Rend les trois planches.

    Retourne `(planche, png, installés, programmés)` par ville, plus les sites
    qu'aucune planche n'a pu dessiner. Les deux compteurs restent SÉPARÉS
    jusqu'au document : les additionner annoncerait comme parc en service des
    mâts qui ne sont pas montés.
    """
    bounds_by_key = _load_bounds()
    out: list[tuple[Plate, bytes, int, int]] = []
    missing: list[str] = []
    for plate in _PLATES:
        png, plate_missing = render_plate(plate, topo, bounds_by_key)
        sites = _plate_data(plate, topo, bounds_by_key[plate.key])[0]
        installed = sum(1 for _n, _la, _lo, planned in sites if not planned)
        out.append((plate, png, installed, len(sites) - installed))
        missing.extend(plate_missing)
    return out, missing


# ------------------------------------------------------------- export Word
def _sync_label(topo: dict) -> str:
    raw = str(topo.get("synced_at") or "")[:10]
    parts = raw.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else "-"


def _network_paragraphs(plates) -> tuple[str, str]:
    """Les deux paragraphes d'ouverture : l'existant, puis ce qui est programmé.

    Les chiffres sont CALCULÉS à partir des planches, jamais écrits en dur — un
    document dont le texte contredit sa propre carte ne serait plus lu.
    """
    installed = sum(count for _plate, _png, count, _planned in plates)
    planned = sum(count for _plate, _png, _installed, count in plates)
    cities = [
        f"{plate.title} ({count})"
        for plate, _png, _installed, count in plates
        if count
    ]

    existing = (
        f"Notre réseau actuel couvre Nouakchott avec une bonne capacité : "
        f"{installed} sites d'infrastructure sont en service, raccordés au siège "
        f"par des dorsales fibre et un maillage de faisceaux radio, avec des "
        f"boucles de secours qui permettent à la plupart des sites d'atteindre "
        f"Internet par plusieurs chemins. L'ensemble est supervisé en continu : "
        f"disponibilité, qualité radio et charge de chaque liaison."
    )
    extension = (
        f"Les sites en rouge sont les extensions programmées, non encore "
        f"installées : {planned} nouveaux sites — {', '.join(cities)} — "
        f"destinés à compléter la couverture là où elle manque et à augmenter "
        f"la capacité du réseau. Leur emplacement est une intention de "
        f"déploiement ; ils n'apparaissent pas encore dans la supervision."
    )
    return existing, extension


def build_topology_docx(topo: dict) -> bytes:
    """Assemble le document Word : une ville par page, en A4 portrait.

    Une planche par page et pas les trois sur une : réduites au tiers d'une
    page, les étiquettes de site deviennent illisibles — or c'est pour les lire
    qu'on imprime ce document.
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor

    plates, missing = render_plates(topo)

    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    for attr in ("left_margin", "right_margin"):
        setattr(section, attr, Cm(1.5))
    section.top_margin, section.bottom_margin = Cm(1.4), Cm(1.2)
    usable = Cm(18.0)

    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("Cartographie des sites A2 Connect")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = RGBColor(*_INK)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub_run = subtitle.add_run(f"Câblage du {_sync_label(topo)}")
    sub_run.font.size = Pt(10)
    sub_run.font.color.rgb = RGBColor(91, 106, 125)

    # Les deux paragraphes ouvrent le document, AVANT la première carte : ils
    # disent quoi lire dans les couleurs. Les découvrir après les planches
    # obligerait à revenir en arrière pour comprendre le rouge.
    existing, extension = _network_paragraphs(plates)
    for text, color in ((existing, _INK), (extension, _RED)):
        para = document.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        para_run = para.add_run(text)
        para_run.font.size = Pt(10.5)
        para_run.font.color.rgb = RGBColor(*color)

    # La légende suit les paragraphes et précède la première planche : elle
    # apprend à lire les couleurs, donc elle doit être vue avant la carte.
    legend = document.add_paragraph()
    legend.alignment = WD_ALIGN_PARAGRAPH.LEFT
    intro = legend.add_run("Légende  ")
    intro.bold = True
    intro.font.size = Pt(9)
    intro.font.color.rgb = RGBColor(91, 106, 125)
    for position, entry in enumerate(legend_entries()):
        if position:
            separator = legend.add_run("   ")
            separator.font.size = Pt(9)
        glyph = legend.add_run(entry["glyph"] + " ")
        glyph.font.size = Pt(9)
        glyph.font.color.rgb = RGBColor(*entry["color"])
        text = legend.add_run(entry["label"])
        text.font.size = Pt(9)
        text.font.color.rgb = RGBColor(*_INK)

    for index, (plate, png, installed, planned) in enumerate(plates):
        if index:
            document.add_page_break()
        heading = document.add_paragraph()
        parts = []
        if installed:
            parts.append(f"{installed} site{'s' if installed > 1 else ''} en service")
        if planned:
            parts.append(f"{planned} programmé{'s' if planned > 1 else ''}")
        head_run = heading.add_run(f"{plate.title} — {' · '.join(parts)}")
        head_run.bold = True
        head_run.font.size = Pt(14)
        head_run.font.color.rgb = RGBColor(*_INK)
        picture = document.add_paragraph()
        picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
        picture.add_run().add_picture(io.BytesIO(png), width=usable)

    if missing:
        # Un site qu'on n'a pas pu dessiner est NOMMÉ. Une carte silencieuse sur
        # ce qu'elle omet se lit comme un réseau qui n'a pas ces sites.
        note = document.add_paragraph()
        note_run = note.add_run(
            "Sites non représentés : " + " · ".join(missing)
        )
        note_run.font.size = Pt(9)
        note_run.font.color.rgb = RGBColor(192, 39, 30)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
