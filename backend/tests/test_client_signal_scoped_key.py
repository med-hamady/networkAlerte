"""La clé de qualité de service est CLOISONNÉE à GET /client-signal.

`/client-signal` est servie sur la VIP `.229`, laquelle fronte l'API ENTIÈRE
(contrairement au listener `.233`, restreint à `/fai`). Faire consommer cette
route par un tiers sans clé dédiée oblige donc à lui confier `API_KEY` — et avec
elle `DELETE /devices/{id}`, `POST /uisp/sync` et tout le reste. C'est exactement
la situation du 2026-08-11 qui a fait naître `UISP_ASSIGN_API_KEY`.

Ce que ces tests verrouillent, et pourquoi chacun compte :

  - La route porte sa propre dépendance d'auth, et ses voisines NON.
  - ⚠️ `client_signal.py` ne contient QU'UNE route. C'est ce qui autorise à s'en
    tenir à ce router plutôt qu'à un fichier séparé (cf. `fai_verify.py`) : une
    dépendance de router est ADDITIVE et non surchargeable par route, donc toute
    route ajoutée dans ce module hériterait de la clé du tiers, en silence.
  - La clé n'est acceptée par AUCUN autre comparateur — sinon elle serait un
    alias de la clé maîtresse, pas une clé cloisonnée.
  - Elle ne retombe ni sur l'auth `/fai` ni sur celle du filtre de contenu : LIRE
    la qualité d'un lien et AGIR sur l'abonné sont deux pouvoirs distincts.
  - Deux clés ne peuvent pas partager une valeur.
  - Une clé non configurée ne vaut jamais « tout le monde passe ».
"""

import ast
import inspect
import textwrap

import pytest

from app.api import deps
from app.api.endpoints import client_signal as client_signal_module
from app.api.router import api_router

MASTER = "master-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FAI = "fai-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VERIFY = "verify-key-cccccccccccccccccccccccccccccccc"
ASSIGN = "assign-key-dddddddddddddddddddddddddddddddd"
CONTENT = "content-key-eeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
SIGNAL = "signal-key-ffffffffffffffffffffffffffffffff"


@pytest.fixture
def keys(monkeypatch, settings):
    """Les six clés distinctes, posées sur l'objet Settings mis en cache."""
    monkeypatch.setattr(settings, "api_key", MASTER, raising=False)
    monkeypatch.setattr(settings, "fai_api_key", FAI, raising=False)
    monkeypatch.setattr(settings, "lr_verify_api_key", VERIFY, raising=False)
    monkeypatch.setattr(settings, "uisp_assign_api_key", ASSIGN, raising=False)
    monkeypatch.setattr(settings, "content_block_api_key", CONTENT, raising=False)
    monkeypatch.setattr(settings, "client_signal_api_key", SIGNAL, raising=False)
    return settings


def _route(path: str):
    for route in api_router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route {path} absente du router")


def _dependency_names(route) -> list[str]:
    # ⚠️ `permission_guard` est ÉCARTÉ, et seulement lui. C'est la dépendance de
    # contrôle des droits par profil (`deps.require_permission`) : elle ne
    # donne accès à rien, elle ne fait que retirer. Ce que ce test surveille,
    # c'est qu'aucune AUTRE auth ne s'ajoute à l'auth cloisonnée — un filtre
    # large (« ignorer ce qu'on ne connaît pas ») laisserait au contraire
    # passer exactement ce qu'on cherche à interdire.
    return [
        d.dependency.__name__
        for d in route.dependencies
        if d.dependency.__name__ != "permission_guard"
    ]


def test_client_signal_route_carries_its_own_scoped_dependency():
    """GET /client-signal est gardée par require_client_signal_client, elle seule."""
    assert _dependency_names(_route("/api/v1/client-signal")) == [
        "require_client_signal_client",
    ]


def test_neighbour_routes_stay_behind_the_master_key():
    """Les routes voisines gardent l'auth normale — le cœur du cloisonnement.

    Si ce test casse, c'est que la dépendance cloisonnée a débordé sur des routes
    que le tiers n'a pas à ouvrir (l'inventaire, la santé des liens du parc).
    """
    for path in ("/api/v1/devices", "/api/v1/lr-health/bad-installations"):
        assert _dependency_names(_route(path)) == ["require_user_or_api_key"]


def test_client_signal_router_holds_exactly_one_route():
    """Une seule route dans ce module — c'est ce qui rend le cloisonnement sûr.

    La dépendance est posée au niveau du ROUTER (pas de la route), et une
    dépendance de router est additive : toute route ajoutée dans
    `client_signal.py` s'ouvrirait automatiquement à la clé du tiers, sans que
    rien ne le signale. Une nouvelle route doit donc aller ailleurs — ou bien ce
    module doit être scindé comme l'est `fai_verify.py`.
    """
    paths = {r.path for r in client_signal_module.router.routes}
    assert paths == {""}, (
        f"routes inattendues dans client_signal.py : {paths} — voir la docstring"
    )


def test_client_signal_key_opens_nothing_else(keys):
    """La clé de qualité de service n'est reconnue par aucun autre comparateur."""
    assert deps._client_signal_api_key_matches(SIGNAL) is True
    assert deps._api_key_matches(SIGNAL) is False
    assert deps._fai_api_key_matches(SIGNAL) is False
    assert deps._lr_verify_api_key_matches(SIGNAL) is False
    assert deps._uisp_assign_api_key_matches(SIGNAL) is False
    assert deps._content_block_api_key_matches(SIGNAL) is False


def test_other_keys_are_not_silently_accepted_on_client_signal(keys):
    """Une autre clé ne devient pas clé de qualité de service par ressemblance."""
    for other in (FAI, VERIFY, ASSIGN, CONTENT, "", None, SIGNAL + "x", SIGNAL[:-1]):
        assert deps._client_signal_api_key_matches(other) is False
    # La maîtresse passe quand même, mais par le repli require_user_or_api_key —
    # pas en se faisant passer pour la clé de qualité de service.
    assert deps._client_signal_api_key_matches(MASTER) is False
    assert deps._api_key_matches(MASTER) is True


def test_client_signal_does_not_fall_back_to_action_keys():
    """Le repli est l'auth NORMALE, jamais l'auth /fai ni le filtre de contenu.

    Lire la qualité d'un lien et couper / filtrer un abonné sont des pouvoirs
    distincts tenus par des systèmes distincts : un repli sur `require_fai_client`
    ferait de la clé du système de paiement une clé de lecture de la qualité de
    service, et rien ne le signalerait.

    ⚠️ On inspecte les noms RÉELLEMENT référencés (via `ast`), jamais le texte du
    source : la docstring de la dépendance cite `require_fai_client` justement
    pour expliquer qu'elle ne s'en sert pas, donc un test sur la chaîne échoue
    sur son propre commentaire.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(deps.require_client_signal_client)))
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    assert "require_user_or_api_key" in referenced
    assert "_client_signal_api_key_matches" in referenced
    for forbidden in (
        "require_fai_client",
        "require_verify_client",
        "_fai_api_key_matches",
        "_lr_verify_api_key_matches",
        "_content_block_api_key_matches",
        "_uisp_assign_api_key_matches",
    ):
        assert forbidden not in referenced, (
            f"{forbidden} ferait de la clé d'un autre système une clé de lecture "
            "de la qualité de service"
        )


def test_production_refuses_two_keys_sharing_one_value():
    """Deux variables portant la MÊME valeur = cloisonnement annulé, en silence.

    Les autres tests comparent des *comparateurs* : ils passeraient tous alors
    même qu'un déploiement aurait collé la clé maîtresse dans
    CLIENT_SIGNAL_API_KEY. Le démarrage est le seul endroit qui voie les valeurs
    réellement déployées — donc le seul qui puisse attraper la faute.
    """
    from app.core.config import Settings

    def build(**overrides):
        # Les six clés sont TOUJOURS passées explicitement : sans ça le test
        # hériterait des valeurs du .env de la machine et ne prouverait rien.
        keys = {
            "api_key": MASTER,
            "fai_api_key": FAI,
            "lr_verify_api_key": VERIFY,
            "uisp_assign_api_key": ASSIGN,
            "content_block_api_key": CONTENT,
            "client_signal_api_key": SIGNAL,
        }
        keys.update(overrides)
        return Settings(
            app_env="production", postgres_password="a-strong-password", **keys,
        )

    for clash in (MASTER, FAI, VERIFY, ASSIGN, CONTENT):
        with pytest.raises(ValueError, match="must differ from"):
            build(client_signal_api_key=clash)

    # Six valeurs distinctes : accepté.
    assert build().client_signal_api_key == SIGNAL

    # Clé non distribuée (vide) : absente, pas « dupliquée » — sinon un
    # déploiement qui ne la distribue pas ne démarrerait plus.
    assert build(client_signal_api_key="").client_signal_api_key == ""


def test_unset_client_signal_key_never_means_open_bar(monkeypatch, settings):
    """Clé non configurée = refus, jamais « laisser passer »."""
    monkeypatch.setattr(settings, "client_signal_api_key", "", raising=False)
    assert deps._client_signal_api_key_matches("") is False
    assert deps._client_signal_api_key_matches(None) is False
    assert deps._client_signal_api_key_matches("n'importe quoi") is False
