"""
Pydantic schema for the AlertPolicy registry.

Read-only — exposed by /api/v1/alert-policies for dashboards, runbooks
and integrations that need to know how each alert_type behaves.
"""

from pydantic import BaseModel


class AlertPolicyRead(BaseModel):
    """Operational policy for a single alert_type."""

    alert_type: str
    severity: str                          # info | warning | critical | dynamic
    notify_immediately: bool
    channels: list[str]
    groupable: bool
    recovery_notification: bool
