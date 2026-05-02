import asyncio
import platform
import socket
import subprocess
from typing import Any

import psutil
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.services import threshold_service

router = APIRouter()


class GpuInfo(BaseModel):
    name: str
    memory_total_mb: int | None = None
    memory_used_mb: int | None = None
    temperature_c: int | None = None
    utilization_pct: int | None = None


class SystemInfo(BaseModel):
    hostname: str
    os_name: str
    cpu_count: int
    cpu_percent: float
    ram_total_gb: float
    ram_used_gb: float
    ram_percent: float
    disk_total_gb: float
    disk_used_gb: float
    disk_percent: float
    gpus: list[GpuInfo]


def _query_nvidia_smi() -> list[GpuInfo]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,temperature.gpu,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []
        gpus = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 5:
                continue
            def _int(v: str) -> int | None:
                try:
                    return int(v)
                except ValueError:
                    return None
            gpus.append(GpuInfo(
                name=parts[0],
                memory_total_mb=_int(parts[1]),
                memory_used_mb=_int(parts[2]),
                temperature_c=_int(parts[3]),
                utilization_pct=_int(parts[4]),
            ))
        return gpus
    except Exception:
        return []


@router.get("/info", response_model=SystemInfo)
async def get_system_info() -> SystemInfo:
    cpu_percent, gpus = await asyncio.gather(
        asyncio.to_thread(psutil.cpu_percent, 0.2),
        asyncio.to_thread(_query_nvidia_smi),
    )

    ram  = psutil.virtual_memory()
    try:
        disk = psutil.disk_usage("/")
    except Exception:
        disk = psutil.disk_usage("C:\\")

    return SystemInfo(
        hostname=socket.gethostname(),
        os_name=platform.system(),
        cpu_count=psutil.cpu_count(logical=True) or 1,
        cpu_percent=round(cpu_percent, 1),
        ram_total_gb=round(ram.total / 1024**3, 1),
        ram_used_gb=round(ram.used  / 1024**3, 1),
        ram_percent=round(ram.percent, 1),
        disk_total_gb=round(disk.total / 1024**3, 1),
        disk_used_gb=round(disk.used  / 1024**3, 1),
        disk_percent=round(disk.percent, 1),
        gpus=gpus,
    )


# ---------------------------------------------------------------------------
# Alert thresholds — GET / PATCH / DELETE per key
# ---------------------------------------------------------------------------

@router.get("/thresholds")
async def get_thresholds(
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return all configurable alert thresholds with their current effective values."""
    return await threshold_service.get_all_thresholds(db, get_settings())


@router.patch("/thresholds")
async def patch_thresholds(
    updates: dict[str, Any],
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Update one or more threshold values. Unknown keys are silently ignored."""
    if not updates:
        raise HTTPException(status_code=422, detail="No values provided")
    await threshold_service.set_thresholds(db, updates)
    await db.commit()
    return await threshold_service.get_all_thresholds(db, get_settings())


@router.delete("/thresholds/{key}", status_code=204)
async def reset_threshold(
    key: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove the DB override for a threshold key, reverting to the env default."""
    deleted = await threshold_service.reset_threshold(db, key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"No override found for key '{key}'")
    await db.commit()
