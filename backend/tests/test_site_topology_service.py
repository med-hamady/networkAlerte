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

from app.services.site_topology_service import (
    build_edges,
    device_index,
    edge_health,
    infra_site_ids,
    layered_graph,
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
    """Cas réel AT2↔AT1 : 1350 Mb/s sous le plancher AF60 de 1,95 Gb/s.

    Le verdict est calculé côté service contre les réglages réels, pour que la
    carte colore en dégradé exactement ce que la section « Liaisons entre sites »
    liste déjà. Recopier un barème dans le frontend les ferait diverger.
    """
    health = edge_health(
        {"status": "up", "device_type": "airfiber",
         "metrics": {"total_capacity_mbps": 1350, "link_potential_pct": 28}},
        {"status": "up", "device_type": "airfiber", "metrics": {}},
    )
    assert health["floor_mbps"] == 1950.0
    assert health["degraded"] is True


def test_af60_above_floor_is_not_degraded():
    health = edge_health(
        {"status": "up", "device_type": "airfiber",
         "metrics": {"total_capacity_mbps": 3902, "link_potential_pct": 73}},
        {"status": "up", "device_type": "airfiber",
         "metrics": {"total_capacity_mbps": 3902, "link_potential_pct": 74}},
    )
    assert health["degraded"] is False


def test_ptp_litebeam_uses_its_own_floor():
    """303 Mb/s serait « dégradé » au plancher AF60 et ne l'est pas au sien."""
    health = edge_health(
        {"status": "up", "device_type": "ptp_litebeam",
         "metrics": {"total_capacity_mbps": 303}},
        {"status": "up", "device_type": "ptp_litebeam", "metrics": {}},
    )
    assert health["floor_mbps"] == 150.0
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
