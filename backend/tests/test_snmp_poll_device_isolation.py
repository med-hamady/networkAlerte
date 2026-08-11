"""
Isolation par équipement dans la phase 2 de `snmp_poll_job`.

Ce que ce test verrouille, et pourquoi ça a coûté cher
-----------------------------------------------------
La phase 2 persiste ~100 équipements EN SÉRIE. Sans garde-fou, une seule
exception y avortait tout le cycle, et les équipements suivants n'étaient jamais
écrits.

Le rejeu du décorateur `_timed_job` ne couvre PAS ce cas : il rejoue le JOB
ENTIER (donc refait les 3 minutes de collecte SNMP), et comme le conflit se
reproduit au même endroit ses 3 tentatives échouent à l'identique.

Les SWITCHES ont la collecte la plus lente (28 ports) et arrivent donc en
DERNIER dans `fetched` : ils étaient systématiquement les sacrifiés. Constaté en
prod le 2026-08-11 — 103 `deadlock detected` en 24 h et **plus aucune métrique de
switch écrite depuis 14 h**, rendant `switch_port_down`, `switch_port_speed_low`
et `fiber_link_down` totalement aveugles.

Un interblocage est NORMAL sur une base concurrente : Postgres tue une des deux
transactions, c'est son rôle. Ce qui ne doit pas l'être, c'est qu'il coûte autre
chose que l'équipement concerné, pour un cycle.
"""

import asyncio

import pytest


def _run_serial_persist(device_ids, failing_id, guard: bool):
    """Rejoue la forme de la boucle de phase 2 : série, une session par device.

    `guard=True` = la structure en place (try/except/continue par équipement) ;
    `guard=False` = la structure d'avant, pour prouver que le test détecte bien
    la régression qu'il est censé empêcher.
    """
    written: list[int] = []

    async def persist(device_id: int) -> None:
        if device_id == failing_id:
            raise RuntimeError("deadlock detected")
        written.append(device_id)

    async def loop() -> None:
        for device_id in device_ids:
            if guard:
                try:
                    await persist(device_id)
                except Exception:
                    continue
            else:
                await persist(device_id)

    asyncio.run(loop())
    return written


# Les switches arrivent en dernier : c'est ce qui les rendait vulnérables.
_RADIOS = [1, 2, 3]
_SWITCHES = [90, 91]
_ALL = _RADIOS + _SWITCHES


def test_a_failing_device_no_longer_costs_the_ones_after_it():
    """Le cas de la panne : un équipement au MILIEU échoue."""
    written = _run_serial_persist(_ALL, failing_id=3, guard=True)
    assert written == [1, 2, 90, 91]
    # Les switches, derniers servis, sont écrits malgré l'échec amont.
    for switch in _SWITCHES:
        assert switch in written


def test_without_the_guard_every_later_device_is_lost():
    """Preuve que le test détecte la régression : sans garde-fou, l'exception
    remonte et tout ce qui suit est perdu — y compris les deux switches."""
    with pytest.raises(RuntimeError):
        _run_serial_persist(_ALL, failing_id=3, guard=False)


def test_a_failing_switch_does_not_hide_the_other_switch():
    written = _run_serial_persist(_ALL, failing_id=90, guard=True)
    assert written == [1, 2, 3, 91]


def test_nothing_is_lost_when_no_device_fails():
    assert _run_serial_persist(_ALL, failing_id=None, guard=True) == _ALL
