"""GET /client-signal — les courbes 7 jours (`history`).

Ce qui est fixé ici : les 4 courbes demandées par l'opérateur, toujours
présentes (vides plutôt qu'absentes), lues depuis `GRAPH_METRICS` (libellés et
seuils jamais recopiés), sur la fenêtre 7 j re-binnée à 30 min comme la fiche,
et sans combler les trous.
"""

import asyncio
import datetime
from types import SimpleNamespace

from app.services import client_signal_service as svc
from app.services import lr_metric_history_service as hist


def test_requested_curves_exist_in_graph_metrics():
    assert svc.CLIENT_CURVES == (
        "lr_latency_ms", "link_potential_pct", "total_capacity_mbps", "dl_throughput_mbps",
    )
    for key in svc.CLIENT_CURVES:
        assert key in hist.GRAPH_METRICS


def test_history_shape(monkeypatch):
    t0 = datetime.datetime(2026, 9, 20, 10, 0, tzinfo=datetime.UTC)
    calls = []

    async def fake_get_history(db, device_id, metric, *, start, end, bin_seconds):
        calls.append((metric, bin_seconds, end - start))
        if metric == "link_potential_pct":
            return []  # ex. LiteBeam M5 : pas de potentiel
        return [{
            "bucket_start": t0, "avg_value": 42.0, "min_value": 30.0,
            "max_value": 80.0, "sample_count": 6,
        }]

    async def fake_effective(db, settings):
        return settings

    monkeypatch.setattr(hist, "get_history", fake_get_history)
    monkeypatch.setattr(svc.threshold_service, "get_effective_settings", fake_effective)

    lr = SimpleNamespace(id=7, model_variant="ltu_lr")
    out = asyncio.run(svc.get_client_history(None, lr))

    assert out.period == "7d" and out.bin_seconds == 1800
    assert set(out.curves) == set(svc.CLIENT_CURVES)
    assert all(b == 1800 and span == datetime.timedelta(days=7) for _, b, span in calls)

    lat = out.curves["lr_latency_ms"]
    assert lat.label == hist.GRAPH_METRICS["lr_latency_ms"]["label"]
    assert lat.threshold_direction == "max" and lat.threshold is not None
    assert (lat.points[0].avg, lat.points[0].min, lat.points[0].max) == (42.0, 30.0, 80.0)

    # Courbe sans données : présente et VIDE, jamais remplie de zéros.
    assert out.curves["link_potential_pct"].points == []
    # Pas de seuil sur le débit.
    assert out.curves["dl_throughput_mbps"].threshold is None
