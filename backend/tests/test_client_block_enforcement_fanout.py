"""
Renforcement des blocages client EN PARALLÈLE — `client_block_service`.

Le job repasse toutes les 120 s sur chaque client bloqué, en SSH. Il le faisait
un LR après l'autre : mesuré en prod le 2026-09-11, un tour durait **309 s en
moyenne et jusqu'à 3833 s** (64 min). Un client bloqué dont le LR reboote
retrouvait donc Internet jusqu'à une heure au lieu de deux minutes.

Propriétés verrouillées ici :

  - **parallèle mais borné** — plusieurs LR à la fois, jamais plus que
    `_ENFORCE_FANOUT` (le pool de threads par défaut du process est partagé) ;
  - **pas d'auto-blocage** — le plafond de parallélisme n'est pas
    `_SSH_CONCURRENCY`, que chaque SSH reprend ;
  - **aucune transaction ouverte pendant le SSH** — sinon la connexion reste
    « idle in transaction » tout le temps de la session ;
  - **un LR qui plante n'emporte pas les autres** ;
  - **l'intention est relue au moment de traiter le LR**, pas au début du tour.

⚠️ Les vérifications faites DANS les faux SSH sont consignées puis contrôlées
après coup, jamais par `assert` sur place : `_fan_out` attrape les exceptions
d'un LR (c'est voulu), un `assert` échoué y serait donc avalé en silence.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import client_block_service


class _FakeLr:
    """Juste ce que les deux passes de renforcement lisent sur un LR."""

    def __init__(self, **kwargs):
        self.id = 1
        self.name = "lr-test"
        self.mac_address = "d0:21:f9:f6:07:c2"
        self.ip_address = "10.135.3.5"
        self.ssh_username = "ubnt"
        self.ssh_password = "secret"
        self.ssh_port = 22
        self.lan_interface = "eth0"
        self.block_mode = "full"
        self.client_blocked = False
        self.unblock_pending = False
        self.router_blocked = False
        self.client_block_enforced_at = None
        self.block_unenforceable_reason = None
        self.block_unenforceable_since = None
        self.blocked_categories = None
        self.block_adult_content = False
        self.content_block_mode = "denylist"
        self.content_block_enforced_at = None
        self.__dict__.update(kwargs)


class _Result:
    def __init__(self, ids):
        self._ids = ids

    def scalars(self):
        return self

    def all(self):
        return list(self._ids)


class _World:
    """Une « base » en mémoire et la factory de sessions qui la sert."""

    def __init__(self, lrs):
        self.lrs = {lr.id: lr for lr in lrs}
        self.selected_ids = sorted(self.lrs)
        self.session_of: dict[int, _FakeSession] = {}

    def factory(self):
        return _FakeSession(self)


class _FakeSession:
    def __init__(self, world):
        self.world = world
        self.in_transaction = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.in_transaction = False
        return False

    async def execute(self, _stmt):
        self.in_transaction = True
        return _Result(self.world.selected_ids)

    async def get(self, _model, lr_id):
        self.in_transaction = True
        lr = self.world.lrs.get(lr_id)
        if lr is not None:
            self.world.session_of[lr_id] = self
        return lr

    async def commit(self):
        self.in_transaction = False


def _quiet_side_effects():
    """Ni routeur ni fichier de journal FAI pendant les tests."""
    return (
        patch.object(client_block_service, "_reconcile_router", AsyncMock(return_value=None)),
        patch.object(client_block_service.fai_audit, "log_action", MagicMock()),
    )


async def _run_blocks(world, fake_assert):
    router, audit = _quiet_side_effects()
    with router, audit, patch.object(client_block_service, "_assert_block", fake_assert):
        return await asyncio.wait_for(
            client_block_service.enforce_blocked_clients(world.factory), timeout=10,
        )


# ── Passe de blocage ────────────────────────────────────────────────────────


async def test_les_lr_sont_traites_en_parallele_mais_bornes():
    """Plusieurs à la fois, jamais plus que le plafond — et sans se bloquer.

    Le faux SSH reprend `_SSH_CONCURRENCY` comme le vrai : si le plafond de
    parallélisme était ce même sémaphore, la passe se figerait (timeout).
    """
    world = _World([_FakeLr(id=i, client_blocked=True) for i in range(1, 21)])
    active = peak = 0

    async def fake_assert(lr, capture_evidence=False):
        nonlocal active, peak
        async with client_block_service._SSH_CONCURRENCY:
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
        return True, "ok", None

    enforced = await _run_blocks(world, fake_assert)

    assert enforced == 20
    assert peak == client_block_service._ENFORCE_FANOUT
    assert all(lr.client_block_enforced_at is not None for lr in world.lrs.values())


async def test_aucune_transaction_ouverte_pendant_le_ssh():
    world = _World([_FakeLr(id=i, client_blocked=True) for i in range(1, 6)])
    open_during_ssh = []

    async def fake_assert(lr, capture_evidence=False):
        if world.session_of[lr.id].in_transaction:
            open_during_ssh.append(lr.id)
        return True, "ok", None

    await _run_blocks(world, fake_assert)

    assert open_during_ssh == []


async def test_un_lr_qui_plante_nemporte_pas_les_autres():
    world = _World([_FakeLr(id=i, client_blocked=True) for i in (1, 2, 3)])

    async def fake_assert(lr, capture_evidence=False):
        if lr.id == 2:
            raise RuntimeError("session SSH cassée")
        return True, "ok", None

    enforced = await _run_blocks(world, fake_assert)

    assert enforced == 2
    assert world.lrs[1].client_block_enforced_at is not None
    assert world.lrs[3].client_block_enforced_at is not None
    assert world.lrs[2].client_block_enforced_at is None


async def test_intention_relue_au_moment_du_traitement():
    """Débloqué entre la sélection et son tour : aucune session SSH."""
    world = _World([_FakeLr(id=1, client_blocked=False)])  # sélectionné, puis débloqué
    fake_assert = AsyncMock(return_value=(True, "ok", None))

    enforced = await _run_blocks(world, fake_assert)

    assert enforced == 0
    fake_assert.assert_not_called()


async def test_rien_a_renforcer_nouvre_aucune_session_par_lr():
    world = _World([])
    fake_assert = AsyncMock()

    assert await _run_blocks(world, fake_assert) == 0
    fake_assert.assert_not_called()


# ── Passe du filtre de contenu ──────────────────────────────────────────────


async def test_filtre_de_contenu_parallele_et_sans_transaction_pendant_le_ssh():
    world = _World([
        _FakeLr(id=i, blocked_categories=["tiktok"]) for i in range(1, 13)
    ])
    active = peak = 0
    open_during_ssh = []

    async def fake_apply(lr, categories):
        nonlocal active, peak
        if world.session_of[lr.id].in_transaction:
            open_during_ssh.append(lr.id)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return True, "ok"

    with patch.object(client_block_service, "_apply_content_block", fake_apply):
        enforced = await asyncio.wait_for(
            client_block_service.enforce_content_blocks(world.factory), timeout=10,
        )

    assert enforced == 12
    assert peak == client_block_service._ENFORCE_FANOUT
    assert open_during_ssh == []
    assert all(lr.content_block_enforced_at is not None for lr in world.lrs.values())


async def test_filtre_efface_entre_temps_ne_declenche_aucun_ssh():
    world = _World([_FakeLr(id=1, blocked_categories=None, block_adult_content=False)])
    fake_apply = AsyncMock(return_value=(True, "ok"))

    with patch.object(client_block_service, "_apply_content_block", fake_apply):
        enforced = await client_block_service.enforce_content_blocks(world.factory)

    assert enforced == 0
    fake_apply.assert_not_called()


# ── Le job ──────────────────────────────────────────────────────────────────


async def test_le_job_ne_partage_plus_une_session_entre_les_lr():
    """Le job laisse chaque passe ouvrir ses sessions — il n'en passe aucune."""
    from app.tasks import jobs

    blocks = AsyncMock(return_value=0)
    contents = AsyncMock(return_value=0)
    with (
        patch.object(jobs.client_block_service, "enforce_blocked_clients", blocks),
        patch.object(jobs.client_block_service, "enforce_content_blocks", contents),
    ):
        await jobs.client_block_enforcement_job()

    blocks.assert_awaited_once_with()
    contents.assert_awaited_once_with()
