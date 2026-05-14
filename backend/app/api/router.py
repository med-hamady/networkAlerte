from fastapi import APIRouter, Depends

from app.api.deps import verify_api_key
from app.api.endpoints import (
    alert_policies,
    devices,
    health,
    incidents,
    lr_health,
    network_uptime,
    notification_channels,
    notifications,
    reports,
    system,
)

api_router = APIRouter(prefix="/api/v1")

# /health is always public — used by Docker health-checks and monitoring agents
api_router.include_router(health.router, tags=["health"])

_auth = [Depends(verify_api_key)]
api_router.include_router(devices.router, prefix="/devices", tags=["devices"], dependencies=_auth)
api_router.include_router(incidents.router, prefix="/incidents", tags=["incidents"], dependencies=_auth)
api_router.include_router(lr_health.router, prefix="/lr-health", tags=["lr-health"], dependencies=_auth)
api_router.include_router(network_uptime.router, prefix="/network-uptime", tags=["network-uptime"], dependencies=_auth)
api_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"], dependencies=_auth)
api_router.include_router(
    notification_channels.router,
    prefix="/notification-channels",
    tags=["notification-channels"],
    dependencies=_auth,
)
api_router.include_router(
    alert_policies.router,
    prefix="/alert-policies",
    tags=["alert-policies"],
    dependencies=_auth,
)
api_router.include_router(system.router, prefix="/system", tags=["system"], dependencies=_auth)
api_router.include_router(reports.router, prefix="/reports", tags=["reports"], dependencies=_auth)
