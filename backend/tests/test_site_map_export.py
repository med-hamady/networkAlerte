"""Cartographie des sites → document Word.

Ce que ces tests verrouillent, et pourquoi ça compte :

1. **Un site qu'on ne peut pas dessiner est NOMMÉ, jamais escamoté.** Le fond de
   carte est embarqué, donc son cadrage est figé : un site sans coordonnées, ou
   posé hors de la fenêtre d'une ville, ne peut pas être tracé. Le passer sous
   silence produirait un document qui se lit comme un réseau qui n'a pas ce
   site — la même règle que la vue carte de `/topology` et ses `outliers`.

2. **Vert = en service, rouge = programmé.** C'est le message principal de la
   carte, et il ne s'appuie sur aucune légende : si la couleur cessait de dire
   l'état, le document annoncerait comme installés des mâts qui ne sont pas
   montés. Les deux compteurs restent séparés jusqu'au titre de chaque planche.

3. **Les sites programmés ne touchent JAMAIS la base.** Ils n'existent pas
   encore ; les écrire en base les ferait compter dans la capacité, pinguer, et
   alerter comme injoignables. Ils vivent dans une constante et n'en sortent
   qu'au moment du dessin.

4. **Les noms de site ne se posent pas SUR les liaisons.** Sans cette règle, le
   placement met tout à l'est et masque les backhauls — c'est-à-dire l'objet
   même de la carte.
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


def _names(sites):
    return {name for name, _lat, _lon, _planned in sites}


# ---------------------------------------------------------------- assets
def test_shipped_basemaps_and_bounds_are_present():
    """Les trois fonds de carte sont livrés avec le code, pas téléchargés."""
    bounds = sms._load_bounds()
    for plate in sms._PLATES:
        assert plate.key in bounds, f"fenêtre géographique absente : {plate.key}"
        assert (sms._ASSETS / f"{plate.key}.jpg").exists()
        window = bounds[plate.key]
        assert window["west"] < window["east"]
        assert window["south"] < window["north"]
        # La mention de source voyage AVEC l'image : c'est elle qui est dessinée
        # sur la planche, donc changer de fournisseur d'imagerie sans changer
        # l'attribution devient impossible.
        assert window.get("attribution"), f"attribution absente : {plate.key}"


# ------------------------------------------------- sites non représentables
def test_site_without_position_is_reported_not_dropped():
    topo = _topo([("A2 HQ", 18.114964, -15.991145), ("A2 NEUF", None, None)])
    sites, _edges, missing = sms._plate_data(sms._PLATES[0], topo, _bounds())

    assert "A2 HQ" in _names(sites)
    assert "A2 NEUF" not in _names(sites)
    assert len(missing) == 1
    assert "A2 NEUF" in missing[0] and "position inconnue" in missing[0]


def test_site_outside_the_frozen_frame_is_reported_not_dropped():
    """Nouadhibou est à 500 km : hors du cadrage de la planche de Nouakchott."""
    topo = _topo([("A2 HQ", 18.114964, -15.991145), ("A2 NDB", 20.94, -17.03)])
    sites, _edges, missing = sms._plate_data(sms._PLATES[0], topo, _bounds())

    assert "A2 NDB" not in _names(sites)
    assert len(missing) == 1 and "hors du cadrage" in missing[0]


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
    live = [e for e in edges if {e[0], e[1]} == {"A2 HQ", "A2 AT1"}]
    assert live and live[0][2] is expected_dashed
    assert live[0][3] is tree


# -------------------------------------------------- installé vs programmé
def test_installed_sites_are_green_and_planned_sites_are_red():
    """La couleur dit l'ÉTAT — c'est tout le message de la carte."""
    assert sms._PIN_INSTALLED == sms._GREEN
    assert sms._PIN_PLANNED == sms._RED
    assert sms._PIN_INSTALLED != sms._PIN_PLANNED


def test_the_live_plate_carries_both_populations():
    """Nouakchott montre l'existant ET ce qui manque, sur le même plan."""
    topo = _topo([("A2 HQ", 18.114964, -15.991145)])
    sites, _edges, _missing = sms._plate_data(sms._PLATES[0], topo, _bounds())

    installed = {n for n, _la, _lo, planned in sites if not planned}
    planned = {n for n, _la, _lo, planned in sites if planned}

    assert installed == {"A2 HQ"}
    assert planned == {n for n, _la, _lo in sms._PLANNED["nouakchott"]["sites"]}


def test_a_planned_site_that_went_live_is_not_drawn_twice():
    """Mis en service, il est déjà en vert : on ne le redouble pas en rouge.

    C'est ce qui rend le retrait de sa ligne de `_PLANNED` facultatif le jour
    de sa mise en service — l'oubli ne produit pas une carte fausse.
    """
    name, lat, lon = sms._PLANNED["nouakchott"]["sites"][0]
    topo = _topo([(name, lat, lon)])
    sites, _edges, _missing = sms._plate_data(sms._PLATES[0], topo, _bounds())

    matching = [s for s in sites if s[0] == name]
    assert len(matching) == 1
    assert matching[0][3] is False, "le site en service doit rester vert"


def test_no_link_is_invented_from_a_planned_nouakchott_site():
    """Leur raccordement n'est pas arrêté : en dessiner un inventerait la topologie."""
    assert sms._PLANNED["nouakchott"]["edges"] == []

    topo = _topo([("A2 HQ", 18.114964, -15.991145)])
    _sites, edges, _missing = sms._plate_data(sms._PLATES[0], topo, _bounds())
    planned_names = {n for n, _la, _lo in sms._PLANNED["nouakchott"]["sites"]}
    for a, b, _dashed, _tree in edges:
        assert a not in planned_names and b not in planned_names


# ------------------------------------------------- cloisonnement des villes
def test_a_city_never_leaks_into_another_plate():
    topo = _topo([("A2 HQ", 18.114964, -15.991145)])
    sites, _edges, _missing = sms._plate_data(sms._PLATES[0], topo, _bounds())
    names = _names(sites)

    for key in ("nouadhibou", "rosso"):
        for name, _lat, _lon in sms._PLANNED[key]["sites"]:
            assert name not in names


def test_planned_cities_ignore_the_database_entirely():
    """Une topologie vide ne vide pas Nouadhibou ni Rosso.

    Ces sites n'existent pas encore — leur absence de la base est leur état
    NORMAL, pas un signal qu'ils ont disparu.
    """
    empty = _topo([])
    for plate in sms._PLATES[1:]:
        bounds = sms._load_bounds()[plate.key]
        sites, edges, missing = sms._plate_data(plate, empty, bounds)
        assert sites, f"{plate.key} devrait rester dessinée sans la base"
        assert all(planned for _n, _la, _lo, planned in sites)
        assert edges
        assert missing == []


def test_planned_sites_sit_inside_their_own_frame():
    """Chaque site programmé tombe dans la fenêtre de sa ville."""
    bounds_by_key = sms._load_bounds()
    for key, cfg in sms._PLANNED.items():
        bounds = bounds_by_key[key]
        for name, lat, lon in cfg["sites"]:
            assert sms._inside(bounds, lat, lon), f"{name} hors de la fenêtre {key}"


def test_rosso_sites_stay_north_of_the_senegal_river():
    """Rosso Mauritanie est au NORD du fleuve ; Rosso Sénégal est l'autre ville.

    Le fleuve passe vers 16,505° N à hauteur de la ville. Un site sous cette
    latitude n'est pas chez nous — erreur commise puis corrigée le 2026-08-18.
    """
    for name, lat, _lon in sms._PLANNED["rosso"]["sites"]:
        assert lat > 16.505, f"{name} est tombé au sud du fleuve (Sénégal)"


def test_rosso_sites_are_aligned_horizontally():
    """Les deux mâts se répondent d'ouest en est, pas du nord au sud."""
    lats = [lat for _n, lat, _lon in sms._PLANNED["rosso"]["sites"]]
    lons = [lon for _n, _lat, lon in sms._PLANNED["rosso"]["sites"]]
    assert max(lats) - min(lats) < 0.001, "les sites de Rosso ne sont plus alignés"
    assert max(lons) - min(lons) > 0.005, "les sites de Rosso se chevauchent"


def test_nouadhibou_sites_stay_on_land():
    """NDB-SUD tombait dans la baie : une pastille sur l'eau se voit tout de suite.

    La ville tient dans une bande étroite de la presqu'île ; on borne la
    longitude à cette bande plutôt qu'à la fenêtre de la planche, qui contient
    beaucoup d'océan des deux côtés.
    """
    for name, _lat, lon in sms._PLANNED["nouadhibou"]["sites"]:
        assert -17.048 < lon < -17.018, f"{name} est hors de la bande urbaine"


# --------------------------------------------------- arrivée Internet
def test_every_feed_points_at_a_site_that_exists_on_its_plate():
    """Une cible mal orthographiée ne dessinerait RIEN, et sans rien dire.

    Le cartouche et son câble ne sont tracés que si le site d'entrée est sur la
    planche ; une faute de frappe dans `target` les ferait disparaître en
    silence, sur la seule information de la carte qui ne vient pas d'une mesure.
    """
    for key, feeds in sms._FEEDS.items():
        plate = next(p for p in sms._PLATES if p.key == key)
        topo = _topo([("A2 HQ", 18.114964, -15.991145)])
        sites, _edges, _missing = sms._plate_data(plate, topo, sms._load_bounds()[key])
        names = _names(sites)
        for feed in feeds:
            assert feed["target"] in names, f"{key} : cible « {feed['target']} » absente"


def test_nouakchott_feed_enters_through_the_headquarters():
    """Le siège est la tête de réseau — c'est ce que la carte doit montrer."""
    targets = [feed["target"] for feed in sms._FEEDS["nouakchott"]]
    assert targets == ["A2 HQ"]


def test_feed_cartouche_is_kept_inside_the_plate():
    """Sa position est visée à la main, sa taille dépend du texte et de la police.

    Sans recadrage, un mot de plus dans le libellé sortirait la moitié du
    cartouche hors de l'image — et personne ne le verrait avant l'impression.
    """
    from PIL import Image, ImageDraw

    bounds_by_key = sms._load_bounds()
    for plate in sms._PLATES:
        bounds = bounds_by_key[plate.key]
        canvas = (bounds["w"] * sms._SS, bounds["h"] * sms._SS)
        draw = ImageDraw.Draw(Image.new("RGBA", (int(canvas[0]), int(canvas[1]))))
        # Cible fictive au centre : on ne teste que le cadrage du cartouche.
        anchors = {
            feed["target"]: (canvas[0] / 2, canvas[1] / 2)
            for feed in sms._FEEDS.get(plate.key, ())
        }
        feeds = sms._feed_geometry(
            sms._FEEDS.get(plate.key, ()), bounds, plate.scale * sms._SS, draw, anchors
        )
        for feed in feeds:
            box = feed["box"]
            assert box[0] >= 0 and box[1] >= 0, f"{plate.key} : cartouche hors cadre"
            assert box[2] <= canvas[0] and box[3] <= canvas[1]


def test_feed_without_its_target_site_draws_nothing():
    """Rien à raccorder ⇒ pas de câble en l'air pointant vers le vide."""
    from PIL import Image, ImageDraw

    bounds = _bounds()
    draw = ImageDraw.Draw(Image.new("RGBA", (100, 100)))
    feeds = sms._feed_geometry(sms._FEEDS["nouakchott"], bounds, 1.0, draw, {})
    assert feeds == []


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

    placed = dict(sms._place_labels(items, r, font, draw, (1200.0, 600.0), segments))
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


# ------------------------------------------------------------ paragraphes
def test_paragraphs_count_what_the_map_actually_shows():
    """Les chiffres du texte sortent des planches, jamais d'une valeur en dur.

    Un document dont le texte contredit sa propre carte ne serait plus lu.
    """
    topo = _topo([("A2 HQ", 18.114964, -15.991145)])
    plates, _missing = sms.render_plates(topo)
    existing, extension = sms._network_paragraphs(plates)

    installed = sum(count for _p, _png, count, _planned in plates)
    planned = sum(count for _p, _png, _installed, count in plates)

    assert f"{installed} sites d'infrastructure" in existing
    assert f"{planned} nouveaux sites" in extension
    assert "rouge" in extension


# ------------------------------------------------------------------ export
def test_docx_carries_one_page_per_city_and_both_paragraphs():
    topo = _topo(
        [("A2 HQ", 18.114964, -15.991145), ("A2 AT1", 18.140022, -15.919665)],
        [("A2 HQ", "A2 AT1", "wired", True)],
    )
    data = sms.build_topology_docx(topo)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        document = archive.read("word/document.xml").decode("utf-8")

    assert len([n for n in names if n.startswith("word/media/")]) == len(sms._PLATES)
    assert document.count('w:type="page"') == len(sms._PLATES) - 1
    assert "Cartographie des sites A2 Holding" in document
    assert "17/08/2026" in document
    assert "bonne capacit" in document      # paragraphe « réseau en service »
    assert "programm" in document           # paragraphe « extensions »


def test_docx_names_the_sites_it_could_not_draw():
    topo = _topo([("A2 HQ", 18.114964, -15.991145), ("A2 NEUF", None, None)])
    data = sms.build_topology_docx(topo)

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        document = archive.read("word/document.xml").decode("utf-8")

    assert "A2 NEUF" in document
    assert "non repr" in document  # « Sites non représentés : … »
