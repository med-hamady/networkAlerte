"""
CRUD service for the notification_channels table.

Channels stored here override the env-based defaults: as soon as at least
one enabled channel exists in DB, notification_service uses those instead
of SLACK_WEBHOOK_URL / WEBHOOK_URL / SMTP_*.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification_channel import NotificationChannel
from app.schemas.notification_channel import (
    NotificationChannelCreate,
    NotificationChannelUpdate,
)

logger = logging.getLogger(__name__)


async def list_channels(
    db: AsyncSession,
    enabled_only: bool = False,
) -> list[NotificationChannel]:
    """List notification channels, optionally filtered to enabled ones."""
    query = select(NotificationChannel).order_by(NotificationChannel.id.asc())
    if enabled_only:
        query = query.where(NotificationChannel.enabled.is_(True))
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_channel(
    db: AsyncSession,
    channel_id: int,
) -> NotificationChannel | None:
    """Get a single channel by ID."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.id == channel_id)
    )
    return result.scalar_one_or_none()


async def create_channel(
    db: AsyncSession,
    data: NotificationChannelCreate,
) -> NotificationChannel:
    """Create a new notification channel."""
    channel = NotificationChannel(**data.model_dump())
    db.add(channel)
    await db.flush()
    await db.refresh(channel)
    logger.info("Created notification channel: %s (%s)", channel.name, channel.channel_type)
    return channel


async def update_channel(
    db: AsyncSession,
    channel_id: int,
    data: NotificationChannelUpdate,
) -> NotificationChannel | None:
    """Update an existing channel. Returns None if not found."""
    channel = await get_channel(db, channel_id)
    if channel is None:
        return None
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(channel, field, value)
    await db.flush()
    await db.refresh(channel)
    logger.info("Updated notification channel: %s", channel.name)
    return channel


async def delete_channel(db: AsyncSession, channel_id: int) -> bool:
    """Delete a channel. Returns True if deleted, False if not found."""
    channel = await get_channel(db, channel_id)
    if channel is None:
        return False
    await db.delete(channel)
    await db.flush()
    logger.info("Deleted notification channel: id=%d", channel_id)
    return True
