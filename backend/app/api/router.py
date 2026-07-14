from fastapi import APIRouter, Depends

from app.api.deps import require_fai_client, require_user_or_api_key
from app.api.endpoints import (
    access,
    auth,
    client_signal,
    clients,
    dashboard,
    devices,
    fai,
    fai_journal,
    health,
    incidents,
    lr_health,
    network_capacity,
    network_uptime,
    sites,
    system,
    traffic,
    uisp,
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
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"], dependencies=_auth)
api_router.include_router(sites.router, prefix="/sites", tags=["sites"], dependencies=_auth)
api_router.include_router(access.router, prefix="/access", tags=["access"], dependencies=_auth)
# /fai additionally accepts FAI_API_KEY — a key scoped to these routes only, held
# by the external payment system (see require_fai_client).
api_router.include_router(
    fai.router, prefix="/fai", tags=["fai"],
    dependencies=[Depends(require_fai_client)],
)
# Lecture du journal : auth NORMALE (dashboard/clé maître). La clé du système de
# paiement n'y a délibérément pas accès — elle ne sert qu'à bloquer/débloquer.
api_router.include_router(
    fai_journal.router, prefix="/fai-journal", tags=["fai"], dependencies=_auth,
)
api_router.include_router(incidents.router, prefix="/incidents", tags=["incidents"], dependencies=_auth)
api_router.include_router(lr_health.router, prefix="/lr-health", tags=["lr-health"], dependencies=_auth)
api_router.include_router(client_signal.router, prefix="/client-signal", tags=["client-signal"], dependencies=_auth)
api_router.include_router(clients.router, prefix="/clients", tags=["clients"], dependencies=_auth)
api_router.include_router(network_capacity.router, prefix="/network-capacity", tags=["network-capacity"], dependencies=_auth)
api_router.include_router(network_uptime.router, prefix="/network-uptime", tags=["network-uptime"], dependencies=_auth)
api_router.include_router(traffic.router, prefix="/traffic", tags=["traffic"], dependencies=_auth)
api_router.include_router(system.router, prefix="/system", tags=["system"], dependencies=_auth)
api_router.include_router(uisp.router, prefix="/uisp", tags=["uisp"], dependencies=_auth)
