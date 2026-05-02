"""
Script de seed — enregistre le LTU Rocket et le LTU LR en base de données.

Usage :
    docker compose exec backend python scripts/seed_devices.py <ip_rocket> <ip_lr>

Exemple :
    docker compose exec backend python scripts/seed_devices.py 192.168.1.20 192.168.1.21

Les devices peuvent aussi être ajoutés via l'API :
    POST /api/v1/devices  {"name": "LTU Rocket", "ip_address": "...", "device_type": "ltu_rocket"}
"""

import asyncio
import os
import sys

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import async_session_factory
from app.models.device import Device


async def seed(rocket_ip: str, lr_ip: str) -> None:
    devices = [
        {"name": "LTU Rocket", "ip_address": rocket_ip, "device_type": "ltu_rocket", "snmp_community": "public"},
        {"name": "LTU LR",     "ip_address": lr_ip,     "device_type": "ltu_lr",     "snmp_community": "public"},
    ]

    async with async_session_factory() as session:
        for data in devices:
            result = await session.execute(
                select(Device).where(Device.ip_address == data["ip_address"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                print(f"[SKIP] {data['name']} ({data['ip_address']}) — déjà en base (id={existing.id})")
            else:
                device = Device(**data)
                session.add(device)
                await session.flush()
                print(f"[OK]   {data['name']} ({data['ip_address']}) — créé (id={device.id})")

        await session.commit()

    print("\nSeed terminé.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage : python scripts/seed_devices.py <ip_rocket> <ip_lr>")
        sys.exit(1)

    asyncio.run(seed(sys.argv[1], sys.argv[2]))
