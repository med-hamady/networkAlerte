"""
Allers-retours base d'un poll et attente des verrous — `jobs`, `lr_metric_history_service`.

Diagnostic du 2026-09-11 (scripts/diag-perf.sh) : la machine avait de la marge
(80 % de CPU libre), mais 27 échantillons Postgres sur 49 attendaient un verrou
`advisory`. Deux causes, verrouillées ici :

  - **trop d'allers-retours sous verrou** — chaque courbe d'un relevé partait en
    upsert séparé, pendant que le verrou du Rocket parent était tenu ;
  - **des tâches parallèles qui visaient le même verrou** — la file de la phase 2
    était construite Rocket par Rocket, donc les tâches simultanées tiraient des
    LR du même Rocket et s'attendaient les unes les autres.
"""

import datetime
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.dialects import postgresql

from app.services import lr_metric_history_service
from app.tasks import jobs


class _CaptureSession:
    """Session qui enregistre ce qu'on lui envoie, sans base."""

    def __init__(self):
        self.statements = []
        self.added = []

    async def execute(self, stmt, params=None):
        self.statements.append(stmt)
        return MagicMock()

    def add(self, obj):
        self.added.append(obj)


_NOW = datetime.datetime(2026, 9, 15, 12, 0, 30, tzinfo=datetime.UTC)


# ── Upsert groupé des courbes ───────────────────────────────────────────────


async def test_courbes_dun_releve_en_une_seule_instruction():
    session = _CaptureSession()

    await lr_metric_history_service.record_samples(
        session, 7,
        {"ul_capacity_mbps": 50.0, "dl_capacity_mbps": 120.0, "lr_latency_ms": 42.0},
        now=_NOW,
    )

    assert len(session.statements) == 1
    compiled = session.statements[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.count("INSERT INTO lr_metric_samples") == 1
    assert "ON CONFLICT ON CONSTRAINT uq_lr_metric_device_name_bucket DO UPDATE" in sql
    # La moyenne glissante lit la valeur PROPOSÉE de chaque ligne, pas une
    # constante : sinon toutes les courbes recevraient la valeur de la première.
    assert "excluded.avg_value" in sql
    names = [v for k, v in compiled.params.items() if k.startswith("metric_name")]
    # Trié : deux écrivains du même équipement verrouillent dans le même ordre.
    assert names == ["dl_capacity_mbps", "lr_latency_ms", "ul_capacity_mbps"]


async def test_aucune_courbe_aucune_instruction():
    session = _CaptureSession()
    await lr_metric_history_service.record_samples(session, 7, {}, now=_NOW)
    assert session.statements == []


async def test_record_sample_unitaire_reste_une_instruction():
    session = _CaptureSession()
    await lr_metric_history_service.record_sample(session, 7, "lr_latency_ms", 42.0, now=_NOW)
    assert len(session.statements) == 1


async def test_persist_device_metrics_groupe_les_courbes_en_un_appel():
    session = _CaptureSession()
    record = AsyncMock()

    with patch.object(jobs.lr_metric_history_service, "record_samples", record):
        await jobs.persist_device_metrics(
            session, 7,
            {
                "dl_capacity_mbps": 120.0,
                "ul_capacity_mbps": 50.0,
                "signal_dbm": -60.0,     # pas une courbe : aucune ligne d'historique
                "lr_latency_ms": None,   # absente : ni ligne ni courbe
            },
            {},
            now=_NOW,
        )

    record.assert_awaited_once()
    _session, device_id, values = record.await_args.args
    assert device_id == 7
    assert values == {"dl_capacity_mbps": 120.0, "ul_capacity_mbps": 50.0}
    # Le « dernier relevé » garde bien une ligne par métrique mesurée.
    assert sorted(m.metric_name for m in session.added) == [
        "dl_capacity_mbps", "signal_dbm", "ul_capacity_mbps",
    ]


# ── File de travail entrelacée par Rocket ───────────────────────────────────


def test_file_entrelacee_par_rocket():
    work = [(1, "A"), (2, "A"), (3, "A"), (4, "B"), (5, "B"), (6, "C")]

    out = jobs._interleave_by_parent(work, lambda w: w[1])

    # Deux voisins ne visent jamais le même Rocket tant qu'il en reste d'autres.
    assert [w[0] for w in out] == [1, 4, 6, 2, 5, 3]


def test_entrelacement_ne_perd_ni_ne_duplique_rien():
    work = [(i, i % 7) for i in range(100)]
    out = jobs._interleave_by_parent(work, lambda w: w[1])
    assert sorted(out) == sorted(work)


def test_les_phases_concurrentes_entrelacent_leur_file():
    """LTU et airOS : sans l'entrelacement, les tâches retombent sur le même verrou."""
    for job in (jobs.ltu_api_poll_job, jobs.airos_api_poll_job):
        assert "_interleave_by_parent(" in inspect.getsource(job), job.__name__
