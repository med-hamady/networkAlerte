"""
Tests for per-device policy overrides.

Validates:
  - merge_overrides applies whitelisted fields and ignores the rest
  - severity / alert_type / recommended_action cannot be overridden
  - invalid channels are filtered, base policy is preserved when patch is empty
  - get_policy_for_device returns a fresh policy reflecting the override
  - missing or malformed override input is handled gracefully
"""

from __future__ import annotations

from app.core.alert_constants import (
    AT_CCQ_LOW,
    AT_ROCKET_DOWN,
    AT_SIGNAL_LOW,
    AlertChannel,
)
from app.services.alert_policy import (
    get_policy,
    get_policy_for_device,
    merge_overrides,
)


# ---------------------------------------------------------------------------
# merge_overrides
# ---------------------------------------------------------------------------

def test_merge_no_override_returns_base():
    base = get_policy(AT_SIGNAL_LOW)
    assert merge_overrides(base, None) is base
    assert merge_overrides(base, {}) is base


def test_merge_overrides_notify_immediately():
    base = get_policy(AT_SIGNAL_LOW)
    assert base.notify_immediately is False
    merged = merge_overrides(base, {"notify_immediately": True})
    assert merged.notify_immediately is True
    # Other fields preserved
    assert merged.alert_type == base.alert_type
    assert merged.severity == base.severity


def test_merge_overrides_channels_filters_unknown():
    base = get_policy(AT_SIGNAL_LOW)
    merged = merge_overrides(base, {"channels": ["webhook", "carrier-pigeon"]})
    assert AlertChannel.WEBHOOK in merged.channels
    assert "carrier-pigeon" not in merged.channels


def test_merge_overrides_empty_channel_list_keeps_base():
    base = get_policy(AT_SIGNAL_LOW)
    merged = merge_overrides(base, {"channels": ["nonsense"]})
    # All values filtered → fallback to base.channels (never empty)
    assert merged.channels == base.channels


def test_merge_overrides_disable_recovery():
    base = get_policy(AT_ROCKET_DOWN)
    assert base.recovery_notification is True
    merged = merge_overrides(base, {"recovery_notification": False})
    assert merged.recovery_notification is False


def test_merge_overrides_ignores_unknown_keys():
    base = get_policy(AT_SIGNAL_LOW)
    merged = merge_overrides(base, {"bogus_field": "x"})
    # No fields on the whitelist → returns base unchanged
    assert merged is base


def test_merge_overrides_does_not_change_severity():
    base = get_policy(AT_SIGNAL_LOW)
    merged = merge_overrides(base, {"severity": "critical"})
    assert merged.severity == base.severity


def test_merge_overrides_does_not_change_recommended_action():
    base = get_policy(AT_SIGNAL_LOW)
    merged = merge_overrides(base, {"recommended_action": "ignore"})
    assert merged.recommended_action == base.recommended_action


def test_merge_overrides_channels_must_be_list_or_tuple():
    base = get_policy(AT_SIGNAL_LOW)
    merged = merge_overrides(base, {"channels": "webhook"})
    # String coerced as channels argument is wrong → ignored, base channels kept
    assert merged.channels == base.channels


# ---------------------------------------------------------------------------
# get_policy_for_device
# ---------------------------------------------------------------------------

def test_get_policy_for_device_no_overrides():
    p = get_policy_for_device(AT_CCQ_LOW, None)
    assert p is get_policy(AT_CCQ_LOW)


def test_get_policy_for_device_unrelated_alert_type():
    overrides = {AT_SIGNAL_LOW: {"notify_immediately": True}}
    p = get_policy_for_device(AT_CCQ_LOW, overrides)
    # Override targets signal_low, not ccq_low → base ccq_low policy
    assert p is get_policy(AT_CCQ_LOW)


def test_get_policy_for_device_applies_match():
    overrides = {AT_SIGNAL_LOW: {"notify_immediately": True}}
    p = get_policy_for_device(AT_SIGNAL_LOW, overrides)
    assert p.notify_immediately is True


def test_get_policy_for_device_with_malformed_override_ignored():
    overrides = {AT_SIGNAL_LOW: "not a dict"}
    p = get_policy_for_device(AT_SIGNAL_LOW, overrides)
    assert p is get_policy(AT_SIGNAL_LOW)


def test_get_policy_for_device_with_none_alert_type():
    p = get_policy_for_device(None, {AT_SIGNAL_LOW: {}})
    # Falls back to fallback policy (no alert_type to look up)
    assert p.alert_type == "_unknown"
