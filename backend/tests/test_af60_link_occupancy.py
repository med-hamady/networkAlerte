"""Occupation d'un backhaul AF60 — le temps d'antenne consommé.

Ce que ces tests verrouillent, et pourquoi ça compte : l'occupation est la SOMME
des fractions de temps d'antenne des deux sens, parce que le lien est TDD (une
seule porteuse 60 GHz de 2160 MHz, les deux sens se partagent le temps). Le
réflexe naturel — `débit_total / total_capacity_mbps` — est FAUX, et rien ne le
signalerait : les deux formules donnent des nombres plausibles et proches sur un
lien peu chargé. C'est précisément quand le lien se remplit qu'elles divergent.

La légitimité du quotient a été établie en mesurant l'équipement le 2026-08-12
(10.135.80.1) : `capacity.dl/ul` a déjà le rendement MAC de 0,78 appliqué (MCS 6
1540×0,78=1201, MCS 8 2310×0,78=1801, MCS 9 2502,5×0,78=1951, et
`capacity.dlIdeal = 1951950` garde la décimale), donc c'est du goodput, la même
grandeur que les compteurs de `wlan0`. Le même relevé a montré que le firmware
n'expose AUCUN airtime : la dérivation n'est pas un raccourci, c'est le seul
chemin.
"""

import json
import types
from pathlib import Path

import pytest

from app.core.alert_constants import (
    AT_AF60_LINK_SATURATED,
    KNOWN_ALERT_TYPES,
    WHATSAPP_ALERT_TYPES,
)
from app.services.af60_api_service import METRIC_UNITS, parse_af60_metrics
from app.services.alert_policy import get_policy
from app.services.alert_rules import (
    Af60LinkSaturatedRule,
    get_failure_threshold,
    get_rules_for_device,
)
from app.services.lr_metric_history_service import GRAPH_METRICS
from app.services.site_topology_service import (
    EDGE_METRICS,
    edge_health,
    edge_occupancy,
    site_occupancy_map,
)
from app.services.threshold_service import THRESHOLD_SCHEMA

FIXTURE = Path(__file__).parent / "fixtures" / "af60_statistics.json"


@pytest.fixture
def raw() -> dict:
    data = json.loads(FIXTURE.read_text())
    return data[0] if isinstance(data, list) else data


def make_settings(**overrides):
    defaults = dict(
        af60_occupancy_warning_pct=75.0,
        af60_occupancy_critical_pct=90.0,
        af60_occupancy_failure_threshold=3,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# La formule
# ---------------------------------------------------------------------------

def test_occupation_derivee_du_lien_reel(raw):
    """Sur la capture réelle : dl 66,1/600 + ul 3,362/975 = 11,36 %."""
    m = parse_af60_metrics(raw)
    assert m["dl_capacity_mbps"] == 600.0
    assert m["ul_capacity_mbps"] == 975.0
    assert m["dl_throughput_mbps"] == 66.1
    assert m["ul_throughput_mbps"] == 3.362
    assert m["link_occupancy_pct"] == pytest.approx(11.36, abs=0.01)


def test_c_est_la_somme_des_deux_sens_pas_le_ratio_des_totaux(raw):
    """Le réflexe `débit_total / total_capacity_mbps` est une AUTRE grandeur.

    `total_capacity_mbps` est la MOYENNE des deux sens (l'agrégat à un partage
    50/50), pas leur somme. Sur cette capture les deux formules donnent 11,36 %
    contre 8,82 % — 29 % d'écart relatif, sur des valeurs toutes deux
    plausibles. Aucun test ne verrait la substitution sans celui-ci.
    """
    m = parse_af60_metrics(raw)
    naif = 100.0 * (m["dl_throughput_mbps"] + m["ul_throughput_mbps"]) / m["total_capacity_mbps"]
    assert naif == pytest.approx(8.82, abs=0.01)
    assert m["link_occupancy_pct"] != pytest.approx(naif, abs=0.5)


def test_le_ratio_naif_peut_depasser_100_pct_donc_il_est_faux(raw):
    """Preuve par l'absurde, avec les capacités réellement mesurées.

    Sur le lien du 2026-08-12 (dl_cap 1801 / ul_cap 1201), un trafic de
    1800 Mb/s en descendant et nul en montant tient dans le tuyau : le sens
    descendant peut porter 1801. Le ratio naïf rend pourtant 120 % — une
    occupation au-dessus de 100 % n'a pas de sens physique. La somme des temps
    d'antenne rend 99,9 %, correct.
    """
    dl_cap, ul_cap, dl_thr, ul_thr = 1801.0, 1201.0, 1800.0, 0.0
    naif = 100.0 * (dl_thr + ul_thr) / ((dl_cap + ul_cap) / 2)
    airtime = 100.0 * (dl_thr / dl_cap + ul_thr / ul_cap)
    assert naif > 100.0
    assert airtime == pytest.approx(99.94, abs=0.01)


def test_une_valeur_manquante_laisse_un_trou_jamais_un_zero(raw):
    """Débit non relevé ⇒ clé absente. « Pas mesuré » n'est pas « au repos »."""
    for iface in raw.get("interfaces", []):
        if iface.get("id") == "wlan0":
            iface["statistics"] = {}
    m = parse_af60_metrics(raw)
    assert m["dl_throughput_mbps"] is None
    assert m["link_occupancy_pct"] is None


def test_capacite_nulle_ne_divise_pas_par_zero(raw):
    """Lien sans modulation négociée : le quotient n'a pas de sens."""
    lq = raw["wireless"]["peers"][0]["local"][0]["linkQuality"]
    lq["capacity"]["dl"] = 0
    m = parse_af60_metrics(raw)
    assert m["link_occupancy_pct"] is None


def test_au_dessus_de_100_pct_n_est_pas_ecrete(raw):
    """Une capacité périmée par rapport au trafic est une INFO de diagnostic.

    La capacité saute par crans de MCS pendant que le débit est continu : un
    dépassement transitoire est possible et doit rester lisible. L'écrêter à 100
    détruirait le seul indice disant que les deux opérandes ne sont plus
    synchrones — et le lien est saturé dans les deux cas.
    """
    for iface in raw.get("interfaces", []):
        if iface.get("id") == "wlan0":
            iface["statistics"]["rxRate"] = 900_000_000  # 900 Mb/s pour 600 de capacité
    m = parse_af60_metrics(raw)
    assert m["link_occupancy_pct"] > 100.0


def test_les_deux_parts_disent_par_quel_bout_le_lien_se_remplit(raw):
    """Le total sature, mais ce sont les parts qui rendent l'info actionnable.

    Sur la capture, 11,36 % de temps d'antenne dont 11,02 en réception : le lien
    se remplit presque uniquement dans un sens. Un total seul ne distingue pas
    ce cas d'un 5,7/5,7 symétrique, qui n'appelle pas le même geste.
    """
    m = parse_af60_metrics(raw)
    assert m["link_occupancy_dl_pct"] == pytest.approx(11.02, abs=0.01)   # 66,1/600
    assert m["link_occupancy_ul_pct"] == pytest.approx(0.34, abs=0.01)    # 3,362/975
    # Les parts SOMMENT au total — c'est la définition même du temps d'antenne.
    assert m["link_occupancy_dl_pct"] + m["link_occupancy_ul_pct"] == pytest.approx(
        m["link_occupancy_pct"], abs=0.02
    )


def test_une_part_manquante_n_invente_pas_de_repartition(raw):
    """Pas de débit relevé ⇒ ni total ni parts. Aucune n'est mise à 0."""
    for iface in raw.get("interfaces", []):
        if iface.get("id") == "wlan0":
            iface["statistics"] = {}
    m = parse_af60_metrics(raw)
    assert m["link_occupancy_dl_pct"] is None
    assert m["link_occupancy_ul_pct"] is None
    assert m["link_occupancy_pct"] is None


def test_les_parts_n_ont_pas_de_seuil_car_c_est_leur_somme_qui_sature():
    """Le lien est TDD : les deux sens se partagent le MÊME temps d'antenne.

    Tracer la ligne des 90 % sur une part isolée ferait croire qu'un descendant
    à 89 % est au bord de la rupture — alors que le lien peut déjà être à 91 %
    au total (donc en alerte), ou à 89 % seulement (donc pas encore). Seul le
    total porte un seuil.
    """
    assert GRAPH_METRICS["link_occupancy_pct"]["threshold_setting"] is not None
    for key in ("link_occupancy_dl_pct", "link_occupancy_ul_pct"):
        assert GRAPH_METRICS[key]["threshold_setting"] is None
        assert GRAPH_METRICS[key]["threshold_direction"] is None


def test_le_message_d_alerte_nomme_le_sens_fautif():
    """Sans la répartition, l'opérateur ne sait pas où intervenir."""
    metrics = {
        "link_occupancy_pct": 94.0,
        "link_occupancy_dl_pct": 89.0,
        "link_occupancy_ul_pct": 5.0,
    }
    msg = Af60LinkSaturatedRule().evaluate("F60 CT1-NR1", metrics, make_settings()).message
    assert "descendant 89 %" in msg
    assert "montant 5 %" in msg


def test_sans_les_parts_le_message_se_replie_sur_le_total():
    """Un relevé partiel ne doit pas produire un message tronqué ni inventé."""
    msg = Af60LinkSaturatedRule().evaluate(
        "F60", {"link_occupancy_pct": 94.0}, make_settings()
    ).message
    assert "94 %" in msg
    assert "descendant" not in msg


def test_la_direction_est_nommee_par_les_sites_pas_par_dl_ul():
    """Sur une liaison, `dl` d'un bout est le `ul` de l'autre.

    `edge_occupancy` fait donc la même traduction que `edge_traffic` :
    A→B = ul de A = dl de B. L'inverser afficherait la charge à l'envers sur la
    moitié des liaisons, sans que rien n'échoue.
    """
    end_a = {"metrics": {"link_occupancy_pct": 94.0,
                         "link_occupancy_dl_pct": 5.0,     # A reçoit peu
                         "link_occupancy_ul_pct": 89.0}}   # A émet beaucoup
    end_b = {"metrics": {"link_occupancy_pct": 94.0,
                         "link_occupancy_dl_pct": 89.0,    # B reçoit beaucoup
                         "link_occupancy_ul_pct": 5.0}}
    occ = edge_occupancy(end_a, end_b)
    assert occ["total_pct"] == 94.0
    assert occ["a_to_b_pct"] == 89.0    # ul de A == dl de B, les deux d'accord
    assert occ["b_to_a_pct"] == 5.0


def test_un_seul_bout_qui_repond_suffit_a_donner_les_deux_sens():
    """Chaque équipement voit les DEUX directions — l'une en émission, l'autre
    en réception. Un bout muet ne doit donc pas priver la liaison de sa
    répartition."""
    end_a = {"metrics": {"link_occupancy_pct": 94.0,
                         "link_occupancy_dl_pct": 5.0,
                         "link_occupancy_ul_pct": 89.0}}
    occ = edge_occupancy(end_a, None)
    assert occ["a_to_b_pct"] == 89.0
    assert occ["b_to_a_pct"] == 5.0


def test_la_cle_est_declaree_dans_les_unites():
    """Sans entrée dans METRIC_UNITS la métrique n'est ni initialisée ni persistée."""
    assert METRIC_UNITS["link_occupancy_pct"] == "%"


# ---------------------------------------------------------------------------
# La règle
# ---------------------------------------------------------------------------

class TestAf60LinkSaturatedRule:
    rule = Af60LinkSaturatedRule()

    def test_au_dessus_du_seuil_critique(self):
        r = self.rule.evaluate("F60 CT1-NR1", {"link_occupancy_pct": 93.0}, make_settings())
        assert r.severity == "critical"
        assert r.metric_name == "link_occupancy_pct"
        assert r.threshold_value == 90.0

    def test_entre_les_deux_seuils(self):
        r = self.rule.evaluate("F60 CT1-NR1", {"link_occupancy_pct": 80.0}, make_settings())
        assert r.severity == "warning"
        assert r.threshold_value == 75.0

    def test_lien_fluide_resout(self):
        r = self.rule.evaluate("F60 CT1-NR1", {"link_occupancy_pct": 11.36}, make_settings())
        assert r.severity is None
        assert not r.skip

    def test_metrique_absente_ne_conclut_pas_au_repos(self):
        """skip, et surtout PAS une résolution : sans mesure on n'affirme rien.

        Résoudre ici fermerait l'incident d'un lien saturé dès que le poll rate
        un cycle — l'alerte s'éteindrait précisément quand l'équipement peine.
        """
        r = self.rule.evaluate("F60 CT1-NR1", {}, make_settings())
        assert r.skip is True
        assert r.severity is None

    def test_saturation_independante_de_la_capacite(self):
        """Un lien de PLEINE capacité peut être saturé — c'est tout l'intérêt.

        `af60_link_substandard` ne voit que les liens RÉTRÉCIS (capacité sous
        son plancher). Un backhaul à 1,9 Gb/s passe tous ses planchers et
        n'alerterait nulle part, alors qu'il peut être plein.
        """
        metrics = {"link_occupancy_pct": 95.0, "total_capacity_mbps": 1900.0}
        assert self.rule.evaluate("F60", metrics, make_settings()).severity == "critical"

    def test_montee_sur_les_af60_avec_anti_flap(self):
        types_ = [type(r).__name__ for r in get_rules_for_device("airfiber")]
        assert "Af60LinkSaturatedRule" in types_
        # Les deux opérandes sont bruités (débit ×1,7 entre deux relevés) : une
        # rafale ne doit jamais ouvrir d'incident.
        assert get_failure_threshold("af60_link_saturated", make_settings()) == 3


# ---------------------------------------------------------------------------
# Surfaces : courbe d'historique, seuils réglables, topologie
# ---------------------------------------------------------------------------

def test_la_courbe_trace_le_seuil_qui_declenche_l_alerte():
    """La ligne du graphe doit être celle de l'incident, pas un barème d'affichage."""
    spec = GRAPH_METRICS["link_occupancy_pct"]
    assert spec["threshold_setting"] == "af60_occupancy_critical_pct"
    assert spec["threshold_direction"] == "max"   # ici c'est le dépassement qui alerte
    assert spec["unit"] == "%"


def test_les_seuils_sont_reglables_depuis_la_page_seuils():
    for key in ("af60_occupancy_warning_pct", "af60_occupancy_critical_pct"):
        assert key in THRESHOLD_SCHEMA, f"{key} absent de THRESHOLD_SCHEMA"
        # La mesure n'étant pas écrêtée, le réglage doit pouvoir dépasser 100.
        assert THRESHOLD_SCHEMA[key]["max"] > 100


def test_la_saturation_ouvre_un_incident_mais_ne_notifie_pas():
    """Décision opérateur du 2026-08-12, à la mise en service.

    Le type ouvre/résout son incident, s'affiche sur /incidents et trace sa
    courbe, mais reste hors de la liste blanche WhatsApp : ses seuils n'ont pas
    encore d'historique terrain, et c'est le seul type dont la cause peut être
    une simple hausse de trafic et non une panne. Verrouillé ici pour qu'un
    ajout futur soit un choix conscient et non un effet de bord — une liste
    blanche se juge sur ce qu'elle NE réveille PAS la nuit.
    """
    assert AT_AF60_LINK_SATURATED in KNOWN_ALERT_TYPES
    assert AT_AF60_LINK_SATURATED not in WHATSAPP_ALERT_TYPES
    # La politique garde son intention (immédiat, non groupable) — c'est la
    # liste blanche qui est le chokepoint, pas elle. Même montage que
    # `mains_power_lost`.
    assert get_policy(AT_AF60_LINK_SATURATED).notify_immediately is True


def test_la_liaison_retient_l_extremite_la_plus_chargee():
    """Max des deux bouts — alors que la capacité prend le min.

    Dans les deux cas on garde l'extrémité la plus mal en point ; ici « plus
    mal » veut dire PLUS HAUT. Un bout qui se croit à 50 % ne doit pas maquiller
    un lien que l'autre bout voit à 92 %.
    """
    assert "link_occupancy_pct" in EDGE_METRICS
    end_a = {"device_type": "airfiber", "status": "up",
             "metrics": {"link_occupancy_pct": 50.0, "total_capacity_mbps": 1900.0}}
    end_b = {"device_type": "airfiber", "status": "up",
             "metrics": {"link_occupancy_pct": 92.0, "total_capacity_mbps": 1900.0}}
    health = edge_health(end_a, end_b)
    assert health["occupancy_pct"] == 92.0
    assert health["saturated"] is True


def test_un_site_porte_l_occupation_de_sa_liaison_la_plus_chargee():
    """Le MAX, jamais la moyenne — sinon une liaison pleine se noie."""
    edges = [
        {"site_a": "A2 HQ", "site_b": "A2 CT1", "health": {"occupancy_pct": 12.0}},
        {"site_a": "A2 CT1", "site_b": "A2 SK1", "health": {"occupancy_pct": 94.0}},
        {"site_a": "A2 CT1", "site_b": "A2 PK1", "health": {"occupancy_pct": 8.0}},
    ]
    occ = site_occupancy_map(edges)
    assert occ["A2 CT1"] == 94.0          # 3 liaisons, une seule pleine
    assert occ["A2 SK1"] == 94.0
    assert occ["A2 HQ"] == 12.0


def test_la_saturation_est_attribuee_aux_deux_bouts():
    """Un tuyau plein gêne ses deux extrémités.

    Ne marquer que l'aval supposerait que le trafic emprunte l'arête d'ARBRE ;
    or `parent` vient du câblage, pas du routage. Après un basculement, le
    chemin qui porte le trafic est précisément celui que l'arbre ne montre pas.
    """
    occ = site_occupancy_map(
        [{"site_a": "A2 SK1", "site_b": "A2 CT2", "health": {"occupancy_pct": 91.0}}]
    )
    assert occ == {"A2 SK1": 91.0, "A2 CT2": 91.0}


def test_un_site_sans_liaison_mesuree_reste_absent_jamais_a_zero():
    """Les liaisons FIBRE n'ont pas d'occupation (deux switches aux bouts).

    Les ramener à 0 les afficherait « fluides » — une affirmation sans mesure,
    exactement ce que le reste du module refuse de faire.
    """
    edges = [{"site_a": "A2 HQ", "site_b": "A2 ARF1", "health": {"occupancy_pct": None}}]
    assert site_occupancy_map(edges) == {}


def test_liaison_sans_mesure_d_occupation_ne_se_dit_pas_fluide():
    """Aucune occupation relevée ⇒ None, et surtout pas 0 ni `saturated=False`
    présenté comme un constat. Les liaisons FIBRE sont dans ce cas (deux
    switches aux bouts) : les déclarer fluides serait affirmer sans mesure."""
    end = {"device_type": "uisp_switch", "status": "up",
           "metrics": {"fiber_dl_throughput_mbps": 445.0}}
    health = edge_health(end, None)
    assert health["occupancy_pct"] is None
    assert health["saturated"] is False
