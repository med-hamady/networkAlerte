"""Rapport PDF des coupures par tranche horaire (`night_outage_report_service`).

Verrouille la règle de comptage décidée par l'opérateur le 2026-09-24 : une
coupure n'est comptée que si elle COMMENCE dans la tranche, et sa durée comptée
s'arrête à la fin de la tranche.
"""

import datetime
from types import SimpleNamespace

from app.services.night_outage_report_service import (
    RawOutage,
    already_down_at_opening,
    assemble_report,
    build_nights,
    fmt_duration,
    outages_in_night,
    render_pdf,
)

UTC = datetime.UTC
D1 = datetime.date(2026, 9, 1)
NOW = datetime.datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def at(day: int, h: int, m: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 9, day, h, m, tzinfo=UTC)


def night_0_8(day: int = 2):
    (n,) = build_nights(datetime.date(2026, 9, day), datetime.date(2026, 9, day), 0, 8, NOW)
    return n


# ------------------------------------------------------------- les tranches
def test_one_window_per_day_inclusive():
    nights = build_nights(D1, datetime.date(2026, 9, 3), 0, 8, NOW)
    assert [(n.start, n.end) for n in nights] == [
        (at(1, 0), at(1, 8)),
        (at(2, 0), at(2, 8)),
        (at(3, 0), at(3, 8)),
    ]
    assert all(n.complete for n in nights)


def test_window_crossing_midnight_closes_next_day():
    (n,) = build_nights(D1, D1, 22, 6, NOW)
    assert (n.start, n.end) == (at(1, 22), at(2, 6))


def test_future_window_skipped_and_current_one_clipped_to_now():
    now = at(5, 3)
    nights = build_nights(datetime.date(2026, 9, 4), datetime.date(2026, 9, 6), 0, 8, now)
    assert len(nights) == 2  # le 6 n'a pas commencé
    assert nights[-1].end == now and not nights[-1].complete


# ------------------------------------------------------------- la règle de comptage
def test_outage_started_before_window_is_not_counted():
    raws = [RawOutage(1, at(1, 23, 30), at(2, 3))]
    night = night_0_8()
    assert outages_in_night(raws, night, NOW) == []
    # … mais elle est signalée comme « déjà coupé à l'ouverture ».
    assert already_down_at_opening(raws, night, NOW) == at(1, 23, 30)


def test_duration_counted_until_window_end_only():
    raws = [RawOutage(1, at(2, 7), at(2, 10))]
    (o,) = outages_in_night(raws, night_0_8(), NOW)
    assert o.counted_seconds == 3600  # 07:00 → 08:00, pas jusqu'à 10:00
    assert o.ended_at == at(2, 10)  # la fin réelle reste affichée


def test_outage_after_window_is_not_counted():
    raws = [RawOutage(1, at(2, 8), at(2, 9))]  # 08:00 = borne de fin, exclue
    assert outages_in_night(raws, night_0_8(), NOW) == []


def test_flaps_merged_inside_the_window_only():
    # 23:58 → 23:59 est hors tranche ; 00:02 est une vraie coupure de la tranche
    # et ne doit pas être avalée par la fusion avec celle d'avant minuit.
    raws = [
        RawOutage(1, at(1, 23, 58), at(1, 23, 59)),
        RawOutage(1, at(2, 0, 2), at(2, 0, 5)),
        RawOutage(1, at(2, 0, 7), at(2, 0, 10)),  # 2 min d'écart → fusionnée
    ]
    (o,) = outages_in_night(raws, night_0_8(), NOW)
    assert o.started_at == at(2, 0, 2)
    assert o.flap_count == 2
    assert o.counted_seconds == 8 * 60


# ------------------------------------------------------------- l'agrégat
def _dev(id_, site, dtype="uisp_switch", name=None):
    return SimpleNamespace(id=id_, site=site, device_type=dtype, name=name or f"dev{id_}")


def test_every_switch_site_listed_even_without_outage():
    devices = [_dev(1, "A2 AT1"), _dev(2, "A2 CT1"), _dev(3, "A2 AT1", "ltu_rocket")]
    raws = {
        1: [RawOutage(1, at(2, 1), at(2, 2))],
        3: [RawOutage(3, at(2, 4), at(2, 5))],
    }
    nights = build_nights(D1, datetime.date(2026, 9, 2), 0, 8, NOW)
    rep = assemble_report(
        devices,
        raws,
        nights,
        first_day=D1,
        last_day=datetime.date(2026, 9, 2),
        from_hour=0,
        to_hour=8,
        now=NOW,
    )
    by_site = {s.site: s for s in rep.sites}
    assert set(by_site) == {"A2 AT1", "A2 CT1"}
    at1 = by_site["A2 AT1"]
    # Le Rocket n'entre PAS dans les chiffres du site : seuls les switches comptent.
    assert (at1.episodes, at1.downtime_seconds, at1.nights_hit) == (1, 3600, 1)
    assert round(at1.availability_pct, 4) == round(100 * (1 - 3600 / (16 * 3600)), 4)
    assert by_site["A2 CT1"].availability_pct == 100.0
    # … et n'apparaît nulle part dans le détail (décision opérateur 2026-09-24).
    assert [e.device_name for e in rep.switch_entries[1]] == ["dev1"]


def test_pdf_renders_with_and_without_outages():
    devices = [_dev(1, "A2 AT1"), _dev(2, "A2 CT1")]
    raws = {1: [RawOutage(1, at(2, 1), None), RawOutage(1, at(1, 23), at(2, 1))]}
    nights = build_nights(D1, datetime.date(2026, 9, 3), 0, 8, NOW)
    rep = assemble_report(
        devices,
        raws,
        nights,
        first_day=D1,
        last_day=datetime.date(2026, 9, 3),
        from_hour=0,
        to_hour=8,
        now=NOW,
    )
    pdf = render_pdf(rep)
    assert pdf.startswith(b"%PDF")

    empty = assemble_report([], {}, [], first_day=D1, last_day=D1, from_hour=0, to_hour=8, now=NOW)
    assert render_pdf(empty).startswith(b"%PDF")


def test_fmt_duration():
    assert fmt_duration(45) == "45s"
    assert fmt_duration(600) == "10 min"
    assert fmt_duration(7680) == "2h 08min"
    assert fmt_duration(3599 + 3600) == "2h"
