from fastapi import APIRouter, Depends

from app.api.deps import require_user_or_api_key
from app.api.endpoints import (
    alert_policies,
    auth,
    device_shell,
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

# /auth/login is public; /auth/me, /logout, /change-password gate themselves
# per-route via `require_user`. Mounting without router-level auth.
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# Every other router accepts EITHER a session cookie (dashboard via the
# Next.js proxy) OR a valid X-API-Key header (direct admin / integration use).
_auth = [Depends(require_user_or_api_key)]
api_router.include_router(devices.router, prefix="/devices", tags=["devices"], dependencies=_auth)
# device_shell mounts the WebSocket on /devices/{id}/shell — no router-level auth
# because browsers cannot attach custom headers to a WebSocket. The ticket
# endpoint inside this router applies require_user_or_api_key per-route instead.
api_router.include_router(device_shell.router, prefix="/devices", tags=["devices"])
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
