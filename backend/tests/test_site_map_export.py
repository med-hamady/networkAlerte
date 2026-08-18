"""Cartographie des sites → document Word.

Ce que ces tests verrouillent, et pourquoi ça compte :

1. **Un site qu'on ne peut pas dessiner est NOMMÉ, jamais escamoté.** Le fond de
   carte est embarqué, donc son cadrage est figé : un site sans coordonnées, ou
   posé hors de la fenêtre d'une ville, ne peut pas être tracé. Le passer sous
   silence produirait un document qui se lit comme un réseau qui n'a pas ce
   site — la même règle que la vue carte de `/topology` et ses `outliers`.

2. **Une liaison dont un bout n'est pas traçable est ignorée sans exploser.**
   C'est le corollaire du point 1 : le jour où un site perd sa position, l'export
   doit rendre une carte amputée et honnête, pas une 500.

3. **Les noms de site ne se posent pas SUR les liaisons.** Sans cette règle, le
   placement met tout à l'est et masque les backhauls — c'est-à-dire l'objet
   même de la carte. Le test le vérifie sur une géométrie où la pose « à l'est »
   couvrirait le trait.

4. **Les villes transcrites ne contaminent pas la planche vivante.** Nouadhibou
   et Rosso sont des constantes reportées d'un plan (hors supervision) ; elles
   ne doivent jamais apparaître sur la planche de Nouakchott, qui n'affiche que
   ce que la base contient.
"""

import io
import zipfile

import pytest

from app.services import site_map_service as sms


def _bounds():
    return sms._load_bounds()["nouakchott"]


def _topo(sites, edges=(), synced="2026-08-17T09:55:20+00:00"):
    return {
        "available": True,
        "synced_at": synced,
        "sites": [
            {"site": name, "latitude": lat, "longitude": lon}
            for name, lat, lon in sites
        ],
        "edges": [
            {"site_a": a, "site_b": b, "medium": medium, "is_tree_edge": tree}
            for a, b, medium, tree in edges
        ],
    }


# ---------------------------------------------------------------- assets
def test_shipped_basemaps_and_bounds_are_present():
    """Les trois fonds de carte sont livrés avec le code, pas téléchargés."""
    bounds = sms._load_bounds()
    for plate in sms._PLATES:
        assert plate.key in bounds, f"fenêtre géographique absente : {plate.key}"
        asset = sms._ASSETS / f"{plate.key}.jpg"
        assert asset.exists(), f"fond de carte absent : {asset}"
        window = bounds[plate.key]
        assert window["west"] < window["east"]
        assert window["south"] < window["north"]


# ------------------------------------------------- sites non représentables
def test_site_without_position_is_reported_not_dropped():
    topo = _topo([("A2 HQ", 18.114964, -15.991145), ("A2 NEUF", None, None)])
    sites, _edges, missing = sms._plate_data(sms._PLATES[0], topo, _bounds())

    assert [name for name, _, _ in sites] == ["A2 HQ"]
    assert len(missing) == 1
    assert "A2 NEUF" in missing[0]
    assert "position inconnue" in missing[0]


def test_site_outside_the_frozen_frame_is_reported_not_dropped():
    """Nouadhibou est à 500 km : hors du cadrage de la planche de Nouakchott."""
    topo = _topo([("A2 HQ", 18.114964, -15.991145), ("A2 NDB", 20.94, -17.03)])
    sites, _edges, missing = sms._plate_data(sms._PLATES[0], topo, _bounds())

    assert [name for name, _, _ in sites] == ["A2 HQ"]
    assert len(missing) == 1
    assert "hors du cadrage" in missing[0]


def test_edge_touching_an_undrawable_site_is_skipped():
    topo = _topo(
        [("A2 HQ", 18.114964, -15.991145), ("A2 NEUF", None, None)],
        [("A2 HQ", "A2 NEUF", "wireless", True)],
    )
    _sites, edges, _missing = sms._plate_data(sms._PLATES[0], topo, _bounds())
    assert edges == []


# ----------------------------------------------------------- nature du trait
@pytest.mark.parametrize(
    "medium,tree,expected_dashed",
    [("wired", True, False), ("wireless", True, True), ("wireless", False, True)],
)
def test_wired_links_are_solid_and_radio_links_are_dashed(medium, tree, expected_dashed):
    topo = _topo(
        [("A2 HQ", 18.114964, -15.991145), ("A2 AT1", 18.140022, -15.919665)],
        [("A2 HQ", "A2 AT1", medium, tree)],
    )
    _sites, edges, _missing = sms._plate_data(sms._PLATES[0], topo, _bounds())
    assert edges[0][2] is expected_dashed
    assert edges[0][3] is tree


# ------------------------------------------------------- placement des noms
def test_label_is_not_placed_on_top_of_a_link():
    """Le nom cède le passage au trait — sinon la carte masque ce qu'elle montre.

    Géométrie choisie pour que la pose « à l'est » (le premier côté essayé)
    tombe en plein sur la liaison : deux sites alignés horizontalement, le nom
    du site de gauche partirait droit sur le trait qui les joint.
    """
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGBA", (1200, 600)))
    font = sms._font(34)
    r = 60.0
    items = [("A2 GAUCHE", 200.0, 300.0), ("A2 DROITE", 1000.0, 300.0)]
    segments = [((200.0, 300.0), (1000.0, 300.0))]

    placed = dict(
        sms._place_labels(items, r, font, draw, (1200.0, 600.0), segments)
    )
    box = placed["A2 GAUCHE"]

    on_the_link = any(
        box[0] <= x <= box[2] and box[1] <= y <= box[3]
        for x, y in sms._segment_samples(segments, r * 0.35)
    )
    assert not on_the_link, "le nom du site a été posé sur la liaison"


def test_label_placement_is_deterministic():
    """Deux exports des mêmes données rendent exactement la même image."""
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGBA", (1200, 900)))
    font = sms._font(34)
    items = [("A2 UN", 300.0, 300.0), ("A2 DEUX", 700.0, 350.0), ("A2 TROIS", 500.0, 700.0)]
    segments = [((300.0, 300.0), (700.0, 350.0)), ((700.0, 350.0), (500.0, 700.0))]

    first = sms._place_labels(items, 60.0, font, draw, (1200.0, 900.0), segments)
    second = sms._place_labels(items, 60.0, font, draw, (1200.0, 900.0), segments)
    assert first == second


# ------------------------------------------------- cloisonnement des villes
def test_transcribed_cities_never_leak_into_the_live_plate():
    """La planche de Nouakchott n'affiche QUE ce que la base contient."""
    topo = _topo([("A2 HQ", 18.114964, -15.991145)])
    sites, _edges, _missing = sms._plate_data(sms._PLATES[0], topo, _bounds())
    names = {name for name, _, _ in sites}

    for city in sms._TRANSCRIBED.values():
        for name, _lat, _lon in city["sites"]:
            assert name not in names


def test_transcribed_cities_ignore_the_database_entirely():
    """À l'inverse : une topologie vide ne vide pas Nouadhibou ni Rosso.

    Ces sites ne sont pas supervisés — leur absence de la base est l'état
    NORMAL, pas un signal qu'ils ont disparu.
    """
    empty = _topo([])
    for plate in sms._PLATES[1:]:
        bounds = sms._load_bounds()[plate.key]
        sites, edges, missing = sms._plate_data(plate, empty, bounds)
        assert sites, f"{plate.key} devrait rester dessinée sans la base"
        assert edges
        assert missing == []


def test_transcribed_sites_sit_inside_their_own_frame():
    """Chaque site transcrit tombe dans la fenêtre de sa ville.

    Rosso est le cas qui a mordu : une latitude prise trop au sud plaçait le
    site de l'autre côté du fleuve Sénégal, donc en territoire sénégalais.
    """
    bounds_by_key = sms._load_bounds()
    for key, cfg in sms._TRANSCRIBED.items():
        bounds = bounds_by_key[key]
        for name, lat, lon in cfg["sites"]:
            assert sms._inside(bounds, lat, lon), f"{name} hors de la fenêtre {key}"


def test_rosso_sites_stay_north_of_the_senegal_river():
    """Rosso Mauritanie est au NORD du fleuve ; Rosso Sénégal est l'autre ville.

    Le fleuve passe vers 16,505° N à hauteur de la ville. Un site sous cette
    latitude n'est pas chez nous — erreur commise puis corrigée le 2026-08-18.
    """
    for name, lat, _lon in sms._TRANSCRIBED["rosso"]["sites"]:
        assert lat > 16.505, f"{name} est tombé au sud du fleuve (Sénégal)"


# ------------------------------------------------------------------ export
def test_docx_carries_one_page_per_city():
    topo = _topo(
        [("A2 HQ", 18.114964, -15.991145), ("A2 AT1", 18.140022, -15.919665)],
        [("A2 HQ", "A2 AT1", "wired", True)],
    )
    data = sms.build_topology_docx(topo)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        document = archive.read("word/document.xml").decode("utf-8")

    images = [n for n in names if n.startswith("word/media/")]
    assert len(images) == len(sms._PLATES)
    assert document.count('w:type="page"') == len(sms._PLATES) - 1
    assert "Cartographie des sites A2 Holding" in document
    assert "17/08/2026" in document


def test_docx_names_the_sites_it_could_not_draw():
    topo = _topo([("A2 HQ", 18.114964, -15.991145), ("A2 NEUF", None, None)])
    data = sms.build_topology_docx(topo)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")

    assert "A2 NEUF" in document
    assert "non repr" in document  # « Sites non représentés : … »
