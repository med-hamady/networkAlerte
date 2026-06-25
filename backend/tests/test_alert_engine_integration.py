"""
Integration tests for alert_engine.py — real PostgreSQL, real ORM, no mock DB.

Each test gets a fresh transaction (via the `db` fixture in conftest.py) that
is rolled back at the end, so no data persists between tests.

Only notification_service is patched to avoid real HTTP/email calls.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.alert_state import AlertState
from app.models.device import Device, Lr, Rocket
from app.models.incident import Incident
from app.services.alert_engine import evaluate_device_metrics

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_notif():
    """Silence all outgoing email notifications."""
    with patch("app.services.alert_engine.notification_service") as mock:
        mock.notify_incident_opened = AsyncMock(return_value=True)
        mock.notify_incident_resolved = AsyncMock(return_value=True)
        yield mock


async def _make_rocket(db) -> Device:
    """Insert a minimal LTU Rocket device and return it."""
    device = Rocket(
        name="Test Rocket",
        ip_address="10.99.0.1",
        radio_tech="ltu",
        status="up",
    )
    db.add(device)
    await db.flush()
    return device


async def _make_lr(db, model_variant="ltu_lr") -> Device:
    """Insert a minimal subscriber LR (rule_category == 'lr') and return it."""
    device = Lr(
        name="Test LR",
        ip_address="10.99.0.50",
        model_variant=model_variant,
        status="up",
    )
    db.add(device)
    await db.flush()
    return device


# ---------------------------------------------------------------------------
# Suppression des incidents côté client (page /incidents = infra uniquement)
# ---------------------------------------------------------------------------

async def test_lr_radio_alert_suppressed(db, settings, patch_notif):
    """Une alerte radio sur un LR abonné (client) n'ouvre aucun incident."""
    lr = await _make_lr(db)

    # Signal très bas : ouvrirait signal_low sur une Rocket, mais le LR est client.
    for _ in range(10):
        await evaluate_device_metrics(db, lr, {"signal_dbm": -95.0}, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(Incident.device_id == lr.id)
    )
    assert result.scalars().first() is None


async def test_lr_bridge_misconfig_suppressed(db, patch_notif):
    """lr_bridge_mode_misconfig n'est plus un incident (géré par /access, 2026-06-25)."""
    from app.services import incident_service

    lr = await _make_lr(db)

    incident, is_new = await incident_service.open_incident(
        db, lr,
        title="LR en mode bridge",
        severity="warning",
        alert_type="lr_bridge_mode_misconfig",
    )
    await db.flush()

    assert is_new is False
    assert incident is None
    row = (
        await db.execute(select(Incident).where(Incident.device_id == lr.id))
    ).scalar_one_or_none()
    assert row is None


async def test_rocket_overload_suppressed(db, patch_notif):
    """rocket_client_overload n'est plus un incident (géré par /capacity, 2026-06-25)."""
    from app.services import incident_service

    rocket = await _make_rocket(db)

    incident, is_new = await incident_service.open_incident(
        db, rocket,
        title="Rocket saturé",
        severity="critical",
        alert_type="rocket_client_overload",
    )
    await db.flush()

    assert is_new is False
    assert incident is None
    row = (
        await db.execute(select(Incident).where(Incident.device_id == rocket.id))
    ).scalar_one_or_none()
    assert row is None


# ---------------------------------------------------------------------------
# Famille C — Signal
# ---------------------------------------------------------------------------

async def test_good_signal_no_incident(db, settings, patch_notif):
    """Signal nominal → aucun incident créé."""
    device = await _make_rocket(db)

    await evaluate_device_metrics(db, device, {"signal_dbm": -60.0}, settings)
    await db.flush()

    result = await db.execute(
        select(Incident).where(Incident.device_id == device.id)
    )
    assert result.scalars().all() == []


async def test_signal_warning_one_cycle_no_incident(db, settings, patch_notif):
    """1 cycle dégradé → compteur=1, seuil=2 → pas d'incident."""
    device = await _make_rocket(db)

    await evaluate_device_metrics(db, device, {"signal_dbm": -78.0}, settings)
    await db.flush()

    # Pas d'incident ouvert
    result = await db.execute(
        select(Incident).where(Incident.device_id == device.id, Incident.status == "open")
    )
    assert result.scalars().all() == []

    # Mais AlertState créé avec failure_count=1
    state_res = await db.execute(
        select(AlertState).where(
            AlertState.device_id == device.id,
            AlertState.alert_type == "signal_low",
        )
    )
    state = state_res.scalar_one_or_none()
    assert state is not None
    assert state.failure_count == 1


async def test_signal_warning_three_cycles_opens_incident(db, settings, patch_notif):
    """3 cycles dégradés → seuil dépassé → incident ouvert avec les bons champs."""
    device = await _make_rocket(db)

    for _ in range(3):
        await evaluate_device_metrics(db, device, {"signal_dbm": -78.0}, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "signal_low",
            Incident.status == "open",
        )
    )
    incident = result.scalar_one_or_none()
    assert incident is not None
    assert incident.severity == "warning"
    assert incident.metric_name == "signal_dbm"
    assert incident.metric_value == -78.0
    assert incident.threshold_value == -75.0
    assert incident.last_triggered_at is not None


async def test_signal_critical_opens_critical_incident(db, settings, patch_notif):
    """Signal < seuil critique → incident critique dès que seuil atteint."""
    device = await _make_rocket(db)

    for _ in range(3):
        await evaluate_device_metrics(db, device, {"signal_dbm": -85.0}, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "signal_low",
            Incident.status == "open",
        )
    )
    incident = result.scalar_one_or_none()
    assert incident is not None
    assert incident.severity == "critical"


async def test_signal_recovery_purges_incident(db, settings, patch_notif):
    """Retour nominal → incident non-disponibilité purgé de la DB (pas d'archive)."""
    device = await _make_rocket(db)

    # Ouvrir l'incident
    for _ in range(3):
        await evaluate_device_metrics(db, device, {"signal_dbm": -78.0}, settings)
        await db.flush()

    # Recovery
    await evaluate_device_metrics(db, device, {"signal_dbm": -60.0}, settings)
    await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "signal_low",
        )
    )
    # signal_low n'est pas un type de disponibilité → supprimé dès résolution.
    assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Anti-spam
# ---------------------------------------------------------------------------

async def test_no_duplicate_incidents(db, settings, patch_notif):
    """5 cycles d'alerte → 1 seul incident créé, notify appelé 1 fois."""
    device = await _make_rocket(db)

    for _ in range(5):
        await evaluate_device_metrics(db, device, {"radio_if_up": 0.0}, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "radio_interface_down",
        )
    )
    incidents = result.scalars().all()
    assert len(incidents) == 1
    assert patch_notif.notify_incident_opened.call_count == 1


async def test_last_triggered_at_updated_each_cycle(db, settings, patch_notif):
    """last_triggered_at mis à jour à chaque cycle même si incident déjà ouvert."""
    device = await _make_rocket(db)

    # Ouvrir l'incident (cycles 1-3)
    for _ in range(3):
        await evaluate_device_metrics(db, device, {"radio_if_up": 0.0}, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "radio_interface_down",
        )
    )
    first_incident = result.scalar_one()
    ts_before = first_incident.last_triggered_at

    # 4ème cycle — incident déjà ouvert, mais last_triggered_at doit être mis à jour
    await evaluate_device_metrics(db, device, {"radio_if_up": 0.0}, settings)
    await db.flush()

    await db.refresh(first_incident)
    assert first_incident.last_triggered_at >= ts_before


# ---------------------------------------------------------------------------
# Famille B — Interface et lien
# ---------------------------------------------------------------------------

async def test_radio_interface_down_immediate(db, settings, patch_notif):
    """radio_interface_down : seuil=0, incident ouvert dès le 1er cycle."""
    device = await _make_rocket(db)

    await evaluate_device_metrics(db, device, {"radio_if_up": 0.0}, settings)
    await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "radio_interface_down",
            Incident.status == "open",
        )
    )
    incident = result.scalar_one_or_none()
    assert incident is not None
    assert incident.severity == "critical"


async def test_eth0_down_immediate(db, settings, patch_notif):
    """eth0_down : seuil=0, incident ouvert dès le 1er cycle."""
    device = await _make_rocket(db)

    await evaluate_device_metrics(db, device, {"eth_if_up": 0.0}, settings)
    await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "eth0_down",
            Incident.status == "open",
        )
    )
    assert result.scalar_one_or_none() is not None


async def test_cpe_disconnected_suppressed(db, settings, patch_notif):
    """cpe_disconnected est supprimé (client-side) même levé sur une Rocket infra."""
    device = await _make_rocket(db)

    await evaluate_device_metrics(db, device, {"peer_count": 0}, settings)
    await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "cpe_disconnected",
        )
    )
    # Jamais créé : cpe_disconnected ∈ INFRA_DEVICE_SUPPRESSED_ALERT_TYPES.
    assert result.scalar_one_or_none() is None


async def test_availability_incident_kept_resolved_for_journal(db, patch_notif):
    """Un incident de disponibilité résolu reste en DB (le journal coupures en a besoin)."""
    from app.services import incident_service

    device = await _make_rocket(db)

    incident, _ = await incident_service.open_incident(
        db, device, title="Rocket down", severity="critical", alert_type="rocket_down"
    )
    await db.flush()

    # Les types de disponibilité ne sont jamais purgés à la résolution.
    resolved = await incident_service.resolve_incidents(
        db, device.id, title="Rocket up", alert_type="rocket_down"
    )
    assert len(resolved) == 1
    await db.flush()

    row = (
        await db.execute(select(Incident).where(Incident.id == incident.id))
    ).scalar_one_or_none()
    assert row is not None
    assert row.status == "resolved"
    assert row.resolved_at is not None


# ---------------------------------------------------------------------------
# AlertState persistence
# ---------------------------------------------------------------------------

async def test_alert_state_persisted_and_incremented(db, settings, patch_notif):
    """AlertState créé en DB et incrémenté correctement cycle après cycle."""
    device = await _make_rocket(db)

    for expected_count in range(1, 4):
        await evaluate_device_metrics(db, device, {"signal_dbm": -78.0}, settings)
        await db.flush()

        result = await db.execute(
            select(AlertState).where(
                AlertState.device_id == device.id,
                AlertState.alert_type == "signal_low",
            )
        )
        state = result.scalar_one_or_none()
        assert state is not None
        assert state.failure_count == expected_count
        assert state.last_evaluated_at is not None


async def test_alert_state_reset_on_recovery(db, settings, patch_notif):
    """AlertState.failure_count remis à 0 dès retour nominal."""
    device = await _make_rocket(db)

    # 2 cycles dégradés
    for _ in range(2):
        await evaluate_device_metrics(db, device, {"signal_dbm": -78.0}, settings)
        await db.flush()

    # Recovery
    await evaluate_device_metrics(db, device, {"signal_dbm": -60.0}, settings)
    await db.flush()

    result = await db.execute(
        select(AlertState).where(
            AlertState.device_id == device.id,
            AlertState.alert_type == "signal_low",
        )
    )
    state = result.scalar_one_or_none()
    assert state is not None
    assert state.failure_count == 0


# ---------------------------------------------------------------------------
# Famille C — CINR et CCQ
# ---------------------------------------------------------------------------

async def test_cinr_low_opens_incident(db, settings, patch_notif):
    """CINR < seuil warning → incident après anti-flap."""
    device = await _make_rocket(db)

    for _ in range(3):
        await evaluate_device_metrics(db, device, {"cinr_db": 15.0}, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "cinr_low",
            Incident.status == "open",
        )
    )
    assert result.scalar_one_or_none() is not None


async def test_ccq_low_opens_incident(db, settings, patch_notif):
    """CCQ < seuil warning → incident après anti-flap."""
    device = await _make_rocket(db)

    for _ in range(3):
        await evaluate_device_metrics(db, device, {"ccq_pct": 60.0}, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "ccq_low",
            Incident.status == "open",
        )
    )
    assert result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Famille C — Dégradation composite
# ---------------------------------------------------------------------------

async def test_radio_link_degraded_two_metrics(db, settings, patch_notif):
    """2 métriques en warning → radio_link_degraded déclenché."""
    device = await _make_rocket(db)

    for _ in range(3):
        await evaluate_device_metrics(db, device, {
            "signal_dbm": -78.0,
            "cinr_db": 15.0,
        }, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "radio_link_degraded",
            Incident.status == "open",
        )
    )
    assert result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Famille D — Capacité
# ---------------------------------------------------------------------------

async def test_capacity_low_warning(db, settings, patch_notif):
    """Capacité à 20% → incident capacity_low warning après anti-flap."""
    device = await _make_rocket(db)

    for _ in range(4):
        await evaluate_device_metrics(db, device, {
            "tx_rate_mbps": 20.0,
            "tx_ideal_mbps": 100.0,
        }, settings)
        await db.flush()

    result = await db.execute(
        select(Incident).where(
            Incident.device_id == device.id,
            Incident.alert_type == "capacity_low",
            Incident.status == "open",
        )
    )
    incident = result.scalar_one_or_none()
    assert incident is not None
    assert incident.severity == "warning"
