"""
Débit dérivé des compteurs d'octets — `jobs._derive_throughput_from_counters`.

Sert les radios qui ne publient AUCUN débit instantané : le LiteBeam M5
(airOS 6) n'en expose ni dans `wstalist`, ni dans `status.cgi`, ni dans
`ifstats.cgi`. Son propre écran « Monitor > Throughput » fait exactement ce
calcul : lire les compteurs cumulés, diviser le delta par le temps écoulé.

Les valeurs du premier test viennent d'un M5 réel (fw v6.3.24 XW, 10.135.2.176,
2026-07-21) : deux lectures à 6 s d'intervalle, +76480 octets.

PostgreSQL réel via la fixture `db` (transaction annulée en fin de test).
"""

import datetime

from sqlalchemy import select

from app.models.device import Device, Lr
from app.models.device_metric import DeviceMetric
from app.tasks.jobs import persist_device_metrics

_COUNTERS = ("radio_rx_bytes", "radio_tx_bytes")
_UNITS = {"radio_rx_bytes": "B", "radio_tx_bytes": "B"}


async def _make_m5(db) -> Device:
    """Un LR LiteBeam M5 minimal."""
    device = Lr(
        name="Test M5",
        ip_address="10.99.0.176",
        status="up",
        model_variant="litebeam_m5",
    )
    db.add(device)
    await db.flush()
    return device


async def _persist(db, device_id, metrics, now):
    await persist_device_metrics(
        db, device_id, metrics, _UNITS, now=now, throughput_from_counters=_COUNTERS,
    )
    await db.flush()


async def test_derives_real_m5_rate_from_counter_delta(db):
    """+76480 octets en 6 s → ~102 kb/s (mesuré sur le M5 réel)."""
    dev = await _make_m5(db)
    t0 = datetime.datetime.now(datetime.UTC)

    await _persist(db, dev.id, {"radio_rx_bytes": 1_103_677_716.0}, t0)
    m = {"radio_rx_bytes": 1_103_754_196.0}
    await _persist(db, dev.id, m, t0 + datetime.timedelta(seconds=6))

    # 76480 o × 8 ÷ 6 s = 101 973 bit/s
    assert m["dl_throughput_mbps"] == 0.102


async def test_first_cycle_leaves_a_gap_not_a_zero(db):
    """Sans relevé précédent, la clé reste ABSENTE.

    Un 0 se lirait « le client ne consomme rien » sur le graphe, alors que la
    réalité est « on ne sait pas encore ».
    """
    dev = await _make_m5(db)
    m = {"radio_rx_bytes": 1_000_000.0}
    await _persist(db, dev.id, m, datetime.datetime.now(datetime.UTC))

    assert "dl_throughput_mbps" not in m


async def test_counter_reset_is_ignored(db):
    """Un reboot remet les compteurs à 0 → delta négatif, à ne pas publier."""
    dev = await _make_m5(db)
    t0 = datetime.datetime.now(datetime.UTC)

    await _persist(db, dev.id, {"radio_rx_bytes": 9_000_000.0}, t0)
    m = {"radio_rx_bytes": 1_000.0}  # redémarrage
    await _persist(db, dev.id, m, t0 + datetime.timedelta(seconds=60))

    assert "dl_throughput_mbps" not in m


async def test_direct_throughput_is_never_overwritten(db):
    """Une famille qui publie son débit (LTU/airOS) garde sa valeur directe.

    La dérivation est un repli, pas une réécriture : la moyenne sur l'intervalle
    de poll est moins fidèle que la mesure instantanée du firmware.
    """
    dev = await _make_m5(db)
    t0 = datetime.datetime.now(datetime.UTC)

    await _persist(db, dev.id, {"radio_rx_bytes": 1_000_000.0}, t0)
    m = {"radio_rx_bytes": 9_000_000.0, "dl_throughput_mbps": 42.0}
    await _persist(db, dev.id, m, t0 + datetime.timedelta(seconds=60))

    assert m["dl_throughput_mbps"] == 42.0


async def test_station_direction_rx_is_downlink(db):
    """Sur une station, ce que la radio REÇOIT est le descendant du client.

    Le sens est fourni par l'appelant : sur un AP il serait inversé. Ce test
    verrouille la convention câblée dans `lr_internet_probe_job`.
    """
    dev = await _make_m5(db)
    t0 = datetime.datetime.now(datetime.UTC)

    await _persist(db, dev.id, {"radio_rx_bytes": 0.0, "radio_tx_bytes": 0.0}, t0)
    m = {"radio_rx_bytes": 10_000_000.0, "radio_tx_bytes": 1_000_000.0}
    await _persist(db, dev.id, m, t0 + datetime.timedelta(seconds=10))

    # Beaucoup reçu, peu émis = un abonné qui télécharge.
    assert m["dl_throughput_mbps"] > m["ul_throughput_mbps"]
    assert m["dl_throughput_mbps"] == 8.0   # 10 Mo × 8 ÷ 10 s
    assert m["ul_throughput_mbps"] == 0.8


async def test_counter_rows_are_still_persisted(db):
    """La dérivation lit le relevé précédent AVANT l'écriture du nouveau.

    Si l'ordre s'inversait, elle comparerait la valeur à elle-même et
    publierait 0 partout — d'où ce garde-fou.
    """
    dev = await _make_m5(db)
    t0 = datetime.datetime.now(datetime.UTC)

    await _persist(db, dev.id, {"radio_rx_bytes": 100.0}, t0)
    await _persist(db, dev.id, {"radio_rx_bytes": 200.0}, t0 + datetime.timedelta(seconds=10))

    rows = await db.execute(
        select(DeviceMetric.metric_value)
        .where(
            DeviceMetric.device_id == dev.id,
            DeviceMetric.metric_name == "radio_rx_bytes",
        )
        .order_by(DeviceMetric.collected_at)
    )
    # Compteur cumulé = HISTORY_METRICS → les deux relevés sont conservés.
    assert [r[0] for r in rows] == [100.0, 200.0]
