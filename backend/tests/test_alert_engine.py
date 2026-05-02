"""
Unit tests for alert_engine.py — uses AsyncMock to avoid real DB.

Tests verify the engine's anti-flap logic: correct number of consecutive
bad cycles required before an alert is opened, and immediate resolution
when the condition clears.
"""

import types
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.alert_engine import evaluate_device_metrics


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_settings(**overrides):
    defaults = dict(
        signal_warning_dbm=-70,
        signal_critical_dbm=-80,
        ccq_warning_pct=75,
        ccq_critical_pct=50,
        cinr_warning_db=20.0,
        cinr_critical_db=10.0,
        capacity_low_warning_pct=30.0,
        capacity_low_critical_pct=15.0,
        rx_tx_error_warning_pct=1.0,
        rx_tx_error_critical_pct=5.0,
        signal_failure_threshold=2,
        cinr_failure_threshold=2,
        ccq_failure_threshold=2,
        capacity_failure_threshold=3,
        error_failure_threshold=2,
        radio_degraded_failure_threshold=2,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def make_device(device_type="ltu_rocket"):
    dev = MagicMock()
    dev.id = 1
    dev.name = "LTU Rocket"
    dev.ip_address = "192.168.1.10"
    dev.device_type = device_type
    return dev


def make_alert_state(failure_count=0, last_metric_value=None):
    state = MagicMock()
    state.failure_count = failure_count
    state.last_metric_value = last_metric_value
    state.last_evaluated_at = None
    return state


# ---------------------------------------------------------------------------
# Helper: create a mock DB that returns a controllable AlertState
# ---------------------------------------------------------------------------

def make_mock_db(failure_count=0, last_metric_value=None):
    """Return an AsyncMock db that yields a given AlertState on _get_or_create_state."""
    db = AsyncMock()
    state = make_alert_state(failure_count=failure_count, last_metric_value=last_metric_value)

    async def mock_execute(query):
        result = MagicMock()
        result.scalar_one_or_none.return_value = state
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = mock_execute
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db, state


# ---------------------------------------------------------------------------
# Tests: signal_low with threshold=2 (opens on 3rd bad cycle)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signal_ok_no_incident_opened():
    """Good signal → no incident opened."""
    db, state = make_mock_db(failure_count=0)
    device = make_device()
    settings = make_settings()

    with patch("app.services.alert_engine.incident_service") as mock_svc, \
         patch("app.services.alert_engine.notification_service"):
        mock_svc.open_incident = AsyncMock()
        mock_svc.resolve_incidents = AsyncMock(return_value=[])
        mock_svc.open_incident.return_value = (MagicMock(), False)

        await evaluate_device_metrics(
            db, device,
            {"signal_dbm": -60.0},
            settings,
        )

        mock_svc.open_incident.assert_not_called()


@pytest.mark.asyncio
async def test_signal_bad_first_cycle_no_incident():
    """Bad signal on 1st cycle → counter=1, threshold=2 → no incident yet."""
    db, state = make_mock_db(failure_count=0)
    device = make_device()
    settings = make_settings()

    with patch("app.services.alert_engine.incident_service") as mock_svc, \
         patch("app.services.alert_engine.notification_service"):
        mock_svc.open_incident = AsyncMock(return_value=(MagicMock(), False))
        mock_svc.resolve_incidents = AsyncMock(return_value=[])

        await evaluate_device_metrics(
            db, device,
            {"signal_dbm": -75.0},
            settings,
        )

        # After this call state.failure_count was 0 → incremented to 1 → 1 > 2 is False
        mock_svc.open_incident.assert_not_called()


@pytest.mark.asyncio
async def test_signal_bad_third_cycle_opens_incident():
    """Bad signal on 3rd cycle → counter becomes 3 > threshold 2 → incident opened.

    We patch _get_or_create_state directly so signal_low returns failure_count=2
    while all other alert_types return a fresh state (count=0), avoiding the
    interference where earlier rules reset the shared mock state to 0.
    """
    db = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()

    signal_state = make_alert_state(failure_count=2)
    other_state = make_alert_state(failure_count=0)

    async def mock_get_or_create(db, device_id, alert_type):
        return signal_state if alert_type == "signal_low" else other_state

    device = make_device()
    settings = make_settings()
    opened_incident = MagicMock()
    opened_incident.id = 42

    with patch("app.services.alert_engine._get_or_create_state", side_effect=mock_get_or_create), \
         patch("app.services.alert_engine._apply_correlation", new_callable=AsyncMock), \
         patch("app.services.alert_engine.incident_service") as mock_svc, \
         patch("app.services.alert_engine.notification_service") as mock_notif:
        mock_svc.open_incident = AsyncMock(return_value=(opened_incident, True))
        mock_svc.resolve_incidents = AsyncMock(return_value=[])
        mock_notif.notify_incident_opened = AsyncMock(return_value=True)

        await evaluate_device_metrics(
            db, device,
            {"signal_dbm": -75.0},
            settings,
        )

        mock_svc.open_incident.assert_called()
        call_kwargs = mock_svc.open_incident.call_args.kwargs
        assert call_kwargs.get("alert_type") == "signal_low"


@pytest.mark.asyncio
async def test_signal_recovers_resolves_incident():
    """Good signal after bad cycles → resolve called, counter reset."""
    db, state = make_mock_db(failure_count=3)  # was in alert state
    device = make_device()
    settings = make_settings()

    resolved_incident = MagicMock()
    resolved_incident.id = 42
    resolved_incident.resolved_at = None

    with patch("app.services.alert_engine.incident_service") as mock_svc, \
         patch("app.services.alert_engine.notification_service") as mock_notif:
        mock_svc.open_incident = AsyncMock(return_value=(MagicMock(), False))
        mock_svc.resolve_incidents = AsyncMock(return_value=[resolved_incident])
        mock_notif.notify_incident_resolved = AsyncMock(return_value=True)

        await evaluate_device_metrics(
            db, device,
            {"signal_dbm": -60.0},  # good signal
            settings,
        )

        # resolve_incidents should be called for signal_low
        resolve_calls = mock_svc.resolve_incidents.call_args_list
        alert_types_resolved = [
            c.kwargs.get("alert_type") for c in resolve_calls
        ]
        assert "signal_low" in alert_types_resolved


# ---------------------------------------------------------------------------
# Tests: radio_interface_down (threshold=0, immediate)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_radio_interface_down_immediate():
    """radio_interface_down has threshold=0 → incident opens on first bad cycle."""
    db, state = make_mock_db(failure_count=0)
    device = make_device()
    settings = make_settings()

    opened_incident = MagicMock()
    opened_incident.id = 10

    with patch("app.services.alert_engine.incident_service") as mock_svc, \
         patch("app.services.alert_engine.notification_service") as mock_notif:
        mock_svc.open_incident = AsyncMock(return_value=(opened_incident, True))
        mock_svc.resolve_incidents = AsyncMock(return_value=[])
        mock_notif.notify_incident_opened = AsyncMock(return_value=True)

        await evaluate_device_metrics(
            db, device,
            {"radio_if_up": 0.0},
            settings,
        )

        mock_svc.open_incident.assert_called()


# ---------------------------------------------------------------------------
# Tests: cpe_disconnected (threshold=0, immediate)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cpe_disconnected_immediate():
    """cpe_disconnected → immediate, no anti-flap wait."""
    db, state = make_mock_db(failure_count=0)
    device = make_device()
    settings = make_settings()

    opened_incident = MagicMock()
    opened_incident.id = 20

    with patch("app.services.alert_engine.incident_service") as mock_svc, \
         patch("app.services.alert_engine.notification_service") as mock_notif:
        mock_svc.open_incident = AsyncMock(return_value=(opened_incident, True))
        mock_svc.resolve_incidents = AsyncMock(return_value=[])
        mock_notif.notify_incident_opened = AsyncMock(return_value=True)

        await evaluate_device_metrics(
            db, device,
            {"peer_count": 0},
            settings,
        )

        mock_svc.open_incident.assert_called()
        call_kwargs = mock_svc.open_incident.call_args.kwargs
        assert call_kwargs.get("alert_type") == "cpe_disconnected"


# ---------------------------------------------------------------------------
# Tests: no applicable rules for uisp_power
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uisp_power_no_rules_no_calls():
    """uisp_power has no engine rules → open_incident never called."""
    db, state = make_mock_db()
    device = make_device(device_type="uisp_power")
    settings = make_settings()

    with patch("app.services.alert_engine.incident_service") as mock_svc:
        mock_svc.open_incident = AsyncMock()

        await evaluate_device_metrics(db, device, {}, settings)

        mock_svc.open_incident.assert_not_called()
