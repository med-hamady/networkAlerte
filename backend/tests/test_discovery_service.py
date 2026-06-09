"""
Integration tests for discovery_service.reconcile_peers — real PostgreSQL.

Focus: MAC is the only stable identity; IP is volatile (DHCP churn). A peer must
never steal a row from another Rocket just because it reuses a churned IP. The
stale holder's IP is freed (NULL); reassignment happens ONLY on a MAC match.

Each test runs in a rolled-back transaction (conftest `db`). notification_service
is patched to avoid real email/HTTP.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.device import Lr, Rocket
from app.services import discovery_service


@pytest.fixture
def patch_notif():
    with patch("app.services.discovery_service.notification_service") as mock:
        mock.notify_incident_opened = AsyncMock(return_value=True)
        mock.notify_incident_resolved = AsyncMock(return_value=True)
        yield mock


async def _rocket(db, name, ip, tech="airmax") -> Rocket:
    r = Rocket(name=name, ip_address=ip, radio_tech=tech, status="up")
    db.add(r)
    await db.flush()
    return r


async def _lr(db, name, ip, mac, rocket_id) -> Lr:
    lr = Lr(
        name=name, ip_address=ip, mac_address=mac, status="up",
        model_variant="litebeam_5ac", rocket_id=rocket_id, auto_discovered=True,
    )
    db.add(lr)
    await db.flush()
    return lr


async def test_new_mac_free_ip_creates_lr(db, patch_notif):
    """Peer with a brand-new MAC and an unused IP → new LR under this Rocket."""
    parent = await _rocket(db, "PK1-OUEST", "10.99.0.1")

    res = await discovery_service.reconcile_peers(db, parent, [
        {"mac": "1c:6a:1b:b4:29:3c", "mgmt_ip": "10.135.2.5", "hostname": "Client A"},
    ])

    assert len(res.created) == 1
    assert res.created[0].rocket_id == parent.id
    assert res.created[0].ip_address == "10.135.2.5"


async def test_new_mac_steals_stale_churned_ip(db, patch_notif):
    """Core fix: a new-MAC peer whose IP is held by a DIFFERENT-MAC LR on ANOTHER
    Rocket must NOT reassign that LR. The stale holder's IP is freed (NULL) and a
    fresh LR is created under the reporting Rocket."""
    other = await _rocket(db, "ARF1-OUEST1", "10.99.0.2")
    stale = await _lr(db, "Old Client", "10.135.2.5", "1c:6a:1b:b4:36:d3", other.id)
    parent = await _rocket(db, "PK1-OUEST", "10.99.0.1")

    res = await discovery_service.reconcile_peers(db, parent, [
        {"mac": "1c:6a:1b:b4:29:3c", "mgmt_ip": "10.135.2.5", "hostname": "New Client"},
    ])
    await db.flush()

    # New LR created under the reporting Rocket.
    assert len(res.created) == 1
    assert res.created[0].rocket_id == parent.id
    assert res.created[0].mac_address == "1c:6a:1b:b4:29:3c"
    # The stale LR is NOT reassigned and NOT renamed — it keeps its identity,
    # only loses the churned IP (freed to NULL).
    assert res.reassigned == []
    refreshed = await db.get(Lr, stale.id)
    assert refreshed.rocket_id == other.id
    assert refreshed.ip_address is None


async def test_mac_match_reassigns_across_rockets(db, patch_notif):
    """A MAC that already exists under another Rocket IS reassigned (legit move)."""
    other = await _rocket(db, "ARF1-OUEST1", "10.99.0.2")
    existing = await _lr(db, "Roaming Client", "10.135.9.9", "aa:bb:cc:dd:ee:ff", other.id)
    parent = await _rocket(db, "PK1-OUEST", "10.99.0.1")

    res = await discovery_service.reconcile_peers(db, parent, [
        {"mac": "aa:bb:cc:dd:ee:ff", "mgmt_ip": "10.135.9.9", "hostname": "Roaming Client"},
    ])

    assert res.created == []
    assert len(res.reassigned) == 1
    moved = await db.get(Lr, existing.id)
    assert moved.rocket_id == parent.id


async def test_legacy_macless_lr_adopted_not_duplicated(db, patch_notif):
    """A peer WITH a MAC that re-finds a legacy MAC-less LR on the SAME Rocket
    at that IP adopts it (pins the MAC) instead of creating a duplicate."""
    parent = await _rocket(db, "LTU-A", "10.99.0.1", tech="ltu")
    legacy = await _lr(db, "Legacy", "10.135.5.5", None, parent.id)

    res = await discovery_service.reconcile_peers(db, parent, [
        {"mac": "11:22:33:44:55:66", "mgmt_ip": "10.135.5.5", "hostname": "Legacy"},
    ])

    assert res.created == []
    adopted = await db.get(Lr, legacy.id)
    assert adopted.mac_address == "11:22:33:44:55:66"
    assert adopted.rocket_id == parent.id


async def test_no_mac_ip_fallback_scoped_to_same_rocket(db, patch_notif):
    """A peer WITHOUT a MAC matches an existing LR by IP only within the SAME
    Rocket; the same IP under another Rocket is never stolen."""
    parent = await _rocket(db, "LTU-A", "10.99.0.1", tech="ltu")
    same = await _lr(db, "Legacy A", "10.135.3.26", None, parent.id)
    other = await _rocket(db, "LTU-B", "10.99.0.2", tech="ltu")
    await _lr(db, "Legacy B", "10.135.3.99", None, other.id)

    # Same-Rocket, no MAC → matched (no duplicate created).
    res = await discovery_service.reconcile_peers(db, parent, [
        {"mac": None, "mgmt_ip": "10.135.3.26", "hostname": "Legacy A"},
    ])
    assert res.created == []
    assert len(res.matched) == 1
    assert res.matched[0].id == same.id


async def test_no_mac_foreign_ip_creates_not_steals(db, patch_notif):
    """A no-MAC peer whose IP is held by an LR on ANOTHER Rocket → that foreign LR
    is left alone (IP freed) and a new LR is created here, never reassigned."""
    other = await _rocket(db, "LTU-B", "10.99.0.2", tech="ltu")
    foreign = await _lr(db, "Foreign", "10.135.4.4", None, other.id)
    parent = await _rocket(db, "LTU-A", "10.99.0.1", tech="ltu")

    res = await discovery_service.reconcile_peers(db, parent, [
        {"mac": None, "mgmt_ip": "10.135.4.4", "hostname": "Foreign"},
    ])
    await db.flush()

    assert res.reassigned == []
    assert len(res.created) == 1
    assert res.created[0].rocket_id == parent.id
    refreshed = await db.get(Lr, foreign.id)
    assert refreshed.rocket_id == other.id
    assert refreshed.ip_address is None
