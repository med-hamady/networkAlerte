"""Device CRUD with polymorphic dispatch.

The Pydantic schemas (RocketCreate / LrCreate / UispPowerCreate / UispSwitchCreate)
each carry their own `device_type` Literal. `create_device` reads that field to
pick the right SQLAlchemy subclass (Rocket / Lr / UispPower / UispSwitch); the
SQLAlchemy joined-inheritance mapper then inserts both the `devices` row and the
type-specific row in one flush.
"""

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import DeviceNotFoundError
from app.models.device import (
    AirFiber,
    ClientModem,
    Device,
    Lr,
    PtpLiteBeam,
    Rocket,
    UispPower,
    UispSwitch,
)
from app.schemas.device import (
    AirFiberCreate,
    ClientModemCreate,
    DeviceCreate,
    DeviceUpdate,
    RocketCreate,
    UispPowerCreate,
    UispSwitchCreate,
)

logger = logging.getLogger(__name__)


# Map discriminator → ORM class. LR is intentionally NOT here: those rows are
# created exclusively by services/discovery_service.reconcile_peers when the
# parent Rocket reports a new peer. New manually-creatable types only need an
# entry here and a matching *Create schema in the DeviceCreate union.
_TYPE_TO_MODEL: dict[str, type[Device]] = {
    "rocket": Rocket,
    "uisp_power": UispPower,
    "uisp_switch": UispSwitch,
    "client_modem": ClientModem,
    "airfiber": AirFiber,
    "ptp_litebeam": PtpLiteBeam,
}


async def get_devices(
    db: AsyncSession, skip: int = 0, limit: int = 100, site: str | None = None,
) -> list[Device]:
    """List devices with pagination — polymorphic load returns subclass instances.

    Optional `site` filter (matches the trigger-maintained, indexed
    `devices.site` column) lets the /sites drill-down load only one site's
    equipment instead of the whole fleet — a small, fast query.
    """
    stmt = select(Device)
    if site is not None:
        stmt = stmt.where(Device.site == site)
    result = await db.execute(stmt.offset(skip).limit(limit))
    return list(result.scalars().all())


async def search_devices(db: AsyncSession, query: str, limit: int = 20):
    """Quick lookup for the /sites search bar.

    Any device matches on `name` or on `ip_address`. For an LR the name also
    carries the client name AND their phone number, so searching a phone number
    searches the name.

    Returns lightweight rows (no polymorphic load) — just enough to render a
    result and deep-link into the device's site. Sorted infra-first then by name.
    """
    pattern = f"%{query}%"
    stmt = (
        select(
            Device.id,
            Device.name,
            Device.ip_address,
            Device.device_type,
            Device.site,
            Device.status,
        )
        .where(
            or_(
                Device.ip_address.ilike(pattern),
                Device.name.ilike(pattern),
            )
        )
        .order_by(Device.device_type, Device.name)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return result.all()


async def get_device(db: AsyncSession, device_id: int) -> Device:
    """Get a single device by ID. Polymorphic — returns the subclass instance."""
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if device is None:
        raise DeviceNotFoundError(device_id)
    return device


async def create_device(db: AsyncSession, data: DeviceCreate) -> Device:
    """Create a new device of the right concrete type.

    The schema's `device_type` Literal drives subclass selection. `snmp_community`
    defaults to the platform-wide value from .env when not provided — without
    this, the SNMP poll job (which filters NOT NULL) would silently skip the
    device.
    """
    payload = data.model_dump()
    device_type = payload.pop("device_type")
    model_cls = _TYPE_TO_MODEL[device_type]

    # Auto-fill snmp_community only for types that are actually SNMP-polled
    # (rockets + switches). UISP Power talks REST API, LRs report via their
    # parent Rocket — neither benefits from a community.
    if payload.get("snmp_community") is None and device_type in ("rocket", "uisp_switch"):
        payload["snmp_community"] = get_settings().snmp_default_community

    device = model_cls(**payload)
    db.add(device)
    await db.flush()
    await db.refresh(device)
    logger.info("Created %s: %s (%s)", device_type, device.name, device.ip_address)
    return device


async def update_device(db: AsyncSession, device_id: int, data: DeviceUpdate) -> Device:
    """Update an existing device — only the fields actually sent are applied.

    The DeviceUpdate union is type-tagged but does not enforce that the payload
    matches the device's current type. Callers must pass the right schema; we
    silently ignore unknown fields on the wrong subclass via getattr/setattr.
    """
    device = await get_device(db, device_id)
    update_data = data.model_dump(exclude_unset=True)
    update_data.pop("device_type", None)  # type is immutable

    # Capture la localisation avant modification — sert à propager le
    # changement aux LR auto-découvertes rattachées si c'est un Rocket.
    old_location = device.location

    for field, value in update_data.items():
        if hasattr(device, field):
            setattr(device, field, value)
    await db.flush()
    await db.refresh(device)

    # Propagation de la localisation : quand un Rocket change de site, on
    # aligne automatiquement toutes ses LR auto-découvertes (les LR sont des
    # CPE physiquement sur le même site que leur Rocket parent). Les LR
    # créées manuellement (auto_discovered=False) ne sont pas touchées : si
    # un opérateur les a saisies à la main, on respecte sa valeur.
    if isinstance(device, Rocket) and "location" in update_data and device.location != old_location:
        # On charge les LR et on assigne par instance ORM. Un
        # update(Lr).values(location=...) en masse échoue : `location` est
        # porté par la table parente `devices` (joined-table inheritance),
        # pas par `lrs` — SQLAlchemy ne sait pas l'écrire via la sous-classe.
        lrs = (
            await db.execute(
                select(Lr).where(Lr.rocket_id == device.id, Lr.auto_discovered.is_(True))
            )
        ).scalars().all()
        for lr in lrs:
            lr.location = device.location
        if lrs:
            await db.flush()
            logger.info(
                "Propagé la nouvelle location %r du Rocket '%s' à %d LR auto-découverte(s)",
                device.location, device.name, len(lrs),
            )

    logger.info("Updated device: %s", device.name)
    return device


async def delete_device(db: AsyncSession, device_id: int) -> None:
    """Delete a device (cascades to the type-specific row and all dependents)."""
    device = await get_device(db, device_id)
    await db.delete(device)
    await db.flush()
    logger.info("Deleted device: %s", device.name)


# Re-exports so callers don't need to know the type module
__all__ = [
    "AirFiberCreate",
    "ClientModemCreate",
    "RocketCreate",
    "UispPowerCreate",
    "UispSwitchCreate",
    "create_device",
    "delete_device",
    "get_device",
    "get_devices",
    "update_device",
]
