"""
Notification channels CRUD — manages the rows in the notification_channels table.

Channels stored here are picked up automatically by notification_service:
as soon as at least one enabled channel exists in DB, the DB rows take
precedence over the env-based fallback (SMTP_* env vars).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.notification_channel import (
    NotificationChannelCreate,
    NotificationChannelRead,
    NotificationChannelUpdate,
)
from app.services import notification_channel_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=list[NotificationChannelRead])
async def list_channels(
    enabled_only: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[NotificationChannelRead]:
    """List notification channels stored in DB."""
    channels = await notification_channel_service.list_channels(db, enabled_only=enabled_only)
    return [NotificationChannelRead.model_validate(c) for c in channels]


@router.get("/{channel_id}", response_model=NotificationChannelRead)
async def get_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
) -> NotificationChannelRead:
    channel = await notification_channel_service.get_channel(db, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Notification channel {channel_id} not found")
    return NotificationChannelRead.model_validate(channel)


@router.post("/", response_model=NotificationChannelRead, status_code=201)
async def create_channel(
    data: NotificationChannelCreate,
    db: AsyncSession = Depends(get_db),
) -> NotificationChannelRead:
    channel = await notification_channel_service.create_channel(db, data)
    await db.commit()
    return NotificationChannelRead.model_validate(channel)


@router.patch("/{channel_id}", response_model=NotificationChannelRead)
async def update_channel(
    channel_id: int,
    data: NotificationChannelUpdate,
    db: AsyncSession = Depends(get_db),
) -> NotificationChannelRead:
    channel = await notification_channel_service.update_channel(db, channel_id, data)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"Notification channel {channel_id} not found")
    await db.commit()
    return NotificationChannelRead.model_validate(channel)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    deleted = await notification_channel_service.delete_channel(db, channel_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Notification channel {channel_id} not found")
    await db.commit()
