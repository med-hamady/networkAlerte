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


# --- La fenêtre couverte doit être EXACTEMENT celle d'avant -----------------

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    """Session factice : enregistre les bornes de chaque requête, n'en exécute
    aucune. Ce qu'on vérifie ici, c'est le DÉCOUPAGE de la fenêtre."""

    def __init__(self, rolled):
        self._rolled = rolled
        self.daily = []   # (from_day, to_day)
        self.live = []    # (cutoff, upper)

    async def scalar(self, _stmt):
        return self._rolled

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "client_consumption_daily" in sql:
            self.daily.append((params["from_day"], params["to_day"]))
        else:
            self.live.append((params["cutoff"], params["upper"]))
        return _FakeResult([])


async def _split(lower, upper, rolled):
    db = _FakeDB(rolled)
    await consumption_service._aggregate_via_daily(db, [1], lower, upper)
    return db


async def test_a_sliding_window_stitches_both_partial_edges():
    """« Les 7 derniers jours » à 14 h 37 commence ET finit en milieu de
    journée. Le résumé ne sait rendre que des journées entières : les deux
    bords doivent être calculés en direct, sinon la fenêtre n'est plus celle
    qu'on annonce."""
    now = datetime.datetime(2026, 9, 25, 14, 37, tzinfo=datetime.UTC)
    lower = now - datetime.timedelta(days=7)      # 18/09 14:37
    db = await _split(lower, now, datetime.date(2026, 9, 24))

    assert db.daily == [(datetime.date(2026, 9, 19), datetime.date(2026, 9, 24))]
    head, tail = sorted(db.live)
    assert head == (lower, datetime.datetime(2026, 9, 19, tzinfo=datetime.UTC))
    assert tail == (datetime.datetime(2026, 9, 25, tzinfo=datetime.UTC), now)


async def test_the_window_has_no_hole_and_no_overlap():
    """Les trois segments doivent se toucher bout à bout : un trou perd de la
    consommation, un recouvrement la compte deux fois."""
    now = datetime.datetime(2026, 9, 25, 9, 12, 44, tzinfo=datetime.UTC)
    lower = now - datetime.timedelta(days=30)
    db = await _split(lower, now, datetime.date(2026, 9, 24))

    from_day, to_day = db.daily[0]
    day_start = datetime.datetime.combine(
        from_day, datetime.time.min, tzinfo=datetime.UTC)
    day_end = datetime.datetime.combine(
        to_day + datetime.timedelta(days=1), datetime.time.min, tzinfo=datetime.UTC)
    head, tail = sorted(db.live)

    assert head[0] == lower and head[1] == day_start
    assert tail[0] == day_end and tail[1] == now


async def test_a_midnight_aligned_range_has_no_head_segment():
    """Une plage de dates commence déjà à minuit : rien à recoudre en tête,
    et surtout pas une requête live inutile sur des relevés purgés."""
    lower = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
    upper = datetime.datetime(2026, 8, 11, tzinfo=datetime.UTC)
    db = await _split(lower, upper, datetime.date(2026, 9, 24))

    assert db.daily == [(datetime.date(2026, 8, 1), datetime.date(2026, 8, 10))]
    assert db.live == [], "aucun bord partiel : tout vient du résumé"


async def test_an_empty_summary_falls_back_to_the_old_behaviour():
    """Avant le remplissage de l'historique, tout doit repasser en live — le
    comportement d'avant, jamais un total amputé."""
    now = datetime.datetime(2026, 9, 25, 14, 37, tzinfo=datetime.UTC)
    lower = now - datetime.timedelta(days=7)
    db = await _split(lower, now, None)

    assert db.daily == []
    assert db.live == [(lower, now)]


def test_all_three_windows_are_sliding_again():
    """Elles l'ont été alignées sur les journées quelques heures le
    2026-09-25 : « 7 j » lu le matin rendait alors jusqu'à 14 % de moins
    qu'avant, sans que rien ne le signale."""
    assert set(consumption_service._PERIOD_TO_TIMEDELTA) == {"24h", "7d", "30d"}
    assert set(consumption_service._DAILY_BACKED_PERIODS) == {"7d", "30d"}


def test_the_retention_floor_follows_the_deepest_window():
    """Écrire 31 en dur tiendrait jusqu'au jour où quelqu'un ajoute un onglet
    « 90 jours » : la purge servirait alors un total amputé, en silence."""
    assert consumption_service.deepest_raw_window_days() == 30
    settings = get_settings()
    floor = max(jobs._RETENTION_FLOOR_DAYS,
                consumption_service.deepest_raw_window_days() + 1)
    assert settings.device_metrics_retention_days >= floor, (
        "le réglage par défaut doit couvrir la fenêtre la plus profonde"
    )
    src = _source(jobs.device_metrics_retention_job)
    assert "deepest_raw_window_days" in src, (
        "le plancher doit être dérivé des fenêtres, jamais écrit en dur"
    )
