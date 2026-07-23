"""
Repli du blocage client sur le routeur — `client_block_service`.

Couper un client se fait normalement sur son propre équipement (SSH sur le LR).
Ça échoue dès qu'il est éteint ou refuse la connexion : le 2026-07-14, sur 222
clients à couper, **163 gardaient leur accès** pour cette raison. Le routeur de
cœur, lui, coupe sans rien demander à l'équipement du client.

Deux propriétés sont testées ici, et elles comptent autant l'une que l'autre :

  - **la couverture** — un client qu'on n'arrive pas à couper sur son équipement
    doit l'être sur le routeur, y compris quand on a ABANDONNÉ son LR ;
  - **le silence** — le routeur ne doit être appelé que lorsque l'état change.
    Le job repasse toutes les 120 s sur chaque client bloqué : une session API
    par client et par cycle saturerait le routeur.
"""

import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.services import client_block_service

_MAC = "d0:21:f9:f6:07:c2"


class _FakeLr:
    """Juste ce que la réconciliation lit sur un LR."""

    def __init__(self, **kwargs):
        self.id = 1
        self.name = "36086261-Toutoumedlimam"
        self.mac_address = _MAC
        self.ip_address = "10.135.3.5"
        self.block_mode = "full"
        self.client_blocked = False
        self.client_block_enforced_at = None
        self.block_unenforceable_reason = None
        self.block_unenforceable_since = None
        self.router_blocked = False
        self.router_blocked_at = None
        self.__dict__.update(kwargs)


def _now():
    return datetime.datetime.now(datetime.UTC)


# ── L'état désiré ───────────────────────────────────────────────────────────


def test_client_actif_nest_jamais_coupe_sur_le_routeur():
    assert client_block_service.desired_router_block(_FakeLr()) is False


def test_coupure_jamais_appliquee_appelle_le_routeur():
    """Le cas de masse : 163 clients sur 222 le 2026-07-14."""
    lr = _FakeLr(client_blocked=True, client_block_enforced_at=None)
    assert client_block_service.desired_router_block(lr) is True


def test_coupure_confirmee_sur_le_lr_se_passe_du_routeur():
    lr = _FakeLr(client_blocked=True, client_block_enforced_at=_now())
    assert client_block_service.desired_router_block(lr) is False


def test_lr_abandonne_reste_couvert_par_le_routeur():
    """Coupé autrefois, puis le LR refuse le login : sans ça il repasserait en
    ligne au premier reboot, et le job ne le retenterait jamais."""
    lr = _FakeLr(
        client_blocked=True,
        client_block_enforced_at=_now(),
        block_unenforceable_reason="Authentication failed.",
    )
    assert client_block_service.desired_router_block(lr) is True


def test_le_mode_whatsapp_ne_change_rien():
    """Le routeur ne sait faire qu'un DROP total : un client en whatsapp_only
    qu'on n'arrive pas à filtrer est coupé entièrement (décision 2026-07-22)."""
    lr = _FakeLr(client_blocked=True, block_mode="whatsapp_only")
    assert client_block_service.desired_router_block(lr) is True


# ── Self-heal : re-tenter un LR abandonné, lentement ────────────────────────
#
# Un abandon structurel (mauvais mot de passe, clé d'hôte) est sauté à chaque
# cycle. Mais un LR re-flashé (nouvelle clé, ré-épinglée sur MAC) ou corrigé
# hors bande se répare seul : on le retente une fois toutes les N heures.


def test_lr_non_abandonne_nest_pas_concerne():
    assert client_block_service._abandon_retry_due(_FakeLr()) is False


def test_abandon_sans_horodatage_est_du_tout_de_suite():
    """Les LR abandonnés AVANT l'ajout de la colonne (since NULL) doivent
    récupérer au premier cycle après déploiement, pas rester bloqués."""
    lr = _FakeLr(
        block_unenforceable_reason="Host key mismatch for 10.135.2.52",
        block_unenforceable_since=None,
    )
    assert client_block_service._abandon_retry_due(lr) is True


def test_abandon_recent_est_saute():
    lr = _FakeLr(
        block_unenforceable_reason="Authentication failed.",
        block_unenforceable_since=_now(),
    )
    assert client_block_service._abandon_retry_due(lr) is False


def test_abandon_ancien_est_retente():
    hours = client_block_service.get_settings().client_block_abandon_retry_hours
    old = _now() - datetime.timedelta(hours=hours + 1)
    lr = _FakeLr(
        block_unenforceable_reason="Host key mismatch for 10.135.2.52",
        block_unenforceable_since=old,
    )
    assert client_block_service._abandon_retry_due(lr) is True


@pytest.mark.asyncio
async def test_abandon_garde_le_deblocage_dans_la_boucle():
    """Un déblocage abandonné (structurel) RESTE dans la boucle (self-heal
    throttlé) : le client qui a payé ne doit pas rester coupé jusqu'à une
    intervention manuelle."""
    lr = _FakeLr(client_blocked=False, unblock_pending=True)
    with patch("app.services.client_block_service.fai_audit.log_action"):
        await client_block_service._abandon(
            lr, "UNBLOCK", "Host key mismatch for 10.135.2.52"
        )
    assert lr.unblock_pending is True
    assert lr.block_unenforceable_reason is not None
    assert lr.block_unenforceable_since is not None


@pytest.mark.asyncio
async def test_abandon_coupure_na_pas_de_deblocage_en_attente():
    lr = _FakeLr(client_blocked=True, unblock_pending=False)
    with patch("app.services.client_block_service.fai_audit.log_action"):
        await client_block_service._abandon(lr, "BLOCK", "Authentication failed.")
    assert lr.unblock_pending is False


# ── Les transitions ─────────────────────────────────────────────────────────


def _router(block_ok=True, unblock_ok=True):
    """Patche le routeur ; retourne les deux mocks pour compter les appels."""
    block = AsyncMock(return_value=(block_ok, "ok"))
    unblock = AsyncMock(return_value=(unblock_ok, "ok"))
    return patch.multiple(
        "app.services.mikrotik_service",
        is_enabled=lambda: True,
        block_by_mac=block,
        unblock_by_mac=unblock,
    ), block, unblock


@pytest.mark.asyncio
async def test_pose_la_regle_quand_le_lr_na_pas_coupe():
    lr = _FakeLr(client_blocked=True)
    ctx, block, unblock = _router()
    with ctx:
        await client_block_service._reconcile_router(lr)
    assert block.await_count == 1
    assert unblock.await_count == 0
    assert lr.router_blocked is True
    assert lr.router_blocked_at is not None


@pytest.mark.asyncio
async def test_retire_la_regle_quand_la_coupure_lr_est_confirmee():
    lr = _FakeLr(client_blocked=True, client_block_enforced_at=_now(), router_blocked=True)
    ctx, block, unblock = _router()
    with ctx:
        await client_block_service._reconcile_router(lr)
    assert unblock.await_count == 1
    assert block.await_count == 0
    assert lr.router_blocked is False


@pytest.mark.asyncio
async def test_aucun_appel_quand_la_regle_est_deja_posee():
    """LE test qui protège le routeur : 200 clients bloqués × 720 cycles/jour."""
    lr = _FakeLr(client_blocked=True, router_blocked=True)
    ctx, block, unblock = _router()
    with ctx:
        await client_block_service._reconcile_router(lr)
    assert block.await_count == 0
    assert unblock.await_count == 0


@pytest.mark.asyncio
async def test_aucun_appel_pour_un_client_actif_sans_regle():
    lr = _FakeLr()
    ctx, block, unblock = _router()
    with ctx:
        await client_block_service._reconcile_router(lr)
    assert block.await_count == 0
    assert unblock.await_count == 0


@pytest.mark.asyncio
async def test_un_echec_laisse_letat_en_desaccord_pour_reessayer():
    """Routeur injoignable : on ne prétend pas avoir posé la règle, sinon plus
    rien ne la poserait jamais."""
    lr = _FakeLr(client_blocked=True)
    ctx, _block, _unblock = _router(block_ok=False)
    with ctx:
        await client_block_service._reconcile_router(lr)
    assert lr.router_blocked is False
    assert client_block_service.desired_router_block(lr) is True  # écart persistant


@pytest.mark.asyncio
async def test_repli_desactive_ne_touche_a_rien():
    """Non-régression : sans MIKROTIK_ENABLED le système se comporte comme avant."""
    lr = _FakeLr(client_blocked=True)
    block = AsyncMock()
    with patch.multiple(
        "app.services.mikrotik_service",
        is_enabled=lambda: False,
        block_by_mac=block,
    ):
        assert await client_block_service._reconcile_router(lr) is None
    assert block.await_count == 0
    assert lr.router_blocked is False
