from fastapi import APIRouter, Depends

from app.api.deps import (
    require_client_signal_client,
    require_content_block_client,
    require_fai_client,
    require_permission,
    require_uisp_assign_client,
    require_user_or_api_key,
    require_verify_client,
)
from app.api.endpoints import (
    access,
    access_control,
    access_diagnostics,
    auth,
    client_signal,
    clients,
    content_filter,
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


def _perm(*keys: str) -> list:
    """Raccourci : l'authentification NORMALE + au moins un des droits donnés.

    ⚠️ Le `_auth` est conservé en plus du contrôle de droits, pas remplacé : il
    répond 401 (« connecte-toi ») là où la permission répond 403 (« ton profil
    ne le permet pas »). Fondre les deux ferait afficher « accès refusé » à un
    visiteur simplement déconnecté, qui n'aurait alors aucune indication qu'il
    lui suffit de se reconnecter.

    Plusieurs clés = un OU. Nécessaire pour les lectures PARTAGÉES entre
    plusieurs pages : `GET /fai-journal` alimente à la fois « Demandes de
    coupure » et « Journal des blocages ».
    """
    return [*_auth, Depends(require_permission(*keys))]

api_router.include_router(devices.router, prefix="/devices", tags=["devices"], dependencies=_auth)
api_router.include_router(
    dashboard.router, prefix="/dashboard", tags=["dashboard"],
    dependencies=_perm("dashboard.view"),
)
api_router.include_router(
    sites.router, prefix="/sites", tags=["sites"], dependencies=_perm("sites.view"),
)
api_router.include_router(
    device_map.router, prefix="/map", tags=["map"], dependencies=_perm("map.view"),
)
api_router.include_router(
    access.router, prefix="/access", tags=["access"], dependencies=_perm("fai.view"),
)
api_router.include_router(
    access_diagnostics.router, prefix="/access-diagnostics",
    tags=["access"], dependencies=_auth,
)
# /fai additionally accepts FAI_API_KEY — a key scoped to these routes only, held
# by the external payment system (see require_fai_client).
api_router.include_router(
    fai.router, prefix="/fai", tags=["fai"],
    dependencies=[Depends(require_fai_client), Depends(require_permission("fai.block"))],
)
# GET /fai/verify — même préfixe /fai mais AUTH PROPRE : sa clé dédiée
# LR_VERIFY_API_KEY (require_verify_client), tenue par le système tiers de
# vérification. Router séparé car une dépendance de router ne se surcharge pas
# par route ; la clé de vérification n'ouvre donc que cette route.
api_router.include_router(
    fai_verify.router, prefix="/fai", tags=["fai"],
    dependencies=[Depends(require_verify_client), Depends(require_permission("fai.view"))],
)
# Lecture du journal : auth NORMALE (dashboard/clé maître). La clé du système de
# paiement n'y a délibérément pas accès — elle ne sert qu'à bloquer/débloquer.
api_router.include_router(
    fai_journal.router, prefix="/fai-journal", tags=["fai"],
    dependencies=_perm("fai.journal.view", "fai.requests.view"),
)
# Lecture EN DIRECT des règles de coupure du routeur de cœur. Même auth que le
# journal, et pour la même raison : le système de paiement n'a pas à lire l'état
# du réseau, seulement à demander des coupures.
api_router.include_router(
    router_rules.router, prefix="/router-rules", tags=["fai"],
    dependencies=_perm("fai.router_rules.view"),
)
# Filtre de contenu par PLATEFORME, indexé par MAC — consommé par le système
# tiers qui vend les options de filtrage. AUTH PROPRE : sa clé dédiée
# CONTENT_BLOCK_API_KEY (require_content_block_client), scellée à ce router.
# Elle ne retombe délibérément PAS sur l'auth /fai : filtrer TikTok chez un
# abonné et lui couper la ligne sont deux pouvoirs distincts, tenus par deux
# systèmes distincts. Les opérateurs continuent d'y accéder par leur session ou
# la clé maîtresse — la clé dédiée AJOUTE un chemin cloisonné, elle n'en retire
# aucun. La page /content-block du dashboard, elle, passe toujours par
# PUT /devices/{id}/content-block (auth normale) : elle envoie l'ensemble
# complet des cases cochées, là où ces routes sont cumulatives.
api_router.include_router(
    content_filter.router, prefix="/content-filter", tags=["content-filter"],
    dependencies=[
        Depends(require_content_block_client),
        Depends(require_permission("fai.content_filter.edit")),
    ],
)
api_router.include_router(
    incidents.router, prefix="/incidents", tags=["incidents"],
    dependencies=_perm("incidents.view"),
)
# Bandeau d'anomalies à acquitter à la main — canal PARALLÈLE à /incidents, qui
# reste inchangé (ouverture et résolution automatiques).
api_router.include_router(
    manual_alerts.router, prefix="/manual-alerts", tags=["incidents"], dependencies=_auth,
)
api_router.include_router(
    lr_health.router, prefix="/lr-health", tags=["lr-health"],
    dependencies=_perm("lr_health.view"),
)
# Qualité du signal + latence d'un abonné par MAC — consommé par le système tiers
# qui interroge la qualité de service. AUTH PROPRE : sa clé dédiée
# CLIENT_SIGNAL_API_KEY (require_client_signal_client), scellée à ce router.
#
# Elle ne retombe délibérément PAS sur l'auth /fai ni sur celle du filtre de
# contenu : LIRE la qualité d'un lien et AGIR sur l'abonné (le couper, filtrer
# son trafic) sont des pouvoirs distincts, tenus par des systèmes distincts. Les
# opérateurs y accèdent toujours par leur session ou la clé maîtresse — la clé
# dédiée AJOUTE un chemin cloisonné, elle n'en retire aucun.
#
# ⚠️ Pas de fichier de router séparé ici (contrairement à /fai/verify et
# /uisp/assign) parce que /client-signal a son PROPRE préfixe : il n'y a aucune
# route voisine dont la clé du tiers hériterait. Ses DEUX routes (le verdict
# live, et /history = les courbes 7 j lues en base) sont toutes deux voulues
# ouvertes à cette clé. Le piège à éviter : une dépendance de router étant
# ADDITIVE, toute AUTRE route ajoutée dans client_signal.py s'ouvrirait
# automatiquement à cette clé — la liste permise est verrouillée par
# tests/test_client_signal_scoped_key.py (`_ROUTES_OPEN_TO_THE_KEY`).
api_router.include_router(
    client_signal.router, prefix="/client-signal", tags=["client-signal"],
    dependencies=[
        Depends(require_client_signal_client),
        Depends(require_permission("fai.view")),
    ],
)
api_router.include_router(
    clients.router, prefix="/clients", tags=["clients"],
    dependencies=_perm("clients.view"),
)
api_router.include_router(
    network_capacity.router, prefix="/network-capacity", tags=["network-capacity"],
    dependencies=_perm("capacity.view"),
)
# Journal des coupures : lu par sa page dédiée (/downtime-log), par le tableau
# de bord ET par la page Rapports (composant SiteOutageCharts partagé) — donc
# un OU, sinon donner « Rapports » sans « Tableau de bord » afficherait une
# page vide.
api_router.include_router(
    network_uptime.router, prefix="/network-uptime", tags=["network-uptime"],
    dependencies=_perm("uptime.view", "dashboard.view", "reports.view", "sites.view"),
)
api_router.include_router(
    network_topology.router, prefix="/network-topology", tags=["network-topology"],
    dependencies=_perm("topology.view"),
)
api_router.include_router(
    traffic.router, prefix="/traffic", tags=["traffic"], dependencies=_perm("traffic.view"),
)
api_router.include_router(system.router, prefix="/system", tags=["system"], dependencies=_auth)
api_router.include_router(
    uisp.router, prefix="/uisp", tags=["uisp"], dependencies=_perm("uisp.sync"),
)
# POST /uisp/assign — même préfixe /uisp mais AUTH PROPRE : sa clé dédiée
# UISP_ASSIGN_API_KEY (require_uisp_assign_client), tenue par le système de
# paiement qui adopte les équipements installés. Router séparé car une dépendance
# de router ne se surcharge pas par route : c'est ce qui garantit que cette clé
# n'ouvre PAS /uisp/sync, qui réécrit l'inventaire.
api_router.include_router(
    uisp_assign.router, prefix="/uisp", tags=["uisp"],
    dependencies=[
        Depends(require_uisp_assign_client),
        Depends(require_permission("uisp.assign")),
    ],
)


# ---------------------------------------------------------------------------
# Section Administration — profils et comptes
# ---------------------------------------------------------------------------
#
# ⚠️ Gardée PAR ROUTE et non au niveau du router (`_auth` seul ici) : le
# catalogue et les listes se lisent avec `admin.access`, alors qu'écrire exige
# `admin.profiles` ou `admin.users`. Une dépendance de router étant ADDITIVE et
# non surchargeable par route, la poser ici donnerait le droit le plus faible à
# toutes les routes — y compris celles qui créent des comptes.
api_router.include_router(
    access_control.router, prefix="/access-control", tags=["access-control"],
    dependencies=_auth,
)
