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
    internet_routes,
    layered_graph,
    peak_load,
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


def test_switch_fiber_counters_feed_the_link_traffic():
    """Une liaison FIBRE tire son débit du port SFP de ses switches.

    Un switch n'expose aucun débit instantané en SNMP ; `snmp_poll_job` le dérive
    de ses compteurs d'octets sous les clés `fiber_*`. Sans ce repli, les
    liaisons de la dorsale resteraient « non mesuré » alors qu'elles portent le
    trafic du réseau (vérifié sur ARF1 port 25 : 384 Mb/s descendant).

    Le SENS est le même que pour une radio — `dl` = ce que l'équipement REÇOIT —
    ce qui laisse toute la lecture inter-sites inchangée.
    """
    a = {"status": "up", "device_type": "uisp_switch",
         "metrics": {"fiber_dl_throughput_mbps": 384.0, "fiber_ul_throughput_mbps": 76.1}}
    b = {"status": "up", "device_type": "uisp_switch", "metrics": {}}
    tr = edge_traffic(a, b)
    assert tr["state"] == "active"
    assert tr["b_to_a_mbps"] == 384.0     # ce que A recoit vient de B
    assert tr["a_to_b_mbps"] == 76.1
    assert tr["total_mbps"] == 460.1


def test_a_real_throughput_still_wins_over_the_fiber_fallback():
    """Le repli `fiber_*` ne doit jamais masquer une mesure directe : si un
    equipement publie son propre debit, c'est lui qui compte."""
    a = {"status": "up", "device_type": "airfiber",
         "metrics": {"dl_throughput_mbps": 10.0, "fiber_dl_throughput_mbps": 999.0}}
    tr = edge_traffic(a, None)
    assert tr["b_to_a_mbps"] == 10.0


def test_switch_without_fiber_port_stays_unmeasured():
    """Seuls les 3 sites dont le `fiber_port_index` est renseigne produisent ces
    cles ; ailleurs la liaison reste honnetement « non mesuree »."""
    tr = edge_traffic(
        {"status": "up", "device_type": "uisp_switch", "metrics": {}},
        {"status": "up", "device_type": "uisp_switch", "metrics": {}},
    )
    assert tr["state"] == "unknown"


# ---------------------------------------------------------------------------
# Routes vers Internet — la meilleure, et où ça sature
# ---------------------------------------------------------------------------
#
# Deux règles portent tout, et toutes deux sortent du terrain :
#
# * **La fibre ne se juge pas comme la radio.** Les trois dorsales du HQ (ARF1,
#   AT1, CT1) sont en fibre : la seule question est « up, et du trafic passe ».
#   Les compter comme « non mesurées » faisait afficher « départage impossible »
#   sur ces trois sites — vu à l'écran avant déploiement.
# * **Le verdict est un DÉBIT, pas un pourcentage.** « Ce chemin peut encore
#   écouler 300 Mb/s » se traduit en décision ; « 45 % » ne se traduit en rien.


def _p(site_a: str, site_b: str) -> frozenset:
    return frozenset((site_a, site_b))


def _merged(
    occ: dict | None = None,
    *,
    down: set | None = None,
    caps: dict | None = None,
    degraded: set | None = None,
    twin: set | None = None,
    idle_fibre: set | None = None,
) -> list[dict]:
    """Les arêtes logiques du parc de test, avec la santé qu'on leur dicte."""
    occ, down = occ or {}, down or set()
    caps, degraded, twin = caps or {}, degraded or set(), twin or set()
    idle_fibre = idle_fibre or set()

    _, raw, _ = _graph()
    out: dict[frozenset, dict] = {}
    for edge in raw:
        key = _p(edge["site_a"], edge["site_b"])
        occupancy = occ.get(key)
        wired = edge["type"] != "wireless"
        out[key] = {
            "site_a": edge["site_a"],
            "site_b": edge["site_b"],
            "links": [{}, {}] if key in twin else [{}],
            "redundant": key in twin,
            "medium": "wired" if wired else "wireless",
            "health": {
                "state": (
                    "down" if key in down
                    else "measured" if occupancy is not None
                    else "unmeasured"
                ),
                "occupancy_pct": occupancy,
                "saturated": occupancy is not None and occupancy >= 90.0,
                "capacity_mbps": caps.get(key),
                "degraded": key in degraded,
                # La fibre ne se juge que sur « up + du trafic passe ».
                "traffic": (
                    ("idle" if key in idle_fibre else "active") if wired else "unknown"
                ),
            },
        }
    return list(out.values())


def _merged_with(load: dict, **kwargs) -> list[dict]:
    """Arêtes portant une charge COURANTE : ``{paire: (débit Mb/s, occupation %)}``.

    La marge se calcule sur la dernière valeur en base de l'équipement, donc sur
    la santé de l'arête elle-même — il n'y a plus de dictionnaire d'historique à
    passer à côté.
    """
    edges = _merged({key: occ for key, (_t, occ) in load.items()}, **kwargs)
    by_pair = {frozenset((e["site_a"], e["site_b"])): e for e in edges}
    for key, (traffic, _occ) in load.items():
        by_pair[key]["health"]["traffic_mbps"] = traffic
    return edges


# Les deux branches réelles de CT2 : par PK1/ARF1 (arête d'ARBRE) et par
# SK1/CT1 (la boucle de redondance constatée sur le parc). Chacune finit par une
# liaison FIBRE vers le HQ — d'où 3 sauts mais seulement 2 sauts radio.
_VIA_PK1 = "A2 CT2>A2 PK1>A2  ARF1>A2 HQ"
_VIA_SK1 = "A2 CT2>A2 SK1>A2 CT1>A2 HQ"

_CT2_PK1 = _p("A2 CT2", "A2 PK1")
_PK1_ARF1 = _p("A2  ARF1", "A2 PK1")
_CT2_SK1 = _p("A2 CT2", "A2 SK1")
_SK1_CT1 = _p("A2 CT1", "A2 SK1")


# --- La formule du débit maximal -------------------------------------------


def test_peak_load_projects_the_ceiling_from_the_occupancy():
    """900 Mb/s qui occupent 75 % du temps d'antenne ⇒ le lien plafonne à 1200,
    donc il reste 300."""
    load = peak_load(900.0, 75.0)
    assert load["max_rate_mbps"] == 1200.0
    assert load["headroom_mbps"] == 300.0
    assert load["peak_traffic_mbps"] == 900.0


def test_a_nearly_empty_link_gets_no_projection_at_all():
    """⚠️ Constaté sur les données réelles : `CT2↔PK1`, qui ne portait quasiment
    rien, était désigné « point de saturation » d'un chemin, avec un « pic
    0 Mb/s ». Diviser un trafic infime par une occupation infime amplifie le
    bruit — 0,4 Mb/s à 0,1 % projette 400 Mb/s de plafond sur rien du tout.

    Un lien vide ne contraint personne : on ne se prononce pas, et il sort du
    calcul de la marge au lieu de la fausser.
    """
    assert peak_load(0.4, 0.1) is None
    assert peak_load(120.0, 4.9) is None
    assert peak_load(120.0, 5.0) is not None, "le seuil, pas au-delà"


def test_the_ceiling_is_never_capacity_minus_traffic():
    """⚠️ LE piège. Sur un lien TDD, `total_capacity_mbps` est la MOYENNE des
    deux sens : la soustraire d'un `dl + ul` est faux dimensionnellement — la
    même erreur qui rendait 120 % d'occupation sur un cas réel.

    Ici un lien dont les deux sens plafonnent à 1801 et 1201 porte 900 Mb/s pour
    75 % de temps d'antenne. La soustraction naïve annoncerait
    `1501 (moyenne) − 900 = 601` de marge ; la vérité est 300, soit **le double
    d'erreur**, dans le sens rassurant.
    """
    naive_mean_capacity = (1801.0 + 1201.0) / 2
    assert peak_load(900.0, 75.0)["headroom_mbps"] == 300.0
    assert naive_mean_capacity - 900.0 != 300.0


# --- La fibre ---------------------------------------------------------------


def test_a_pure_fibre_route_is_the_best_and_is_fully_covered():
    """ARF1 est relié au HQ par une seule liaison FIBRE. C'est évidemment sa
    meilleure route — et le refuser était le défaut vu à l'écran : compté comme
    « non mesuré », ce chemin faisait afficher « départage impossible » et
    laissait recommander un détour de 5 sauts par tout le maillage.
    """
    group = internet_routes(_merged(), "A2 HQ")["A2  ARF1"]
    best = group["paths"][0]

    assert best["id"] == "A2  ARF1>A2 HQ"
    assert best["radio_hop_count"] == 0, "aucun saut radio : rien ne la bride"
    assert best["coverage"] == "full", "une fibre n'est pas un trou de mesure"
    assert best["is_best"] is True
    assert group["best_id"] == "A2  ARF1>A2 HQ"
    assert group["best_reason"] is None


def test_a_fibre_hop_never_carries_the_bottleneck():
    """Le goulot est toujours un saut RADIO : la fibre n'a pas de taux, et n'a
    pas besoin d'en avoir un."""
    group = internet_routes(
        _merged_with({_CT2_PK1: (300.0, 30.0), _PK1_ARF1: (200.0, 20.0)}),
        "A2 HQ",
    )["A2 CT2"]
    route = next(p for p in group["paths"] if p["id"] == _VIA_PK1)

    assert route["hop_count"] == 3 and route["radio_hop_count"] == 2
    fibre = [h for h in route["hops"] if h["is_fibre"]]
    assert len(fibre) == 1 and fibre[0]["to"] == "A2 HQ"
    assert all(not h["is_bottleneck"] for h in fibre)
    # La couverture ne compte QUE les sauts radio : 2 sur 2 ⇒ complète.
    assert route["coverage"] == "full"
    assert route["measured_hops"] == 2


def test_an_idle_fibre_is_flagged_without_disqualifying():
    """Une dorsale debout mais sans trafic est anormale et doit se voir — sans
    pour autant affirmer que le chemin ne passe pas.

    ⚠️ `unknown` n'est PAS `idle` : un switch n'expose pas toujours son débit.
    """
    group = internet_routes(
        _merged(idle_fibre={_p("A2 HQ", "A2  ARF1")}), "A2 HQ",
    )["A2  ARF1"]
    best = group["paths"][0]

    # ⚠️ L'ordre des bouts est celui de l'arête (alphabétique) : « A2  ARF1 »
    # passe avant « A2 HQ », son double espace triant avant le « H ».
    assert best["fibre_idle_hops"] == [{"site_a": "A2  ARF1", "site_b": "A2 HQ"}]
    assert best["usable"] is True
    assert best["is_best"] is True, "signalé, pas disqualifié"


# --- Le classement : la marge en Mb/s ---------------------------------------


def test_best_route_is_the_one_with_the_most_room_left():
    """LA décision opérateur : la plus grande marge restante, en Mb/s.

    La branche gagnante est ici la boucle HORS ARBRE — ce qui verrouille que le
    parcours en largeur décrit le CÂBLAGE et ne présume pas du meilleur chemin.
    """
    group = internet_routes(
        _merged_with({
            _CT2_PK1: (200.0, 20.0),    # plafond 1000 → marge 800
            _PK1_ARF1: (900.0, 75.0),   # plafond 1200 → marge 300  ◄ bride
            _CT2_SK1: (100.0, 10.0),    # plafond 1000 → marge 900
            _SK1_CT1: (400.0, 40.0),    # plafond 1000 → marge 600  ◄ bride
        }),
        "A2 HQ",
    )["A2 CT2"]

    assert group["best_id"] == _VIA_SK1
    best = group["paths"][0]
    assert best["headroom_mbps"] == 600.0
    assert best["max_rate_mbps"] == 1000.0
    assert best["is_best"] is True


def test_the_bottleneck_is_the_tightest_margin_not_the_highest_percentage():
    """⚠️ Un lien à 90 % de 1950 Mb/s laisse 195 Mb/s ; un lien à 50 % de
    300 Mb/s n'en laisse que 150. C'est le SECOND qui bride le chemin, alors que
    le premier affiche le plus gros pourcentage. Classer au pourcentage
    enverrait l'opérateur sur le mauvais maillon.
    """
    group = internet_routes(
        _merged_with({
            _CT2_PK1: (1755.0, 90.0),   # marge 195, le plus CHARGÉ
            _PK1_ARF1: (150.0, 50.0),   # marge 150, le plus JUSTE
        }),
        "A2 HQ",
    )["A2 CT2"]
    route = next(p for p in group["paths"] if p["id"] == _VIA_PK1)

    assert route["bottleneck"]["site_a"] == "A2  ARF1"
    assert route["bottleneck"]["site_b"] == "A2 PK1"
    assert route["headroom_mbps"] == 150.0
    assert sum(1 for h in route["hops"] if h["is_bottleneck"]) == 1


def test_a_longer_route_wins_when_it_has_more_room():
    """Le corollaire, explicitement choisi par l'opérateur : un détour au large
    vaut mieux qu'un raccourci à l'étroit. Le nombre de sauts ne départage qu'à
    égalité de marge."""
    group = internet_routes(
        _merged_with({
            _SK1_CT1: (880.0, 88.0),    # 2 sauts, marge 120
            _CT2_SK1: (150.0, 15.0),
            _CT2_PK1: (220.0, 22.0),    # 4 sauts, marge la plus faible = 780
            _PK1_ARF1: (180.0, 18.0),
        }),
        "A2 HQ",
    )["A2 SK1"]

    best = group["paths"][0]
    assert best["hop_count"] == 4
    assert best["headroom_mbps"] > 120.0
    assert group["best_id"] == best["id"]


def test_ranking_is_deterministic_on_ties():
    """Un « meilleur » qui change tout seul d'un appel à l'autre détruirait la
    confiance."""
    load = {k: (400.0, 40.0) for k in (_CT2_PK1, _PK1_ARF1, _CT2_SK1, _SK1_CT1)}
    first = internet_routes(_merged_with(load), "A2 HQ")["A2 CT2"]
    again = internet_routes(_merged_with(load), "A2 HQ")["A2 CT2"]
    assert [p["id"] for p in first["paths"]] == [p["id"] for p in again["paths"]]
    assert first["best_id"] == again["best_id"]


# --- Ce qui disqualifie, et ce qui ne disqualifie pas -----------------------


def test_a_down_hop_disqualifies_the_route_but_it_is_still_listed():
    """Une route coupée n'est jamais élue — mais elle reste AFFICHÉE : l'opérateur
    doit voir que sa seconde route existe et qu'elle est morte."""
    group = internet_routes(
        _merged_with({_CT2_PK1: (600.0, 60.0), _CT2_SK1: (100.0, 10.0)}, down={_SK1_CT1}),
        "A2 HQ",
    )["A2 CT2"]

    cut = next(p for p in group["paths"] if p["id"] == _VIA_SK1)
    assert cut["usable"] is False
    assert cut["is_best"] is False
    assert cut["down_hops"] == [
        {"site_a": "A2 CT1", "site_b": "A2 SK1", "fibre_cut": False}
    ]
    assert group["best_id"] == _VIA_PK1
    assert group["paths"][-1]["id"] == _VIA_SK1, "une route coupée passe en dernier"


def test_a_cut_fibre_reroutes_the_site_through_its_radio_backup():
    """LE scénario de la dorsale coupée, et le seul où l'écran se taisait.

    ⚠️ Une fibre coupée ne rend PAS ses switches injoignables : le site reste
    atteignable par sa liaison radio de secours, les deux bouts répondent au
    ping, et l'arête passerait pour saine. La dorsale morte continuerait donc
    d'être affichée comme la meilleure route — précisément quand l'opérateur a
    besoin qu'on lui montre l'autre chemin.

    Le verdict vient du port SFP (`port_N_up`, la même métrique que l'alerte
    `fiber_link_down`), posé ici par `down` sur la liaison fibre.
    """
    group = internet_routes(
        _merged_with(
            {_CT2_SK1: (200.0, 20.0), _SK1_CT1: (250.0, 25.0)},
            down={_p("A2 CT1", "A2 HQ")},          # la fibre CT1↔HQ est coupée
        ),
        "A2 HQ",
    )["A2 CT1"]

    # Sa dorsale directe est morte…
    direct = next(p for p in group["paths"] if p["hop_count"] == 1)
    assert direct["usable"] is False
    assert direct["is_best"] is False
    # …et c'est le chemin RADIO de secours qui est désigné.
    best = group["paths"][0]
    assert best["usable"] is True and best["is_best"] is True
    assert best["radio_hop_count"] > 0
    assert group["best_id"] == best["id"]


def test_no_failover_projection_is_ever_added():
    """⚠️ Le réflexe à ne pas avoir : annoncer « après bascule ce maillon portera
    X » en additionnant sa charge actuelle et le trafic du chemin coupé.

    C'est un DOUBLE COMPTAGE, et c'est physique : dès que la dorsale d'ARF1
    tombe, elle ne porte plus rien et le trafic d'ARF1 est DÉJÀ reparti par PK1
    et TS1 — la mesure courante de ces liaisons contient donc déjà ce qu'on
    voulait y ajouter. Après une coupure réelle il n'y a rien à projeter : la
    marge du secours a fondu toute seule, et elle se LIT.

    (Le calcul avait aussi été nourri d'une mesure fantôme : `ARF1↔HQ` annonçait
    18 645 Mb/s pour un agrégat aval de 788 — un facteur 24 au-dessus du possible
    physique, un compteur SNMP qui reboucle suffisant à le fabriquer.)
    """
    group = internet_routes(
        _merged_with(
            {
                _p("A2  ARF1", "A2 PK1"): (336.5, 17.0),
                _p("A2  ARF1", "A2 TS1"): (451.7, 36.0),
                _CT2_PK1: (250.0, 25.0),
                _CT2_SK1: (215.4, 12.0),
                _SK1_CT1: (412.7, 27.0),
            },
            down={_p("A2  ARF1", "A2 HQ")},
        ),
        "A2 HQ",
    )["A2  ARF1"]

    best = group["paths"][0]
    assert best["usable"] is True and best["is_best"] is True
    # La marge est celle qu'on MESURE sur le secours, sans rien y ajouter.
    assert best["headroom_mbps"] is not None
    for route in group["paths"]:
        assert "displaced_mbps" not in route
        assert "expected_at_bottleneck_mbps" not in route


def test_degraded_hop_does_not_disqualify_nor_double_penalise():
    """`degraded` dit que la capacité est sous son plancher — un fait que la
    marge intègre DÉJÀ, puisqu'elle se déduit du temps d'antenne consommé. Le
    repénaliser compterait deux fois la même chose."""
    group = internet_routes(
        _merged_with({
            _CT2_PK1: (200.0, 20.0), _PK1_ARF1: (150.0, 15.0),
            _CT2_SK1: (900.0, 90.0), _SK1_CT1: (880.0, 88.0),
        }, degraded={_CT2_PK1}),
        "A2 HQ",
    )["A2 CT2"]

    best = group["paths"][0]
    assert best["id"] == _VIA_PK1, "un saut dégradé mais au large reste le meilleur"
    assert best["usable"] is True
    assert best["degraded_hops"] == [{"site_a": "A2 CT2", "site_b": "A2 PK1"}]


def test_a_route_without_load_history_is_never_declared_best():
    """On ne recommande pas un chemin dont on ignore la charge. Ne pas trancher
    est une réponse, à condition de dire pourquoi — et c'est actionnable : ce
    segment n'est pas instrumenté."""
    group = internet_routes(_merged({_CT2_PK1: 30.0}), "A2 HQ")["A2 CT2"]

    assert all(p["headroom_mbps"] is None for p in group["paths"])
    assert all(p["is_best"] is False for p in group["paths"])
    assert group["best_id"] is None
    assert group["best_reason"] == "aucune route dont la charge soit mesurée"


def test_an_unmeasured_shortcut_is_not_outranked_by_a_long_measured_detour():
    """Un chemin radio sans historique passe devant quand il est strictement plus
    court que tous les chiffrés — c'est la référence de l'opérateur. Il est
    *placé*, jamais *élu*.
    """
    # SK1 sort en 2 sauts par CT1, ou en 4 par CT2/PK1/ARF1. Seul le LONG chemin
    # a un historique de charge : le court, plus direct, doit rester en tête sans
    # être élu — sinon on recommanderait un détour de 4 sauts par défaut de
    # mesure sur le lien évident.
    group = internet_routes(
        _merged_with({
            _CT2_SK1: (400.0, 40.0), _CT2_PK1: (300.0, 30.0), _PK1_ARF1: (200.0, 20.0),
        }),
        "A2 HQ",
    )["A2 SK1"]

    assert group["paths"][0]["hop_count"] == 2, "le plus court d'abord"
    assert group["paths"][0]["headroom_mbps"] is None
    assert group["paths"][0]["is_best"] is False
    assert group["best_id"] is None
    assert group["best_reason"] == "le chemin le plus court n'a pas d'historique de charge"


# --- Ce qu'on affiche, et ce qu'on annonce ----------------------------------


def test_every_route_is_returned_never_hidden():
    """L'opérateur veut voir TOUTES les sorties de son site — c'est la demande,
    et elle prime sur toute idée de tri qu'aurait le backend.

    ⚠️ Ce module a d'abord masqué des chemins : un par point de rupture, une
    seule route coupée. L'intention se défendait (AT2 affichait deux détours
    morts sur le MÊME lien) mais la décision n'appartient pas ici : un chemin
    replié dans le rendu se déplie, un chemin absent de la réponse, non.
    """
    group = internet_routes(_merged(), "A2 HQ")["A2 CT2"]
    assert group["kept"] == group["found"] == 3
    # Y compris le grand détour de 8 sauts par l'autre boucle.
    assert max(p["hop_count"] for p in group["paths"]) == 8


def test_a_route_says_when_it_breaks_at_the_same_place_as_another():
    """Ce qui reste de la règle de dédoublonnage : on ANNOTE au lieu de cacher.

    Sur les vraies données, DN1 sortait par `KS1→SM1→TS1→ARF1→HQ` ET par ce même
    chemin prolongé de quatre sauts — même goulot `SM1↔TS1`. Le second n'est pas
    une alternative pour ce point de rupture, et le dire vaut mieux que le taire.
    """
    pairs = {
        _p("A2  ARF1", "A2 TS1"): (390.0, 39.0),
        _p("A2 SM1", "A2 TS1"): (120.0, 12.0),
        _p("A2 KS1", "A2 SM1"): (200.0, 20.0),
        _p("A2 DN1", "A2 KS1"): (550.0, 55.0),
        _p("A2 AT1", "A2 DN1"): (550.0, 55.0),
    }
    group = internet_routes(_merged_with(pairs), "A2 HQ")["A2 SM1"]

    flagged = [p for p in group["paths"] if p["same_bottleneck_as"]]
    assert flagged, "un chemin au goulot déjà vu doit être annoté"
    for route in flagged:
        twin = next(p for p in group["paths"] if p["id"] == route["same_bottleneck_as"])
        assert twin["bottleneck"] == route["bottleneck"]
    # …et il reste AFFICHÉ.
    assert group["kept"] == group["found"]


def test_unmeasured_routes_are_never_collapsed_together():
    """Le garde-fou de la règle ci-dessus. Un chemin SANS goulot connu n'a pas de
    point de rupture : les regrouper décréterait qu'ils cèdent au même endroit,
    ce qu'on ignore — et sur un parc sans mesure de charge, ça n'afficherait
    qu'UNE route par site, masquant la redondance elle-même.
    """
    group = internet_routes(_merged(), "A2 HQ")["A2 CT2"]
    assert all(p["bottleneck"] is None for p in group["paths"])
    assert group["kept"] > 1, "les chemins non mesurés ne se collapsent pas"


def test_cut_routes_are_all_listed_and_sorted_last():
    """Une route coupée reste AFFICHÉE — l'opérateur doit voir que sa sortie
    existe et qu'elle est morte — et toutes le sont, pas seulement une. Elles
    passent simplement en fin de classement."""
    group = internet_routes(
        _merged_with({_p("A2 AT1", "A2 AT2"): (130.0, 13.0)},
                     down={_p("A2 AT1", "A2 DN1")}),
        "A2 HQ",
    )["A2 AT2"]

    assert group["kept"] == group["found"]
    cut = [p for p in group["paths"] if not p["usable"]]
    assert len(cut) == 2, "les DEUX détours morts sont listés"
    assert group["paths"][0]["usable"] is True and group["paths"][0]["is_best"] is True
    assert all(not p["usable"] for p in group["paths"][-2:]), "les coupées en dernier"


def test_ct2_routes_include_both_real_branches():
    """Les deux branches réelles doivent sortir : n'énumérer que l'arête d'arbre
    reviendrait au rendu arborescent que ce module refuse.

    ⚠️ Le parc en porte un TROISIÈME, de 8 sauts, qui repart par l'autre boucle.
    Il est réel, donc énuméré et compté dans `found`, pas escamoté.
    """
    group = internet_routes(_merged(), "A2 HQ")["A2 CT2"]
    assert group["found"] == 3
    short = [p for p in group["paths"] if p["hop_count"] == 3]
    assert {p["id"] for p in short} == {_VIA_PK1, _VIA_SK1}


def test_redundant_edge_counts_as_one_hop():
    """Deux radios entre les mêmes sites sont UN saut redondant : les énumérer
    séparément distinguerait des chemins que la couche IP ne choisit pas."""
    group = internet_routes(_merged(twin={_p("A2 KS1", "A2 SM1")}), "A2 HQ")["A2 SM1"]

    via_ks1 = next(p for p in group["paths"] if "A2 KS1" in p["sites"])
    hop = next(h for h in via_ks1["hops"] if {h["site_a"], h["site_b"]} == {"A2 KS1", "A2 SM1"})
    assert hop["redundant"] is True and hop["links_count"] == 2
    assert sum(1 for p in group["paths"] if "A2 KS1" in p["sites"]) == 1


# --- Les bornes de l'énumération --------------------------------------------


def test_the_default_hop_cap_covers_the_whole_parc():
    """La borne par DÉFAUT ne doit tronquer AUCUN site du parc réel.

    ⚠️ Le raisonnement naturel — « le parc tient dans 4 sauts de profondeur,
    8 laisse le double » — est FAUX : ce qui borne un chemin est la longueur du
    plus long chemin SIMPLE, qui serpente à travers les boucles, et il fait
    11 sauts ici. À 8, l'énumération se coupait sur 6 sites sur 17.
    """
    routes = internet_routes(_merged(), "A2 HQ")
    truncated = [
        site for site, group in routes.items()
        if group["truncated"]["by_hops"] or group["truncated"]["by_budget"]
    ]
    assert truncated == []
    longest = max(p["hop_count"] for g in routes.values() for p in g["paths"])
    assert longest < site_topology_service.ROUTE_MAX_HOPS, (
        "plus aucune marge sous la borne — la remonter"
    )


def test_hop_cap_is_reported_not_silent():
    """Un rendu qui écarte un chemin sans le dire est la faute que ce module
    refuse déjà pour les boucles du graphe."""
    group = internet_routes(_merged(), "A2 HQ", max_hops=1)["A2 CT2"]
    assert group["truncated"]["by_hops"] is True
    assert group["found"] == 0
    assert group["reason"] == "aucun chemin vers la racine"


def test_exploration_budget_is_reported_not_silent():
    group = internet_routes(_merged(), "A2 HQ", max_expansions=3)["A2 CT2"]
    assert group["truncated"]["by_budget"] is True


def test_kept_routes_are_the_best_ones_and_the_count_is_reported():
    """On tronque par le HAUT du classement, et on dit combien ont été trouvées."""
    group = internet_routes(
        _merged_with({
            _CT2_PK1: (800.0, 80.0),    # marge 200 → bride la branche PK1
            _PK1_ARF1: (100.0, 10.0),
            _CT2_SK1: (250.0, 25.0),    # marge 750
            _SK1_CT1: (250.0, 25.0),
        }),
        "A2 HQ",
        keep=1,
    )["A2 CT2"]

    assert group["found"] == 3 and group["kept"] == 1
    assert [p["id"] for p in group["paths"]] == [_VIA_SK1]


def test_root_and_unreachable_sites_get_an_explicit_reason():
    """Jamais une liste vide muette : une absence de route se lirait comme un
    oubli du calcul."""
    routes = internet_routes(_merged(), "A2 HQ", {"A2 HQ", "A2 ORPHELIN"})
    assert routes["A2 HQ"]["reason"] == "racine"
    assert routes["A2 HQ"]["paths"] == []
    assert routes["A2 ORPHELIN"]["reason"] == "aucun chemin vers la racine"


def test_hop_pair_orientation_matches_the_edges_payload():
    """VERROU du frontend : il résout chaque saut dans `edges[]` par la clé
    `site_a|site_b`. Réorienter la paire dans le sens de la marche ferait rater
    le lookup EN SILENCE — surlignage et infobulle cesseraient de marcher sans
    qu'aucun test ne bronche."""
    edges = _merged()
    known = {(e["site_a"], e["site_b"]) for e in edges}
    for group in internet_routes(edges, "A2 HQ").values():
        for route in group["paths"]:
            for hop in route["hops"]:
                assert (hop["site_a"], hop["site_b"]) in known
                assert {hop["from"], hop["to"]} == {hop["site_a"], hop["site_b"]}


# --- Qui décide de la direction du trafic -----------------------------------


def test_a_site_with_several_exits_is_a_decider():
    """TS1 arbitre réellement : elle peut sortir par ARF1 ou par SM1."""
    groups = internet_routes(_merged(), "A2 HQ")
    ts1 = groups["A2 TS1"]
    assert ts1["role"] == "decider"
    assert set(ts1["exits"]) == {"A2  ARF1", "A2 SM1"}


def test_a_single_exit_site_is_a_child_and_points_at_its_decider():
    """⚠️ VEL1 n'a QUE TS1. Lui annoncer « 3 routes possibles » laisse croire à
    un choix qu'elle n'a pas : ses trois chemins sont ceux de TS1, et c'est TS1
    qui arbitre. L'écran doit renvoyer vers le site où l'on peut agir."""
    groups = internet_routes(_merged(), "A2 HQ")
    vel1 = groups["A2 VEL1"]
    assert vel1["role"] == "child"
    assert vel1["exits"] == ["A2 TS1"]
    assert vel1["decider"] == "A2 TS1"


def test_a_dead_end_neighbour_is_not_an_exit():
    """⚠️ Une SORTIE est le premier saut d'un chemin qui mène quelque part, pas
    n'importe quelle liaison voisine. TJN1 est voisin de DN1 mais c'est un
    cul-de-sac : le compter ferait passer DN1 pour plus redondant qu'il n'est."""
    dn1 = internet_routes(_merged(), "A2 HQ")["A2 DN1"]
    assert set(dn1["exits"]) == {"A2 AT1", "A2 KS1"}
    assert "A2 TJN1" not in dn1["exits"]


def test_a_child_hanging_off_the_root_has_no_decider_at_all():
    """NR1 et SNDE sortent directement sur la racine : personne en amont ne peut
    les rerouter. Un `null` muet se lirait comme un calcul manquant — c'est un
    fait du réseau, et l'écran doit le dire."""
    groups = internet_routes(_merged(), "A2 HQ")
    nr1 = groups["A2 NR1"]
    assert nr1["role"] == "child"
    assert nr1["exits"] == ["A2 HQ"]
    assert nr1["decider"] is None


def test_the_root_is_neither_a_decider_nor_a_child():
    assert internet_routes(_merged(), "A2 HQ")["A2 HQ"]["role"] == "root"


def test_a_cut_link_carries_nothing_in_either_direction():
    """⚠️ Cas vécu sur `ARF1↔HQ` : la fibre est coupée, et le compteur SNMP de
    son port SFP annonçait pourtant **18 645 Mb/s** dans un sens (et 0 dans
    l'autre — la signature même de la coupure). Un débit fantôme d'un facteur 24
    au-dessus de ce que la branche peut produire.

    Sans cette remise à zéro, la valeur survit à la panne et contamine tout :
    marge, goulot, couleur de la liaison, infobulle. Un lien coupé porte ZÉRO —
    et zéro, pas « inconnu » : on ne s'abstient pas faute de mesure, on sait.
    """
    health = {
        "state": "down", "traffic_mbps": 18645.0,
        "traffic_a_to_b_mbps": 18645.0, "traffic_b_to_a_mbps": 0.0,
        "occupancy_pct": 55.0, "saturated": True,
        "occupancy_a_to_b_pct": 55.0, "occupancy_b_to_a_pct": 0.0,
    }
    site_topology_service.silence_dead_link(health)

    assert health["traffic_mbps"] == 0.0
    assert health["traffic_a_to_b_mbps"] == 0.0
    assert health["traffic_b_to_a_mbps"] == 0.0
    assert health["occupancy_pct"] is None
    assert health["saturated"] is False


def test_a_cut_fibre_is_not_reported_as_an_idle_backbone():
    """« Debout mais sans trafic » et « coupée » appellent deux gestes
    différents : le premier est une anomalie à investiguer, le second une panne
    déjà identifiée. Confondre les deux noierait le vrai signal."""
    group = internet_routes(
        _merged(down={_p("A2 CT1", "A2 HQ")}, idle_fibre={_p("A2 CT1", "A2 HQ")}),
        "A2 HQ",
    )["A2 CT1"]

    direct = next(p for p in group["paths"] if p["hop_count"] == 1)
    assert direct["usable"] is False
    assert direct["fibre_idle_hops"] == [], "coupée ≠ debout et inerte"
