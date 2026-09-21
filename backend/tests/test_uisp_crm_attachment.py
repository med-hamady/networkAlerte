"""Rattachement d'un abonné au client CRM dans UISP — lu par le sync des stations.

La station porte son site ; c'est le SITE qui porte le client CRM. Trois cas que
la fiche distingue et qu'il ne faut pas fondre : rattaché, « unknown » (pas de
site), et rattaché à un site créé à la main sans client CRM.
"""

from app.services.uisp_sync_service import crm_attachment

_SITES = {
    "site-client": {
        "identification": {"id": "site-client", "name": "Ba, Amadou"},
        "ucrm": {"client": {"id": 1361, "name": "Ba, Amadou"}},
    },
    "site-manuel": {"identification": {"id": "site-manuel", "name": "Haydara, Ousmane"}},
}


def _station(site: dict | None) -> dict:
    return {"identification": {"mac": "e4:38:83:a8:da:74", "site": site}}


def test_attached_station_carries_the_crm_client():
    got = crm_attachment(_station({"id": "site-client", "name": "Ba, Amadou"}), _SITES)
    # L'id CRM arrive parfois en entier : stocké en chaîne, comme partout ailleurs.
    assert got == {
        "uisp_site_name": "Ba, Amadou",
        "uisp_crm_client_id": "1361",
        "uisp_crm_client_name": "Ba, Amadou",
    }


def test_station_without_site_is_unknown():
    got = crm_attachment(_station(None), _SITES)
    assert got == {"uisp_site_name": None, "uisp_crm_client_id": None, "uisp_crm_client_name": None}


def test_site_without_crm_client_keeps_its_name_but_no_client():
    got = crm_attachment(_station({"id": "site-manuel", "name": "Haydara, Ousmane"}), _SITES)
    assert got["uisp_site_name"] == "Haydara, Ousmane"
    assert got["uisp_crm_client_id"] is None
    assert got["uisp_crm_client_name"] is None
