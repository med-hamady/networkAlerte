"""GET /client-signal — le Rocket auquel le LR est connecté (`rocket`).

Deux sources, dans cet ordre : la fiche supervisée (`lrs.rocket_id`, arbitrée
radio/UISP), puis le seul NOM d'AP annoncé par UISP. Rien d'autre n'est deviné :
sans rattachement ni nom, `rocket` vaut `None`.
"""

from types import SimpleNamespace

from app.services.client_signal_service import connected_rocket


def _lr(rocket=None, uisp_ap_name=None):
    return SimpleNamespace(rocket=rocket, uisp_ap_name=uisp_ap_name)


def test_supervised_rocket_wins():
    rocket = SimpleNamespace(
        id=12, name="A2-CT1-EST", mac_address="aa:bb:cc:00:11:22",
        ip_address="10.135.144.1", site="A2 CT1", radio_tech="ltu", status="up",
    )
    out = connected_rocket(_lr(rocket=rocket, uisp_ap_name="autre nom"))
    assert out.source == "supervision"
    assert (out.id, out.name, out.ip_address, out.site) == (
        12, "A2-CT1-EST", "10.135.144.1", "A2 CT1",
    )
    assert out.mac == "aa:bb:cc:00:11:22"
    assert out.radio_tech == "ltu"


def test_falls_back_to_uisp_ap_name_without_inventing_fields():
    out = connected_rocket(_lr(uisp_ap_name="A2-TS1-OMNI"))
    assert out.source == "uisp"
    assert out.name == "A2-TS1-OMNI"
    assert out.id is None and out.ip_address is None and out.site is None


def test_no_attachment_is_none():
    assert connected_rocket(_lr()) is None
