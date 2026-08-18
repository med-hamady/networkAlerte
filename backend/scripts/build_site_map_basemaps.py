#!/usr/bin/env python3
"""Reconstruit les fonds de carte embarqués de la cartographie des sites.

Les trois images de `data/maps/` sont livrées AVEC le code : l'export Word ne
télécharge rien au moment où on le demande (le serveur de prod est derrière un
FortiGate et n'a pas d'accès Internet sortant garanti — un export qui dépend
d'un CDN échouerait le jour où on en a besoin).

Ce script est donc l'outil de MAINTENANCE de ces images, à lancer depuis un
poste qui a Internet, puis à commiter :

    python scripts/build_site_map_basemaps.py                 # les 3 villes
    python scripts/build_site_map_basemaps.py --city rosso    # une seule

À relancer quand — et seulement quand — il faut élargir le cadrage d'une ville
(un site posé hors fenêtre est refusé par `site_map_service` et NOMMÉ dans le
document, ce qui est le signal). Modifier alors la fenêtre dans `_WINDOWS`
ci-dessous, relancer, vérifier l'image, commiter les deux.

⚠️ Le fichier `bounds.json` produit ici est le contrat avec le service : il
porte la fenêtre géographique ET les dimensions en pixels de chaque image. Les
deux doivent rester d'accord, sinon les sites se posent à côté de leur vraie
place — sans que rien n'échoue. C'est pourquoi il est ÉCRIT PAR CE SCRIPT et
jamais à la main.

Tuiles : CARTO « voyager » sur données OpenStreetMap. Les deux mentions de
source sont dessinées dans chaque planche par le service (exigence de licence).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

_OUT = Path(__file__).resolve().parents[1] / "data" / "maps"
_TILE_PX = 256
_HEADERS = {"User-Agent": "a2-network-supervisor/1.0 (site map basemaps)"}

# Imagerie SATELLITE + calque de repères (routes et noms de lieux), la
# combinaison « hybride » d'Esri. ⚠️ L'URL Esri est en `{z}/{y}/{x}` — l'ordre
# est inversé par rapport à la convention XYZ de tout le reste du monde, et
# l'intervertir rend des tuiles d'un autre continent sans jamais échouer.
_IMAGERY_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
_REFERENCE_URL = (
    "https://services.arcgisonline.com/ArcGIS/rest/services/"
    "Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}"
)
_ATTRIBUTION = "© Esri · Maxar · Earthstar Geographics"

# Fenêtre géographique et zoom de chaque planche.
#
# Le zoom fixe la résolution : à z14 une tuile de 256 px couvre 0,0220° de
# longitude, donc une ville de 0,18° tient dans ~2000 px — assez pour que les
# noms de site restent lisibles sur une page A4, sans faire un fichier énorme.
_WINDOWS: dict[str, tuple[float, float, float, float, int]] = {
    #            west,      south,    east,      north,   zoom
    "nouakchott": (-16.058, 17.962, -15.882, 18.166, 14),
    "nouadhibou": (-17.088, 20.885, -16.983, 21.000, 14),
    "rosso": (-15.818, 16.495, -15.776, 16.531, 15),
}


def _lon_to_px(lon: float, zoom: int) -> float:
    return (lon + 180.0) / 360.0 * (2**zoom) * _TILE_PX


def _lat_to_px(lat: float, zoom: int) -> float:
    s = math.sin(math.radians(lat))
    return (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * (2**zoom) * _TILE_PX


def _fetch_tile(url: str, layer: str, zoom: int, x: int, y: int,
                cache: Path) -> Image.Image:
    path = cache / f"{layer}_{zoom}_{x}_{y}.bin"
    if not path.exists():
        request = urllib.request.Request(
            url.format(z=zoom, x=x, y=y), headers=_HEADERS
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            path.write_bytes(response.read())
        time.sleep(0.05)  # courtoisie envers le fournisseur de tuiles
    return Image.open(path)


def _tile(zoom: int, x: int, y: int, cache: Path) -> Image.Image:
    """Une tuile satellite, avec les repères Esri composés par-dessus.

    Le calque de repères porte les grands axes et les noms de quartiers. Sur de
    l'imagerie brute, ce sont eux qui permettent de situer un site autrement
    qu'en reconnaissant la forme des toits — un plan sans aucun texte se lit mal
    dès qu'on n'est pas du quartier.
    """
    base = _fetch_tile(_IMAGERY_URL, "img", zoom, x, y, cache).convert("RGBA")
    try:
        marks = _fetch_tile(_REFERENCE_URL, "ref", zoom, x, y, cache).convert("RGBA")
    except OSError:
        return base.convert("RGB")  # repères indisponibles : l'imagerie suffit
    return Image.alpha_composite(base, marks).convert("RGB")


def build(city: str, cache: Path) -> dict:
    west, south, east, north, zoom = _WINDOWS[city]
    left, right = _lon_to_px(west, zoom), _lon_to_px(east, zoom)
    top, bottom = _lat_to_px(north, zoom), _lat_to_px(south, zoom)

    tx0, tx1 = int(left // _TILE_PX), int(right // _TILE_PX)
    ty0, ty1 = int(top // _TILE_PX), int(bottom // _TILE_PX)
    canvas = Image.new(
        "RGB", ((tx1 - tx0 + 1) * _TILE_PX, (ty1 - ty0 + 1) * _TILE_PX)
    )
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            canvas.paste(
                _tile(zoom, tx, ty, cache),
                ((tx - tx0) * _TILE_PX, (ty - ty0) * _TILE_PX),
            )

    crop = canvas.crop(
        (
            int(left - tx0 * _TILE_PX),
            int(top - ty0 * _TILE_PX),
            int(right - tx0 * _TILE_PX),
            int(bottom - ty0 * _TILE_PX),
        )
    )
    # JPEG et pas PNG : un fond de carte est une photo, la compression sans
    # perte le ferait peser cinq fois plus pour rien. Le dessin par-dessus
    # (pastilles, traits, noms) est vectoriel et composé après.
    target = _OUT / f"{city}.jpg"
    crop.save(target, quality=80, optimize=True, progressive=True)
    return {
        "name": city,
        "w": crop.width,
        "h": crop.height,
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "z": zoom,
        "attribution": _ATTRIBUTION,
        "bytes": target.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--city", choices=sorted(_WINDOWS), help="ne reconstruire qu'une ville"
    )
    parser.add_argument(
        "--cache",
        default=str(Path(__file__).resolve().parent / ".tilecache"),
        help="dossier de cache des tuiles (évite de les retélécharger)",
    )
    args = parser.parse_args()

    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    _OUT.mkdir(parents=True, exist_ok=True)

    bounds_path = _OUT / "bounds.json"
    existing = {}
    if bounds_path.exists():
        existing = {e["name"]: e for e in json.loads(bounds_path.read_text("utf-8"))}

    cities = [args.city] if args.city else list(_WINDOWS)
    for city in cities:
        meta = build(city, cache)
        existing[city] = meta
        print(
            f"{city:12s} {meta['w']}x{meta['h']} px  "
            f"{meta['bytes'] / 1024:.0f} Ko  z{meta['z']}"
        )

    # Ordre stable : deux exécutions produisent le même fichier, donc un diff
    # git qui ne montre que ce qui a réellement changé.
    ordered = [existing[name] for name in _WINDOWS if name in existing]
    bounds_path.write_text(
        json.dumps(ordered, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\n{bounds_path} mis à jour ({len(ordered)} planches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
