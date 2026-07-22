"""
« Hors supervision » — quand les DEUX sources se taisent sur un abonné.

Un LR sans IP sort du sweep de ping (`_ping_sweep` filtre `ip_address IS NOT
NULL`) : plus rien ne mesure son état, il reste en `status='unknown'`. Si le
contrôleur UISP ne l'a pas vu non plus depuis `OUT_OF_SUPERVISION_DAYS`, aucune
source ne dit quoi que ce soit de lui.

Ce n'est ni une panne constatée (on n'a rien vu tomber) ni un accès actif. En
prod le 2026-07-22 ils étaient 124 sur ~1000 — 12 % du parc — comptés comme
« accès actif » et affichés en rouge « INCONNU », donc lus comme des pannes.

La règle est écrite DEUX fois — ici en Python (fiche équipement) et en SQL dans
`fn_access_clients` (migration `cc3d4e5f6a7b`, page /access). Ces tests fixent
la version Python ; les deux doivent rester d'accord.
"""

import datetime

import pytest

from app.schemas.device import is_out_of_supervision


def _ago(days: float) -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)


def test_a_device_with_an_ip_is_always_supervised():
    """Avec une IP il reste dans le sweep de ping — on le mesure, donc on sait.

    Même si UISP l'a perdu de vue depuis des mois : notre propre ping fait foi.
    """
    assert is_out_of_supervision("10.135.4.13", _ago(400)) is False
    assert is_out_of_supervision("10.135.4.13", None) is False


def test_no_ip_and_uisp_silent_for_days_is_out_of_supervision():
    assert is_out_of_supervision(None, _ago(30)) is True


def test_no_ip_but_uisp_saw_it_recently_is_not_out_of_supervision():
    """Le cas qui compte : UISP le voit → il est vivant, il va revenir seul.

    C'est la population récupérable (9 des 124 en prod) : le prochain cycle de
    son AP lui rend une IP du plan de management. La signaler « hors
    supervision » masquerait le fait qu'elle est en cours de reprise.
    """
    assert is_out_of_supervision(None, _ago(0.5)) is False


def test_never_seen_by_uisp_counts_as_silence():
    """`uisp_last_seen` nul = jamais vu = jamais mesuré, pas « récemment vu ».

    ~50 des 124 lignes de prod étaient dans ce cas : créées, dépossédées de leur
    IP avant le premier ping, jamais rien affiché de vrai.
    """
    assert is_out_of_supervision(None, None) is True


def test_naive_timestamp_is_read_as_utc_not_crashed_on():
    """Un `uisp_last_seen` sans fuseau ne doit pas faire exploser la fiche.

    Selon le driver, une colonne timestamptz peut remonter naïve ; comparer
    naïf et aware lève un TypeError qui casserait la page équipement entière.
    """
    naive_old = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
    ).replace(tzinfo=None)
    assert is_out_of_supervision(None, naive_old) is True


@pytest.mark.parametrize("days", [1, 7, 90])
def test_threshold_follows_the_setting(monkeypatch, days):
    """Le seuil vient du `.env` : l'opérateur l'ajuste sans migration."""
    from app.core import config

    # Champ pydantic (pas une property) : il se patche sur l'INSTANCE mise en
    # cache par `get_settings`, pas sur la classe où il n'existe pas.
    monkeypatch.setattr(config.get_settings(), "out_of_supervision_days", days)
    assert is_out_of_supervision(None, _ago(days + 1)) is True
    assert is_out_of_supervision(None, _ago(days - 0.5)) is False
