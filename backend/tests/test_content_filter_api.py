"""API de filtre de contenu par plateforme — sémantique et cloisonnement.

Ce que ces tests verrouillent, et pourquoi chacun compte :

  - **La traduction d'INTENTION.** L'ensemble stocké (`blocked_categories`) ne
    veut pas dire « bloqué » : son sens dépend de la DIRECTION du filtre. En
    « allowlist » il liste les seuls services JOIGNABLES. Un `append` naïf y
    ferait de TikTok le seul site accessible à l'abonné à qui on demandait de le
    bloquer — silencieusement, durablement, et sans qu'aucun autre test ne le
    voie.
  - **Le cumul.** Bloquer TikTok ne doit pas débloquer Facebook. C'est toute la
    différence avec `PUT /devices/{id}/content-block` (qui prend l'ensemble
    complet), et c'est ce qui permet à un tiers d'agir sans connaître l'état
    courant du client.
  - **Le refus d'une plateforme inconnue.** Le chemin interne ignore les clés
    inconnues, ce qui est juste pour un rejeu et faux pour une API : un
    « titkok » mal orthographié rendrait 200 sans rien bloquer.
  - **Le cloisonnement de la clé** — même doctrine que
    `test_uisp_assign_scoped_key.py`.
"""

import pytest

from app.api import deps
from app.api.router import api_router
from app.services import client_block_service as cbs

DENY = cbs.CONTENT_MODE_DENY
ALLOW = cbs.CONTENT_MODE_ALLOW


# ── Traduction d'intention ───────────────────────────────────────────────────
def test_blocking_adds_in_denylist_and_keeps_the_rest():
    """Cumulatif : bloquer TikTok laisse Facebook bloqué."""
    target, mode = cbs.platform_target_categories(
        ["facebook"], DENY, ["tiktok"], blocked=True,
    )
    assert target == ["facebook", "tiktok"]
    assert mode == DENY


def test_unblocking_removes_only_the_named_platform():
    target, mode = cbs.platform_target_categories(
        ["facebook", "tiktok"], DENY, ["tiktok"], blocked=False,
    )
    assert target == ["facebook"]
    assert mode == DENY


def test_blocking_in_allowlist_removes_from_the_reachable_set():
    """⚠️ Le test central : en allowlist, bloquer = retirer de l'ensemble.

    L'ensemble y liste ce qui est JOIGNABLE. Ajouter « tiktok » comme on le fait
    en denylist rendrait TikTok accessible — l'inverse exact de l'ordre reçu.
    """
    target, mode = cbs.platform_target_categories(
        ["whatsapp", "tiktok"], ALLOW, ["tiktok"], blocked=True,
    )
    assert target == ["whatsapp"]  # tiktok n'est plus joignable
    assert mode == ALLOW
    assert "tiktok" in cbs.effective_blocked_platforms(target, ALLOW)


def test_unblocking_in_allowlist_adds_to_the_reachable_set():
    target, mode = cbs.platform_target_categories(
        ["whatsapp"], ALLOW, ["tiktok"], blocked=False,
    )
    assert target == ["whatsapp", "tiktok"]
    assert mode == ALLOW
    assert "tiktok" not in cbs.effective_blocked_platforms(target, ALLOW)


def test_allowlist_refuses_to_empty_itself():
    """Vider l'ensemble en allowlist EFFACE le filtre → tout l'internet rouvert.

    C'est le contraire d'un ordre de blocage : on refuse au lieu de surprendre.
    """
    with pytest.raises(cbs.ContentFilterConflictError):
        cbs.platform_target_categories(["tiktok"], ALLOW, ["tiktok"], blocked=True)


def test_no_active_filter_ignores_a_stale_allowlist_direction():
    """Sans filtre actif, la direction stockée est un vestige — on part en deny.

    `content_block_mode` garde la dernière direction utilisée même après un
    effacement. L'honorer ici obligerait, pour bloquer TikTok, à énumérer tout
    l'internet comme « autorisé ».
    """
    target, mode = cbs.platform_target_categories(None, ALLOW, ["tiktok"], blocked=True)
    assert (target, mode) == (["tiktok"], DENY)


def test_unblocking_with_no_filter_is_a_no_op():
    assert cbs.platform_target_categories([], DENY, ["tiktok"], blocked=False) == ([], DENY)


def test_effective_blocked_platforms_reads_through_the_direction():
    """La réponse dit ce qui est INJOIGNABLE, pas ce qui est stocké."""
    assert cbs.effective_blocked_platforms(["tiktok"], DENY) == ["tiktok"]
    # En allowlist, tout le catalogue SAUF l'ensemble autorisé.
    blocked = cbs.effective_blocked_platforms(["whatsapp"], ALLOW)
    assert "whatsapp" not in blocked
    assert "tiktok" in blocked and "facebook" in blocked
    # Pas de filtre = rien de bloqué, quelle que soit la direction résiduelle.
    assert cbs.effective_blocked_platforms(None, ALLOW) == []
    assert cbs.effective_blocked_platforms([], ALLOW) == []


# ── Validation des plateformes ───────────────────────────────────────────────
def test_unknown_platform_is_refused_not_ignored():
    """Une faute de frappe doit échouer, pas rendre « appliqué » sans rien faire."""
    with pytest.raises(cbs.UnknownPlatformError):
        cbs.validate_platforms(["titkok"])
    with pytest.raises(cbs.UnknownPlatformError):
        cbs.validate_platforms([])
    # …alors que le chemin interne, lui, les ignore (et c'est juste pour un rejeu).
    assert cbs._normalize_categories(["titkok"]) == []


def test_valid_platforms_are_deduplicated_in_catalogue_order():
    assert cbs.validate_platforms(["TIKTOK", " tiktok ", "facebook"]) == [
        "facebook", "tiktok",
    ]


# ── Cloisonnement de la clé ──────────────────────────────────────────────────
MASTER = "master-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
FAI = "fai-key-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
CONTENT = "content-key-eeeeeeeeeeeeeeeeeeeeeeeeeeeeee"


@pytest.fixture
def keys(monkeypatch, settings):
    monkeypatch.setattr(settings, "api_key", MASTER, raising=False)
    monkeypatch.setattr(settings, "fai_api_key", FAI, raising=False)
    monkeypatch.setattr(settings, "content_block_api_key", CONTENT, raising=False)
    return settings


def _route(path: str):
    for route in api_router.routes:
        if getattr(route, "path", None) == path:
            return route
    raise AssertionError(f"route {path} absente du router")


def _dependency_names(route) -> list[str]:
    return [d.dependency.__name__ for d in route.dependencies]


@pytest.mark.parametrize(
    "path", ["/api/v1/content-filter/block", "/api/v1/content-filter/unblock",
             "/api/v1/content-filter/status", "/api/v1/content-filter/platforms"],
)
def test_routes_carry_their_own_scoped_dependency(path):
    assert _dependency_names(_route(path)) == ["require_content_block_client"]


def test_content_key_opens_nothing_else(keys):
    """La clé de filtrage n'est reconnue par aucun autre comparateur.

    Elle ne doit surtout pas ouvrir /fai/block : filtrer TikTok chez un abonné
    et lui couper la ligne entière sont deux pouvoirs distincts.
    """
    assert deps._content_block_api_key_matches(CONTENT) is True
    assert deps._api_key_matches(CONTENT) is False
    assert deps._fai_api_key_matches(CONTENT) is False
    assert deps._lr_verify_api_key_matches(CONTENT) is False
    assert deps._uisp_assign_api_key_matches(CONTENT) is False


def test_other_keys_are_not_silently_accepted(keys):
    for other in (FAI, MASTER, "", None, CONTENT + "x", CONTENT[:-1]):
        assert deps._content_block_api_key_matches(other) is False


def test_unset_content_key_never_means_open_bar(monkeypatch, settings):
    monkeypatch.setattr(settings, "content_block_api_key", "", raising=False)
    assert deps._content_block_api_key_matches("") is False
    assert deps._content_block_api_key_matches(None) is False
    assert deps._content_block_api_key_matches("n'importe quoi") is False


def test_production_refuses_a_content_key_shared_with_another():
    """Deux variables portant la même valeur = cloisonnement annulé en silence."""
    from app.core.config import Settings

    def build(**overrides):
        keys = {
            "api_key": MASTER,
            "fai_api_key": FAI,
            "lr_verify_api_key": "verify-key-cccccccccccccccccccccccccccccccc",
            "uisp_assign_api_key": "assign-key-dddddddddddddddddddddddddddddddd",
            "content_block_api_key": CONTENT,
        }
        keys.update(overrides)
        return Settings(
            app_env="production", postgres_password="a-strong-password", **keys,
        )

    with pytest.raises(ValueError, match="must differ from"):
        build(content_block_api_key=MASTER)
    with pytest.raises(ValueError, match="must differ from"):
        build(content_block_api_key=FAI)
    assert build().content_block_api_key == CONTENT
    # Clé non distribuée : absente, pas « dupliquée ».
    assert build(content_block_api_key="").content_block_api_key == ""


# ── Contenu adulte (18+) : une PSEUDO-plateforme ─────────────────────────────
#
# Le 18+ se demande comme TikTok (« bloque-moi ça chez cet abonné ») mais ne
# marche pas du tout pareil : pas de liste de domaines (des millions de sites,
# intenables sur une radio), donc bascule du résolveur amont du dnsmasq vers un
# résolveur familial, et état stocké dans la colonne booléenne
# `block_adult_content` — JAMAIS dans `blocked_categories`.
#
# D'où ces tests : tout l'enjeu est que les deux dimensions ne se mélangent
# jamais, et chacun des pièges ci-dessous rend un `200 filtre appliqué` avec
# rien de bloqué sur l'abonné — la panne exactement invisible depuis l'API.
ADULT = cbs.ADULT_PLATFORM


class _FakeLr:
    """Juste ce que `set_platform_block` lit avant de déléguer."""

    def __init__(self, categories=None, mode=DENY, adult=False):
        self.name = "LR-TEST"
        self.blocked_categories = categories
        self.content_block_mode = mode
        self.block_adult_content = adult


@pytest.fixture
def captured(monkeypatch):
    """Intercepte `set_content_block` : on teste le ROUTAGE, pas le SSH."""
    calls = {}

    async def _fake(session, lr, categories, mode=None, block_adult=None):
        calls.update(
            categories=categories, mode=mode, block_adult=block_adult, called=True,
        )
        return True, "ok"

    monkeypatch.setattr(cbs, "set_content_block", _fake)
    return calls


def test_adult_is_accepted_as_a_platform_key():
    assert cbs.validate_platforms([" ADULT "]) == [ADULT]


def test_adult_typo_is_still_refused():
    """Élargir le catalogue ne doit pas relâcher la rigueur (cf. « titkok »)."""
    with pytest.raises(cbs.UnknownPlatformError):
        cbs.validate_platforms(["adlut"])


def test_adult_never_enters_the_category_set():
    """⚠️ La garantie structurelle : `adult` n'a pas de domaines.

    S'il entrait dans un ensemble de catégories, `_normalize_categories` le
    jetterait en silence et `content_block_domains_for` rendrait une liste vide
    — l'API répondrait « filtre appliqué » sans avoir rien posé.
    """
    assert cbs._normalize_categories([ADULT]) == []
    assert ADULT not in cbs.VALID_CONTENT_CATEGORIES
    assert cbs.split_adult(["tiktok", ADULT]) == (["tiktok"], True)


@pytest.mark.asyncio
async def test_blocking_adult_on_a_virgin_client_still_reaches_the_device(captured):
    """⚠️ Le piège principal : le court-circuit « rien à retirer ».

    Un client sans aucun filtre a `blocked_categories is None` et une demande
    18+ ne produit AUCUNE catégorie cible. Le raccourci sans-SSH d'origine
    répondait donc ok=True « rien à retirer » — succès annoncé, filtre jamais
    posé, abonné toujours sur les sites adultes.
    """
    ok, _ = await cbs.set_platform_block(
        None, _FakeLr(), [ADULT], blocked=True,
    )
    assert ok
    assert captured["called"] is True
    assert captured["block_adult"] is True
    assert captured["categories"] == []  # aucune catégorie inventée au passage


@pytest.mark.asyncio
async def test_unblocking_everything_on_a_virgin_client_stays_a_no_op(captured):
    """Le raccourci doit survivre : un déblocage à vide n'ouvre pas de session SSH."""
    ok, msg = await cbs.set_platform_block(
        None, _FakeLr(), [ADULT], blocked=False,
    )
    assert ok
    assert "rien à retirer" in msg
    assert not captured  # aucun appel — le client dort peut-être


@pytest.mark.asyncio
async def test_adult_alone_leaves_the_categories_and_direction_untouched(captured):
    """Cumulatif : demander le 18+ ne rejoue pas la direction du client.

    Sur un client en allowlist, re-soumettre sa direction à
    `platform_target_categories` rendrait « denylist » — donc renverserait
    silencieusement la politique de cet abonné.
    """
    lr = _FakeLr(categories=["whatsapp"], mode=ALLOW)
    await cbs.set_platform_block(None, lr, [ADULT], blocked=True)
    assert captured["categories"] == ["whatsapp"]
    assert captured["mode"] is None  # « ne touche pas à la direction »
    assert captured["block_adult"] is True


@pytest.mark.asyncio
async def test_adult_in_allowlist_is_not_refused_as_the_last_platform(captured):
    """Le 409 « dernière plateforme autorisée » ne concerne PAS le 18+.

    Il est orthogonal à la direction : c'est un booléen, il ne vide aucun
    ensemble, donc il ne peut pas rouvrir tout l'internet à l'abonné.
    """
    lr = _FakeLr(categories=["whatsapp"], mode=ALLOW)
    ok, _ = await cbs.set_platform_block(None, lr, [ADULT], blocked=True)
    assert ok
    assert captured["categories"] == ["whatsapp"]  # toujours joignable


@pytest.mark.asyncio
async def test_adult_and_a_category_travel_together_in_one_call(captured):
    lr = _FakeLr(categories=["facebook"], mode=DENY)
    await cbs.set_platform_block(None, lr, ["tiktok", ADULT], blocked=True)
    assert captured["categories"] == ["facebook", "tiktok"]
    assert captured["block_adult"] is True


@pytest.mark.asyncio
async def test_a_category_alone_never_touches_the_adult_flag(captured):
    """`None` = « ne touche pas au 18+ » : bloquer TikTok ne doit pas le lever."""
    lr = _FakeLr(categories=None, mode=DENY, adult=True)
    await cbs.set_platform_block(None, lr, ["tiktok"], blocked=True)
    assert captured["block_adult"] is None


def test_status_reports_adult_from_the_boolean():
    """Sans ça, GET /status dit « rien de bloqué » juste après un POST réussi."""
    assert cbs.effective_blocked_platforms(None, DENY, True) == [ADULT]
    assert cbs.effective_blocked_platforms(["tiktok"], DENY, True) == ["tiktok", ADULT]
    assert cbs.effective_blocked_platforms(None, DENY, False) == []


def test_catalogue_advertises_adult_and_its_mechanism(settings):
    """`domain_count: 0` se lirait comme une clé inerte sans `mechanism`."""
    import asyncio

    from app.api.endpoints.content_filter import list_platforms

    entries = asyncio.run(list_platforms())
    by_key = {e.key: e for e in entries}
    assert by_key[ADULT].domains == []
    assert by_key[ADULT].mechanism == "upstream_resolver"
    assert by_key["tiktok"].mechanism == "domains"
    assert by_key[ADULT].label and by_key[ADULT].description
