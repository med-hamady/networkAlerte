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


def test_uisp_seeing_it_live_confirms_the_ip():
    """Station active MAINTENANT → l'IP que UISP donne est actuelle, pas un souvenir."""
    assert is_confirmed("active", None, _SINCE) is True


def test_radio_rediscovery_after_the_bad_write_confirms_it():
    """Le radio a réécrit l'IP depuis : lui voit le terrain, il fait foi."""
    assert is_confirmed("disconnected", _at(13, 15), _SINCE) is True


def test_radio_sighting_before_the_bad_write_confirms_nothing():
    """Une découverte ANTÉRIEURE ne dit rien de ce que le passage fautif a écrit."""
    assert is_confirmed("disconnected", _at(6, 0), _SINCE) is False


def test_no_source_at_all_means_the_ip_is_a_guess():
    assert is_confirmed("disconnected", None, _SINCE) is False
    assert is_confirmed(None, None, _SINCE) is False


def test_naive_timestamp_is_read_as_utc():
    """Une colonne timestamptz peut remonter naïve selon le driver — pas de crash.

    Comparer naïf et aware lèverait un TypeError au milieu d'un nettoyage de
    masse, à mi-parcours des écritures.
    """
    assert is_confirmed("disconnected", _at(13, 15).replace(tzinfo=None), _SINCE) is True
