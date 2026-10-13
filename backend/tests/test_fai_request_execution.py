"""Real persistence with simulated device operations. No live DB or network."""

import asyncio
import datetime
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.endpoints import fai_requests
from app.db.base import Base
from app.models.device import Lr
from app.models.fai_request import FaiRequest
from app.services import client_block_service as blocks
from app.services import fai_audit, mikrotik_service, ssh_service
from app.services import fai_request_service as tracking

_real_set_full = blocks._set_full


@pytest.fixture
async def world(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'requests.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = fai_audit.get_settings()
    monkeypatch.setattr(settings, "fai_log_path", str(tmp_path / "audit.log"))
    monkeypatch.setattr(settings, "fai_evidence_dir", str(tmp_path / "evidence"))
    locks = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def isolated_lock(session, device_id):
        async with locks[device_id]:
            yield

    monkeypatch.setattr(tracking, "device_lock", isolated_lock)
    full = AsyncMock(return_value=(True, "Port LAN DOWN confirmé.", "simulation"))
    whatsapp = AsyncMock(return_value=(True, "Filtre confirmé."))
    monkeypatch.setattr(blocks, "_set_full", full)
    monkeypatch.setattr(blocks, "_set_whatsapp", whatsapp)
    monkeypatch.setattr(mikrotik_service, "is_enabled", lambda: True)
    router = AsyncMock(return_value=(True, "Blocage actif confirmé sur routeur."))
    cleanup = AsyncMock(return_value=(True, mikrotik_service.NO_RULE_MESSAGE))
    monkeypatch.setattr(mikrotik_service, "block_by_mac", router)
    monkeypatch.setattr(mikrotik_service, "unblock_by_mac", cleanup)
    async with factory() as session:
        lr = Lr(name="CLIENT TEST", mac_address="02:de:00:00:03:01",
                ip_address="192.0.2.1", device_type="lr", status="up", site="TEST",
                model_variant="litebeam_ac",
                topology_mode="router", lan_interface="eth0", ssh_username="test",
                ssh_password="dummy-not-a-real-password")
        session.add(lr)
        await session.commit()
        lr_id = lr.id
    yield factory, lr_id, full, router, cleanup
    await engine.dispose()


async def cut(world, **kwargs):
    factory, lr_id, *_ = world
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        await blocks.block_client(session, lr, "TEST", source="payment", user="agent test", **kwargs)
        return lr._fai_request.request_key


async def state(world):
    factory, lr_id, *_ = world
    async with factory() as session:
        request = (await session.execute(select(FaiRequest).order_by(FaiRequest.id.desc()))).scalars().first()
        lr = await session.get(Lr, lr_id)
        return request, lr, fai_requests.view(request, lr)


async def test_full_success_and_later_restoration_keep_history(world):
    key = await cut(world)
    request, _, view = await state(world)
    completed = request.completed_at
    assert view["status"] == "completed"
    assert not view["remaining"]
    confirmed_actions = request.actions
    factory, lr_id, full, *_ = world
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        await blocks.unblock_client(session, lr)
    request, _, view = await state(world)
    assert request.request_key == key
    assert request.completed_at == completed
    assert request.actions == confirmed_actions
    assert view["status"] == "completed"
    assert view["current"]["block_requested"] is False
    assert request.active is False
    full.reset_mock()
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    full.assert_not_awaited()


async def test_offline_fallback_is_partial_then_return_completes(world):
    factory, lr_id, full, router, _ = world
    full.return_value = False, "SSH timed out : appareil non joignable.", None
    detected = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        tracking.observe_connectivity(lr, False, detected)
        lr.status = "down"
        await session.commit()
    await cut(world)
    request, _, view = await state(world)
    assert view["status"] == "incomplete"
    assert request.completed_at is None
    assert next(a for a in request.actions if a["key"] == "router")["result"] == "confirmed"
    assert next(a for a in request.actions if a["key"] == "client")["confirmed_at"] is None
    assert view["current"]["offline_detected_at"].startswith(detected.isoformat()[:19])
    router.assert_awaited_once()
    full.return_value = True, "Port LAN DOWN confirmé.", "simulation"
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        returned = tracking.now()
        tracking.observe_connectivity(lr, True, returned)
        lr.status = "up"
        await tracking.connectivity_event(session, lr, True, returned)
        await session.commit()
    async with factory() as session:
        assert await blocks._enforce_one_block(session, lr_id) == 1
    request, _, view = await state(world)
    assert view["status"] == "completed"
    assert view["current"]["offline_detected_at"] is None
    assert view["current"]["online_detected_at"] is not None
    async with factory() as session:
        events = await fai_requests.get_events(request.request_key, session)
    kinds = [e["kind"] for e in events["events"]]
    assert "online" in kinds and "completed" in kinds
    assert sum(kind == "completed" for kind in kinds) == 1


async def test_direct_cut_does_not_execute_router_path(world):
    _, _, _, router, cleanup = world
    await cut(world)
    request, _, view = await state(world)
    assert view["status"] == "completed" and view["cut_at"]
    assert view["waiting"]["periods"] == [] and view["waiting"]["total_seconds"] == 0
    assert not any(a["key"].startswith("router") for a in request.actions)
    router.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.parametrize("message,result", [
    ("Authentication failed.", "failed"),
    ("Commande acceptée, état non confirmé.", "unknown"),
])
async def test_failure_or_unknown_cannot_complete(world, message, result):
    world[2].return_value = False, message, None
    await cut(world)
    request, _, view = await state(world)
    client = next(a for a in request.actions if a["key"] == "client")
    assert client["result"] == result
    assert client["reason"] == message
    assert client["confirmed_at"] is None
    assert view["status"] == "incomplete"


async def test_cancel_stops_old_retry_and_new_request_has_own_identity(world):
    factory, lr_id, full, *_ = world
    full.return_value = False, "SSH timed out", None
    old_key = await cut(world)
    async with factory() as session:
        await fai_requests.cancel_request(old_key, session)
    full.reset_mock()
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    full.assert_not_awaited()
    request, _, view = await state(world)
    assert view["status"] == "incomplete" and request.active is False
    full.return_value = True, "Port confirmé.", None
    new_key = await cut(world)
    assert new_key != old_key
    async with factory() as session:
        old = (await session.execute(select(FaiRequest).where(FaiRequest.request_key == old_key))).scalar_one()
        assert old.completed_at is None and old.active is False


async def test_concurrent_restore_waits_and_queued_retry_reloads_intent(world):
    factory, lr_id, full, *_ = world
    started, release = asyncio.Event(), asyncio.Event()
    operations = []

    async def fake_full(lr, cut, capture_evidence=False):
        operations.append(cut)
        if cut:
            started.set()
            await release.wait()
        return True, "État confirmé.", None

    full.side_effect = fake_full
    cutting = asyncio.create_task(cut(world))
    await asyncio.wait_for(started.wait(), 3)

    async def restore():
        async with factory() as session:
            lr = await session.get(Lr, lr_id)  # intentionally read before lock
            await blocks.unblock_client(session, lr)

    restoring = asyncio.create_task(restore())
    await asyncio.sleep(0.05)
    assert operations == [True]
    release.set()
    await asyncio.gather(cutting, restoring)
    async with factory() as session:
        assert await blocks._enforce_one_block(session, lr_id) == 0
    assert operations == [True, False]
    _, lr, view = await state(world)
    assert lr.client_blocked is False
    assert view["status"] == "completed"  # historical success before restoration


async def test_filters_counts_and_legacy_do_not_infer_current_success(world, monkeypatch):
    factory, _, _, *_ = world
    await cut(world)
    fai_audit.log_action("BLOCK", ok=True, mac="02:de:00:00:03:01", name="OLD",
                         mode="full", source="payment", message="Blocage appliqué (ancien).")
    request, _, _ = await state(world)
    entries, stats = fai_audit.read_entries(limit=None)
    # Make the collision deterministic even if execution spans two seconds.
    for entry in entries:
        if entry["name"] == "OLD":
            entry["timestamp"] = request.received_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(fai_audit, "read_entries", lambda **kwargs: (entries, stats))
    async with factory() as session:
        response = await fai_requests.get_requests(db=session, source="payment", status="unverified",
                                                   search=None, start=None, end=None, limit=1, offset=0)
    assert response["counts"] == {"total": 2, "completed": 1, "incomplete": 0, "unverified": 1}
    assert response["matching"] == 1
    legacy = response["entries"][0]
    assert legacy["legacy"] is True
    assert legacy["status"] == "unverified"
    assert legacy["received_at"] is None and legacy["completed_at"] is None
    assert legacy["actions"][0]["result"] == "unknown"


async def test_explicit_request_marker_deduplicates_but_orphan_is_preserved(world):
    factory, _, *_ = world
    key = await cut(world)
    orphan = "12345678-1234-1234-1234-123456789abc"
    for request_id in (key, orphan):
        fai_audit.log_action("BLOCK", ok=True, mac="02:de:00:00:03:01", name="AUDIT",
                             mode="full", source="payment", message="Trace de demande.",
                             request_id=request_id)
    async with factory() as session:
        response = await fai_requests.get_requests(
            db=session, source="payment", status=None, limit=100, offset=0)
    assert response["counts"] == {"total": 2, "completed": 1, "incomplete": 0, "unverified": 1}
    assert sum(row["legacy"] for row in response["entries"]) == 1


async def test_imported_legacy_key_deduplicates_only_its_original_entry(world):
    factory, _, *_ = world
    await cut(world)
    fai_audit.log_action("BLOCK", ok=True, mac="02:de:00:00:03:01", name="IMPORTED",
                         mode="full", source="payment", message="Ancienne trace.")
    entries, _ = fai_audit.read_entries(limit=None)
    entry = next(e for e in entries if e["name"] == "IMPORTED")
    async with factory() as session:
        request = (await session.execute(select(FaiRequest))).scalar_one()
        request.request_key = fai_audit.legacy_request_key(
            entry["timestamp"], entry["mac"], entry["action"])
        await session.commit()
    # Keep just the original legacy trace: the former modern marker would now
    # correctly be an orphan, which the previous test covers separately.
    with patch.object(fai_audit, "read_entries", return_value=([entry], {"total": 1})):
        async with factory() as session:
            response = await fai_requests.get_requests(
                db=session, source="payment", status=None, limit=100, offset=0)
    assert response["counts"] == {"total": 1, "completed": 1, "incomplete": 0, "unverified": 0}


async def test_deleted_inventory_does_not_change_completed_history(world):
    await cut(world)
    request, _, _ = await state(world)
    assert fai_requests.view(request, None)["status"] == "completed"
    assert fai_requests.view(request, None)["active"] is False


async def test_current_situation_filters_scope_counts_and_preserve_history(world):
    factory, lr_id, full, *_ = world
    await cut(world)
    full.return_value = False, "SSH timed out : rétablissement non confirmé.", None
    async with factory() as session:
        await blocks.unblock_client(session, await session.get(Lr, lr_id))
        session.add(FaiRequest(
            request_key="deleted-request", device_id=None, name="FICHE SUPPRIMEE",
            source="payment", mode="full", received_at=tracking.now(), active=False,
            actions=[tracking.action("client", "LR supprimé", "Fermer le port LAN")],
        ))
        await session.commit()
    async with factory() as session:
        restored = await fai_requests.get_requests(
            db=session, source="payment", status=None, situation="restored",
            search=None, start=None, end=None, limit=100, offset=0,
        )
        assert restored["counts"] == {"total": 1, "completed": 1, "incomplete": 0, "unverified": 0}
        assert restored["entries"][0]["status"] == "completed"
        assert restored["entries"][0]["current"]["restoration_pending"] is True
        combined = await fai_requests.get_requests(
            db=session, source="payment", status="incomplete", situation="restored",
            search=None, start=None, end=None, limit=100, offset=0,
        )
        assert combined["entries"] == [] and combined["matching"] == 0
        assert combined["counts"] == restored["counts"]
        deleted = await fai_requests.get_requests(
            db=session, source="payment", status="incomplete", situation="deleted",
            search="FICHE", start=None, end=None, limit=1, offset=0,
        )
        assert deleted["counts"] == {"total": 1, "completed": 0, "incomplete": 1, "unverified": 0}
        assert deleted["entries"][0]["current"]["known"] is False
        assert deleted["entries"][0]["name"] == "FICHE SUPPRIMEE"
        assert "Fiche supprimée" in deleted["entries"][0]["closed_reason"]
        assert "None" not in deleted["entries"][0]["summary"]


def test_observation_records_detection_not_physical_poweroff():
    lr = Lr(name="TEST", status="unknown")
    stamp = tracking.now()
    tracking.observe_connectivity(lr, False, stamp)
    assert lr.offline_detected_at == stamp
    lr.status = "down"
    tracking.observe_connectivity(lr, False, stamp + datetime.timedelta(seconds=60))
    assert lr.offline_detected_at == stamp
    tracking.observe_connectivity(lr, True, stamp + datetime.timedelta(seconds=120))
    assert lr.offline_detected_at is None
    assert lr.online_detected_at == stamp + datetime.timedelta(seconds=120)


def test_first_online_observation_does_not_invent_a_return_time():
    lr = Lr(name="TEST", status="unknown")
    tracking.observe_connectivity(lr, True, tracking.now())
    assert lr.online_detected_at is None


async def test_no_credentials_is_visible_without_a_network_attempt(world):
    factory, lr_id, full, router, cleanup = world
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        lr.ssh_password = None
        await session.commit()
    await cut(world)
    request, lr, view = await state(world)
    assert view["status"] == "incomplete" and request.active is False
    assert lr.client_blocked is False
    full.assert_not_awaited()
    router.assert_not_awaited()
    cleanup.assert_not_awaited()


async def test_bridge_request_is_recorded_without_network_attempt(world):
    factory, lr_id, full, router, cleanup = world
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        lr.topology_mode = "bridge"
        await session.commit()
    await cut(world)
    request, _, view = await state(world)
    assert view["status"] == "incomplete"
    assert request.active is False
    assert "bridge" in request.actions[0]["reason"]
    full.assert_not_awaited()
    router.assert_not_awaited()
    cleanup.assert_not_awaited()


async def test_offline_restore_before_return_prevents_later_cut(world):
    factory, lr_id, full, *_ = world
    full.return_value = False, "SSH timed out", None
    await cut(world)
    async with factory() as session:
        await blocks.unblock_client(session, await session.get(Lr, lr_id))
    full.return_value = True, "Port UP confirmé.", None
    full.reset_mock()
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    assert full.await_count == 1
    assert full.await_args.kwargs["cut"] is False
    request, lr, view = await state(world)
    assert request.active is False and request.completed_at is None
    assert lr.client_blocked is False and lr.unblock_pending is False
    assert view["status"] == "incomplete"


async def test_postgres_lock_is_released_and_does_not_consume_session_pool(monkeypatch):
    session = MagicMock()
    session.bind = MagicMock(spec=tracking.AsyncEngine)
    session.bind.dialect.name = "postgresql"
    connection = MagicMock()
    connection.execution_options = AsyncMock(return_value=connection)
    connection.execute = AsyncMock()
    connection.invalidate = AsyncMock()
    locking_engine = MagicMock()
    locking_engine.connect.return_value.__aenter__ = AsyncMock(return_value=connection)
    locking_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
    create = MagicMock(return_value=locking_engine)
    monkeypatch.setattr(tracking, "create_async_engine", create)
    # Fixture-independent test exercises the real PostgreSQL lock code with no DB.
    async with tracking.device_lock(session, 42):
        pass
    assert "pg_advisory_lock" in str(connection.execute.await_args_list[0].args[0])
    assert "pg_advisory_unlock" in str(connection.execute.await_args_list[1].args[0])
    assert create.call_args.kwargs["poolclass"] is tracking.NullPool
    session.bind.connect.assert_not_called()


@pytest.mark.parametrize("after_add", [
    [], [{".id": "*1", "disabled": True}],
    [{".id": "*1", "chain": "input", "action": "drop", "src-mac-address": "02:DE:00:00:03:01"}],
    [{".id": "*1", "chain": "forward", "action": "accept", "src-mac-address": "02:DE:00:00:03:01"}],
    [{".id": "*1", "chain": "forward", "action": "drop", "src-mac-address": "02:DE:00:00:03:02"}],
])
def test_router_command_ack_without_active_rule_is_failure(after_add):
    api = MagicMock()
    api.rawCmd.side_effect = [[], [], after_add]

    @contextmanager
    def fake_session():
        yield api

    with patch.object(mikrotik_service, "_session", fake_session):
        ok, message = mikrotik_service._block_sync("02:DE:00:00:03:01", "TEST")
    assert ok is False
    assert "relecture" in message


def test_ssh_command_ack_without_readback_is_failure():
    transport = MagicMock()
    with (patch.object(ssh_service, "_open_transport", return_value=(transport, None, None)),
          patch.object(ssh_service, "identity_refusal", return_value=None),
          patch.object(ssh_service, "_collect_forbidden_ifaces", return_value=set()),
          patch.object(ssh_service, "_exec", return_value=0),
          patch.object(ssh_service, "_read_admin_up", return_value=None),
          patch.object(ssh_service, "_start_session_watchdog", return_value=MagicMock())):
        ok, message, *_ = ssh_service._set_iface_state_sync(
            "192.0.2.1", 22, "test", "dummy", "eth0", False, None, [],
        )
    assert ok is False
    assert "non confirmé" in message


async def test_unaccepted_restoration_cannot_commit_history_without_intent(world):
    await cut(world)
    factory, lr_id, *_ = world

    @tracking.serialized
    async def unblock_client(session, lr):
        # Simulate a crash before restoration accepts/commits the device intent.
        raise RuntimeError("simulated interruption")

    async with factory() as session:
        with pytest.raises(RuntimeError, match="simulated interruption"):
            await unblock_client(session, await session.get(Lr, lr_id))
        await session.rollback()
    request, lr, _ = await state(world)
    assert request.active is True and lr.client_blocked is True


async def test_wait_continues_after_return_until_cut_and_survives_reload(world, monkeypatch):
    factory, lr_id, full, *_ = world
    clock = [datetime.datetime(2026, 10, 7, 14, 0, tzinfo=datetime.UTC)]
    monkeypatch.setattr(tracking, "now", lambda: clock[0])
    full.return_value = False, "SSH timed out : appareil non joignable.", None
    await cut(world, received_at=clock[0] - datetime.timedelta(minutes=5))
    request, _, view = await state(world)
    assert view["stage"] == "waiting" and view["cut_at"] is None
    assert view["waiting"]["started_at"].startswith("2026-10-07T14:00:00")
    assert view["received_at"].startswith("2026-10-07T13:55:00")
    clock[0] += datetime.timedelta(minutes=10)
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    _, _, view = await state(world)
    assert len(view["waiting"]["periods"]) == 1
    assert view["waiting"]["total_seconds"] == 600
    clock[0] += datetime.timedelta(minutes=20)
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        # The last status may still be up even though a cut attempt timed out.
        assert lr.status == "up"
        await tracking.connectivity_event(session, lr, True, clock[0])
        await session.commit()
    clock[0] += datetime.timedelta(minutes=1)
    _, _, view = await state(world)  # fresh session = refresh/reopen
    assert view["status"] == "incomplete" and view["stage"] == "to_cut"
    assert view["waiting"]["in_progress"] is True
    assert view["waiting"]["total_seconds"] == 1860
    clock[0] += datetime.timedelta(minutes=1)
    full.return_value = True, "Port LAN DOWN confirmé.", "simulation"
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    request, _, view = await state(world)
    assert view["status"] == "completed"
    assert view["cut_at"].startswith("2026-10-07T14:32:00")
    assert view["waiting"]["returned_at"].startswith("2026-10-07T14:30:00")
    assert view["waiting"]["total_seconds"] == 1920
    assert view["waiting"]["in_progress"] is False
    clock[0] += datetime.timedelta(days=2)
    async with factory() as session:
        await blocks.unblock_client(session, await session.get(Lr, lr_id))
    _, _, reloaded = await state(world)
    assert reloaded["cut_at"] == view["cut_at"]
    assert reloaded["waiting"] == view["waiting"]
    assert reloaded["status"] == "completed"
    async with factory() as session:
        events = (await fai_requests.get_events(request.request_key, session))["events"]
    kinds = [event["kind"] for event in events]
    assert kinds.index("received") < kinds.index("waiting_started") < kinds.index("online") < kinds.index("completed")
    assert sum(kind == "waiting_started" for kind in kinds) == 1


async def test_router_cleanup_retry_preserves_actual_client_cut_time(world, monkeypatch):
    factory, lr_id, _, _, cleanup = world
    clock = [datetime.datetime(2026, 10, 7, 14, 32, tzinfo=datetime.UTC)]
    monkeypatch.setattr(tracking, "now", lambda: clock[0])
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        lr.router_blocked = True  # Existing fallback to clean, not a direct cut path.
        await session.commit()
    cleanup.return_value = False, "Routeur inaccessible."
    await cut(world)
    _, _, first = await state(world)
    assert first["status"] == "completed" and first["stage"] == "completed"
    assert first["cut_at"].startswith("2026-10-07T14:32:00")
    clock[0] += datetime.timedelta(minutes=3)
    cleanup.return_value = True, mikrotik_service.NO_RULE_MESSAGE
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    _, _, final = await state(world)
    assert final["status"] == "completed"
    assert final["cut_at"] == first["cut_at"]
    assert final["completed_at"] == first["completed_at"]
    assert not final["remaining"]


async def test_authentication_refusal_is_not_waiting_for_return(world):
    factory, lr_id, full, *_ = world
    full.return_value = False, "Authentication failed", None
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        lr.status = "down"
        lr.offline_detected_at = tracking.now() - datetime.timedelta(hours=3)
        await session.commit()
    await cut(world)
    _, _, view = await state(world)
    assert view["stage"] == "attention"
    assert view["waiting"]["periods"] == []
    assert view["cut_at"] is None


async def test_cancel_freezes_wait_without_inventing_a_return(world, monkeypatch):
    clock = [datetime.datetime(2026, 10, 7, 14, 0, tzinfo=datetime.UTC)]
    monkeypatch.setattr(tracking, "now", lambda: clock[0])
    world[2].return_value = False, "SSH timed out", None
    key = await cut(world)
    clock[0] += datetime.timedelta(minutes=5)
    async with world[0]() as session:
        await fai_requests.cancel_request(key, session)
    clock[0] += datetime.timedelta(hours=2)
    _, _, view = await state(world)
    assert view["stage"] == "closed" and view["status"] == "incomplete"
    assert view["waiting"]["total_seconds"] == 300
    assert view["waiting"]["returned_at"] is None
    assert view["waiting"]["periods"][0]["end_reason"] == "closed"
    world[2].reset_mock()
    async with world[0]() as session:
        await blocks._enforce_one_block(session, world[1])
    world[2].assert_not_awaited()


async def test_repeated_unavailability_preserves_first_wait_start(world, monkeypatch):
    clock = [datetime.datetime(2026, 10, 7, 14, 0, tzinfo=datetime.UTC)]
    monkeypatch.setattr(tracking, "now", lambda: clock[0])
    factory, lr_id, full, *_ = world
    full.return_value = False, "SSH timed out", None
    await cut(world)
    for return_minutes, next_failure_minutes in ((10, 15), (25, None)):
        clock[0] = clock[0].replace(minute=return_minutes)
        async with factory() as session:
            await tracking.connectivity_event(session, await session.get(Lr, lr_id), True, clock[0])
            await session.commit()
        if next_failure_minutes:
            clock[0] = clock[0].replace(minute=next_failure_minutes)
            async with factory() as session:
                await blocks._enforce_one_block(session, lr_id)
    clock[0] += datetime.timedelta(hours=1)
    _, _, view = await state(world)
    assert len(view["waiting"]["periods"]) == 1
    assert view["waiting"]["total_seconds"] == 5100  # 14:00 to 15:25, including time after return
    assert view["waiting"]["in_progress"] is True
    assert view["stage"] == "to_cut"


async def test_identification_is_snapshotted_from_real_fields(world):
    factory, lr_id, *_ = world
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        lr.uisp_crm_client_id = "12345"
        lr.uisp_crm_client_name = "CLIENT CRM TEST"
        await session.commit()
    await cut(world, contact_name="AGENT NOM TEST", contact_phone="+000 SIMULATION")
    async with factory() as session:
        lr = await session.get(Lr, lr_id)
        lr.uisp_crm_client_name = "CHANGED NAME"
        await session.commit()
    _, _, view = await state(world)
    assert view["client_id"] == "12345"
    assert view["client_name"] == "CLIENT CRM TEST"
    assert view["contact_name"] == "AGENT NOM TEST"
    assert view["contact_phone"] == "+000 SIMULATION"


@pytest.mark.parametrize("wrong_identity", [False, True])
async def test_authenticated_return_does_not_stop_wait_before_cut(world, monkeypatch, wrong_identity):
    factory, lr_id, full, *_ = world
    clock = [datetime.datetime(2026, 10, 7, 14, 0, tzinfo=datetime.UTC)]
    monkeypatch.setattr(tracking, "now", lambda: clock[0])
    full.return_value = False, "SSH timed out", None
    await cut(world)
    clock[0] += datetime.timedelta(minutes=30)
    message = ssh_service.IDENTITY_REFUSAL_PREFIX + " autre client" if wrong_identity else "Command timed out"
    monkeypatch.setattr(ssh_service, "set_lan_interface", AsyncMock(
        return_value=(False, message, "test-fingerprint", "dummy", None)))
    monkeypatch.setattr(blocks, "_set_full", _real_set_full)
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    _, _, view = await state(world)
    assert view["status"] == "incomplete" and view["cut_at"] is None
    assert view["waiting"]["in_progress"] is True
    assert view["waiting"]["total_seconds"] == 1800
    assert bool(view["waiting"]["returned_at"]) is not wrong_identity
    assert len(view["waiting"]["periods"]) == 1


async def test_deleted_record_freezes_wait_and_disables_old_request(world, monkeypatch):
    from app.services import device_service

    clock = [datetime.datetime(2026, 10, 7, 14, 0, tzinfo=datetime.UTC)]
    monkeypatch.setattr(tracking, "now", lambda: clock[0])
    factory, lr_id, full, *_ = world
    full.return_value = False, "SSH timed out", None
    await cut(world)
    clock[0] += datetime.timedelta(minutes=5)
    async with factory() as session:
        await device_service.delete_device(session, lr_id)
        await session.commit()
    clock[0] += datetime.timedelta(days=1)
    _, _, view = await state(world)
    assert view["active"] is False and view["stage"] == "closed"
    assert view["waiting"]["total_seconds"] == 300
    assert view["waiting"]["returned_at"] is None


async def test_return_then_command_failure_keeps_live_wait_until_confirmation(world, monkeypatch):
    factory, lr_id, full, router, _ = world
    clock = [datetime.datetime(2026, 10, 7, 14, 0, tzinfo=datetime.UTC)]
    monkeypatch.setattr(tracking, "now", lambda: clock[0])
    full.return_value = False, "SSH timed out", None
    await cut(world, received_at=clock[0] - datetime.timedelta(hours=1))
    clock[0] += datetime.timedelta(minutes=20)
    async with factory() as session:
        await tracking.connectivity_event(session, await session.get(Lr, lr_id), True, clock[0])
        await session.commit()
    full.return_value = False, "Commande acceptée, état non confirmé.", None
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    clock[0] += datetime.timedelta(minutes=5)
    _, _, pending = await state(world)
    assert pending["status"] == "incomplete" and pending["cut_at"] is None
    assert pending["waiting"]["total_seconds"] == 1500 and pending["waiting"]["in_progress"]
    clock[0] += datetime.timedelta(minutes=5)
    full.return_value = True, "Port LAN DOWN confirmé.", None
    async with factory() as session:
        await blocks._enforce_one_block(session, lr_id)
    _, _, done = await state(world)
    assert done["status"] == "completed" and done["cut_at"].startswith("2026-10-07T14:30:00")
    assert done["waiting"]["total_seconds"] == 1800 and not done["waiting"]["in_progress"]
    clock[0] += datetime.timedelta(days=1)
    assert (await state(world))[2]["waiting"] == done["waiting"]
    router.assert_awaited_once()


async def test_old_return_ended_interval_uses_actual_cut_time(world, monkeypatch):
    await cut(world)
    async with world[0]() as session:
        request = (await session.execute(select(FaiRequest))).scalar_one()
        request.waiting_periods = [{"started_at": "2026-10-07T14:00:00+00:00",
                                    "ended_at": "2026-10-07T14:20:00+00:00", "end_reason": "returned"}]
        actions = [dict(a) for a in request.actions]
        actions[0]["confirmed_at"] = "2026-10-07T14:30:00+00:00"
        request.actions = actions
        await session.commit()
    _, _, view = await state(world)
    assert view["waiting"]["started_at"].startswith("2026-10-07T14:00:00")
    assert view["waiting"]["returned_at"].startswith("2026-10-07T14:20:00")
    assert view["waiting"]["total_seconds"] == 1800


async def test_history_without_wait_tracking_does_not_claim_zero_wait(world):
    await cut(world)
    async with world[0]() as session:
        request = (await session.execute(select(FaiRequest))).scalar_one()
        request.identification = {}  # Rows predating the tracking migration.
        await session.commit()
    _, _, view = await state(world)
    assert not view["waiting"]["known"]
