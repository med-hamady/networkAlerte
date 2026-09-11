"""Le contrat de POST /uisp/assign — ce que l'équipe d'automatisation consomme.

Demandes du 2026-09-11, chacune verrouillée ici :

1. **Rattachement dans le même appel** — après la pose de clé, attendre que
   l'équipement se déclare au contrôleur (60 s max) puis l'associer ; sinon
   `pending_registration` + `retry_after_seconds`. Le 10/09, un équipement
   adopté en 9 s avait reçu `pending_registration` sans que le rattachement ne
   soit même tenté.
2. **Format stable** — `assigned`, `pending_registration`,
   `retry_after_seconds` et `error_code` dans TOUTES les réponses, un
   `error_code` stable sur chaque erreur. Additif : le `detail` historique et
   les statuts HTTP sont conservés.
3. **`force: true`** — un équipement déjà rattaché à un autre client est refusé
   en 409 `device_already_assigned`, détenteur nommé, sauf `force`.

⚠️ Si un test de ce fichier casse, c'est le contrat d'un TIERS qui change :
mettre à jour l'historique de docs/api-uisp-assign.md et le prévenir AVANT de
déployer — pas après qu'il l'a découvert en production.
"""

import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.api.endpoints import uisp_assign
from app.api.router import api_router
from app.core.exceptions import register_exception_handlers
from app.db.session import get_db
from app.services import uisp_assignment_service as svc
from app.services import uisp_service

REPO_ROOT = Path(__file__).resolve().parents[2]
MAC_IN = "78:45:58:0B:BC:76"
BODY = {"mac": MAC_IN, "crm_client_id": "1361"}
ASSIGN_KEY = "assign-key-dddddddddddddddddddddddddddddddd"
CONTRACT_KEYS = ("assigned", "pending_registration", "retry_after_seconds", "error_code")

# Deux clients CRM HOMONYMES — le cas réel « Ba, Amadou » (1361 et 1369). Rien ne
# les distingue par le nom : c'est exactement pourquoi le contrat parle en ids.
SITE_TARGET = {
    "identification": {"id": "site-1361"},
    "ucrm": {"client": {"id": "1361", "name": "Ba, Amadou"}, "service": {"id": "501", "name": "20Mb"}},
}
SITE_OTHER = {
    "identification": {"id": "site-1369"},
    "ucrm": {"client": {"id": "1369", "name": "Ba, Amadou"}, "service": {"id": "502", "name": "20Mb"}},
}
SITE_TARGET_2ND_SERVICE = {
    "identification": {"id": "site-1361-b"},
    "ucrm": {"client": {"id": "1361", "name": "Ba, Amadou"}, "service": {"id": "503", "name": "20Mb"}},
}
SITES = [SITE_TARGET, SITE_OTHER]


def _device(site_id: str | None = None) -> dict:
    return {
        "identification": {
            "id": "dev-1",
            "mac": MAC_IN,
            "site": {"id": site_id} if site_id else None,
        },
    }


class FakeUISP:
    """Contrôleur factice. `script` = ce que rend chaque appel à `fetch_devices`
    (le dernier élément se répète ; une exception est levée telle quelle)."""

    def __init__(self, script: list, sites: list[dict] | None = None):
        self.script = list(script)
        self.sites = SITES if sites is None else sites
        self.device_calls = 0
        self.assigned: list[tuple[str, str]] = []

    async def fetch_sites(self) -> list[dict]:
        return self.sites

    async def fetch_devices(self, role: str | None = None) -> list[dict]:
        step = self.script[min(self.device_calls, len(self.script) - 1)]
        self.device_calls += 1
        if isinstance(step, BaseException):
            raise step
        return step

    async def assign_device_to_site(self, device_id: str, site_id: str) -> dict:
        self.assigned.append((device_id, site_id))
        return {}


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj


class FakeSession:
    """Session factice : rend `lr` à la recherche par MAC, compte les rollbacks."""

    def __init__(self, lr=None, *, with_lr: bool = True):
        self.lr = lr if lr is not None else (object() if with_lr else None)
        self.rollbacks = 0

    async def execute(self, _stmt):
        return _Result(self.lr)

    async def commit(self):
        pass

    async def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def fast_wait(monkeypatch):
    """Mêmes règles d'attente, à une échelle de temps de test."""
    monkeypatch.setattr(svc, "REGISTRATION_POLL_S", 0.01)
    monkeypatch.setattr(svc, "REGISTRATION_WAIT_S", 0.3)


def _wire(monkeypatch, script: list, *, sites=None, enroll_ok: bool = True) -> FakeUISP:
    fake = FakeUISP(script, sites)
    monkeypatch.setattr(svc, "_client", lambda: fake)

    async def fake_enroll(_session, _lr):
        return enroll_ok, ("adopté par le contrôleur" if enroll_ok else "SSH refusé")

    monkeypatch.setattr(svc.uisp_enrollment_service, "enroll_lr", fake_enroll)
    return fake


# ═══ 1. Rattachement dans le même appel ═════════════════════════════════════


async def test_waits_for_registration_then_assigns_in_the_same_call(monkeypatch, fast_wait):
    """Le cas du 10/09 : absent, clé posée, déclaré peu après → associé DANS l'appel."""
    # appel 0 = recherche initiale (absent) ; 1 = 1re lecture de l'attente ; 2 = présent
    fake = _wire(monkeypatch, [[], [], [_device()]])

    report = await svc.assign_device_to_crm_client(FakeSession(), MAC_IN, "1361")

    assert report["assigned"] is True
    assert report["pending_registration"] is False
    assert report["retry_after_seconds"] is None
    assert report["key_injected"] is True
    assert fake.assigned == [("dev-1", "site-1361")]
    assert [s["step"] for s in report["steps"]] == [
        "resolve_target", "inject_key", "await_registration", "assign",
    ]


async def test_registration_not_seen_in_time_returns_pending_with_retry_after(monkeypatch, fast_wait):
    """Hors délai : on le DIT (pending + quand rejouer), on n'associe rien au hasard."""
    monkeypatch.setattr(svc, "REGISTRATION_WAIT_S", 0.05)
    fake = _wire(monkeypatch, [[]])

    report = await svc.assign_device_to_crm_client(FakeSession(), MAC_IN, "1361")

    assert report["assigned"] is False
    assert report["pending_registration"] is True
    assert report["retry_after_seconds"] == svc.RETRY_AFTER_S
    assert fake.assigned == []
    assert fake.device_calls >= 2, "l'attente doit avoir réellement interrogé le contrôleur"


async def test_a_controller_hiccup_during_the_wait_does_not_fail_the_call(monkeypatch, fast_wait):
    """La clé est posée, l'équipement adopté : une lecture ratée n'est pas un 502."""
    fake = _wire(monkeypatch, [[], httpx.ConnectError("contrôleur injoignable"), [_device()]])

    report = await svc.assign_device_to_crm_client(FakeSession(), MAC_IN, "1361")

    assert report["assigned"] is True
    assert fake.assigned == [("dev-1", "site-1361")]


def test_the_wait_never_outlives_the_call_budget(monkeypatch):
    """L'attente est rognée par le budget GLOBAL de l'appel, pas seulement par ses 60 s."""
    monkeypatch.setattr(svc, "REGISTRATION_WAIT_S", 60)
    monkeypatch.setattr(svc, "CALL_BUDGET_S", 100)

    # Pose de clé rapide (10 s) : l'attente dispose de ses 60 s pleines.
    assert svc.registration_deadline(started=0.0, now=10.0) == 70.0
    # Pose de clé lente (55 s) : 60 s de plus feraient 115 s — rognée à 100.
    assert svc.registration_deadline(started=0.0, now=55.0) == 100.0
    # Budget déjà épuisé : aucune attente, l'appelant reçoit retry_after_seconds.
    assert svc.registration_deadline(started=0.0, now=100.0) <= 100.0


def test_the_call_budget_fits_under_the_proxy_timeout():
    """`CALL_BUDGET_S` et le `proxy_read_timeout` de nginx vivent dans deux fichiers.

    Si le budget venait à dépasser le proxy (ou le proxy à descendre sous le
    budget), l'appelant recevrait un 504 sur une association RÉUSSIE — le piège
    déjà corrigé trois fois. Il faut au moins 15 s de marge pour l'écriture
    finale au contrôleur.
    """
    conf = REPO_ROOT / "nginx" / "nginx.conf"
    if not conf.exists():
        pytest.skip("nginx.conf hors de portée (tests lancés dans le conteneur backend)")
    block = re.search(
        r"location \^~ /api/v1/uisp/assign \{(.*?)\}", conf.read_text(encoding="utf-8"), re.S,
    )
    assert block, "location /api/v1/uisp/assign introuvable dans nginx.conf"
    timeout = int(re.search(r"proxy_read_timeout\s+(\d+)s;", block.group(1)).group(1))
    assert timeout >= svc.CALL_BUDGET_S + 15, (
        f"proxy_read_timeout {timeout}s ne couvre pas CALL_BUDGET_S={svc.CALL_BUDGET_S}s "
        f"+ 15 s de marge — le proxy couperait des associations réussies"
    )


# ═══ 3. Équipement déjà rattaché : refus sauf `force` ═══════════════════════


async def test_a_device_owned_by_another_client_is_refused_and_names_the_owner(monkeypatch):
    """Le détenteur est nommé par son id (qui l'identifie) ET son nom CRM."""
    fake = _wire(monkeypatch, [[_device(site_id="site-1369")]])

    with pytest.raises(svc.AlreadyAssignedError) as err:
        await svc.assign_device_to_crm_client(FakeSession(), MAC_IN, "1361")

    assert err.value.error_code == "device_already_assigned"
    assert err.value.current_crm_client_id == "1369"
    # Même nom que la cible (homonymes) : seul l'id dit qu'il s'agit d'un AUTRE client.
    assert err.value.current_client_name == "Ba, Amadou"
    assert fake.assigned == [], "un refus n'écrit rien au contrôleur"


async def test_force_moves_a_device_owned_by_another_client(monkeypatch):
    fake = _wire(monkeypatch, [[_device(site_id="site-1369")]])

    report = await svc.assign_device_to_crm_client(FakeSession(), MAC_IN, "1361", reassign=True)

    assert report["assigned"] is True
    assert fake.assigned == [("dev-1", "site-1361")]


async def test_the_refusal_also_applies_to_a_device_found_after_registration(monkeypatch, fast_wait):
    """Même règle que l'équipement soit trouvé d'emblée ou juste après s'être déclaré."""
    fake = _wire(monkeypatch, [[], [_device(site_id="site-1369")]])

    with pytest.raises(svc.AlreadyAssignedError):
        await svc.assign_device_to_crm_client(FakeSession(), MAC_IN, "1361")
    assert fake.assigned == []


# ═══ 2. Format stable ═══════════════════════════════════════════════════════


async def test_contract_keys_are_present_on_the_no_op_path(monkeypatch):
    """`pending_registration` n'apparaissait autrefois QUE lorsqu'il valait vrai."""
    fake = _wire(monkeypatch, [[_device(site_id="site-1361")]])

    report = await svc.assign_device_to_crm_client(FakeSession(), MAC_IN, "1361")

    assert report["assigned"] is True
    assert report["pending_registration"] is False
    assert report["retry_after_seconds"] is None
    assert fake.assigned == [], "déjà chez le bon client : aucune écriture"


@pytest.mark.parametrize(
    "crm_client_id, crm_service_id, sites, with_lr, enroll_ok, code",
    [
        ("9999", None, SITES, True, True, "crm_client_not_found"),
        # 502 appartient au client 1369 : l'inversion d'ids que le contrôle attrape
        ("1361", "502", SITES, True, True, "crm_service_mismatch"),
        ("1361", None, [SITE_TARGET, SITE_TARGET_2ND_SERVICE], True, True, "multiple_services"),
        ("1361", None, SITES, False, True, "device_not_found"),
        ("1361", None, SITES, True, False, "device_unreachable"),
    ],
)
async def test_every_service_error_carries_a_contract_code(
    monkeypatch, crm_client_id, crm_service_id, sites, with_lr, enroll_ok, code,
):
    _wire(monkeypatch, [[]], sites=sites, enroll_ok=enroll_ok)

    with pytest.raises(svc.AssignmentError) as err:
        await svc.assign_device_to_crm_client(
            FakeSession(with_lr=with_lr), MAC_IN, crm_client_id, crm_service_id,
        )

    assert err.value.error_code == code
    assert code in uisp_assign.ERROR_CODES, f"{code} émis par le service mais absent du contrat"


def test_the_error_code_table_is_frozen():
    """Modifier cette table, c'est changer le contrat d'un tiers.

    Ajouter un code, en retirer un, ou changer un statut HTTP : mettre à jour
    l'historique de docs/api-uisp-assign.md et prévenir l'équipe AVANT de
    déployer. Ce test existe pour que ce ne soit jamais un effet de bord.
    """
    assert uisp_assign.ERROR_CODES == {
        "invalid_mac": 400,
        "uisp_not_configured": 400,
        "unauthorized": 401,
        "uisp_write_forbidden": 403,
        "crm_client_not_found": 404,
        "crm_service_mismatch": 404,
        "device_not_found": 404,
        "multiple_services": 409,
        "device_already_assigned": 409,
        "invalid_request": 422,
        "internal_error": 500,
        "device_unreachable": 502,
        "uisp_error": 502,
    }


def test_the_integration_doc_lists_every_error_code():
    """Un code que la doc ne cite pas est un code que l'appelant ne sait pas traiter."""
    doc = REPO_ROOT / "docs" / "api-uisp-assign.md"
    if not doc.exists():
        pytest.skip("docs/ hors de portée (tests lancés dans le conteneur backend)")
    text = doc.read_text(encoding="utf-8")
    missing = [c for c in uisp_assign.ERROR_CODES if f"`{c}`" not in text]
    assert not missing, f"codes absents de docs/api-uisp-assign.md : {missing}"


# ═══ Par HTTP — la route telle que l'appelant la voit ═══════════════════════


@pytest.fixture
def http(monkeypatch, settings):
    monkeypatch.setattr(settings, "uisp_assign_api_key", ASSIGN_KEY, raising=False)
    monkeypatch.setattr(settings, "api_key", "master-key-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", raising=False)
    monkeypatch.setattr(svc, "is_configured", lambda: True)
    session = FakeSession()
    app = FastAPI()
    app.include_router(api_router)
    register_exception_handlers(app)

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db
    return app, session


async def _call(app, method: str, path: str, *, key: str | None = ASSIGN_KEY, **kw):
    headers = {"X-API-Key": key} if key else {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        return await client.request(method, path, headers=headers, **kw)


async def test_over_http_a_device_that_registers_in_time_is_assigned_in_one_call(
    http, monkeypatch, fast_wait,
):
    app, session = http
    fake = _wire(monkeypatch, [[], [_device()]])

    r = await _call(app, "POST", "/api/v1/uisp/assign", json=BODY)

    assert r.status_code == 200
    body = r.json()
    for key in CONTRACT_KEYS:
        assert key in body, f"clé du contrat absente : {key}"
    assert body["assigned"] is True
    assert body["pending_registration"] is False
    assert body["retry_after_seconds"] is None
    assert body["error_code"] is None
    assert fake.assigned == [("dev-1", "site-1361")]
    assert session.rollbacks == 0


async def test_over_http_pending_carries_retry_after(http, monkeypatch, fast_wait):
    app, _ = http
    monkeypatch.setattr(svc, "REGISTRATION_WAIT_S", 0.05)
    _wire(monkeypatch, [[]])

    r = await _call(app, "POST", "/api/v1/uisp/assign", json=BODY)

    assert r.status_code == 200
    body = r.json()
    assert body["assigned"] is False
    assert body["pending_registration"] is True
    assert body["retry_after_seconds"] == svc.RETRY_AFTER_S
    assert body["error_code"] is None


@pytest.mark.parametrize(
    "exc, code",
    [
        (ValueError("Invalid MAC address: 'x'"), "invalid_mac"),
        (svc.AssignmentError("pas de client", "crm_client_not_found"), "crm_client_not_found"),
        (svc.AssignmentError("service d'un autre", "crm_service_mismatch"), "crm_service_mismatch"),
        (svc.AssignmentError("inconnu", "device_not_found"), "device_not_found"),
        (svc.AmbiguousClientError("2 services", [{"crm_service_id": "501"}]), "multiple_services"),
        (svc.AlreadyAssignedError("déjà rattaché", "1369", "Ba, Amadou"), "device_already_assigned"),
        (svc.DeviceUnreachableError("SSH refusé"), "device_unreachable"),
        (uisp_service.UISPAuthError("token en lecture"), "uisp_write_forbidden"),
        (RuntimeError("contrôleur tombé"), "uisp_error"),
    ],
)
async def test_every_error_leaves_in_the_contract_envelope(http, monkeypatch, exc, code):
    app, session = http

    async def boom(*_a, **_k):
        raise exc

    monkeypatch.setattr(svc, "assign_device_to_crm_client", boom)

    r = await _call(app, "POST", "/api/v1/uisp/assign", json=BODY)

    assert r.status_code == uisp_assign.ERROR_CODES[code]
    body = r.json()
    assert body["error_code"] == code
    assert body["assigned"] is False
    assert body["pending_registration"] is False
    assert body["retry_after_seconds"] is None
    assert body["message"]
    assert "detail" in body, "le champ historique `detail` doit survivre à l'enveloppe"
    # Une erreur annule la transaction, comme quand la route levait une HTTPException.
    assert session.rollbacks == 1


async def test_409_device_already_assigned_names_the_owner_and_keeps_the_old_detail(
    http, monkeypatch,
):
    app, _ = http

    async def boom(*_a, **_k):
        raise svc.AlreadyAssignedError("déjà rattaché", "1369", "Ba, Amadou")

    monkeypatch.setattr(svc, "assign_device_to_crm_client", boom)

    body = (await _call(app, "POST", "/api/v1/uisp/assign", json=BODY)).json()

    assert body["current_crm_client_id"] == "1369"
    assert body["current_client_name"] == "Ba, Amadou"
    # À l'identique d'avant : qui lisait detail.current_crm_client_id continue.
    assert body["detail"] == {"message": "déjà rattaché", "current_crm_client_id": "1369"}


@pytest.mark.parametrize(
    "extra, expected",
    [({}, False), ({"force": True}, True), ({"reassign": True}, True)],
)
async def test_force_and_its_legacy_name_both_authorize_a_move(http, monkeypatch, extra, expected):
    """`force` est le nom demandé ; `reassign` reste accepté — le retirer casserait qui l'utilise."""
    app, _ = http
    seen: dict = {}

    async def spy(_db, _mac, _crm, _service, reassign=False):
        seen["reassign"] = reassign
        return {"assigned": True, "pending_registration": False, "retry_after_seconds": None}

    monkeypatch.setattr(svc, "assign_device_to_crm_client", spy)

    await _call(app, "POST", "/api/v1/uisp/assign", json={**BODY, **extra})

    assert seen["reassign"] is expected


async def test_a_refused_key_answers_in_the_envelope(http):
    """Le 401 est levé AVANT l'endpoint (dépendance d'auth) : c'est le cas que
    `_StableErrorRoute` existe pour couvrir."""
    app, _ = http

    r = await _call(app, "POST", "/api/v1/uisp/assign", key="mauvaise-cle", json=BODY)

    assert r.status_code == 401
    body = r.json()
    assert body["error_code"] == "unauthorized"
    assert body["detail"] == "Authentication required"
    assert r.headers.get("www-authenticate"), "l'en-tête standard du 401 doit survivre"


async def test_an_invalid_body_answers_in_the_envelope(http):
    app, _ = http

    r = await _call(app, "POST", "/api/v1/uisp/assign", json={"mac": MAC_IN})  # sans crm_client_id

    assert r.status_code == 422
    body = r.json()
    assert body["error_code"] == "invalid_request"
    assert isinstance(body["detail"], list) and body["detail"], "le champ en cause doit être nommé"


async def test_the_envelope_is_local_to_this_route(http):
    """Le reste de l'API garde le format d'erreur FastAPI natif — rien ne bouge ailleurs."""
    app, _ = http

    r = await _call(
        app, "GET", "/api/v1/client-signal", key=None, params={"mac": "aa:bb:cc:dd:ee:ff"},
    )

    assert r.status_code == 401
    assert r.json() == {"detail": "Authentication required"}
