"""
Tests unitaires de af60_api_service.parse_af60_metrics — Python pur, sans DB.

La fixture est la réponse RÉELLE de ``/api/v1.0/statistics`` capturée le
2026-08-03 sur un AF60-LR de production (10.135.80.1, fw v2.6.8), conservée
ENTIÈRE — y compris les blocs que le parser ignore. C'est la leçon de la fixture
airOS trimmée : une fixture réduite « aux champs que le code lit » avait fait
conclure à tort que le firmware n'exposait pas le débit. Elle doit rester la
preuve de ce que l'équipement envoie.

Ce que ces tests verrouillent, et pourquoi ça compte : le débit AF60 se lit sur
``interfaces[wlan0]`` et **pas** sur ``peers[0].common.counters``, et
``dl`` = ce que l'équipement reçoit. Les deux points ont été établis en mesurant
le lien, pas en lisant une doc — un futur « alignement sur le LTU » les casserait
sans que rien n'échoue par ailleurs (aucune règle d'alerte AF60 ne lit le débit).
"""

import json
from pathlib import Path

import pytest

import app.services.af60_api_service as af60
from app.services.af60_api_service import (
    METRIC_UNITS,
    collect_af60_metrics,
    parse_af60_metrics,
)

FIXTURE = Path(__file__).parent / "fixtures" / "af60_statistics.json"


@pytest.fixture
def raw() -> dict:
    data = json.loads(FIXTURE.read_text())
    return data[0] if isinstance(data, list) else data


def test_throughput_lu_sur_wlan0_pas_sur_le_bloc_peer(raw):
    """dl/ul viennent des compteurs de wlan0, dans le bon sens.

    Sur cette capture wlan0 porte rxRate=66_100_104 / txRate=3_362_455 bit/s,
    alors que peers[0].common.counters annonce txRate=71_021_000 /
    rxRate=3_640_000. Les valeurs attendues ci-dessous ne sont donc atteignables
    QUE depuis wlan0 : si quelqu'un rebranche le parser sur le bloc peer, ce test
    tombe.
    """
    m = parse_af60_metrics(raw)

    # bit/s → Mb/s, et dl = ce que l'équipement REÇOIT sur la radio.
    assert m["dl_throughput_mbps"] == pytest.approx(66.100, abs=0.001)
    assert m["ul_throughput_mbps"] == pytest.approx(3.362, abs=0.001)


def test_le_sens_du_debit_suit_celui_de_la_capacite(raw):
    """dl/ul du débit et de la capacité doivent parler de la même direction.

    Le firmware annonce lui-même capacity.dl=600000 / ul=975000 kb/s avec
    mcs.rxIdx=6 < txIdx=9 : la direction la moins bien modulée est la moins
    capable, donc dl est bien le sens REÇU. Le débit doit s'aligner dessus,
    sinon la fiche afficherait un débit descendant sous une capacité montante.
    """
    m = parse_af60_metrics(raw)

    assert m["dl_capacity_mbps"] == 600.0
    assert m["ul_capacity_mbps"] == 975.0
    # Capacité totale = MOYENNE des deux sens (lien TDD), pas leur somme : le
    # dashboard airFiber de l'équipement l'affiche ainsi (vérifié 2026-08-05).
    # (600 + 975) / 2 = 787.5 — surtout PAS 1575.
    assert m["total_capacity_mbps"] == 787.5
    # Le sens majoritaire du trafic (reçu) est aussi celui étiqueté "dl".
    assert m["dl_throughput_mbps"] > m["ul_throughput_mbps"]
    # Et un débit ne peut pas dépasser la capacité de son propre sens.
    assert m["dl_throughput_mbps"] < m["dl_capacity_mbps"]
    assert m["ul_throughput_mbps"] < m["ul_capacity_mbps"]


def test_wlan0_absent_laisse_un_trou_jamais_un_zero(raw):
    """Firmware sans wlan0 ⇒ clés à None (trou dans la courbe), pas 0.

    Un 0 se lirait « le backhaul n'écoule plus rien », c.-à-d. exactement
    l'incident qu'on cherche à voir. Et surtout : pas de repli sur le bloc peer,
    qui retarde au point d'annoncer 0,06 Mb/s pendant que le lien porte 76 Mb/s.
    """
    raw["interfaces"] = [i for i in raw["interfaces"] if i.get("id") != "wlan0"]

    m = parse_af60_metrics(raw)

    assert m["dl_throughput_mbps"] is None
    assert m["ul_throughput_mbps"] is None
    # Le reste du parsing n'est pas affecté.
    assert m["signal_dbm"] == -65.0


def test_toutes_les_cles_declarees_sont_produites(raw):
    """Le dict rendu couvre exactement METRIC_UNITS (les unités du job)."""
    assert set(parse_af60_metrics(raw)) == set(METRIC_UNITS)


# ── Fallback de mot de passe (auto-réparation des AF60 en 403) ───────────────
#
# collect_af60_metrics essaie le mot de passe stocké puis chaque repli, et rend
# CELUI qui a authentifié pour que le job le promeuve sur la fiche. On simule
# l'auth en monkeypatchant LTUApiClient.fetch_stats : elle rend la vraie payload
# UNIQUEMENT pour le "bon" mot de passe, None sinon (= 403/injoignable). C'est
# exactement le comportement du firmware. Aucun réseau, aucune DB.


def _patch_fetch(monkeypatch, good_password: str, raw: dict) -> list[str]:
    """Fait réussir fetch_stats seulement pour `good_password`. Renvoie la liste
    (mutable) des mots de passe réellement essayés, dans l'ordre."""
    tried: list[str] = []

    async def fake_fetch(self):
        tried.append(self._password)
        return raw if self._password == good_password else None

    monkeypatch.setattr(af60.LTUApiClient, "fetch_stats", fake_fetch)
    return tried


@pytest.mark.asyncio
async def test_le_mot_de_passe_stocke_marche_aucun_repli_essaye(raw, monkeypatch):
    tried = _patch_fetch(monkeypatch, "PRIMARY", raw)

    result = await collect_af60_metrics(
        "10.0.0.1", "ubnt", "PRIMARY", fallback_passwords=["FB1", "FB2"],
    )

    assert result is not None
    metrics, working = result
    assert working == "PRIMARY"          # le pw retourné = celui qui a marché
    assert metrics["dl_capacity_mbps"] == 600.0
    assert tried == ["PRIMARY"]          # un AF60 sain n'essaie AUCUN repli


@pytest.mark.asyncio
async def test_repli_accepte_et_rendu_pour_promotion(raw, monkeypatch):
    # Le stocké échoue (403), le 2e repli authentifie.
    tried = _patch_fetch(monkeypatch, "A2F60@4321$a2", raw)

    result = await collect_af60_metrics(
        "10.0.0.1", "ubnt", "A2F60@4321",
        fallback_passwords=["A2F60@4321$A2", "A2F60@4321$a2"],
    )

    assert result is not None
    _metrics, working = result
    # C'est le repli qui a marché qui est rendu → le job le promeut sur la fiche.
    assert working == "A2F60@4321$a2"
    # Essayés dans l'ordre, stockage d'abord, arrêt au 1er succès.
    assert tried == ["A2F60@4321", "A2F60@4321$A2", "A2F60@4321$a2"]


@pytest.mark.asyncio
async def test_aucun_mot_de_passe_ne_marche_rend_none(raw, monkeypatch):
    tried = _patch_fetch(monkeypatch, "JAMAIS_CELUI_LA", raw)

    result = await collect_af60_metrics(
        "10.0.0.1", "ubnt", "A2F60@4321",
        fallback_passwords=["A2F60@4321$A2", "A2F60@4321$a2"],
    )

    assert result is None                # aucun candidat n'authentifie
    assert tried == ["A2F60@4321", "A2F60@4321$A2", "A2F60@4321$a2"]
