"""
Pydantic schemas for the NotificationChannel resource.

Each channel has a `channel_type` ("email") that determines the expected
shape of `config`:

  email : {"recipients": ["a@b.com", "c@d.com"]}

The schemas validate the channel_type against the registry of supported
types so unknown values are rejected at the API boundary.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.alert_constants import CHANNEL_VALUES


class NotificationChannelCreate(BaseModel):
    """Schema for creating a notification channel."""

    name: str
    channel_type: str
    config: dict[str, Any] = {}
    enabled: bool = True

    @field_validator("channel_type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if v not in CHANNEL_VALUES:
            raise ValueError(
                f"Unknown channel_type '{v}'. Must be one of: {sorted(CHANNEL_VALUES)}"
            )
        return v


class NotificationChannelUpdate(BaseModel):
    """Schema for updating a notification channel (all fields optional)."""

    name: str | None = None
    channel_type: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None

    @field_validator("channel_type")
    @classmethod
    def _validate_type(cls, v: str | None) -> str | None:
        if v is not None and v not in CHANNEL_VALUES:
            raise ValueError(
                f"Unknown channel_type '{v}'. Must be one of: {sorted(CHANNEL_VALUES)}"
            )
        return v


class NotificationChannelRead(BaseModel):
    """Schema for reading a notification channel."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    channel_type: str
    config: dict[str, Any]
    enabled: bool
