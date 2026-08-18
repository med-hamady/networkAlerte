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

⚠️ **Nouakchott vient de la BASE, les deux autres villes sont TRANSCRITES.**
Les 17 sites de Nouakchott ont des coordonnées relevées (`site_locations`) et
des liaisons mesurées : tout est lu dans la topologie à chaque export, donc un
backhaul posé hier apparaît. Nouadhibou et Rosso ne sont **pas** dans le
périmètre supervisé — ni site, ni équipement, ni liaison — et sont reportés du
plan existant par la constante `_TRANSCRIBED` ci-dessous. Le jour où ces sites
entrent dans UISP, ils remonteront tout seuls par le chemin normal et la
constante n'aura plus qu'à disparaître.
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
_INK = (15, 47, 94)
_GREEN = (21, 127, 60)      # backhaul radio de dorsale
_BLUE = (26, 95, 208)       # fibre / cuivre
_AMBER = (224, 138, 30)     # backhaul radio hors arbre = boucle de secours
_RED = (192, 39, 30)
_WHITE = (255, 255, 255)
_LABEL_BORDER = (195, 204, 217)

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
    color: tuple[int, int, int]
    scale: float


_PLATES: tuple[Plate, ...] = (
    Plate("nouakchott", "Nouakchott", _GREEN, 1.0),
    Plate("nouadhibou", "Nouadhibou", _BLUE, 1.55),
    Plate("rosso", "Rosso", _RED, 1.5),
)

# Sites reportés du plan existant, hors supervision (cf. en-tête du module).
# Positions approchées : elles situent le site dans sa ville, elles ne sont pas
# un relevé. Aucune n'est écrite en base — les inventer là serait les faire
# passer pour des données mesurées.
_TRANSCRIBED: dict[str, dict] = {
    "nouadhibou": {
        "sites": [
            ("NDB-NORD", 20.9782, -17.0285),
            ("NDB-CENTRE", 20.9385, -17.0415),
            ("NDB-SUD", 20.9075, -17.0245),
        ],
        "edges": [
            ("NDB-NORD", "NDB-CENTRE"),
            ("NDB-CENTRE", "NDB-SUD"),
            ("NDB-NORD", "NDB-SUD"),
        ],
    },
    "rosso": {
        "sites": [
            ("RSO-NORD", 16.5215, -15.8020),
            ("RSO-SUD", 16.5105, -15.7965),
        ],
        "edges": [("RSO-NORD", "RSO-SUD")],
    },
}


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


def _label_box(cx: float, cy: float, r: float, side: str, tw: float,
               th: float) -> tuple[float, float, float, float]:
    pad_x, pad_y = th * 0.55, th * 0.38
    w, h = tw + pad_x * 2, th + pad_y * 2
    gap = r * 0.55
    if side == "s":
        x, y = cx - w / 2, cy + r * 1.7
    elif side == "n":
        x, y = cx - w / 2, cy - r - gap - h
    elif side == "e":
        x, y = cx + r + gap, cy - h / 2
    else:
        x, y = cx - r - gap - w, cy - h / 2
    return x, y, x + w, y + h


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


def _place_labels(items: Sequence[tuple[str, float, float]], r: float,
                  font: ImageFont.FreeTypeFont, draw: ImageDraw.ImageDraw,
                  canvas: tuple[float, float],
                  segments: Sequence[tuple[tuple[float, float], tuple[float, float]]],
                  ) -> list[tuple[str, tuple]]:
    """Choisit de quel côté de sa pastille chaque nom se pose.

    Placement glouton, essayé dans l'ordre est / ouest / sud / nord, noté par
    ce qu'il recouvrirait : les autres noms déjà posés, les pastilles, **les
    traits de liaison**, et le débord hors cadre. Volontairement AUTOMATIQUE et
    non une table de réglages par site : un backhaul posé sur un nouveau site
    doit apparaître à l'export suivant sans qu'on ait à toucher au code.

    ⚠️ Les traits comptent, et c'est le point qui change tout : sans eux, tous
    les noms se posent à l'est et masquent précisément les liaisons que la carte
    est là pour montrer. Ils sont pénalisés plus lourdement qu'un simple
    chevauchement de surface — un nom qui mord sur un autre nom se lit encore,
    un nom posé sur un backhaul l'efface.

    L'ordre de parcours est figé (nord → sud) pour que deux exports des mêmes
    données rendent exactement la même image.
    """
    width, height = canvas
    pins = [(x - r, y - r, x + r, y + r * 1.7) for _, x, y in items]
    samples = _segment_samples(segments, r * 0.35)
    line_weight = r * r * 0.9
    placed: list[tuple] = []
    out: list[tuple[str, tuple]] = []
    for name, x, y in items:
        left, top, right, bottom = draw.textbbox((0, 0), name, font=font)
        tw, th = right - left, bottom - top
        best, best_cost = None, None
        for side in ("e", "w", "s", "n"):
            box = _label_box(x, y, r, side, tw, th)
            cost = sum(_overlap(box, other) for other in placed)
            cost += sum(_overlap(box, pin) for pin in pins)
            cost += line_weight * sum(
                1 for px, py in samples
                if box[0] <= px <= box[2] and box[1] <= py <= box[3]
            )
            if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
                cost += 1e9
            if best_cost is None or cost < best_cost:
                best, best_cost = box, cost
        placed.append(best)
        out.append((name, best))
    return out


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
    """Mention de source du fond de carte — exigée par les licences OSM/CARTO.

    Elle est dessinée DANS l'image : le document Word n'a pas d'autre endroit
    où elle survivrait à un copier-coller de la carte.
    """
    text = "\u00a9 OpenStreetMap contributors \u00b7 \u00a9 CARTO"
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
                ) -> tuple[list[tuple[str, float, float]], list[tuple[str, str, bool, bool]], list[str]]:
    """Sites et liaisons à tracer sur une planche, plus les sites non traçables.

    Pour Nouakchott, tout sort de la topologie servie par l'API — donc de la
    base. Pour les deux autres villes, de la transcription du plan existant.
    """
    if plate.key != "nouakchott":
        cfg = _TRANSCRIBED[plate.key]
        sites = [(name, lat, lon) for name, lat, lon in cfg["sites"]]
        edges = [(a, b, True, True) for a, b in cfg["edges"]]
        return sites, edges, []

    sites: list[tuple[str, float, float]] = []
    missing: list[str] = []
    for site in topo.get("sites", []):
        lat, lon = site.get("latitude"), site.get("longitude")
        name = site.get("site", "")
        if lat is None or lon is None:
            missing.append(f"{name} (position inconnue)")
            continue
        if not _inside(bounds, lat, lon):
            missing.append(f"{name} (hors du cadrage de la carte)")
            continue
        sites.append((" ".join(name.split()), lat, lon))

    drawable = {name for name, _, _ in sites}
    edges: list[tuple[str, str, bool, bool]] = []
    for edge in topo.get("edges", []):
        a = " ".join(str(edge.get("site_a", "")).split())
        b = " ".join(str(edge.get("site_b", "")).split())
        if a not in drawable or b not in drawable:
            continue
        wired = edge.get("medium") == "wired"
        edges.append((a, b, not wired, bool(edge.get("is_tree_edge", True))))
    return sites, edges, missing


def render_plate(plate: Plate, topo: dict, bounds_by_key: dict[str, dict]
                 ) -> tuple[bytes, list[str]]:
    """Dessine une planche et rend le PNG plus la liste des sites non traçables."""
    bounds = bounds_by_key.get(plate.key)
    if bounds is None:
        raise MapAssetsError(f"Fenêtre géographique absente pour « {plate.key} »")
    try:
        base = Image.open(_ASSETS / f"{plate.key}.jpg").convert("RGBA")
    except OSError as exc:  # pragma: no cover - défaut d'installation
        raise MapAssetsError(f"Fond de carte « {plate.key} » illisible : {exc}") from exc

    sites, edges, missing = _plate_data(plate, topo, bounds)

    scale = plate.scale
    r = 32 * scale * _SS
    width = 7.2 * scale * _SS
    font = _font(int(round(35 * scale * _SS)))

    overlay = Image.new("RGBA", (bounds["w"] * _SS, bounds["h"] * _SS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # La pastille est posée AU-DESSUS du point, sa pointe sur les coordonnées :
    # c'est la pointe qui désigne le lieu, pas le centre du disque.
    anchors: dict[str, tuple[float, float]] = {}
    for name, lat, lon in sites:
        x, y = _project(bounds, lat, lon)
        anchors[name] = (x * _SS, y * _SS - 1.25 * r)

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for a, b, dashed, tree in edges:
        color = _AMBER if (dashed and not tree) else (_GREEN if dashed else _BLUE)
        _link(draw, anchors[a], anchors[b], color, width, dashed, r + width)
        segments.append((anchors[a], anchors[b]))

    ordered = sorted(sites, key=lambda s: -s[1])
    for name, _lat, _lon in ordered:
        cx, cy = anchors[name]
        _pin(draw, cx, cy, r, plate.color)

    items = [(name, *anchors[name]) for name, _lat, _lon in ordered]
    canvas = (bounds["w"] * _SS, bounds["h"] * _SS)
    for name, box in _place_labels(items, r, font, draw, canvas, segments):
        draw.rounded_rectangle(box, radius=(box[3] - box[1]) * 0.30, fill=_WHITE,
                               outline=_LABEL_BORDER, width=int(round(2 * scale * _SS / 2)))
        draw.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), name,
                  font=font, fill=_INK, anchor="mm")

    _compass(draw, bounds, scale * _SS)
    _attribution(draw, bounds, scale * _SS)

    overlay = overlay.resize((bounds["w"], bounds["h"]), Image.LANCZOS)
    composed = Image.alpha_composite(base, overlay).convert("RGB")

    buffer = io.BytesIO()
    composed.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), missing


def render_plates(topo: dict) -> tuple[list[tuple[Plate, bytes, int]], list[str]]:
    """Rend les trois planches. Retourne (planche, png, nb de sites) + non traçables."""
    bounds_by_key = _load_bounds()
    out: list[tuple[Plate, bytes, int]] = []
    missing: list[str] = []
    for plate in _PLATES:
        png, plate_missing = render_plate(plate, topo, bounds_by_key)
        count = len(_plate_data(plate, topo, bounds_by_key[plate.key])[0])
        out.append((plate, png, count))
        missing.extend(plate_missing)
    return out, missing


# ------------------------------------------------------------- export Word
def _sync_label(topo: dict) -> str:
    raw = str(topo.get("synced_at") or "")[:10]
    parts = raw.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else "-"


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
    run = title.add_run("Cartographie des sites A2 Holding")
    run.bold = True
    run.font.size = Pt(20)
    run.font.color.rgb = RGBColor(*_INK)

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    counts = " \u00b7 ".join(f"{plate.title} {count} sites" for plate, _png, count in plates)
    sub_run = subtitle.add_run(f"{counts} \u2014 c\u00e2blage du {_sync_label(topo)}")
    sub_run.font.size = Pt(10)
    sub_run.font.color.rgb = RGBColor(91, 106, 125)

    for index, (plate, png, count) in enumerate(plates):
        if index:
            document.add_page_break()
        heading = document.add_paragraph()
        head_run = heading.add_run(f"{plate.title} \u2014 {count} sites")
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
            "Sites non repr\u00e9sent\u00e9s : " + " \u00b7 ".join(missing)
        )
        note_run.font.size = Pt(9)
        note_run.font.color.rgb = RGBColor(192, 39, 30)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
