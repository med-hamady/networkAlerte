"""
Read-only access to the alert_policy registry.

GET /api/v1/alert-policies              — list all policies
GET /api/v1/alert-policies/{alert_type} — single policy by alert_type
"""

from fastapi import APIRouter, HTTPException

from app.schemas.alert_policy import AlertPolicyRead
from app.services.alert_policy import ALERT_POLICIES, get_policy

router = APIRouter()


def _to_schema(policy) -> AlertPolicyRead:
    return AlertPolicyRead(
        alert_type=policy.alert_type,
        severity=policy.severity,
        notify_immediately=policy.notify_immediately,
        channels=list(policy.channels),
        groupable=policy.groupable,
        recovery_notification=policy.recovery_notification,
    )


@router.get("", response_model=list[AlertPolicyRead])
async def list_alert_policies() -> list[AlertPolicyRead]:
    """List every alert_type known to the system with its operational policy."""
    return [
        _to_schema(p) for p in sorted(ALERT_POLICIES.values(), key=lambda x: x.alert_type)
    ]


@router.get("/{alert_type}", response_model=AlertPolicyRead)
async def get_alert_policy(alert_type: str) -> AlertPolicyRead:
    """Return the policy for a single alert_type."""
    if alert_type not in ALERT_POLICIES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown alert_type '{alert_type}'",
        )
    return _to_schema(get_policy(alert_type))
