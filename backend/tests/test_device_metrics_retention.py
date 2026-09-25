"""Rétention sur `device_metrics` et bascule des fenêtres 7 j / 30 j.

`device_metrics` était la seule table du projet qui grossissait sans fin
(58,4 M lignes / 5,9 Go au 2026-09-24) parce que la consommation se CALCULE
par différences successives sur ses compteurs cumulés. Le résumé quotidien
(`client_consumption_daily`) fait cette soustraction une fois pour toutes, ce
qui libère enfin les relevés bruts.

Ce qui est verrouillé ici, ce sont les trois façons dont cette purge pourrait
effacer des données qu'on ne saurait pas reconstituer — et la bascule qui l'a
rendue possible.
"""

import ast
import datetime
import inspect
import textwrap

import pytest

from app.core.config import get_settings
from app.services import consumption_service
from app.tasks import jobs


def _source(fn) -> str:
    return textwrap.dedent(inspect.getsource(fn))


# --- La purge elle-même ------------------------------------------------------

def test_retention_only_ever_deletes_byte_counters():
    """Une purge `WHERE collected_at < cutoff` toute seule effacerait aussi les
    métriques ÉCRASÉES EN PLACE (signal, latence, capacité : une seule ligne par
    (device, metric)). Un équipement qui n'est plus interrogé depuis des mois
    perdrait sa dernière valeur connue et disparaîtrait de `/lr-health` — en
    silence, sans qu'aucune alerte ne le signale."""
    src = _source(jobs.device_metrics_retention_job)
    assert "DELETE FROM device_metrics" in src
    assert "metric_name = ANY" in src, (
        "la purge ne filtre plus sur les métriques : elle efface tout"
    )
    assert "COUNTER_METRICS" in src, (
        "la liste des métriques purgeables doit venir de consumption_service, "
        "jamais être recopiée ici"
    )


def test_purgeable_metrics_are_exactly_the_four_counters():
    """Ajouter une clé à cette liste, c'est autoriser sa purge par date. Les
    quatre compteurs d'octets sont les SEULES métriques empilées en série ;
    tout le reste est collapsé."""
    assert set(consumption_service.COUNTER_METRICS) == {
        "peer_tx_bytes", "peer_rx_bytes", "radio_rx_bytes", "radio_tx_bytes",
    }
    assert consumption_service.COUNTER_METRICS is consumption_service._COUNTER_METRICS


def test_retention_never_purges_beyond_what_is_summarised():
    """Purger un jour que le résumé n'a pas totalisé perd sa consommation pour
    de bon. Si le job de nuit est en échec depuis trois jours, ces trois jours
    doivent rester intacts."""
    src = _source(jobs.device_metrics_retention_job)
    assert "last_rolled_day" in src
    assert "rolled is None" in src, (
        "résumé vide (historique jamais rempli) = aucune purge, jamais"
    )
    assert "rolled_bound" in src and "cutoff = rolled_bound" in src


def test_retention_has_a_floor_below_the_sliding_24h_window():
    """La fenêtre 24 h de `/clients` lit encore le brut et GLISSE : à 00 h 05
    elle redescend à 00 h 05 la veille. Un réglage à 0 dans l'env ne doit pas
    pouvoir la vider."""
    assert jobs._RETENTION_FLOOR_DAYS >= 2
    src = _source(jobs.device_metrics_retention_job)
    assert "_RETENTION_FLOOR_DAYS" in src and "max(" in src


def test_retention_deletes_in_committed_batches():
    """Jamais une transaction unique sur des millions de lignes — elle
    tiendrait des verrous pendant que les polls écrivent dans la même table."""
    src = _source(jobs.device_metrics_retention_job)
    assert "LIMIT :batch" in src
    assert "await session.commit()" in src


def test_retention_is_registered_in_the_fast_group():
    """Un job dont le groupe n'a pas de conteneur DISPARAÎT en silence."""
    assert "device_metrics_retention" in jobs._FAST_JOB_IDS
    assert "client_consumption_matview_refresh" not in jobs._FAST_JOB_IDS
    assert "client_consumption_7d_refresh" not in jobs._FAST_JOB_IDS


def test_retention_can_be_disabled_and_has_a_default_window():
    settings = get_settings()
    assert settings.device_metrics_retention_days >= jobs._RETENTION_FLOOR_DAYS
    assert isinstance(settings.device_metrics_retention_enabled, bool)


# --- La bascule qui l'a rendue possible --------------------------------------

def test_no_window_reads_the_matviews_any_more():
    """Tant qu'une lecture relit plusieurs semaines de relevés bruts, la purge
    afficherait un total FAUX sans la moindre erreur pour le dire : la fenêtre
    30 j serait calculée sur les 7 jours restants."""
    src = inspect.getsource(consumption_service)
    tree = ast.parse(src)
    strings = {
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    # On cherche une LECTURE (`FROM <vue>`), pas la simple mention du nom :
    # le module documente justement pourquoi ces deux matviews ont disparu, et
    # cette explication doit pouvoir rester.
    for view in ("client_consumption_30d", "client_consumption_7d"):
        assert not any(f"FROM {view}" in s for s in strings), (
            f"{view} est encore interrogée alors que la matview est supprimée"
        )


def test_the_two_refresh_jobs_are_gone():
    """Chaque REFRESH relisait la fenêtre entière de device_metrics (>19 min
    d'E/S, incident du 2026-07-20) ET obligeait à conserver 30 jours de brut."""
    assert not hasattr(jobs, "client_consumption_matview_refresh_job")
    assert not hasattr(jobs, "client_consumption_7d_refresh_job")


@pytest.mark.parametrize(
    "period,expected_days_back",
    [("7d", 6), ("30d", 29)],
)
def test_calendar_window_counts_n_days_not_n_plus_one(period, expected_days_back):
    """Le résumé rend des journées ENTIÈRES : partir de `aujourd'hui - 7 jours`
    couvrirait 8 journées calendaires et surestimerait le total d'une journée
    complète."""
    now = datetime.datetime(2026, 9, 25, 14, 37, 12, tzinfo=datetime.UTC)
    start = consumption_service.calendar_period_start(now, period)
    assert start.tzinfo is not None
    assert (now.date() - start.date()).days == expected_days_back
    assert start.time() == datetime.time.min, (
        "la borne doit tomber sur un début de journée, sinon elle annonce une "
        "fenêtre que le chiffre ne respecte pas"
    )


def test_only_24h_is_still_a_sliding_window():
    """C'est la seule fenêtre qui lise encore le brut — donc la seule qui fixe
    un plancher à la rétention."""
    assert set(consumption_service._PERIOD_TO_TIMEDELTA) == {"24h"}
    assert set(consumption_service._CALENDAR_PERIOD_DAYS) == {"7d", "30d"}
