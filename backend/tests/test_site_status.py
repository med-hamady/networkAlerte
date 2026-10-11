"""GET /site-status — état up/down de chaque site, et sa clé CLOISONNÉE.

Deux choses verrouillées :

  - la RÈGLE : un site est down quand son switch l'est (celle du Journal des
    coupures) ; plusieurs switches → le pire ; `unknown` n'est pas une panne ;
    un site sans switch n'est pas listé ;
  - le CLOISONNEMENT : `SITE_STATUS_API_KEY` n'ouvre que cette route, n'est
    acceptée par aucun autre comparateur et ne retombe sur aucune autre clé.
"""

import ast
import inspect
import textwrap

import pytest

from app.api import deps
from app.api.endpoints import site_status as site_status_module
from app.api.router import api_router
from app.services.site_status_service import site_statuses_from_switches

MASTER = "master-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FAI = "fai-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
VERIFY = "verify-key-cccccccccccccccccccccccccccccccc"
ASSIGN = "assign-key-dddddddddddddddddddddddddddddddd"
CONTENT = "content-key-eeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
SIGNAL = "signal-key-ffffffffffffffffffffffffffffffff"
SITES = "sites-key-0000000000000000000000000000000000"

_ROUTES_OPEN_TO_THE_KEY = {""}


@pytest.fixture
def keys(monkeypatch, settings):
    for name, value in (
        ("api_key", MASTER), ("fai_api_key", FAI), ("lr_verify_api_key", VERIFY),
        ("uisp_assign_api_key", ASSIGN), ("content_block_api_key", CONTENT),
        ("client_signal_api_key", SIGNAL), ("site_status_api_key", SITES),
    ):
        monkeypatch.setattr(settings, name, value, raising=False)
    return settings


# ------------------------------------------------------------------ la règle
def _as_dict(rows):
    return {s.site: s.status for s in site_statuses_from_switches(rows)}


def test_site_follows_its_switch():
    assert _as_dict([("A2 AT1", "up"), ("A2 CT1", "down")]) == {
        "A2 AT1": "up", "A2 CT1": "down",
    }


def test_one_switch_down_puts_the_site_down_whatever_the_order():
    assert _as_dict([("A2 HQ", "up"), ("A2 HQ", "down")]) == {"A2 HQ": "down"}
    assert _as_dict([("A2 HQ", "down"), ("A2 HQ", "up")]) == {"A2 HQ": "down"}


def test_unknown_is_not_an_outage():
    """On n'affirme une panne que sur constat."""
    assert _as_dict([("A2 SK1", "unknown"), ("A2 PK1", None)]) == {
        "A2 SK1": "up", "A2 PK1": "up",
    }


def test_switch_without_site_is_skipped_and_names_are_kept_verbatim():
    out = _as_dict([(None, "down"), ("  ", "down"), ("A2  ARF1", "up")])
    assert out == {"A2  ARF1": "up"}  # double espace conservé


def test_sites_are_sorted():
    sites = [s.site for s in site_statuses_from_switches([("B", "up"), ("A", "up")])]
    assert sites == ["A", "B"]


# --------------------------------------------------------- le cloisonnement
def _route(path):
    for route in api_router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route {path} absente du router")


def test_route_carries_only_its_scoped_dependency():
    names = [
        d.dependency.__name__
        for d in _route("/api/v1/site-status").dependencies
        if d.dependency.__name__ != "permission_guard"
    ]
    assert names == ["require_site_status_client"]


def test_router_holds_only_the_listed_routes():
    """Une route ajoutée dans site_status.py hériterait de la clé du tiers."""
    assert {r.path for r in site_status_module.router.routes} == _ROUTES_OPEN_TO_THE_KEY


def test_site_status_key_opens_nothing_else(keys):
    assert deps._site_status_api_key_matches(SITES) is True
    for other in (
        deps._api_key_matches, deps._fai_api_key_matches, deps._lr_verify_api_key_matches,
        deps._uisp_assign_api_key_matches, deps._content_block_api_key_matches,
        deps._client_signal_api_key_matches,
    ):
        assert other(SITES) is False
    for other in (MASTER, FAI, VERIFY, ASSIGN, CONTENT, SIGNAL, "", None, SITES[:-1]):
        assert deps._site_status_api_key_matches(other) is False


def test_no_fallback_on_other_scoped_keys():
    tree = ast.parse(textwrap.dedent(inspect.getsource(deps.require_site_status_client)))
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert {"require_user_or_api_key", "_site_status_api_key_matches"} <= referenced
    for forbidden in (
        "require_fai_client", "require_verify_client", "require_client_signal_client",
        "require_content_block_client", "require_uisp_assign_client",
        "_fai_api_key_matches", "_client_signal_api_key_matches",
    ):
        assert forbidden not in referenced


def test_production_refuses_a_shared_value():
    from app.core.config import Settings

    def build(**overrides):
        values = {
            "api_key": MASTER, "fai_api_key": FAI, "lr_verify_api_key": VERIFY,
            "uisp_assign_api_key": ASSIGN, "content_block_api_key": CONTENT,
            "client_signal_api_key": SIGNAL, "site_status_api_key": SITES,
        }
        values.update(overrides)
        return Settings(app_env="production", postgres_password="a-strong-password", **values)

    for clash in (MASTER, FAI, VERIFY, ASSIGN, CONTENT, SIGNAL):
        with pytest.raises(ValueError, match="must differ from"):
            build(site_status_api_key=clash)
    assert build().site_status_api_key == SITES
    assert build(site_status_api_key="").site_status_api_key == ""


def test_unset_key_never_means_open_bar(monkeypatch, settings):
    monkeypatch.setattr(settings, "site_status_api_key", "", raising=False)
    assert deps._site_status_api_key_matches("") is False
    assert deps._site_status_api_key_matches(None) is False
