"""Restoration requires confirmed LR and router cleanup; all I/O is mocked."""

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import client_block_service as blocks
from app.services import mikrotik_service


@pytest.fixture
def world(monkeypatch):
    lr = SimpleNamespace(
        id=951,
        name="CLIENT TEST RESTORATION",
        mac_address="02:00:00:00:09:51",
        ip_address="192.0.2.51",
        ssh_username="test",
        ssh_password="dummy-not-a-real-password",
        client_blocked=True,
        client_blocked_reason="TEST",
        client_blocked_at=None,
        client_block_enforced_at=None,
        unblock_pending=False,
        router_blocked=False,
        router_blocked_at=None,
        block_mode="full",
        block_unenforceable_reason=None,
        block_unenforceable_since=None,
    )
    session = SimpleNamespace(bind=None, commit=AsyncMock(), get=AsyncMock(return_value=lr))
    lan = AsyncMock(return_value=(True, "Port LAN UP confirmé.", "SIMULATION"))
    whatsapp = AsyncMock(return_value=(True, "Filtre WhatsApp retiré."))
    router = AsyncMock(return_value=(False, "Routeur inaccessible."))
    audit = MagicMock()
    monkeypatch.setattr(blocks, "_set_full", lan)
    monkeypatch.setattr(blocks, "_set_whatsapp", whatsapp)
    monkeypatch.setattr(mikrotik_service, "is_enabled", lambda: True)
    monkeypatch.setattr(mikrotik_service, "unblock_by_mac", router)
    monkeypatch.setattr(mikrotik_service, "block_by_mac", AsyncMock(side_effect=AssertionError("unexpected cut")))
    monkeypatch.setattr(blocks.fai_audit, "log_action", audit)
    monkeypatch.setattr(blocks.fai_request_service, "record", AsyncMock())
    return SimpleNamespace(lr=lr, session=session, lan=lan, whatsapp=whatsapp, router=router, audit=audit)


@pytest.mark.parametrize("known_rule", [False, True])
async def test_unblock_requires_router_confirmation_even_for_unknown_legacy_rule(world, known_rule):
    world.lr.router_blocked = known_rule

    ok, message, evidence = await blocks.unblock_client(world.session, world.lr)

    assert ok is False
    assert "Routeur inaccessible" in message
    assert evidence == "SIMULATION"
    assert world.lr.client_blocked is False
    assert world.lr.unblock_pending is True
    assert world.lr.router_blocked is known_rule
    assert world.lr.block_unenforceable_reason is None
    world.router.assert_awaited_once_with(world.lr.mac_address)
    world.lan.assert_awaited_once_with(world.lr, cut=False, capture_evidence=True)


@pytest.mark.parametrize("known_rule", [False, True])
@pytest.mark.parametrize("confirmation", ["Règle retirée et absence confirmée.", mikrotik_service.NO_RULE_MESSAGE])
async def test_retry_waits_for_router_then_finishes_once_confirmed(world, known_rule, confirmation):
    world.lr.router_blocked = known_rule
    await blocks.unblock_client(world.session, world.lr)

    assert await blocks._enforce_one_block(world.session, world.lr.id) == 0
    assert world.lr.unblock_pending is True
    assert world.router.await_count == 2  # unconditional even for an unknown rule
    assert not any(call.args[0] == "RETRY_OK" for call in world.audit.call_args_list)

    world.router.return_value = True, confirmation
    assert await blocks._enforce_one_block(world.session, world.lr.id) == 1
    assert world.lr.unblock_pending is False
    assert world.lr.router_blocked is False
    assert world.router.await_count == 3
    retry_events = [call for call in world.audit.call_args_list if call.args[0] == "RETRY_OK"]
    assert len(retry_events) == 1
    assert retry_events[0].kwargs["ok"] is True

    assert await blocks._enforce_one_block(world.session, world.lr.id) == 0
    assert world.router.await_count == 3  # completed restoration stops polling


async def test_confirmed_empty_router_is_success_without_claiming_rule_removal(world):
    world.lr.router_blocked = True
    world.router.return_value = True, mikrotik_service.NO_RULE_MESSAGE

    ok, message, _ = await blocks.unblock_client(world.session, world.lr)

    assert ok is True
    assert "Accès internet rétabli" in message
    assert world.lr.unblock_pending is False
    assert world.lr.router_blocked is False
    world.audit.assert_not_called()


@pytest.mark.parametrize("known_rule", [False, True])
async def test_disabled_router_cannot_clear_a_known_rule(world, monkeypatch, known_rule):
    world.lr.router_blocked = known_rule
    monkeypatch.setattr(mikrotik_service, "is_enabled", lambda: False)

    ok, _, _ = await blocks.unblock_client(world.session, world.lr)

    assert ok is not known_rule
    assert world.lr.unblock_pending is known_rule
    world.router.assert_not_awaited()
    if known_rule:
        assert await blocks._enforce_one_block(world.session, world.lr.id) == 0
        assert world.lr.unblock_pending is True
        assert not any(call.args[0] == "RETRY_OK" for call in world.audit.call_args_list)


@pytest.mark.parametrize("local_problem", ["credentials", "address", "throttled"])
async def test_retry_cleans_legacy_router_rule_even_while_lr_cannot_be_restored(world, monkeypatch, local_problem):
    world.lr.client_blocked = False
    world.lr.unblock_pending = True
    world.router.return_value = True, "Règle retirée et absence confirmée."
    if local_problem == "credentials":
        world.lr.ssh_password = None
    elif local_problem == "address":
        world.lr.ip_address = None
    else:
        world.lr.block_unenforceable_reason = "Authentication failed"
        world.lr.block_unenforceable_since = datetime.datetime.now(datetime.UTC)
        monkeypatch.setattr(blocks, "_abandon_retry_due", lambda lr: False)

    assert await blocks._enforce_one_block(world.session, world.lr.id) == 0

    world.router.assert_awaited_once_with(world.lr.mac_address)
    world.lan.assert_not_awaited()
    assert world.lr.unblock_pending is True
    assert not any(call.args[0] == "RETRY_OK" for call in world.audit.call_args_list)


async def test_unconfirmed_local_restore_stays_pending_despite_router_success(world):
    world.router.return_value = True, mikrotik_service.NO_RULE_MESSAGE
    world.lan.return_value = False, "Port LAN : état non confirmé.", "SIMULATION"

    ok, _, _ = await blocks.unblock_client(world.session, world.lr)
    assert ok is False
    assert world.lr.unblock_pending is True
    assert await blocks._enforce_one_block(world.session, world.lr.id) == 0
    assert world.lr.unblock_pending is True
    assert not any(call.args[0] == "RETRY_OK" for call in world.audit.call_args_list)
