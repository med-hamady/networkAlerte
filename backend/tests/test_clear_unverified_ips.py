"""
Règle de décision du script `scripts/clear_unverified_ips.py`.

Nettoyage des IP écrites le 2026-07-22 par un passage du sync UISP sans
garde-fou de fraîcheur : pour une station déconnectée, l'IP annoncée par UISP
n'est qu'un souvenir que le DHCP a pu réattribuer. Une ligne portant l'IP d'un
AUTRE abonné fait pinger le mauvais équipement et ferait appliquer un blocage
FAI au mauvais client — les opérations SSH ciblent l'IP de la fiche.

On ne garde donc l'IP que si une source la confirme ENCORE.
"""

import datetime

from scripts.clear_unverified_ips import is_confirmed, plan_cleanup

_SINCE = datetime.datetime(2026, 7, 22, 12, 40, tzinfo=datetime.UTC)
_IP = "10.135.4.13"      # dans le plan de management


def _at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 7, 22, hour, minute, tzinfo=datetime.UTC)


def _recent(hours: float) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)


def test_uisp_seeing_it_live_confirms_the_ip():
    """Station active MAINTENANT → l'IP que UISP donne est actuelle, pas un souvenir."""
    assert is_confirmed(_IP, "active", None, None, _SINCE, 24) is True


def test_radio_rediscovery_after_the_bad_write_confirms_it():
    """Le radio a réécrit l'IP depuis : lui voit le terrain, il fait foi."""
    assert is_confirmed(_IP, "disconnected", None, _at(13, 15), _SINCE, 24) is True


def test_recently_seen_by_uisp_confirms_it_even_while_down():
    """LE cas du client fondateur : « en outage depuis 1 h », mais IP juste.

    Un booléen `active` l'aurait jetée — vérifiée à la main sur l'équipement,
    elle était pourtant bonne. Une panne d'une heure ne périme pas un bail DHCP.
    """
    assert is_confirmed(_IP, "disconnected", _recent(1), None, _SINCE, 24) is True


def test_long_gone_station_does_not_confirm_anything():
    """Vue il y a 3 semaines : son adresse a pu être redonnée à un autre abonné."""
    assert is_confirmed(_IP, "disconnected", _recent(24 * 21), None, _SINCE, 24) is False


def test_radio_sighting_before_the_bad_write_confirms_nothing():
    """Une découverte ANTÉRIEURE ne dit rien de ce que le passage fautif a écrit."""
    assert is_confirmed(_IP, "disconnected", None, _at(6, 0), _SINCE, 24) is False


def test_no_source_at_all_means_the_ip_is_a_guess():
    assert is_confirmed(_IP, "disconnected", None, None, _SINCE, 24) is False
    assert is_confirmed(_IP, None, None, None, _SINCE, 24) is False


def test_trust_window_is_a_parameter():
    """Fenêtre réglable : miroir de UISP_IP_TRUST_HOURS côté sync."""
    assert is_confirmed(_IP, "disconnected", _recent(10), None, _SINCE, 24) is True
    assert is_confirmed(_IP, "disconnected", _recent(10), None, _SINCE, 6) is False


def test_naive_timestamps_are_read_as_utc():
    """Une colonne timestamptz peut remonter naïve — comparer naïf et aware
    lèverait un TypeError à mi-parcours d'un nettoyage de masse."""
    assert is_confirmed(_IP, "disconnected", None, _at(13, 15).replace(tzinfo=None), _SINCE, 24) is True
    assert is_confirmed(_IP, "disconnected", _recent(1).replace(tzinfo=None), None, _SINCE, 24) is True


def test_an_ip_outside_the_management_plan_is_never_confirmed():
    """LAN d'usine / APIPA : fausse par construction, meme vue a l'instant.

    Sans ce prealable, une station que UISP voit ACTIVE tout en annoncant son
    LAN gardait une adresse par laquelle nous ne joignons rien (constate en
    prod : `192.168.1.71` encore en base, rescape d'avant le garde-fou).
    """
    assert is_confirmed("192.168.1.71", "active", _recent(0.1), None, _SINCE, 24) is False
    assert is_confirmed("169.254.3.4", "active", None, _at(13, 15), _SINCE, 24) is False


# ── plan_cleanup : decider d'abord, muter ensuite ───────────────────────────

class _FakeLr:
    """Juste ce que `plan_cleanup` lit — pas besoin de base pour decider."""

    def __init__(self, ip, uisp_status=None, uisp_last_seen=None, last_discovered_at=None):
        self.id = 1
        self.name = "client"
        self.ip_address = ip
        self.uisp_status = uisp_status
        self.uisp_last_seen = uisp_last_seen
        self.last_discovered_at = last_discovered_at


def test_plan_keeps_the_ip_it_is_about_to_remove():
    """Le couple (ligne, IP retiree) porte l'ADRESSE, pas None.

    Regression vecue en prod : la decision et la mutation etaient dans la meme
    boucle, l'IP etait donc effacee avant d'etre affichee et le formatage
    tombait sur un None — le script mourait au milieu des mutations. Rien
    n'etait committe, mais le nettoyage n'avait pas lieu et l'erreur
    ressemblait a une panne de fond.
    """
    doomed = _FakeLr("10.135.9.24", uisp_status="disconnected")
    kept, cleared = plan_cleanup([doomed], _SINCE, 24)

    assert kept == []
    assert cleared == [(doomed, "10.135.9.24")]


def test_plan_does_not_mutate_anything():
    """`plan_cleanup` decide seulement — l'ecriture appartient a l'appelant."""
    row = _FakeLr("10.135.9.24", uisp_status="disconnected")
    plan_cleanup([row], _SINCE, 24)
    assert row.ip_address == "10.135.9.24"


def test_plan_splits_kept_and_cleared():
    live = _FakeLr(_IP, uisp_status="active")
    stale = _FakeLr("10.135.9.24", uisp_status="disconnected")
    kept, cleared = plan_cleanup([live, stale], _SINCE, 24)
    assert kept == [live]
    assert [ip for _row, ip in cleared] == ["10.135.9.24"]
