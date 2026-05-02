import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DeviceNotFoundError
from app.models.device import Device
from app.schemas.device import DeviceCreate, DeviceUpdate

logger = logging.getLogger(__name__)


async def get_devices(db: AsyncSession, skip: int = 0, limit: int = 100) -> list[Device]:
    """List all devices with pagination."""
    result = await db.execute(select(Device).offset(skip).limit(limit))
    return list(result.scalars().all())


async def get_device(db: AsyncSession, device_id: int) -> Device:
    """Get a single device by ID."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if device is None:
        raise DeviceNotFoundError(device_id)
    return device


async def create_device(db: AsyncSession, data: DeviceCreate) -> Device:
    """Create a new device."""
    device = Device(**data.model_dump())
    db.add(device)
    await db.flush()
    await db.refresh(device)
    logger.info("Created device: %s (%s)", device.name, device.ip_address)
    return device


async def update_device(db: AsyncSession, device_id: int, data: DeviceUpdate) -> Device:
    """Update an existing device."""
    device = await get_device(db, device_id)
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(device, field, value)
    await db.flush()
    await db.refresh(device)
    logger.info("Updated device: %s", device.name)
    return device


async def delete_device(db: AsyncSession, device_id: int) -> None:
    """Delete a device."""
    device = await get_device(db, device_id)
    await db.delete(device)
    await db.flush()
    logger.info("Deleted device: %s", device.name)
