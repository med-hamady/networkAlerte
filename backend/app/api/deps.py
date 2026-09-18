"""
FastAPI dependencies shared across endpoints.

Two flavours of authentication coexist:

  - **X-API-Key header** (`verify_api_key`) — for direct calls to the backend
    bypassing the dashboard (admin scripts, integrations). The key is a long
    shared secret read from settings.
  - **Session cookie** (`require_user`) — for the browser. Created by
    /auth/login, persisted server-side in `auth_sessions`. Carries a user
    identity (useful for audit), can be revoked, expires automatically.

Most routes accept EITHER (`require_user_or_api_key`), so the same code
path serves both the dashboard and admin scripts without duplication. The
auth router itself uses `require_user` directly because the API key is
not enough to identify whose password to change.
"""

import hmac
import logging
from collections.abc import Awaitable, Callable

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db
from app.models.user import User
from app.services import auth_service, profile_service
from app.services.auth_service import SESSION_COOKIE_NAME

logger = logging.getLogger(__name__)


async def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Reject requests missing or carrying a wrong X-API-Key header.

    Authentication is skipped entirely when Settings.api_key is empty so that
    local dev environments don't need to configure a key. Production startup
    refuses to boot when api_key is empty (see Settings._validate_production_secrets).
    """
    settings = get_settings()
    if not settings.api_key:
        return  # auth disabled (dev mode — refused at startup in production)
    # compare_digest requires str (not None) — treat absent header as empty string
    if not hmac.compare_digest(x_api_key or "", settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def _api_key_matches(x_api_key: str | None) -> bool:
    """True if the supplied header equals the configured API key (timing-safe)."""
    settings = get_settings()
    if not settings.api_key:
        return False  # auth-disabled mode falls through to require_user
    return hmac.compare_digest(x_api_key or "", settings.api_key)


def _fai_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated payment-system key (timing-safe)."""
    settings = get_settings()
    if not settings.fai_api_key:
        return False  # no dedicated key configured — /fai falls back to normal auth
    return hmac.compare_digest(x_api_key or "", settings.fai_api_key)


def _lr_verify_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated /fai/verify key (timing-safe)."""
    settings = get_settings()
    if not settings.lr_verify_api_key:
        return False  # no dedicated key — /fai/verify falls back to the /fai auth
    return hmac.compare_digest(x_api_key or "", settings.lr_verify_api_key)


def _uisp_assign_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated /uisp/assign key (timing-safe)."""
    settings = get_settings()
    if not settings.uisp_assign_api_key:
        return False  # no dedicated key — /uisp/assign falls back to normal auth
    return hmac.compare_digest(x_api_key or "", settings.uisp_assign_api_key)


def _content_block_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated /content-filter key (timing-safe)."""
    settings = get_settings()
    if not settings.content_block_api_key:
        return False  # no dedicated key — /content-filter falls back to normal auth
    return hmac.compare_digest(x_api_key or "", settings.content_block_api_key)


def _client_signal_api_key_matches(x_api_key: str | None) -> bool:
    """True if the header equals the dedicated /client-signal key (timing-safe)."""
    settings = get_settings()
    if not settings.client_signal_api_key:
        return False  # no dedicated key — /client-signal falls back to normal auth
    return hmac.compare_digest(x_api_key or "", settings.client_signal_api_key)


def _log_master_key_use(request: Request) -> None:
    """Trace toute authentification par la clé MAÎTRESSE (jamais la clé elle-même).

    Sans ça, « qui dépend encore d'`API_KEY` ? » est une question SANS RÉPONSE :
    le `log_format main` de nginx n'enregistre aucun en-tête, donc un appel porté
    par la clé maîtresse et un appel porté par une clé cloisonnée y sont
    rigoureusement identiques. C'est exactement ce qui bloquait la rotation
    décidée le 2026-08-11 — impossible de savoir ce qu'on casserait en tournant.

    WARNING et non INFO, pour deux raisons : l'usage d'un secret qui ouvre
    l'API ENTIÈRE est un événement de sécurité, et le volume produit est
    précisément la grandeur qu'on cherche à mesurer avant de tourner. Un log
    bruyant n'est pas un défaut ici, c'est le résultat.

    ⚠️ On journalise le CHEMIN, jamais la query string ni l'en-tête : le premier
    dit quel consommateur dépend de quoi (le seul renseignement utile), les
    seconds ajouteraient des identifiants d'abonné et le secret lui-même dans un
    fichier de log.
    """
    client = request.client.host if request.client else "?"
    forwarded = request.headers.get("x-forwarded-for")
    logger.warning(
        "master API_KEY used on %s %s from %s (xff=%s) — ce consommateur devrait "
        "porter une cle cloisonnee ; cf. rotation d'API_KEY",
        request.method,
        request.url.path,
        client,
        forwarded or "-",
    )


# Sentinelle : « pas encore résolu ». `None` est une valeur LÉGITIME du cache
# (authentification par clé API = aucune identité d'utilisateur), donc elle ne
# peut pas servir de « absent » — sans cette sentinelle, chaque requête portée
# par une clé API relirait la session à chaque dépendance.
_UNRESOLVED = object()


def _mark_api_key_auth(request: Request) -> None:
    """Marquer la requête comme authentifiée par une CLÉ, sans identité humaine.

    ⚠️ Ce marquage est ce qui empêche le contrôle de droits de casser les cinq
    intégrations tierces. Une clé cloisonnée n'a pas de profil — elle est déjà
    limitée à sa route par sa propre dépendance de router — donc
    `require_permission` doit la laisser passer. Sans ce drapeau, la dépendance
    de permission re-authentifierait par `require_user_or_api_key`, qui ne
    connaît QUE la clé maîtresse : le système de paiement recevrait un 401 sur
    `POST /fai/block`, c.-à-d. qu'aucun impayé ne serait plus coupé.
    """
    request.state.auth_user = None
    request.state.auth_via_api_key = True


async def _resolve_session_user(request: Request, db: AsyncSession) -> User | None:
    """Résoudre le cookie de session UNE FOIS par requête HTTP.

    ⚠️ Ce cache existe pour une raison mesurable, pas par élégance : les routers
    portent déjà `require_user_or_api_key` au niveau du router, et les
    permissions s'ajoutent PAR ROUTE (une dépendance de router étant additive et
    non surchargeable — cf. `/uisp/assign`). Les deux dépendances s'exécutent
    donc sur le même appel, et sans mémoïsation chaque requête du dashboard
    paierait deux fois la lecture de session, sur un chemin où le projet compte
    déjà ses allers-retours DB (cf. « Attendre un verrou coûte autant qu'un
    interblocage »).

    La portée est celle de l'objet `Request`, donc d'un seul appel HTTP : rien
    ne survit d'une requête à la suivante, et un changement de profil s'applique
    dès l'appel d'après.
    """
    cached = getattr(request.state, "auth_user", _UNRESOLVED)
    if cached is not _UNRESOLVED:
        return cached
    raw = request.cookies.get(SESSION_COOKIE_NAME)
    user = await auth_service.get_user_from_token(db, raw)
    request.state.auth_user = user
    return user


async def require_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Return the user owning the current session cookie, or raise 401."""
    user = await _resolve_session_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    return user


async def require_user_or_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Accept either a valid X-API-Key header or a valid session cookie.

    Returns the User on cookie auth, None on API key auth (no user identity).
    Raises 401 if neither path is valid.

    C'est le CHOKEPOINT de la clé maîtresse : toutes les dépendances cloisonnées
    retombent ici quand leur propre clé n'est pas présentée. Y journaliser son
    usage couvre donc l'API entière en un seul point (cf. `_log_master_key_use`).
    """
    if _api_key_matches(x_api_key):
        _log_master_key_use(request)
        _mark_api_key_auth(request)
        return None
    user = await _resolve_session_user(request, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Session"},
        )
    return user


async def require_fai_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for the /fai routes: the dedicated payment key, or the normal auth.

    The payment system holds `fai_api_key`, which unlocks nothing but these three
    routes — so handing it to a third party (and rotating it) never touches the
    dashboard or the admin scripts. Operators keep reaching /fai through their
    session cookie or the master `api_key`, which is what the dashboard's own
    block/unblock buttons use.
    """
    if _fai_api_key_matches(x_api_key):
        _mark_api_key_auth(request)
        return None
    return await require_user_or_api_key(request, x_api_key, db)


async def require_verify_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for GET /fai/verify: its own dedicated key, or the /fai auth.

    The verification consumer (a third party polling LR readiness, distinct from
    the payment system) holds `lr_verify_api_key`, which unlocks ONLY this route.
    Falling back to `require_fai_client` keeps the payment key, master api_key and
    operator sessions working too — so the dedicated key ADDS a scoped path, it
    never removes the existing ones.
    """
    if _lr_verify_api_key_matches(x_api_key):
        _mark_api_key_auth(request)
        return None
    return await require_fai_client(request, x_api_key, db)


async def require_uisp_assign_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for POST /uisp/assign: its own dedicated key, or the normal auth.

    The payment system adopts newly installed CPEs by MAC; it holds
    `uisp_assign_api_key`, which unlocks ONLY this route. It deliberately does
    NOT fall back to `require_fai_client`: the block/unblock key answers a
    different question (couper un abonné) and must not become a way to write to
    the UISP controller. Falling back to `require_user_or_api_key` keeps the
    master key and operator sessions working — the dedicated key ADDS a scoped
    path, it never removes an existing one.

    ⚠️ Why this key exists at all: `/uisp/assign` is served on the .229 VIP,
    which fronts the WHOLE API (unlike the .233 listener, restricted to /fai).
    Letting a third party consume this route without a scoped key means handing
    over `api_key` — and with it `DELETE /devices/{id}` and `/uisp/sync`.
    """
    if _uisp_assign_api_key_matches(x_api_key):
        _mark_api_key_auth(request)
        return None
    return await require_user_or_api_key(request, x_api_key, db)


async def require_content_block_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for the /content-filter routes: their own key, or the normal auth.

    The third party driving per-platform filtering holds `content_block_api_key`,
    which unlocks ONLY these routes. It deliberately does NOT fall back to
    `require_fai_client`: filtering TikTok on a subscriber and cutting his line
    entirely are two different powers, and the two callers are two different
    systems — sharing a key would make each able to do the other's job. Falling
    back to `require_user_or_api_key` keeps the master key and operator sessions
    working: the dedicated key ADDS a scoped path, it never removes one.
    """
    if _content_block_api_key_matches(x_api_key):
        _mark_api_key_auth(request)
        return None
    return await require_user_or_api_key(request, x_api_key, db)


async def require_client_signal_client(
    request: Request,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Auth for GET /client-signal: its own dedicated key, or the normal auth.

    The third party polling a subscriber's link quality holds
    `client_signal_api_key`, which unlocks ONLY this route. Without it, letting
    that consumer read a signal means handing over the master `api_key` — and
    with it `DELETE /devices/{id}`, `/uisp/sync` and every other route, since
    `/client-signal` is served on the .229 VIP which fronts the WHOLE API.

    It deliberately does NOT fall back to `require_fai_client` nor to the
    content-filter key: READING the quality of a subscriber's link and ACTING on
    that subscriber (cutting his line, filtering his traffic) are distinct
    powers held by distinct systems. Falling back to `require_user_or_api_key`
    keeps the master key and operator sessions working — the dedicated key ADDS
    a scoped path, it never removes one.
    """
    if _client_signal_api_key_matches(x_api_key):
        _mark_api_key_auth(request)
        return None
    return await require_user_or_api_key(request, x_api_key, db)


# ---------------------------------------------------------------------------
# Contrôle des droits — le cloisonnement par PROFIL
# ---------------------------------------------------------------------------

def require_permission(
    *keys: str,
) -> Callable[..., Awaitable[User | None]]:
    """Dépendance de route : exiger AU MOINS UNE des permissions données.

    ⚠️ **Le contrôle est ici, côté serveur, et pas seulement dans le dashboard.**
    Masquer un bouton dans le frontend ne protège rien : le proxy du dashboard
    relaie les appels avec le cookie de session de l'utilisateur, donc un compte
    « agent » peut appeler n'importe quelle route à la main depuis l'onglet
    réseau de son navigateur. Une permission qui n'est pas posée sur la route
    n'existe pas.

    ⚠️ **Une authentification par CLÉ API passe outre**, délibérément. Les clés
    sont des identités de MACHINE, sans profil : la clé maîtresse ouvre déjà
    toute l'API par définition (c'est ce que dit `_log_master_key_use`), et les
    cinq clés cloisonnées sont déjà limitées à leur route par leur propre
    dépendance de router. Leur imposer un profil n'ajouterait aucun
    cloisonnement et casserait l'outillage d'exploitation.

    Le OU entre les clés sert les endpoints partagés par plusieurs pages :
    `GET /fai-journal` alimente à la fois « Demandes de coupure » et « Journal
    des blocages », et doit répondre à qui n'a reçu que l'une des deux.
    """

    # ⚠️ Le NOM de cette fonction est public : plusieurs tests de cloisonnement
    # (`test_uisp_assign_scoped_key`, `test_client_signal_scoped_key`,
    # `test_content_filter_api`) énumèrent les dépendances d'une route pour
    # vérifier qu'elle ne porte QUE son auth cloisonnée. Ils écartent celle-ci
    # par son nom — la renommer ferait échouer ces tests sans que rien ne soit
    # cassé, ou pire, la ferait passer pour une auth de plus.
    async def permission_guard(
        request: Request,
        x_api_key: str | None = Header(default=None),
        db: AsyncSession = Depends(get_db),
    ) -> User | None:
        # Authentification par clé (maîtresse ou cloisonnée) : hors du système
        # de profils, cf. docstring. Le drapeau est posé par la dépendance de
        # ROUTER, qui s'exécute avant celle de route.
        if getattr(request.state, "auth_via_api_key", False):
            return None
        user = await require_user_or_api_key(request, x_api_key, db)
        if user is None:
            return None  # clé API — hors du système de profils (cf. docstring)
        if not profile_service.has_permission(user, *keys):
            logger.warning(
                "Accès refusé — user=%s profil=%s sur %s %s (droit requis : %s)",
                user.username,
                user.profile.name if user.profile else None,
                request.method,
                request.url.path,
                " ou ".join(keys),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ton profil ne permet pas cette action.",
            )
        return user

    return permission_guard


def caller_has_permission(request: Request, *keys: str) -> bool:
    """Le porteur de CETTE requête détient-il l'un de ces droits ?

    À utiliser dans un endpoint qui doit RETIRER une partie de sa réponse
    (un bloc de chiffres, un champ sensible) plutôt que refuser l'appel entier.
    C'est la contrepartie serveur des permissions de nature `DATA` : la donnée
    voyage dans la même réponse que le reste de la page, donc la masquer dans le
    navigateur la laisserait parfaitement lisible dans l'onglet réseau.

    ⚠️ Une authentification par CLÉ répond TOUJOURS vrai. Une clé est une
    identité de machine, sans profil : la traiter comme « aucun droit » ferait
    disparaître ces blocs des réponses servies à l'outillage d'exploitation et
    aux intégrations, silencieusement — un appelant recevrait une réponse
    amputée sans la moindre erreur pour le lui dire.

    ⚠️ Ne lit QUE l'état déjà résolu par la dépendance d'authentification du
    router ; il n'authentifie rien lui-même. Un endpoint qui l'appellerait sans
    dépendance d'auth en amont verrait `auth_user` absent, donc « aucun droit » —
    le sens sûr, mais le symptôme serait un bloc qui manque sans raison.
    """
    if getattr(request.state, "auth_via_api_key", False):
        return True
    return profile_service.has_permission(
        getattr(request.state, "auth_user", None), *keys,
    )
