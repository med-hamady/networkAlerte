"""
Unit tests for alert_policy.py — pure Python, no DB required.

Validates:
  - every known alert_type is covered by a policy
  - severity values are within the allowed enum
  - critical incidents always notify immediately (after the dynamic resolver)
  - all channels declared by a policy are valid
  - dynamic-severity policies inherit immediate-flag from the actual severity
  - the should_notify gate respects channels, recovery flag and info-severity rule
"""

from __future__ import annotations

from app.core.alert_constants import (
    AT_BATTERY_LOW_CRIT,
    AT_RADIO_LINK_DEGRADED,
    AT_ROCKET_DOWN,
    AT_SIGNAL_LOW,
    CHANNEL_VALUES,
    KNOWN_ALERT_TYPES,
    SEVERITY_VALUES,
    NotificationEvent,
    Severity,
)
from app.services.alert_policy import (
    ALERT_POLICIES,
    effective_notify_immediately,
    get_policy,
    should_notify,
)

# ---------------------------------------------------------------------------
# Coverage and consistency
# ---------------------------------------------------------------------------

def test_every_known_alert_type_has_a_policy():
    missing = KNOWN_ALERT_TYPES - ALERT_POLICIES.keys()
    assert not missing, f"alert_types without a policy: {missing}"


def test_every_policy_targets_a_known_alert_type():
    extra = ALERT_POLICIES.keys() - KNOWN_ALERT_TYPES
    assert not extra, f"alert_policy declares unknown alert_types: {extra}"


def test_every_policy_severity_is_valid():
    allowed = SEVERITY_VALUES | {Severity.DYNAMIC}
    for at, policy in ALERT_POLICIES.items():
        assert policy.severity in allowed, \
            f"{at} has unknown severity '{policy.severity}'"


def test_every_policy_channel_is_valid():
    for at, policy in ALERT_POLICIES.items():
        for channel in policy.channels:
            assert channel in CHANNEL_VALUES, \
                f"{at} declares unknown channel '{channel}'"


def test_critical_severity_implies_notify_immediately():
    for at, policy in ALERT_POLICIES.items():
        if policy.severity == Severity.CRITICAL:
            assert policy.notify_immediately is True, \
                f"{at} is critical but does not notify immediately"


def test_warning_default_is_not_immediate():
    """Warnings should not page on their own — they ride deferred channels."""
    for at, policy in ALERT_POLICIES.items():
        if policy.severity == Severity.WARNING:
            assert policy.notify_immediately is False, \
                f"{at} is warning but flagged immediate (review intent)"


def test_known_alert_types_are_alphabetically_unique():
    """No duplicate alert_type keys."""
    assert len(KNOWN_ALERT_TYPES) == len({k for k in KNOWN_ALERT_TYPES})


# ---------------------------------------------------------------------------
# get_policy fallback
# ---------------------------------------------------------------------------

def test_get_policy_known():
    p = get_policy(AT_ROCKET_DOWN)
    assert p.alert_type == AT_ROCKET_DOWN
    assert p.severity == Severity.CRITICAL


def test_get_policy_unknown_returns_fallback():
    p = get_policy("nonexistent_alert_xyz")
    assert p.alert_type == "_unknown"
    assert p.notify_immediately is False


def test_get_policy_none_returns_fallback():
    p = get_policy(None)
    assert p.alert_type == "_unknown"


# ---------------------------------------------------------------------------
# effective_notify_immediately — dynamic resolver
# ---------------------------------------------------------------------------

def test_effective_notify_immediately_critical_always_immediate():
    p = get_policy(AT_SIGNAL_LOW)              # default warning, not immediate
    assert effective_notify_immediately(p, Severity.CRITICAL) is True


def test_effective_notify_immediately_dynamic_inherits_from_incident():
    p = get_policy(AT_RADIO_LINK_DEGRADED)
    assert p.severity == Severity.DYNAMIC
    # When the rule reports critical, we must page
    assert effective_notify_immediately(p, Severity.CRITICAL) is True
    # When the rule reports warning, deferred is fine
    assert effective_notify_immediately(p, Severity.WARNING) is False


def test_effective_notify_immediately_warning_default_deferred():
    p = get_policy(AT_SIGNAL_LOW)
    assert effective_notify_immediately(p, Severity.WARNING) is False


# ---------------------------------------------------------------------------
# should_notify — channel gating
# ---------------------------------------------------------------------------

def test_should_notify_critical_uses_all_channels():
    p = get_policy(AT_BATTERY_LOW_CRIT)
    for channel in p.channels:
        assert should_notify(
            channel, p, Severity.CRITICAL, NotificationEvent.OPENED,
        ) is True


def test_should_notify_skips_channel_outside_policy():
    # The fallback policy only includes email — slack is absent.
    p = get_policy("unknown_alert_type_xyz")
    assert "slack" not in p.channels
    assert should_notify(
        "slack", p, Severity.WARNING, NotificationEvent.OPENED,
    ) is False


def test_should_notify_resolved_blocked_when_recovery_disabled():
    p = get_policy(AT_ROCKET_DOWN)
    # Build a clone with recovery disabled — exercise the gate
    from dataclasses import replace
    p_no_recovery = replace(p, recovery_notification=False)
    assert should_notify(
        "slack", p_no_recovery, Severity.CRITICAL, NotificationEvent.RESOLVED,
    ) is False


def test_should_notify_info_only_fires_if_immediate():
    p = get_policy(AT_SIGNAL_LOW)
    assert should_notify(
        "slack", p, Severity.INFO, NotificationEvent.OPENED,
    ) is False
