"""
Tests unitaires de site_topology_service — Python pur, sans DB ni contrôleur.

Le jeu de données reproduit le parc RÉEL tel que mesuré le 2026-08-04 par
`scripts/dump_site_topology.py` : 17 sites d'infra, la racine HQ reliée à ARF1 /
AT1 / CT1 en **ethernet** (fibre) et à NR1 / SNDE en radio, et les deux boucles
de redondance constatées (SK1↔CT2, KS1↔SM1).

Ce que ces tests verrouillent, et pourquoi ça compte :

* **Un site d'abonné SANS clé CRM ne doit pas devenir un site d'infra.** Le parc
  en porte deux, créés à la main dans UISP (« Haydara, Ousmane », « El id,
  Mohamed fall »). Avec le seul filtre `ucrm.client`, le premier apparaissait
  comme un site enfant de SK1 — alors que le lien est un banal AP↔abonné — et le
  second comme un site d'infra orphelin. Un futur « simplifions, la clé CRM
  suffit » les ferait revenir sans que rien d'autre n'échoue.
* **Le type de lien n'est pas un filtre.** Trois des cinq liaisons du HQ sont en
  ethernet : filtrer sur `wireless` amputerait le graphe de sa racine.
* **Le graphe n'est pas un arbre.** Les arêtes hors arbre doivent être rendues,
  pas supprimées.
* **Une liaison non mesurée n'est pas une liaison saine** — c'est le mensonge le
  plus coûteux que la carte pourrait produire.
"""

import types

import pytest
from sqlalchemy.sql.dml import Delete

from app.core.config import get_settings
from app.services import site_topology_service
from app.services.site_topology_service import (
    build_edges,
    device_index,
    edge_health,
    edge_traffic,
    infra_site_ids,
    layered_graph,
    sync_site_links,
)

# --- Construction du jeu de données ----------------------------------------
# Chaque site d'infra porte un équipement que classify_device reconnaît ; les
# sites clients ne portent qu'une station (classify_device → None).

_INFRA_SITES = [
    "A2 HQ", "A2  ARF1", "A2 AT1", "A2 CT1", "A2 NR1", "A2 SNDE",
    "A2 PK1", "A2 TS1", "A2 AT2", "A2 DN1", "A2 SK1",
    "A2 PK2", "A2 CT2", "A2 SM1", "A2 VEL1", "A2 KS1", "A2 TJN1",
]
# Sites d'ABONNÉS sans clé CRM — l'anomalie de provisioning constatée en prod.
_ROGUE_CLIENT_SITES = ["Haydara, Ousmane", "El id, Mohamed fall"]
# Site d'abonné normal (porteur de ucrm.client).
_CRM_CLIENT_SITE = "Diallo, Fatimata"

_BACKHAULS = [
    ("A2 HQ", "A2  ARF1", "ethernet"), ("A2 HQ", "A2 AT1", "ethernet"),
    ("A2 HQ", "A2 CT1", "ethernet"),
    ("A2 HQ", "A2 NR1", "wireless"), ("A2 HQ", "A2 SNDE", "wireless"),
    ("A2  ARF1", "A2 PK1", "wireless"), ("A2  ARF1", "A2 TS1", "wireless"),
    ("A2 AT1", "A2 AT2", "wireless"), ("A2 AT1", "A2 DN1", "wireless"),
    ("A2 CT1", "A2 SK1", "wireless"),
    ("A2 PK1", "A2 PK2", "wireless"), ("A2 PK1", "A2 CT2", "wireless"),
    ("A2 TS1", "A2 SM1", "wireless"), ("A2 TS1", "A2 VEL1", "wireless"),
    ("A2 DN1", "A2 KS1", "wireless"), ("A2 DN1", "A2 TJN1", "wireless"),
    ("A2 SK1", "A2 CT2", "wireless"),   # boucle de redondance réelle
    ("A2 KS1", "A2 SM1", "wireless"),   # boucle de redondance réelle
]


def _build():
    """Renvoie (raw_sites, raw_devices, links) au format du contrôleur."""
    site_ids: dict[str, str] = {}

    def sid(name: str) -> str:
        return site_ids.setdefault(name, f"site-{len(site_ids)}")

    raw_devices: list[dict] = []
    counter = [0]

    def add_device(name: str, site: str, *, infra: bool) -> str:
        counter[0] += 1
        dev_id = f"dev-{counter[0]}"
        raw_devices.append({
            "identification": {
                "id": dev_id,
                "name": name,
                "mac": f"aa:bb:cc:{counter[0] // 256:02x}:{counter[0] % 256:02x}:01",
                # infra → AF60 (classify_device: model AF60* en premier).
                # client → airMax role=station (classify_device → None).
                "type": "airFiber" if infra else "airMax",
                "role": "ap" if infra else "station",
                "model": "AF60-LR" if infra else "LBE-5AC-Gen2",
                "site": {"id": sid(site), "name": site},
            },
            "overview": {"wirelessMode": "ap-ptp" if infra else "sta-ptmp"},
        })
        return dev_id

    links: list[dict] = []

    def add_link(a: str, b: str, ltype: str, state: str = "active") -> None:
        links.append({
            "type": ltype, "state": state,
            "from": {"device": {"identification": {"id": a}}},
            "to": {"device": {"identification": {"id": b}}},
        })

    for a, b, ltype in _BACKHAULS:
        add_link(
            add_device(f"F60 {a}>{b}", a, infra=True),
            add_device(f"F60 {b}>{a}", b, infra=True),
            ltype,
        )

    # Les deux sites d'abonnés SANS clé CRM, dont un relié par un lien AP↔station
    # à un Rocket de SK1 — le cas qui polluait le graphe.
    rocket = add_device("A2-SK1-EST", "A2 SK1", infra=True)
    add_link(rocket, add_device("LR Ousmane", _ROGUE_CLIENT_SITES[0], infra=False), "wireless")
    add_device("LR Mohamed", _ROGUE_CLIENT_SITES[1], infra=False)  # orphelin, aucun lien
    add_link(rocket, add_device("LR Fatimata", _CRM_CLIENT_SITE, infra=False), "wireless")

    raw_sites = [
        {
            "identification": {"id": sid(name), "name": name},
            **({"ucrm": {"client": {"id": "42"}}} if name == _CRM_CLIENT_SITE else {}),
        }
        for name in site_ids
    ]
    return raw_sites, raw_devices, links


def _graph():
    raw_sites, raw_devices, links = _build()
    infra = infra_site_ids(raw_sites, raw_devices)
    edges, skipped = build_edges(links, device_index(raw_devices), infra)
    return infra, edges, skipped


# --- Les sites d'infra ------------------------------------------------------


def test_infra_sites_are_exactly_the_a2_sites():
    infra, _, _ = _graph()
    assert sorted(infra.values()) == sorted(_INFRA_SITES)


def test_client_site_without_crm_key_is_not_infra():
    """LE test du durcissement : la clé CRM seule ne suffit pas.

    Ces deux sites n'ont pas de `ucrm.client` — seul le fait qu'ils ne portent
    aucun équipement d'infra les écarte.
    """
    infra, _, _ = _graph()
    for rogue in _ROGUE_CLIENT_SITES:
        assert rogue not in infra.values()


def test_site_name_is_taken_verbatim_double_space_kept():
    """« A2  ARF1 » porte un double espace en prod ; `devices.site` le stocke
    tel quel, donc normaliser ici casserait la jointure avec notre inventaire."""
    infra, _, _ = _graph()
    assert "A2  ARF1" in infra.values()


# --- Les arêtes -------------------------------------------------------------


def test_edges_match_the_provisioned_backhauls():
    _, edges, _ = _graph()
    assert len(edges) == len(_BACKHAULS)
    pairs = {frozenset((e["site_a"], e["site_b"])) for e in edges}
    assert pairs == {frozenset((a, b)) for a, b, _ in _BACKHAULS}


def test_ethernet_links_are_kept_not_only_wireless():
    """Trois des cinq liaisons du HQ sont en fibre : filtrer sur `wireless`
    amputerait le graphe de sa racine."""
    _, edges, _ = _graph()
    from_hq = [e for e in edges if "A2 HQ" in (e["site_a"], e["site_b"])]
    assert len(from_hq) == 5
    assert sum(1 for e in from_hq if e["type"] == "ethernet") == 3


def test_ap_to_subscriber_links_are_skipped():
    _, edges, skipped = _graph()
    names = {e[side]["name"] for e in edges for side in ("device_a", "device_b")}
    assert "LR Ousmane" not in names
    assert "LR Fatimata" not in names
    assert skipped["bout hors site d'infra"] == 2


def test_edge_orientation_is_stable():
    """Une arête ne doit pas changer d'identité selon le sens de provisioning."""
    _, edges, _ = _graph()
    for edge in edges:
        assert edge["site_a"] <= edge["site_b"]


# --- Le layout --------------------------------------------------------------


def test_all_infra_sites_are_reachable_from_the_root():
    infra, edges, _ = _graph()
    layout = layered_graph(edges, set(infra.values()), "A2 HQ")
    assert layout["root"] == "A2 HQ"
    assert layout["root_source"] == "paramètre"
    assert layout["orphan_sites"] == []
    assert len(layout["components"]) == 1
    assert set(layout["depth"]) == set(_INFRA_SITES)


def test_graph_is_not_a_tree_and_extra_edges_are_surfaced():
    """Les deux boucles réelles doivent RESSORTIR, pas être silencieusement
    jetées : un rendu arborescent en cacherait une."""
    infra, edges, _ = _graph()
    layout = layered_graph(edges, set(infra.values()), "A2 HQ")
    extra = {frozenset((e["site_a"], e["site_b"])) for e in layout["extra_edges"]}
    assert extra == {
        frozenset(("A2 SK1", "A2 CT2")),
        frozenset(("A2 KS1", "A2 SM1")),
    }


def test_unknown_root_falls_back_and_says_so():
    """Un repli silencieux se lirait comme une déduction."""
    infra, edges, _ = _graph()
    layout = layered_graph(edges, set(infra.values()), "A2 INEXISTANT")
    assert layout["root"] == "A2 HQ"          # le plus haut degré
    assert layout["root_source"] == "degré maximal"


def test_depth_matches_the_field_layout():
    infra, edges, _ = _graph()
    layout = layered_graph(edges, set(infra.values()), "A2 HQ")
    assert layout["depth"]["A2 HQ"] == 0
    assert layout["depth"]["A2  ARF1"] == 1
    assert layout["depth"]["A2 PK1"] == 2
    assert layout["depth"]["A2 PK2"] == 3


# --- La santé d'une liaison -------------------------------------------------


def test_unmeasured_edge_is_never_reported_healthy():
    """Le mensonge le plus coûteux de la carte serait un lien vert non mesuré."""
    health = edge_health({"status": "up", "metrics": {}}, {"status": "up", "metrics": {}})
    assert health["state"] == "unmeasured"
    assert health["capacity_mbps"] is None
    assert health["link_potential_pct"] is None


def test_edge_health_keeps_the_worst_of_both_ends():
    """Un lien vaut son extrémité la plus dégradée."""
    health = edge_health(
        {"status": "up", "metrics": {"total_capacity_mbps": 3902, "link_potential_pct": 73}},
        {"status": "up", "metrics": {"total_capacity_mbps": 1350, "link_potential_pct": 28}},
    )
    assert health["state"] == "measured"
    assert health["capacity_mbps"] == 1350
    assert health["link_potential_pct"] == 28
    assert health["measured_ends"] == 2


def test_single_measured_end_is_used():
    health = edge_health(
        {"status": "up", "metrics": {"total_capacity_mbps": 3602}},
        {"status": "up", "metrics": {}},
    )
    assert health["state"] == "measured"
    assert health["capacity_mbps"] == 3602
    assert health["measured_ends"] == 1


def test_af60_below_floor_is_degraded():
    """Une capacité AF60 sous son plancher est rendue dégradée.

    ⚠️ Le plancher est LU DANS LES RÉGLAGES, jamais écrit en dur ici. Ce test
    figeait `1950.0` et a cassé le jour où l'opérateur a corrigé la capacité
    AF60 (moyenne des deux sens d'un lien TDD, et non leur somme → plancher
    ramené à 975). Ce qu'on vérifie est le CÂBLAGE — que la famille AF60 tire
    bien son seuil de `af60_capacity_display_min_mbps` —, pas la valeur choisie,
    qui doit rester réglable sans faire échouer la suite.
    """
    floor = get_settings().af60_capacity_display_min_mbps
    health = edge_health(
        {"status": "up", "device_type": "airfiber",
         "metrics": {"total_capacity_mbps": floor - 1, "link_potential_pct": 28}},
        {"status": "up", "device_type": "airfiber", "metrics": {}},
    )
    assert health["floor_mbps"] == floor
    assert health["degraded"] is True


def test_af60_above_floor_is_not_degraded():
    floor = get_settings().af60_capacity_display_min_mbps
    health = edge_health(
        {"status": "up", "device_type": "airfiber",
         "metrics": {"total_capacity_mbps": floor + 1, "link_potential_pct": 73}},
        {"status": "up", "device_type": "airfiber",
         "metrics": {"total_capacity_mbps": floor + 1, "link_potential_pct": 74}},
    )
    assert health["degraded"] is False


def test_ptp_litebeam_uses_its_own_floor():
    """Un PTP LiteBeam tire son seuil de SON réglage, pas de celui de l'AF60.

    Les deux planchers sont très différents (AF60 en Gb/s, PTP en centaines de
    Mb/s) : les confondre rendrait tous les PTP « dégradés » en permanence.
    """
    settings = get_settings()
    ptp_floor = settings.airmax_backhaul_capacity_min_mbps
    assert ptp_floor != settings.af60_capacity_display_min_mbps, (
        "test sans valeur si les deux planchers deviennent égaux"
    )
    health = edge_health(
        {"status": "up", "device_type": "ptp_litebeam",
         "metrics": {"total_capacity_mbps": ptp_floor + 1}},
        {"status": "up", "device_type": "ptp_litebeam", "metrics": {}},
    )
    assert health["floor_mbps"] == ptp_floor
    assert health["degraded"] is False


def test_unknown_capacity_is_not_degraded():
    """Une capacité inconnue n'est pas une capacité dégradée — même règle que
    `ifSpeed = 0` sur les cages SFP : sans mesure, on n'affirme rien."""
    health = edge_health(
        {"status": "up", "device_type": "airfiber", "metrics": {}},
        {"status": "up", "device_type": "airfiber", "metrics": {}},
    )
    assert health["state"] == "unmeasured"
    assert health["degraded"] is False


def test_ethernet_link_has_no_capacity_floor():
    """Un lien switch↔switch n'a pas de plancher de capacité : ses ports ont
    déjà leurs propres règles (switch_port_speed_low)."""
    health = edge_health(
        {"status": "up", "device_type": "uisp_switch", "metrics": {}},
        {"status": "up", "device_type": "uisp_switch", "metrics": {}},
    )
    assert health["floor_mbps"] is None
    assert health["degraded"] is False


def test_down_end_makes_the_edge_down_even_with_stale_measures():
    """Cas réel CT1↔SK1 : le F60 côté CT1 est down mais porte encore sa dernière
    capacité en base. La dernière valeur connue ne doit pas maquiller la panne."""
    health = edge_health(
        {"status": "down", "metrics": {"total_capacity_mbps": 3902, "link_potential_pct": 66}},
        {"status": "up", "metrics": {"total_capacity_mbps": 3902, "link_potential_pct": 66}},
    )
    assert health["state"] == "down"


# ---------------------------------------------------------------------------
# Le sync quotidien du câblage
# ---------------------------------------------------------------------------
#
# Le câblage est rapatrié 1×/jour dans `site_links` ; la page ne parle plus au
# contrôleur. Ces tests portent sur l'écriture, avec une session factice : ce qui
# compte ici est CE QUI EST ÉCRIT et surtout QUAND ON EFFACE.


class _FakeSession:
    """Session minimale : retient les ajouts et si un DELETE a été émis."""

    def __init__(self):
        self.added = []
        self.deleted = False

    async def execute(self, statement):
        if isinstance(statement, Delete):
            self.deleted = True
        return None

    def add(self, obj):
        self.added.append(obj)


def _patch_controller(monkeypatch, *, devices, sites, links):
    """Remplace le client UISP et les réglages par des doubles."""

    class _StubClient:
        def __init__(self, *a, **kw):
            pass

        async def fetch_devices(self):
            return devices

        async def fetch_sites(self):
            return sites

        async def fetch_data_links(self):
            return links

    monkeypatch.setattr(site_topology_service.uisp_service, "UISPClient", _StubClient)
    monkeypatch.setattr(
        site_topology_service, "get_settings",
        lambda: types.SimpleNamespace(
            uisp_base_url="https://uisp.test", uisp_username="", uisp_password="",
            uisp_api_token="tok", uisp_verify_tls=False, uisp_request_timeout=30,
            topology_root_site="A2 HQ",
        ),
    )


@pytest.mark.asyncio
async def test_sync_writes_one_row_per_physical_link(monkeypatch):
    sites, devices, links = _build()
    _patch_controller(monkeypatch, devices=devices, sites=sites, links=links)

    session = _FakeSession()
    result = await sync_site_links(session)

    assert result["ok"] is True
    assert result["physical_links"] == len(_BACKHAULS)
    assert len(session.added) == len(_BACKHAULS)
    assert session.deleted is True   # remplacement intégral

    # Sites ordonnés à l'écriture : une liaison ne doit pas changer d'identité
    # selon le sens dans lequel UISP a provisionné le lien.
    for row in session.added:
        assert row.site_a <= row.site_b
    # La MAC est conservée des deux côtés — c'est par elle que la lecture
    # rejoint notre inventaire pour colorer la liaison.
    assert all(row.mac_a and row.mac_b for row in session.added)


@pytest.mark.asyncio
async def test_sync_never_wipes_the_table_on_an_empty_payload(monkeypatch):
    """LE garde-fou. Un fetch qui ne rend aucun lien ne doit PAS être lu comme
    « le parc n'a plus aucun backhaul » : effacer la table sur ce signal
    supprimerait toute la topologie. Même leçon que la passe de suppression du
    sync des stations."""
    sites, devices, _ = _build()
    _patch_controller(monkeypatch, devices=devices, sites=sites, links=[])

    session = _FakeSession()
    result = await sync_site_links(session)

    assert result["ok"] is False
    assert session.deleted is False, "la table ne doit pas être vidée"
    assert session.added == []


@pytest.mark.asyncio
async def test_sync_ignores_links_that_resolve_to_no_infra_site(monkeypatch):
    """Un contrôleur qui ne rend que des liens AP↔abonné laisse la table
    intacte : zéro liaison résolue tombe sous le même garde-fou."""
    sites, devices, links = _build()
    # Ne garder que le lien vers un abonné (le Rocket SK1 ↔ LR Ousmane).
    client_links = [
        link for link in links
        if any(
            (link[side]["device"]["identification"]["id"] or "").startswith("dev-")
            and "Ousmane" in next(
                (d["identification"]["name"] for d in devices
                 if d["identification"]["id"] == link[side]["device"]["identification"]["id"]),
                "",
            )
            for side in ("from", "to")
        )
    ]
    _patch_controller(monkeypatch, devices=devices, sites=sites, links=client_links)

    session = _FakeSession()
    result = await sync_site_links(session)

    assert result["ok"] is False
    assert session.deleted is False


# ---------------------------------------------------------------------------
# État d'un site : la règle qui décide de la couleur des liaisons
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _CountSession:
    """Session qui rend un agrégat (site, status, count) figé."""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, statement):
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_site_is_down_only_when_every_device_is_down():
    """LE critère de couleur. Un équipement HS ne met pas un site à terre :
    peindre en rouge les liaisons d'un site qui fonctionne enverrait chercher
    une panne de backhaul là où il n'y en a pas."""
    session = _CountSession([
        ("A2 HQ", "up", 13), ("A2 HQ", "down", 1),      # panne isolée
        ("A2 CT1", "down", 7),                           # site entièrement tombé
        ("A2 NR1", "up", 7),                             # site sain
        ("A2 AT2", "up", 9), ("A2 AT2", "unknown", 1),   # inconnu ≠ down
    ])
    health = await site_topology_service.site_device_health(session)

    assert health["A2 HQ"] == {"total": 14, "down": 1, "is_down": False}
    assert health["A2 CT1"] == {"total": 7, "down": 7, "is_down": True}
    assert health["A2 NR1"]["is_down"] is False
    # Un équipement `unknown` n'est pas compté comme tombé — on n'affirme une
    # panne que sur constat, jamais sur une absence d'information.
    assert health["A2 AT2"] == {"total": 10, "down": 0, "is_down": False}


@pytest.mark.asyncio
async def test_blank_site_names_are_ignored_in_counts():
    """Un site vide/blanc n'est pas un site : il ne doit pas créer un nœud."""
    session = _CountSession([("   ", "up", 3), (None, "down", 2), ("A2 HQ", "up", 1)])
    health = await site_topology_service.site_device_health(session)
    assert set(health) == {"A2 HQ"}


# ---------------------------------------------------------------------------
# Trafic : ce qui distingue une liaison qui écoule d'une liaison inerte
# ---------------------------------------------------------------------------


def _end(status="up", dtype="airfiber", dl=None, ul=None):
    metrics = {}
    if dl is not None:
        metrics["dl_throughput_mbps"] = dl
    if ul is not None:
        metrics["ul_throughput_mbps"] = ul
    return {"status": status, "device_type": dtype, "metrics": metrics}


def test_traffic_unknown_when_no_end_reports_a_rate():
    """LE piège à ne pas rouvrir. Les liaisons FIBRE ont des switches aux deux
    bouts, et un switch n'expose aucun débit en SNMP. Les compter comme inertes
    signalerait trois pannes de trafic permanentes sur la dorsale du HQ."""
    tr = edge_traffic(_end(dtype="uisp_switch"), _end(dtype="uisp_switch"))
    assert tr["state"] == "unknown"
    assert tr["total_mbps"] is None
    assert tr["a_to_b_mbps"] is None and tr["b_to_a_mbps"] is None


def test_traffic_idle_only_on_a_measured_zero():
    tr = edge_traffic(_end(dl=0.0, ul=0.0), _end())
    assert tr["state"] == "idle"
    assert tr["total_mbps"] == 0.0


def test_traffic_active_above_the_floor():
    tr = edge_traffic(_end(dl=40.0, ul=2.5), _end())
    assert tr["state"] == "active"
    assert tr["total_mbps"] == 42.5
    # dl de A = ce que A recoit = le flux B -> A ; ul de A = le flux A -> B.
    assert tr["b_to_a_mbps"] == 40.0
    assert tr["a_to_b_mbps"] == 2.5


def test_traffic_keeps_the_busiest_end():
    """Les deux bouts décrivent le même lien, mais l'un peut n'avoir aucun
    relevé frais : prendre le maximum évite de déclarer inerte une liaison que
    l'autre extrémité voit passer du trafic."""
    tr = edge_traffic(_end(dl=0.0, ul=0.0), _end(dl=88.0, ul=4.0))
    assert tr["state"] == "active"
    # A -> B : ul de A (0) contre dl de B (88) -> on garde 88.
    assert tr["a_to_b_mbps"] == 88.0
    # B -> A : dl de A (0) contre ul de B (4) -> on garde 4.
    assert tr["b_to_a_mbps"] == 4.0
    assert tr["total_mbps"] == 92.0


def test_health_carries_the_traffic_verdict():
    health = edge_health(_end(dl=0.0, ul=0.0), _end(dl=0.0, ul=0.0))
    assert health["traffic"] == "idle"
    assert health["traffic_mbps"] == 0.0


def test_redundant_link_is_active_if_one_branch_carries():
    """Si UNE branche écoule, la liaison écoule — le trafic passe par celle qui
    marche, exactement comme pour la santé."""
    idle = edge_health(_end(dl=0.0, ul=0.0), _end(dl=0.0, ul=0.0))
    busy = edge_health(_end(dl=30.0, ul=1.0), _end(dl=30.0, ul=1.0))
    from app.services.site_topology_service import _worst
    assert _worst([idle, busy])["traffic"] == "active"


def test_traffic_directions_are_named_by_site_not_by_up_down():
    """Le meme flux est mesure deux fois sous deux noms opposes : `dl` d'un bout
    est le `ul` de l'autre. Confondre les deux afficherait le trafic a l'envers
    sur la moitie des liaisons — d'ou des directions nommees par les SITES."""
    # A emet 120 vers B, B emet 8 vers A. Les deux bouts le voient, en miroir.
    tr = edge_traffic(_end(dl=8.0, ul=120.0), _end(dl=120.0, ul=8.0))
    assert tr["a_to_b_mbps"] == 120.0
    assert tr["b_to_a_mbps"] == 8.0


def test_traffic_direction_survives_a_single_measured_end():
    """Un seul bout mesure : les deux directions restent connues, car il rend
    son `dl` ET son `ul`."""
    tr = edge_traffic(_end(dl=8.0, ul=120.0), _end())
    assert tr["a_to_b_mbps"] == 120.0
    assert tr["b_to_a_mbps"] == 8.0
