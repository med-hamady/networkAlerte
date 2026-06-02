"""Unit tests for incident_service helpers — pure Python, no DB."""

from app.services.incident_service import _TITLE_MAX_LEN, _truncate_title


def test_short_title_unchanged():
    title = "ALERTE CRITIQUE : Rocket indisponible"
    assert _truncate_title(title) == title


def test_title_exactly_max_unchanged():
    title = "x" * _TITLE_MAX_LEN
    assert _truncate_title(title) == title
    assert len(_truncate_title(title)) == _TITLE_MAX_LEN


def test_long_title_truncated_to_max_with_ellipsis():
    # Mirrors the real lr_link_substandard overflow that broke the insert.
    title = (
        "ALERTE CRITIQUE : lien client dégradé sur 30557575- Mohamed lemine "
        "Abdel Maleck — Potentiel du lien 6% (plancher critique 40%) ; "
        "Capacité totale 21.0 Mbps (plancher critique 60.0 Mbps) ; "
        "Rate local 3× (plancher critique 4×) ; "
        "Rate distant 3× (plancher critique 4×)"
    )
    assert len(title) > _TITLE_MAX_LEN
    out = _truncate_title(title)
    assert len(out) == _TITLE_MAX_LEN  # fits VARCHAR(255)
    assert out.endswith("…")
    assert out[:-1] == title[: _TITLE_MAX_LEN - 1]
