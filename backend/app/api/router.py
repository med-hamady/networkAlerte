from fastapi import APIRouter, Depends

from app.api.deps import (
    require_fai_client,
    require_uisp_assign_client,
    require_user_or_api_key,
    require_verify_client,
)
from app.api.endpoints import (
    access,
    access_diagnostics,
    auth,
    client_signal,
    clients,
    dashboard,
    device_map,
    devices,
    fai,
    fai_journal,
    fai_verify,
    health,
    incidents,
    lr_health,
    manual_alerts,
    network_capacity,
    network_topology,
    network_uptime,
    router_rules,
    sites,
    system,
    traffic,
    uisp,
    uisp_assign,
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
api_router.include_router(device_map.router, prefix="/map", tags=["map"], dependencies=_auth)
api_router.include_router(access.router, prefix="/access", tags=["access"], dependencies=_auth)
api_router.include_router(
    access_diagnostics.router, prefix="/access-diagnostics",
    tags=["access"], dependencies=_auth,
)
# /fai additionally accepts FAI_API_KEY — a key scoped to these routes only, held
# by the external payment system (see require_fai_client).
api_router.include_router(
    fai.router, prefix="/fai", tags=["fai"],
    dependencies=[Depends(require_fai_client)],
)
# GET /fai/verify — même préfixe /fai mais AUTH PROPRE : sa clé dédiée
# LR_VERIFY_API_KEY (require_verify_client), tenue par le système tiers de
# vérification. Router séparé car une dépendance de router ne se surcharge pas
# par route ; la clé de vérification n'ouvre donc que cette route.
api_router.include_router(
    fai_verify.router, prefix="/fai", tags=["fai"],
    dependencies=[Depends(require_verify_client)],
)
# Lecture du journal : auth NORMALE (dashboard/clé maître). La clé du système de
# paiement n'y a délibérément pas accès — elle ne sert qu'à bloquer/débloquer.
api_router.include_router(
    fai_journal.router, prefix="/fai-journal", tags=["fai"], dependencies=_auth,
)
# Lecture EN DIRECT des règles de coupure du routeur de cœur. Même auth que le
# journal, et pour la même raison : le système de paiement n'a pas à lire l'état
# du réseau, seulement à demander des coupures.
api_router.include_router(
    router_rules.router, prefix="/router-rules", tags=["fai"], dependencies=_auth,
)
api_router.include_router(incidents.router, prefix="/incidents", tags=["incidents"], dependencies=_auth)
# Bandeau d'anomalies à acquitter à la main — canal PARALLÈLE à /incidents, qui
# reste inchangé (ouverture et résolution automatiques).
api_router.include_router(
    manual_alerts.router, prefix="/manual-alerts", tags=["incidents"], dependencies=_auth,
)
api_router.include_router(lr_health.router, prefix="/lr-health", tags=["lr-health"], dependencies=_auth)
api_router.include_router(client_signal.router, prefix="/client-signal", tags=["client-signal"], dependencies=_auth)
api_router.include_router(clients.router, prefix="/clients", tags=["clients"], dependencies=_auth)
api_router.include_router(network_capacity.router, prefix="/network-capacity", tags=["network-capacity"], dependencies=_auth)
api_router.include_router(network_uptime.router, prefix="/network-uptime", tags=["network-uptime"], dependencies=_auth)
api_router.include_router(network_topology.router, prefix="/network-topology", tags=["network-topology"], dependencies=_auth)
api_router.include_router(traffic.router, prefix="/traffic", tags=["traffic"], dependencies=_auth)
api_router.include_router(system.router, prefix="/system", tags=["system"], dependencies=_auth)
api_router.include_router(uisp.router, prefix="/uisp", tags=["uisp"], dependencies=_auth)
# POST /uisp/assign — même préfixe /uisp mais AUTH PROPRE : sa clé dédiée
# UISP_ASSIGN_API_KEY (require_uisp_assign_client), tenue par le système de
# paiement qui adopte les équipements installés. Router séparé car une dépendance
# de router ne se surcharge pas par route : c'est ce qui garantit que cette clé
# n'ouvre PAS /uisp/sync, qui réécrit l'inventaire.
api_router.include_router(
    uisp_assign.router, prefix="/uisp", tags=["uisp"],
    dependencies=[Depends(require_uisp_assign_client)],
)
