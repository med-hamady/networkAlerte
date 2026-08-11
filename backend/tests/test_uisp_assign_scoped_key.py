"""La clé d'adoption est CLOISONNÉE à POST /uisp/assign — et doit le rester.

Contexte (2026-08-11) : le système de paiement consommait `/uisp/assign` avec
`API_KEY`, la clé maîtresse, sur la VIP publique `.229` — laquelle sert l'API
ENTIÈRE (contrairement au listener `.233`, restreint à `/fai`). Cette clé ouvrait
donc aussi `DELETE /devices/{id}` et `POST /uisp/sync`.

Ce que ces tests verrouillent, et pourquoi chacun compte :

  - `/uisp/assign` porte sa propre dépendance d'auth, et `/uisp/sync` NON. Le
    jour où quelqu'un « range » les deux routes dans le même router (ce dont
    elles ont l'air : même préfixe, même domaine métier), la dépendance de router
    étant ADDITIVE et non surchargeable par route, la clé du tiers ouvrirait le
    sync — c.-à-d. la réécriture de l'inventaire. Rien ne le signalerait.
  - La clé d'adoption n'est acceptée par AUCUN autre comparateur. C'est ce qui
    fait d'elle une clé cloisonnée plutôt qu'un alias de la clé maîtresse.
  - Deux clés ne peuvent pas partager une valeur : ça annulerait le cloisonnement
    en silence, et aucun test de comparateur ne le verrait.
  - Une clé non configurée ne vaut jamais « tout le monde passe ».
"""

import pytest

from app.api import deps
from app.api.router import api_router

MASTER = "master-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FAI = "fai-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VERIFY = "verify-key-cccccccccccccccccccccccccccccccc"
ASSIGN = "assign-key-dddddddddddddddddddddddddddddddd"


@pytest.fixture
def keys(monkeypatch, settings):
    """Les quatre clés distinctes, posées sur l'objet Settings mis en cache."""
    monkeypatch.setattr(settings, "api_key", MASTER, raising=False)
    monkeypatch.setattr(settings, "fai_api_key", FAI, raising=False)
    monkeypatch.setattr(settings, "lr_verify_api_key", VERIFY, raising=False)
    monkeypatch.setattr(settings, "uisp_assign_api_key", ASSIGN, raising=False)
    return settings


def _route(path: str):
    for route in api_router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route {path} absente du router")


def _dependency_names(route) -> list[str]:
    return [d.dependency.__name__ for d in route.dependencies]


def test_assign_route_carries_its_own_scoped_dependency():
    """POST /uisp/assign est gardée par require_uisp_assign_client, elle seule."""
    assert _dependency_names(_route("/api/v1/uisp/assign")) == [
        "require_uisp_assign_client",
    ]


def test_sync_route_stays_behind_the_master_key():
    """/uisp/sync reste sous la clé maîtresse — le cœur du cloisonnement.

    Si ce test casse, c'est que les deux routes ont été réunies dans un même
    router : le tiers qui adopte des équipements pourrait alors déclencher la
    réécriture de l'inventaire.
    """
    assert _dependency_names(_route("/api/v1/uisp/sync")) == [
        "require_user_or_api_key",
    ]


def test_assign_key_opens_nothing_else(keys):
    """La clé d'adoption n'est reconnue par aucun autre comparateur."""
    assert deps._uisp_assign_api_key_matches(ASSIGN) is True
    assert deps._api_key_matches(ASSIGN) is False
    assert deps._fai_api_key_matches(ASSIGN) is False
    assert deps._lr_verify_api_key_matches(ASSIGN) is False


def test_other_keys_are_not_silently_accepted_on_assign(keys):
    """Une autre clé ne devient pas une clé d'adoption par ressemblance."""
    for other in (FAI, VERIFY, "", None, ASSIGN + "x", ASSIGN[:-1]):
        assert deps._uisp_assign_api_key_matches(other) is False
    # La maîtresse passe quand même, mais par le repli require_user_or_api_key —
    # pas en se faisant passer pour la clé d'adoption.
    assert deps._uisp_assign_api_key_matches(MASTER) is False
    assert deps._api_key_matches(MASTER) is True


def test_production_refuses_two_keys_sharing_one_value():
    """Deux variables portant la MÊME valeur = cloisonnement annulé, en silence.

    Les autres tests comparent des *comparateurs* : ils passeraient tous alors
    même qu'un déploiement aurait collé la clé maîtresse dans
    UISP_ASSIGN_API_KEY. Le démarrage est le seul endroit qui voie les valeurs
    réellement déployées — donc le seul qui puisse attraper la faute.
    """
    from app.core.config import Settings

    def build(**overrides):
        # Les quatre clés sont TOUJOURS passées explicitement : sans ça le test
        # hériterait des valeurs du .env de la machine et ne prouverait rien.
        keys = {
            "api_key": MASTER,
            "fai_api_key": FAI,
            "lr_verify_api_key": VERIFY,
            "uisp_assign_api_key": ASSIGN,
        }
        keys.update(overrides)
        return Settings(
            app_env="production", postgres_password="a-strong-password", **keys,
        )

    # La clé d'adoption ne peut pas être la clé maîtresse…
    with pytest.raises(ValueError, match="must differ from"):
        build(uisp_assign_api_key=MASTER)
    # …ni la clé du système de paiement, ni celle de vérification.
    with pytest.raises(ValueError, match="must differ from"):
        build(uisp_assign_api_key=FAI)
    with pytest.raises(ValueError, match="must differ from"):
        build(uisp_assign_api_key=VERIFY)

    # Quatre valeurs distinctes : accepté.
    assert build().uisp_assign_api_key == ASSIGN

    # Clés non distribuées (vides) : absentes, pas « dupliquées » — sinon un
    # déploiement qui n'utilise aucune clé cloisonnée ne démarrerait plus.
    ok_empty = build(fai_api_key="", lr_verify_api_key="", uisp_assign_api_key="")
    assert ok_empty.uisp_assign_api_key == ""


def test_unset_assign_key_never_means_open_bar(monkeypatch, settings):
    """Clé non configurée = refus, jamais « laisser passer »."""
    monkeypatch.setattr(settings, "uisp_assign_api_key", "", raising=False)
    assert deps._uisp_assign_api_key_matches("") is False
    assert deps._uisp_assign_api_key_matches(None) is False
    assert deps._uisp_assign_api_key_matches("n'importe quoi") is False
