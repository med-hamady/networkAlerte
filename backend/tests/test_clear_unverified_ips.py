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

from scripts.clear_unverified_ips import is_confirmed

_SINCE = datetime.datetime(2026, 7, 22, 12, 40, tzinfo=datetime.UTC)


def _at(hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 7, 22, hour, minute, tzinfo=datetime.UTC)


def _recent(hours: float) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)


def test_uisp_seeing_it_live_confirms_the_ip():
    """Station active MAINTENANT → l'IP que UISP donne est actuelle, pas un souvenir."""
    assert is_confirmed("active", None, None, _SINCE, 24) is True


def test_radio_rediscovery_after_the_bad_write_confirms_it():
    """Le radio a réécrit l'IP depuis : lui voit le terrain, il fait foi."""
    assert is_confirmed("disconnected", None, _at(13, 15), _SINCE, 24) is True


def test_recently_seen_by_uisp_confirms_it_even_while_down():
    """LE cas du client fondateur : « en outage depuis 1 h », mais IP juste.

    Un booléen `active` l'aurait jetée — vérifiée à la main sur l'équipement,
    elle était pourtant bonne. Une panne d'une heure ne périme pas un bail DHCP.
    """
    assert is_confirmed("disconnected", _recent(1), None, _SINCE, 24) is True


def test_long_gone_station_does_not_confirm_anything():
    """Vue il y a 3 semaines : son adresse a pu être redonnée à un autre abonné."""
    assert is_confirmed("disconnected", _recent(24 * 21), None, _SINCE, 24) is False


def test_radio_sighting_before_the_bad_write_confirms_nothing():
    """Une découverte ANTÉRIEURE ne dit rien de ce que le passage fautif a écrit."""
    assert is_confirmed("disconnected", None, _at(6, 0), _SINCE, 24) is False


def test_no_source_at_all_means_the_ip_is_a_guess():
    assert is_confirmed("disconnected", None, None, _SINCE, 24) is False
    assert is_confirmed(None, None, None, _SINCE, 24) is False


def test_trust_window_is_a_parameter():
    """Fenêtre réglable : miroir de UISP_IP_TRUST_HOURS côté sync."""
    assert is_confirmed("disconnected", _recent(10), None, _SINCE, 24) is True
    assert is_confirmed("disconnected", _recent(10), None, _SINCE, 6) is False


def test_naive_timestamps_are_read_as_utc():
    """Une colonne timestamptz peut remonter naïve — comparer naïf et aware
    lèverait un TypeError à mi-parcours d'un nettoyage de masse."""
    assert is_confirmed("disconnected", None, _at(13, 15).replace(tzinfo=None), _SINCE, 24) is True
    assert is_confirmed("disconnected", _recent(1).replace(tzinfo=None), None, _SINCE, 24) is True
