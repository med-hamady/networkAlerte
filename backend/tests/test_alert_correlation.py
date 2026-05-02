"""
Unit tests for alert_correlation.py — pure Python, no DB required.
"""

import datetime

import pytest

from app.services.alert_correlation import determine_probable_cause


def _ts(minutes_ago: float) -> datetime.datetime:
    """Return a UTC datetime N minutes in the past."""
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)


class TestDetermineProbableCause:
    def test_all_ok_returns_none(self):
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up", "switch": "up"},
            set(),
        )
        assert result is None

    def test_switch_down_and_rocket_down(self):
        result = determine_probable_cause(
            {"rocket": "down", "lr": "up", "switch": "down"},
            {"rocket_down", "switch_down"},
        )
        assert result == "switch_down"

    def test_switch_up_eth0_down(self):
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up", "switch": "up"},
            {"eth0_down"},
        )
        assert result == "local_link_issue"

    def test_switch_unknown_eth0_down(self):
        # Switch status unknown (not in dict) — eth0_down still triggers local_link_issue
        # because switch is not explicitly "down"
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up"},
            {"eth0_down"},
        )
        assert result == "local_link_issue"

    def test_rocket_up_lr_up_radio_interface_down(self):
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up", "switch": "up"},
            {"radio_interface_down"},
        )
        assert result == "radio_link_issue"

    def test_rocket_up_lr_up_cpe_disconnected(self):
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up"},
            {"cpe_disconnected"},
        )
        assert result == "radio_link_issue"

    def test_rocket_up_lr_up_signal_low(self):
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up"},
            {"signal_low"},
        )
        assert result == "radio_quality_issue"

    def test_rocket_up_lr_up_cinr_low(self):
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up"},
            {"cinr_low"},
        )
        assert result == "radio_quality_issue"

    def test_rocket_up_lr_up_ccq_low(self):
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up"},
            {"ccq_low"},
        )
        assert result == "radio_quality_issue"

    def test_rocket_up_lr_up_radio_link_degraded(self):
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up"},
            {"radio_link_degraded"},
        )
        assert result == "radio_quality_issue"

    def test_switch_down_takes_priority_over_eth0(self):
        # Cas 1 has higher priority than Cas 2
        result = determine_probable_cause(
            {"rocket": "down", "switch": "down"},
            {"eth0_down", "rocket_down"},
        )
        assert result == "switch_down"

    def test_rocket_down_lr_up_no_switch_no_cause(self):
        # Rocket down but switch unknown and no eth0_down → no specific cause
        result = determine_probable_cause(
            {"rocket": "down", "lr": "up"},
            {"rocket_down"},
        )
        assert result is None

    def test_empty_inputs(self):
        result = determine_probable_cause({}, set())
        assert result is None


class TestTemporalCorrelation:
    """Tests for the incident_timestamps temporal-ordering refinement."""

    def test_switch_down_before_rocket_confirmed(self):
        """Switch went down 3 min before rocket → switch_down confirmed."""
        result = determine_probable_cause(
            {"rocket": "down", "switch": "down"},
            {"switch_down", "rocket_down"},
            incident_timestamps={
                "switch_down": _ts(5),
                "rocket_down": _ts(2),  # switch down first
            },
        )
        assert result == "switch_down"

    def test_switch_down_within_window_confirmed(self):
        """Switch went down 1 min after rocket but within the 5-min window → switch_down."""
        result = determine_probable_cause(
            {"rocket": "down", "switch": "down"},
            {"switch_down", "rocket_down"},
            incident_timestamps={
                "switch_down": _ts(2),
                "rocket_down": _ts(4),  # rocket 2 min before switch (within window)
            },
        )
        assert result == "switch_down"

    def test_switch_down_much_later_than_rocket_no_cause(self):
        """Switch went down 10 min after rocket → unrelated events → None."""
        result = determine_probable_cause(
            {"rocket": "down", "switch": "down"},
            {"switch_down", "rocket_down"},
            incident_timestamps={
                "switch_down": _ts(1),
                "rocket_down": _ts(11),  # rocket 10 min before switch (beyond window)
            },
        )
        assert result is None

    def test_no_timestamps_falls_back_to_heuristic(self):
        """Without timestamps, Case 1 still applies the heuristic switch_down."""
        result = determine_probable_cause(
            {"rocket": "down", "switch": "down"},
            {"switch_down", "rocket_down"},
        )
        assert result == "switch_down"

    def test_partial_timestamps_missing_switch(self):
        """Only rocket timestamp available — cannot verify order → heuristic applies."""
        result = determine_probable_cause(
            {"rocket": "down", "switch": "down"},
            {"switch_down", "rocket_down"},
            incident_timestamps={"rocket_down": _ts(5)},
        )
        assert result == "switch_down"

    def test_timestamps_irrelevant_for_radio_quality(self):
        """Timestamps have no effect on Case 4 (radio quality)."""
        result = determine_probable_cause(
            {"rocket": "up", "lr": "up"},
            {"signal_low"},
            incident_timestamps={"switch_down": _ts(1), "rocket_down": _ts(100)},
        )
        assert result == "radio_quality_issue"
