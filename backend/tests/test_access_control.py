"""Profils d'accès — ce que ces tests empêchent de casser.

Le système de profils a deux façons de mal tourner, et elles sont opposées :

  1. **Un droit qui ne garde rien** — une route ajoutée sans permission est
     ouverte à tout compte connecté, donc le cloisonnement paraît en place alors
     qu'il ne l'est pas. C'est le pire des deux cas, parce qu'on cesse de
     chercher. `test_every_route_is_gated` refuse une route non gardée qui ne
     figure pas explicitement à l'allowlist.

  2. **Un droit qui garde TROP** — une intégration tierce, un administrateur
     qui se verrouille dehors, un compte dont plus personne ne peut réparer le
     profil. Les garde-fous correspondants sont vérifiés ici plutôt que dans
     l'API : un second chemin d'écriture qui les contournerait ne serait vu par
     aucun test d'endpoint.

⚠️ Le test le plus important est `test_scoped_third_party_keys_bypass_profiles` :
les cinq clés tierces n'ont aucun profil, donc une dépendance de permission
naïve les refuserait — et le système de paiement cesserait de couper les
impayés, en silence, sans que rien d'autre n'échoue.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from app.api import deps
from app.api.router import api_router
from app.core import permissions
from app.core.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_GROUPS,
    PERMISSION_KEYS,
    normalize_permissions,
    unknown_permissions,
)
from app.services import profile_service

# ---------------------------------------------------------------------------
# Le catalogue
# ---------------------------------------------------------------------------

def test_permission_keys_are_unique():
    """Deux permissions homonymes : la seconde masquerait la première en silence."""
    keys = [perm.key for perm in ALL_PERMISSIONS]
    assert len(keys) == len(set(keys)), "clés de permission en double"


def test_every_permission_has_a_label_and_a_description():
    """L'écran d'administration est construit ENTIÈREMENT depuis le catalogue.

    Une permission sans libellé y apparaîtrait comme une case vide : l'admin ne
    saurait pas ce qu'il coche, donc cocherait au hasard ou ne cocherait pas.
    """
    for perm in ALL_PERMISSIONS:
        assert perm.label.strip(), f"{perm.key} sans libellé"
        assert perm.description.strip(), f"{perm.key} sans description"


def test_page_permissions_carry_a_route_and_the_others_do_not():
    """La nature d'une permission porte l'écran ET le contrôle d'arrivée.

    Une PAGE sans route ne pourrait pas être rattachée à un écran ; une ACTION
    ou une DATA avec route se ferait passer pour une page dans le formulaire et
    dans le contrôle d'arrivée d'`AppShell`.
    """
    for perm in ALL_PERMISSIONS:
        if perm.kind is permissions.PermissionKind.PAGE:
            assert perm.route, f"{perm.key} est une page sans route"
        else:
            assert perm.route is None, f"{perm.key} ({perm.kind}) porte une route"


def test_data_permissions_are_enforced_server_side():
    """⚠️ Une permission de nature DATA doit RETIRER la donnée de la réponse.

    C'est toute sa difficulté : contrairement à une page (qu'on refuse) ou à une
    action (qu'on refuse), la donnée voyage dans la MÊME réponse que le reste de
    l'écran. La masquer dans le navigateur la laisserait parfaitement lisible
    dans l'onglet réseau — le droit ne vaudrait alors rien, tout en donnant
    l'impression du contraire.

    Ce test vérifie que chaque clé de nature DATA est bien citée quelque part
    dans les endpoints, via `caller_has_permission`. Il ne prouve pas que le
    retrait est correct, mais il attrape le cas où quelqu'un ajoute une case à
    cocher en ne l'appliquant QUE côté frontend.

    ⚠️ Les permissions marquées `ui_only` sont EXEMPTÉES — ce sont celles dont
    on assume qu'elles ne masquent qu'à l'écran. L'exemption passe par le
    drapeau et non par une liste dans ce fichier : c'est le catalogue qui doit
    porter l'aveu, puisque c'est lui que lit l'administrateur au moment de
    cocher la case.
    """
    endpoints_dir = Path(__file__).resolve().parents[1] / "app" / "api" / "endpoints"
    sources = "\n".join(
        f.read_text(encoding="utf-8") for f in endpoints_dir.glob("*.py")
    )
    for perm in ALL_PERMISSIONS:
        if perm.kind is not permissions.PermissionKind.DATA or perm.ui_only:
            continue
        assert f'"{perm.key}"' in sources, (
            f"{perm.key} est de nature DATA mais n'est appliquée dans aucun "
            "endpoint : la donnée partirait quand même au navigateur."
        )
        assert "caller_has_permission" in sources, (
            "aucun endpoint n'utilise caller_has_permission"
        )


def test_caller_has_permission_never_strips_data_from_an_api_key():
    """Une clé d'API doit continuer de recevoir la réponse ENTIÈRE.

    Une clé est une identité de machine, sans profil. La traiter comme « aucun
    droit » amputerait silencieusement les réponses servies à l'outillage
    d'exploitation et aux intégrations — sans la moindre erreur pour le dire.
    """
    source = textwrap.dedent(inspect.getsource(deps.caller_has_permission))
    assert "auth_via_api_key" in source, (
        "caller_has_permission ne consulte pas le drapeau d'authentification "
        "par clé : les intégrations recevraient des réponses tronquées."
    )


def test_normalize_drops_unknown_keys_but_reject_reports_them():
    """Tolérant en RELECTURE, strict en SAISIE — et la différence est voulue.

    Relire : une clé retirée du catalogue ne doit pas faire échouer le
    chargement du profil des comptes qui la portaient encore.
    Saisir : un `fai.blok` mal orthographié doit être REFUSÉ, sinon l'API répond
    « profil enregistré » sans avoir donné le droit, et le défaut se découvre le
    jour où l'agent ne peut pas travailler.
    """
    submitted = ["fai.block", "fai.blok", "sites.view"]
    assert normalize_permissions(submitted) == ["sites.view", "fai.block"]
    assert unknown_permissions(submitted) == ["fai.blok"]


def test_normalize_orders_by_catalog_not_by_input():
    """Deux profils aux mêmes droits produisent la même valeur en base.

    Sans ça, le même jeu de droits saisi dans un autre ordre s'écrirait
    différemment et tout diff de la colonne deviendrait illisible.
    """
    a = normalize_permissions(["fai.block", "sites.view"])
    b = normalize_permissions(["sites.view", "fai.block"])
    assert a == b


# ---------------------------------------------------------------------------
# Résolution des droits
# ---------------------------------------------------------------------------

class _FakeProfile:
    def __init__(self, permissions_: list[str] | None, is_system: bool = False):
        self.permissions = permissions_
        self.is_system = is_system
        self.name = "Système" if is_system else "Agent"
        self.id = 1


class _FakeUser:
    def __init__(self, profile):
        self.profile = profile
        self.profile_id = getattr(profile, "id", None)
        self.id = 42
        self.username = "agent"
        self.enabled = True


def test_no_profile_means_no_rights_never_all_rights():
    """Le sens SÛR : un compte orphelin ne devient pas administrateur.

    Un compte peut se retrouver sans profil (créé de travers, profil effacé à
    la main en base). Traiter ce cas comme « tous les droits » donnerait le
    contrôle total du superviseur au premier accident de données.
    """
    assert profile_service.effective_permissions(_FakeUser(None)) == frozenset()
    assert profile_service.effective_permissions(None) == frozenset()
    assert not profile_service.has_permission(_FakeUser(None), "sites.view")


def test_system_profile_holds_every_permission_without_reading_its_column():
    """L'administrateur détient TOUT par construction, colonne vide comprise.

    ⚠️ C'est ce qui garantit qu'une permission ajoutée au catalogue lui
    appartient immédiatement. Si ses droits venaient de sa colonne, la première
    permission qu'on oublierait de lui re-cocher pourrait être celle qui ouvre
    l'administration — et plus personne ne pourrait la lui rendre.
    """
    admin = _FakeUser(_FakeProfile(permissions_=[], is_system=True))
    assert profile_service.effective_permissions(admin) == PERMISSION_KEYS
    for perm in ALL_PERMISSIONS:
        assert profile_service.has_permission(admin, perm.key)


def test_has_permission_is_an_or_across_keys():
    """Les lectures PARTAGÉES entre deux pages doivent répondre à l'une ou l'autre.

    `GET /fai-journal` alimente « Demandes de coupure » ET « Journal des
    blocages ». Exiger les deux clés fermerait la route à qui n'a reçu qu'une
    des deux pages, alors qu'il a le droit de la voir.
    """
    user = _FakeUser(_FakeProfile(["fai.requests.view"]))
    assert profile_service.has_permission(user, "fai.journal.view", "fai.requests.view")
    assert not profile_service.has_permission(user, "fai.journal.view", "fai.block")


# ---------------------------------------------------------------------------
# Le cloisonnement des routes
# ---------------------------------------------------------------------------

def _permission_keys_of(route: APIRoute) -> set[str]:
    """Les clés exigées par une route, router-level et route-level confondus."""
    keys: set[str] = set()
    for dep in route.dependant.dependencies:
        call = dep.call
        closure = getattr(call, "__closure__", None) or ()
        for cell in closure:
            value = cell.cell_contents
            if isinstance(value, tuple) and value and all(isinstance(v, str) for v in value):
                keys.update(value)
    return keys


# Routes délibérément ouvertes à TOUT compte connecté. Chaque entrée est une
# décision, pas un oubli — d'où la justification exigée à côté.
_UNGATED_BY_DESIGN = {
    # Public par construction : sonde Docker et supervision externe.
    ("GET", "/api/v1/health"),
    # L'authentification elle-même : gater /auth/login rendrait la connexion
    # impossible, et /auth/me est ce qui SERT les droits au dashboard.
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/change-password"),
    # Version et uptime du scheduler — ni donnée d'abonné ni levier d'action.
    ("GET", "/api/v1/system/info"),
    # Le bandeau d'anomalies est porté par AppShell, donc présent sur toutes les
    # pages : le gater le ferait disparaître pour des profils qui ont pourtant
    # le droit d'être au courant. Seul l'ACQUITTEMENT est un droit.
    ("GET", "/api/v1/manual-alerts"),
    # Section Administration : gardées PAR ROUTE (admin.access en lecture,
    # admin.profiles / admin.users en écriture), pas au niveau du router.
}


def test_the_permission_extractor_actually_sees_keys():
    """Garde-fou du garde-fou : un extracteur muet ferait passer tous les tests.

    `_permission_keys_of` lit les cellules de fermeture de la dépendance. Si sa
    forme changeait, il rendrait l'ensemble vide partout — et
    `test_every_route_is_gated` déclarerait TOUTES les routes non gardées, donc
    échouerait bruyamment. Le cas dangereux est l'inverse : un extracteur qui
    rendrait quelque chose de non vide pour tout. On vérifie donc une clé
    connue, exacte.
    """
    found: set[str] = set()
    for route in api_router.routes:
        if isinstance(route, APIRoute) and route.path == "/api/v1/traffic/throughput":
            found = _permission_keys_of(route)
    assert found == {"traffic.view"}, found


def test_every_route_is_gated():
    """Aucune route ne doit se retrouver ouverte par OUBLI.

    ⚠️ C'est le test le plus important du lot. Une route ajoutée sans
    permission est accessible à tout compte connecté — y compris un profil
    « agent » — alors que l'écran, lui, donne l'impression d'un cloisonnement
    en place. Le défaut est invisible : rien n'échoue, rien n'est journalisé,
    et personne ne le cherche.

    Ajouter une route à `_UNGATED_BY_DESIGN` doit rester un geste conscient,
    justifié à côté de la ligne.
    """
    ungated: list[str] = []
    for route in api_router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in _UNGATED_BY_DESIGN:
                continue
            if not _permission_keys_of(route):
                ungated.append(f"{method} {route.path}")
    assert not ungated, (
        "Routes sans contrôle de droits — ajoute une permission, ou inscris-les "
        "dans _UNGATED_BY_DESIGN avec la raison :\n  " + "\n  ".join(sorted(ungated))
    )


def test_routes_only_reference_permissions_that_exist():
    """Une clé mal orthographiée sur une route fermerait la route à TOUT LE MONDE.

    Personne ne détiendrait `sites.veiw` — pas même l'administrateur, dont les
    droits sont le catalogue. La page deviendrait inaccessible sans qu'aucune
    erreur ne l'explique.
    """
    unknown: set[str] = set()
    for route in api_router.routes:
        if isinstance(route, APIRoute):
            unknown |= _permission_keys_of(route) - PERMISSION_KEYS
    assert not unknown, f"clés inexistantes citées par des routes : {sorted(unknown)}"


def test_scoped_third_party_keys_bypass_profiles():
    """Les cinq intégrations tierces ne portent AUCUN profil — et doivent passer.

    ⚠️ Le piège que ce test verrouille : une dépendance de permission qui
    re-authentifie par `require_user_or_api_key` ne connaît que la clé
    MAÎTRESSE. Le système de paiement, qui présente `FAI_API_KEY`, recevrait
    alors un 401 sur `POST /fai/block` — c.-à-d. qu'aucun impayé ne serait plus
    coupé, sans que rien d'autre n'échoue ni ne le signale.

    Le mécanisme est le drapeau posé par la dépendance de ROUTER
    (`_mark_api_key_auth`), lue par `require_permission`.
    """
    source = textwrap.dedent(inspect.getsource(deps))
    tree = ast.parse(source)

    marked: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("require_")
        ):
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "_mark_api_key_auth"
                ):
                    marked.add(node.name)

    for dependency in (
        "require_user_or_api_key",     # la clé maîtresse
        "require_fai_client",
        "require_verify_client",
        "require_uisp_assign_client",
        "require_content_block_client",
        "require_client_signal_client",
    ):
        assert dependency in marked, (
            f"{dependency} n'appelle pas _mark_api_key_auth : les appels portés "
            "par cette clé seront refusés par le contrôle de droits."
        )


def test_require_permission_short_circuits_on_api_key_auth():
    """La dépendance de permission lit le drapeau AVANT de chercher un profil."""
    source = textwrap.dedent(inspect.getsource(deps.require_permission))
    assert "auth_via_api_key" in source, (
        "require_permission ne consulte pas le drapeau d'authentification par "
        "clé : les intégrations tierces seront refusées."
    )


# ---------------------------------------------------------------------------
# Le frontend cite-t-il des clés qui existent ?
# ---------------------------------------------------------------------------

def test_frontend_permission_keys_all_exist_in_the_catalog():
    """Une faute de frappe dans `lib/permissions.ts` masque un écran EN SILENCE.

    Côté dashboard, `can('sites.veiw')` rend simplement faux : l'entrée de menu
    disparaît pour tout le monde, y compris pour l'administrateur, et aucune
    erreur n'apparaît nulle part. C'est le genre de défaut qu'on met des jours à
    relier à sa cause — d'où ce test, qui relit le fichier TypeScript.
    """
    path = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "permissions.ts"
    if not path.exists():  # dépôt backend seul — rien à vérifier
        pytest.skip("frontend absent de cette copie de travail")

    import re

    text = path.read_text(encoding="utf-8")
    block = text.split("export const PERM", 1)[1].split("} as const", 1)[0]
    cited = set(re.findall(r"'([a-z_]+(?:\.[a-z_]+)+)'", block))

    assert cited, "aucune clé lue dans lib/permissions.ts — le format a changé"
    missing = cited - PERMISSION_KEYS
    assert not missing, (
        "clés citées par le frontend et absentes du catalogue backend : "
        f"{sorted(missing)}"
    )


def test_every_page_permission_is_used_by_the_frontend():
    """Une page gardée côté API mais jamais citée côté menu serait inatteignable.

    L'inverse du test précédent : ici c'est le backend qui aurait pris de
    l'avance. Le symptôme serait une page à laquelle on donne le droit sans que
    l'entrée de menu apparaisse jamais.
    """
    path = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "permissions.ts"
    if not path.exists():
        pytest.skip("frontend absent de cette copie de travail")

    text = path.read_text(encoding="utf-8")
    for group in PERMISSION_GROUPS:
        for perm in group.permissions:
            if perm.kind is permissions.PermissionKind.PAGE:
                assert f"'{perm.key}'" in text, (
                    f"la page {perm.key} n'est citée nulle part dans le frontend : "
                    "son entrée de menu n'apparaîtra jamais"
                )


# ---------------------------------------------------------------------------
# Les garde-fous d'auto-verrouillage
# ---------------------------------------------------------------------------
#
# ⚠️ Ce qu'ils protègent n'est pas une règle métier confortable : un superviseur
# dont plus personne ne peut administrer les comptes se répare à la main, en
# base, sur le serveur de production. Chacun de ces refus est le seul obstacle
# entre un clic et cette situation.

@pytest.mark.asyncio
async def test_the_system_profile_cannot_be_modified():
    """Décocher « Administration » sur le profil système enfermerait tout le monde.

    C'est le geste d'apparence la plus anodine du formulaire — une case parmi
    trente — et celui dont la conséquence est la plus lourde. Le profil système
    n'est donc pas éditable du tout, plutôt que « éditable sauf cette case ».
    """
    profile = _FakeProfile(permissions_=[], is_system=True)
    with pytest.raises(profile_service.ProfileError, match="système"):
        await profile_service.update_profile(None, profile, permissions=[])


@pytest.mark.asyncio
async def test_the_system_profile_cannot_be_deleted():
    """Même raison : plus de profil système, plus d'administrateur possible."""
    profile = _FakeProfile(permissions_=[], is_system=True)
    with pytest.raises(profile_service.ProfileError, match="système"):
        await profile_service.delete_profile(None, profile)


def test_an_admin_cannot_strip_his_own_admin_rights():
    """Le geste reste possible — mais il faut qu'un AUTRE administrateur le fasse.

    Distinct du « dernier administrateur » : ici le système en garde d'autres,
    mais l'auteur du geste perdrait dans la seconde l'écran depuis lequel il
    travaille. Exiger un tiers, c'est aussi garder la trace de qui l'a décidé.
    """
    admin = _FakeUser(_FakeProfile(permissions_=[], is_system=True))
    with pytest.raises(profile_service.ProfileError, match="ton propre"):
        profile_service.assert_not_self_locking(admin, admin, still_admin=False)

    # Rétrograder QUELQU'UN D'AUTRE reste permis.
    other = _FakeUser(_FakeProfile(permissions_=[], is_system=True))
    other.id = 99
    profile_service.assert_not_self_locking(admin, other, still_admin=False)

    # Et se modifier soi-même SANS perdre l'administration ne pose aucun problème.
    profile_service.assert_not_self_locking(admin, admin, still_admin=True)


def test_a_non_admin_editing_himself_is_not_blocked():
    """Le garde-fou ne doit pas déborder sur un compte ordinaire.

    Un agent qui change son propre nom affiché ne retire l'administration à
    personne : le refuser serait une gêne sans contrepartie.
    """
    agent = _FakeUser(_FakeProfile(["sites.view"]))
    profile_service.assert_not_self_locking(agent, agent, still_admin=False)


def test_the_scoped_auth_runs_before_the_permission_guard():
    """L'ORDRE des dépendances est porteur, et son échec serait silencieux ici.

    `permission_guard` laisse passer un appel porté par une clé en lisant le
    drapeau `auth_via_api_key`, que pose la dépendance d'auth du ROUTER. Si le
    garde s'exécutait en PREMIER, le drapeau ne serait pas encore posé : il
    re-authentifierait par `require_user_or_api_key`, qui ne connaît que la clé
    maîtresse, et le système de paiement recevrait un 401 sur `POST /fai/block`.

    FastAPI place aujourd'hui les dépendances de router avant celles de route.
    Rien dans notre code ne le garantit — d'où ce test, qui constate l'ordre
    réel plutôt que de le supposer.
    """
    checked = 0
    for route in api_router.routes:
        if not isinstance(route, APIRoute):
            continue
        names = [d.call.__name__ for d in route.dependant.dependencies]
        if "permission_guard" not in names:
            continue
        auth_names = [n for n in names if n.startswith("require_")]
        if not auth_names:
            continue
        assert names.index(auth_names[0]) < names.index("permission_guard"), (
            f"{route.path} : {auth_names[0]} doit s'exécuter AVANT permission_guard "
            f"(ordre constaté : {names})"
        )
        checked += 1
    assert checked > 20, f"trop peu de routes vérifiées ({checked}) — le test ne prouve rien"


def test_ui_only_permissions_say_so_where_the_admin_reads_them():
    """Un masquage d'affichage doit s'AVOUER dans sa propre description.

    ⚠️ C'est le seul endroit où l'administrateur peut apprendre la différence.
    Il coche une case au milieu de trente autres, et rien à l'écran ne distingue
    un droit applique côté serveur d'un simple retrait d'affichage. Une case qui
    ne le dirait pas ferait croire cloisonné ce qui ne l'est pas — et on
    cesserait de chercher.

    Le drapeau `ui_only` est publié par l'API pour que le formulaire puisse le
    signaler ; ce test verrouille la mention dans le texte lui-même, qui reste
    lisible meme si le rendu change.
    """
    for perm in ALL_PERMISSIONS:
        if not perm.ui_only:
            continue
        assert "affichage" in perm.description.lower(), (
            f"{perm.key} masque seulement à l'écran mais sa description ne le "
            "dit pas : l'administrateur la croira appliquée côté serveur."
        )


def test_a_data_permission_is_enforced_unless_it_declares_otherwise():
    """Le défaut est l'application SERVEUR ; l'exception doit être explicite.

    `ui_only` vaut False par défaut, donc une nouvelle permission `DATA` ajoutée
    sans y penser tombe dans `test_data_permissions_are_enforced_server_side` et
    fait échouer la suite tant qu'elle n'est pas appliquée. C'est le bon sens de
    l'oubli : on n'obtient un masquage de façade qu'en le demandant.
    """
    from app.core.permissions import Permission, PermissionKind

    sample = Permission(key="x.y", label="X", description="d", kind=PermissionKind.DATA)
    assert sample.ui_only is False


def test_ui_only_permissions_are_wired_in_the_frontend():
    """⚠️ Une case `ui_only` sans clé côté dashboard ne ferait STRICTEMENT RIEN.

    C'est le pendant exact de `test_data_permissions_are_enforced_server_side`,
    et la symétrie n'est pas décorative :

      - un droit DATA **applique côté serveur** ne doit PAS être cité dans le
        frontend — le composant réagit à l'ABSENCE de la donnée (`stats === null`).
        Redire la règle la mettrait à deux endroits qui divergeraient, et c'est
        la version frontend qui serait la fausse ;
      - un droit **`ui_only`** DOIT l'être, puisque rien dans la réponse ne le
        signale. Sans sa clé dans `lib/permissions.ts`, l'administrateur coche
        une case qui n'a aucun effet — et il n'a AUCUN moyen de s'en apercevoir
        autrement qu'en comparant deux écrans côte à côte.
    """
    path = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "permissions.ts"
    if not path.exists():
        pytest.skip("frontend absent de cette copie de travail")

    text_ = path.read_text(encoding="utf-8")
    for perm in ALL_PERMISSIONS:
        if not perm.ui_only:
            continue
        assert f"'{perm.key}'" in text_, (
            f"{perm.key} masque seulement à l'écran mais n'est cité nulle part "
            "dans lib/permissions.ts : la case cochée n'aurait aucun effet."
        )
