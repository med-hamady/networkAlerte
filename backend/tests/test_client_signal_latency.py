"""Latence live de GET /client-signal — bandes de qualité et cas « pas de mesure ».

L'API rend désormais deux choses au système tiers : la qualité du signal (lue en
base) et une latence **mesurée à l'appel** (le LR ping Internet, 5 paquets de
56 o). Ces tests fixent la grille opérateur du 2026-07-22 :

    < 80 excellent | 80-100 très bien | 100-120 bien | 120-150 mauvaise | ≥ 150 catastrophique

et surtout la règle qui compte pour le consommateur : **une mesure qui n'aboutit
pas ne devient jamais un chiffre**. Un LR sans transit doit rendre
`indetermine` + la raison, pas 0 ms (qui se lirait « excellent »).
"""

import pytest

from app.core.config import get_settings
from app.services.client_signal_service import (
    _latency_message,
    classify_latency,
)


@pytest.fixture
def settings():
    return get_settings()


@pytest.mark.parametrize(
    ("avg_ms", "expected"),
    [
        (0.0, "excellent"),
        (42.0, "excellent"),
        (79.9, "excellent"),
        (80.0, "tres_bien"),      # borne basse incluse dans la bande du dessus
        (99.9, "tres_bien"),
        (100.0, "bien"),
        (119.9, "bien"),
        (120.0, "mauvaise"),
        (149.9, "mauvaise"),
        (150.0, "catastrophique"),
        (900.0, "catastrophique"),
    ],
)
def test_latency_bands(avg_ms, expected, settings):
    """Chaque borne appartient à la bande la PLUS dégradée des deux qui la touchent."""
    assert classify_latency(avg_ms, settings) == expected


def test_no_measurement_is_never_a_number(settings):
    """Pas de mesure → `indetermine`, jamais 0 ms (qui se lirait « excellent »)."""
    assert classify_latency(None, settings) == "indetermine"


def test_indetermine_message_carries_the_reason():
    """Le tiers doit distinguer « lien mauvais » de « rien mesuré »."""
    msg = _latency_message("indetermine", None, "pas de transit vers 8.8.8.8")
    assert "indéterminée" in msg
    assert "pas de transit vers 8.8.8.8" in msg


def test_measured_message_carries_the_value():
    assert _latency_message("excellent", 42.4, None) == "Latence excellente (42 ms)"
    assert _latency_message("catastrophique", 312.0, None) == (
        "Latence catastrophique (312 ms)"
    )
