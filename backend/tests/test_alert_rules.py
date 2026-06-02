"""
Unit tests for alert_rules.py — pure Python, no DB required.

Each test instantiates a rule and calls evaluate() with a minimal
settings object and a metrics dict.
"""

import types

import pytest

from app.services.alert_rules import (
    CapacityLowRule,
    CCQLowRule,
    CINRLowRule,
    CPEDisconnectedRule,
    Eth0DownRule,
    HighRxTxErrorsRule,
    RadioInterfaceDownRule,
    RadioLinkDegradedRule,
    SignalLowRule,
    ThroughputAnomalyRule,
    get_failure_threshold,
    get_rules_for_device,
)

# ---------------------------------------------------------------------------
# Minimal settings stub
# ---------------------------------------------------------------------------

def make_settings(**overrides):
    """Return a minimal settings-like SimpleNamespace."""
    defaults = dict(
        signal_warning_dbm=-75,
        signal_critical_dbm=-80,
        signal_tolerance_dbm=0.0,
        ccq_warning_pct=75,
        ccq_critical_pct=50,
        ccq_tolerance_pct=0.0,
        cinr_warning_db=20.0,
        cinr_critical_db=10.0,
        cinr_tolerance_db=0.0,
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
        throughput_anomaly_drop_pct=50.0,
        throughput_anomaly_min_mbps=1.0,
        throughput_anomaly_failure_threshold=3,
        # LR link substandard — per-family floors
        lr_link_potential_min_pct_ltu=50.0,
        lr_link_potential_min_pct_airmax=40.0,
        lr_total_capacity_min_mbps=60.0,
        lr_rx_rate_critical_idx_ltu=6.0,
        lr_rx_rate_warning_idx_airmax=6.0,
        lr_rx_rate_critical_idx_airmax=4.0,
        lr_link_substandard_failure_threshold=4,
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


SETTINGS = make_settings()
DEVICE_NAME = "LTU Rocket"


# ---------------------------------------------------------------------------
# Famille B — Interface et lien
# ---------------------------------------------------------------------------

class TestRadioInterfaceDownRule:
    rule = RadioInterfaceDownRule()

    def test_radio_up_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {"radio_if_up": 1.0}, SETTINGS)
        assert r.severity is None

    def test_radio_down_critical(self):
        r = self.rule.evaluate(DEVICE_NAME, {"radio_if_up": 0.0}, SETTINGS)
        assert r.severity == "critical"
        assert r.alert_type == "radio_interface_down"
        assert r.metric_value == 0.0

    def test_no_metric_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {}, SETTINGS)
        assert r.severity is None


class TestEth0DownRule:
    rule = Eth0DownRule()

    def test_eth_up_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {"eth_if_up": 1.0}, SETTINGS)
        assert r.severity is None

    def test_eth_down_critical(self):
        r = self.rule.evaluate(DEVICE_NAME, {"eth_if_up": 0.0}, SETTINGS)
        assert r.severity == "critical"
        assert r.alert_type == "eth0_down"

    def test_no_metric_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {}, SETTINGS)
        assert r.severity is None


class TestCPEDisconnectedRule:
    rule = CPEDisconnectedRule()

    def test_peer_connected_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {"peer_count": 1}, SETTINGS)
        assert r.severity is None

    def test_no_peer_critical(self):
        r = self.rule.evaluate(DEVICE_NAME, {"peer_count": 0}, SETTINGS)
        assert r.severity == "critical"
        assert r.alert_type == "cpe_disconnected"
        assert r.metric_value == 0.0

    def test_no_metric_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {}, SETTINGS)
        assert r.severity is None


# ---------------------------------------------------------------------------
# Famille C — Qualité radio
# ---------------------------------------------------------------------------

class TestSignalLowRule:
    rule = SignalLowRule()

    def test_good_signal_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {"signal_dbm": -60.0}, SETTINGS)
        assert r.severity is None

    def test_warning_signal(self):
        r = self.rule.evaluate(DEVICE_NAME, {"signal_dbm": -78.0}, SETTINGS)
        assert r.severity == "warning"
        assert r.alert_type == "signal_low"
        assert r.metric_value == pytest.approx(-78.0)
        assert r.threshold_value == -75

    def test_critical_signal(self):
        r = self.rule.evaluate(DEVICE_NAME, {"signal_dbm": -82.0}, SETTINGS)
        assert r.severity == "critical"
        assert r.threshold_value == -80

    def test_exactly_at_warning_threshold_is_ok(self):
        # At exactly -75 dBm, signal is NOT below warning → no alert
        r = self.rule.evaluate(DEVICE_NAME, {"signal_dbm": -75.0}, SETTINGS)
        assert r.severity is None

    def test_no_metric_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {}, SETTINGS)
        assert r.severity is None


class TestCINRLowRule:
    rule = CINRLowRule()

    def test_good_cinr_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {"cinr_db": 25.0}, SETTINGS)
        assert r.severity is None

    def test_warning_cinr(self):
        r = self.rule.evaluate(DEVICE_NAME, {"cinr_db": 15.0}, SETTINGS)
        assert r.severity == "warning"
        assert r.alert_type == "cinr_low"
        assert r.threshold_value == 20.0

    def test_critical_cinr(self):
        r = self.rule.evaluate(DEVICE_NAME, {"cinr_db": 8.0}, SETTINGS)
        assert r.severity == "critical"
        assert r.threshold_value == 10.0

    def test_no_metric_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {}, SETTINGS)
        assert r.severity is None


class TestCCQLowRule:
    rule = CCQLowRule()

    def test_good_ccq_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {"ccq_pct": 90.0}, SETTINGS)
        assert r.severity is None

    def test_warning_ccq(self):
        r = self.rule.evaluate(DEVICE_NAME, {"ccq_pct": 60.0}, SETTINGS)
        assert r.severity == "warning"
        assert r.alert_type == "ccq_low"
        assert r.threshold_value == 75

    def test_critical_ccq(self):
        r = self.rule.evaluate(DEVICE_NAME, {"ccq_pct": 35.0}, SETTINGS)
        assert r.severity == "critical"
        assert r.threshold_value == 50

    def test_no_metric_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {}, SETTINGS)
        assert r.severity is None


class TestRadioLinkDegradedRule:
    rule = RadioLinkDegradedRule()

    def test_all_metrics_ok_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "signal_dbm": -60.0, "cinr_db": 25.0, "ccq_pct": 90.0,
        }, SETTINGS)
        assert r.severity is None

    def test_one_bad_metric_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "signal_dbm": -78.0, "cinr_db": 25.0, "ccq_pct": 90.0,
        }, SETTINGS)
        assert r.severity is None

    def test_two_bad_metrics_warning(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "signal_dbm": -78.0, "cinr_db": 15.0, "ccq_pct": 90.0,
        }, SETTINGS)
        assert r.severity == "warning"
        assert r.alert_type == "radio_link_degraded"

    def test_two_bad_metrics_one_critical_escalates(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "signal_dbm": -85.0, "cinr_db": 15.0, "ccq_pct": 90.0,
        }, SETTINGS)
        assert r.severity == "critical"

    def test_all_three_bad_critical(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "signal_dbm": -85.0, "cinr_db": 8.0, "ccq_pct": 30.0,
        }, SETTINGS)
        assert r.severity == "critical"
        assert r.alert_type == "radio_link_degraded"


# ---------------------------------------------------------------------------
# Famille D — Performance
# ---------------------------------------------------------------------------

class TestCapacityLowRule:
    rule = CapacityLowRule()

    def test_good_capacity_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 80.0, "tx_ideal_mbps": 100.0,
        }, SETTINGS)
        assert r.severity is None

    def test_warning_capacity(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 20.0, "tx_ideal_mbps": 100.0,
        }, SETTINGS)
        assert r.severity == "warning"
        assert r.alert_type == "capacity_low"
        assert r.metric_value == pytest.approx(20.0)

    def test_critical_capacity(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 10.0, "tx_ideal_mbps": 100.0,
        }, SETTINGS)
        assert r.severity == "critical"

    def test_no_ideal_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {"tx_rate_mbps": 5.0}, SETTINGS)
        assert r.severity is None

    def test_zero_ideal_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 5.0, "tx_ideal_mbps": 0.0,
        }, SETTINGS)
        assert r.severity is None


class TestHighRxTxErrorsRule:
    rule = HighRxTxErrorsRule()

    def test_no_prev_data_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "radio_in_errors": 100.0,
            "radio_out_errors": 50.0,
            "radio_rx_bytes": 1_000_000.0,
            "radio_tx_bytes": 500_000.0,
        }, SETTINGS)
        assert r.severity is None

    def test_low_error_rate_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "radio_in_errors": 105.0,
            "radio_out_errors": 53.0,
            "radio_rx_bytes": 1_100_000.0,
            "radio_tx_bytes": 600_000.0,
            "prev_in_errors": 100.0,
            "prev_out_errors": 50.0,
            "prev_rx_bytes": 1_000_000.0,
            "prev_tx_bytes": 500_000.0,
        }, SETTINGS)
        # delta_errors=8, delta_bytes=200_000 → rate=0.004% — below 1%
        assert r.severity is None

    def test_warning_error_rate(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "radio_in_errors": 3000.0,
            "radio_out_errors": 2000.0,
            "radio_rx_bytes": 1_100_000.0,
            "radio_tx_bytes": 600_000.0,
            "prev_in_errors": 100.0,
            "prev_out_errors": 50.0,
            "prev_rx_bytes": 1_000_000.0,
            "prev_tx_bytes": 500_000.0,
        }, SETTINGS)
        # delta_errors=4850, delta_bytes=200_000 → rate≈2.4% — above 1%, below 5%
        assert r.severity == "warning"
        assert r.alert_type == "high_rx_tx_errors"

    def test_critical_error_rate(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "radio_in_errors": 10_100.0,
            "radio_out_errors": 50.0,
            "radio_rx_bytes": 1_100_000.0,
            "radio_tx_bytes": 600_000.0,
            "prev_in_errors": 100.0,
            "prev_out_errors": 50.0,
            "prev_rx_bytes": 1_000_000.0,
            "prev_tx_bytes": 500_000.0,
        }, SETTINGS)
        # delta_errors=10_000, delta_bytes=200_000 → rate=5% — exactly at critical
        assert r.severity == "critical"

    def test_no_traffic_no_alert(self):
        r = self.rule.evaluate(DEVICE_NAME, {
            "radio_in_errors": 200.0,
            "radio_out_errors": 100.0,
            "radio_rx_bytes": 100.0,
            "radio_tx_bytes": 100.0,
            "prev_in_errors": 100.0,
            "prev_out_errors": 50.0,
            "prev_rx_bytes": 0.0,
            "prev_tx_bytes": 0.0,
        }, SETTINGS)
        # delta_bytes = 200 < 1000 → no alert
        assert r.severity is None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRuleRegistry:
    def test_rocket_has_all_rules(self):
        rules = get_rules_for_device("ltu_rocket")
        rule_types = {type(r).__name__ for r in rules}
        assert "RadioInterfaceDownRule" in rule_types
        assert "Eth0DownRule" in rule_types
        assert "CPEDisconnectedRule" in rule_types
        assert "SignalLowRule" in rule_types
        assert "CINRLowRule" in rule_types
        assert "CCQLowRule" in rule_types
        assert "RadioLinkDegradedRule" in rule_types
        assert "CapacityLowRule" in rule_types
        assert "HighRxTxErrorsRule" in rule_types

    def test_lr_has_subset(self):
        rules = get_rules_for_device("lr")
        rule_types = {type(r).__name__ for r in rules}
        assert "RadioInterfaceDownRule" in rule_types
        assert "SignalLowRule" in rule_types
        assert "CPEDisconnectedRule" not in rule_types

    def test_power_no_rules(self):
        rules = get_rules_for_device("uisp_power")
        assert rules == []

    def test_unknown_type_no_rules(self):
        rules = get_rules_for_device("unknown_device")
        assert rules == []

    def test_rocket_has_throughput_anomaly(self):
        rules = get_rules_for_device("ltu_rocket")
        rule_types = {type(r).__name__ for r in rules}
        assert "ThroughputAnomalyRule" in rule_types

    def test_lr_has_throughput_anomaly(self):
        rules = get_rules_for_device("lr")
        rule_types = {type(r).__name__ for r in rules}
        assert "ThroughputAnomalyRule" in rule_types

    def test_switch_no_throughput_anomaly(self):
        rules = get_rules_for_device("uisp_switch")
        rule_types = {type(r).__name__ for r in rules}
        assert "ThroughputAnomalyRule" not in rule_types

    def test_failure_thresholds(self):
        assert get_failure_threshold("signal_low", SETTINGS) == 2
        assert get_failure_threshold("cinr_low", SETTINGS) == 2
        assert get_failure_threshold("ccq_low", SETTINGS) == 2
        assert get_failure_threshold("capacity_low", SETTINGS) == 3
        assert get_failure_threshold("radio_interface_down", SETTINGS) == 0
        assert get_failure_threshold("eth0_down", SETTINGS) == 0
        assert get_failure_threshold("throughput_anomaly", SETTINGS) == 3


# ---------------------------------------------------------------------------
# Famille D — Anomalie de débit
# ---------------------------------------------------------------------------

class TestThroughputAnomalyRule:
    rule = ThroughputAnomalyRule()

    def test_no_rate_no_alert(self):
        """tx_rate_mbps absent → no alert (data not available yet)."""
        r = self.rule.evaluate(DEVICE_NAME, {}, SETTINGS)
        assert r.severity is None

    def test_no_ema_no_alert(self):
        """tx_rate_mbps present but no EMA baseline yet → no alert."""
        r = self.rule.evaluate(DEVICE_NAME, {"tx_rate_mbps": 5.0}, SETTINGS)
        assert r.severity is None

    def test_ema_below_min_no_alert(self):
        """EMA below throughput_anomaly_min_mbps → ignore (idle link)."""
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 0.1,
            "tx_rate_ema_mbps": 0.5,  # below min of 1.0 Mbps
        }, SETTINGS)
        assert r.severity is None

    def test_no_significant_drop_no_alert(self):
        """Current rate close to EMA (20% drop, threshold is 50%) → no alert."""
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 8.0,
            "tx_rate_ema_mbps": 10.0,  # 20% drop → below 50% threshold
        }, SETTINGS)
        assert r.severity is None

    def test_exactly_at_threshold_no_alert(self):
        """Exactly 50% drop → alert triggered (boundary condition)."""
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 5.0,
            "tx_rate_ema_mbps": 10.0,  # exactly 50% drop
        }, SETTINGS)
        assert r.severity == "warning"

    def test_drop_exceeds_threshold_warning(self):
        """Current rate 70% below EMA with sufficient baseline → warning."""
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 3.0,
            "tx_rate_ema_mbps": 10.0,  # 70% drop
        }, SETTINGS)
        assert r.severity == "warning"
        assert r.alert_type == "throughput_anomaly"
        assert r.metric_name == "tx_drop_pct"
        assert r.metric_value == pytest.approx(70.0, abs=0.1)
        assert r.threshold_value == 50.0

    def test_recovery_when_rate_returns(self):
        """Rate recovers close to EMA → no alert (resolved by engine)."""
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 9.5,
            "tx_rate_ema_mbps": 10.0,  # 5% drop — well below 50%
        }, SETTINGS)
        assert r.severity is None

    def test_custom_drop_threshold(self):
        """Custom 30% drop threshold from settings override."""
        settings = make_settings(throughput_anomaly_drop_pct=30.0)
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 6.0,
            "tx_rate_ema_mbps": 10.0,  # 40% drop → above custom 30% threshold
        }, settings)
        assert r.severity == "warning"

    def test_zero_rate_is_full_drop(self):
        """Zero throughput with active baseline → 100% drop → warning."""
        r = self.rule.evaluate(DEVICE_NAME, {
            "tx_rate_mbps": 0.0,
            "tx_rate_ema_mbps": 10.0,
        }, SETTINGS)
        assert r.severity == "warning"
        assert r.metric_value == 100.0
