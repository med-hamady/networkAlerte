"""Suppression / remplacement d'antenne (S-Ticketing) — ce qui ne doit jamais casser.

Une suppression ne se rattrape pas. Chaque test verrouille un des garde-fous de
`antenna_lifecycle_service` :

* une MAC d'INFRA n'est jamais supprimée (une MAC de Rocket dans un ticket
  arracherait un secteur) ;
* une antenne EN LIGNE est refusée sauf `force` ;
* un client BLOQUÉ est refusé sans dérogation (le blocage vit sur sa ligne) ;
* l'ancienne antenne d'un remplacement doit appartenir au client annoncé ;
* le remplacement RATTACHE avant de SUPPRIMER : une nouvelle antenne pas encore
  déclarée laisse l'ancienne en place ;
* UISP d'abord, la base ensuite.

Plus le cloisonnement de la clé et la marge sous le proxy nginx.
"""

import re
from pathlib import Path

import pytest

from app.api import deps
from app.api.endpoints import antennas
from app.api.router import api_router
from app.models.device import Lr, Rocket
from app.services import antenna_lifecycle_service as svc
from app.services import uisp_assignment_service, uisp_service

REPO_ROOT = Path(__file__).resolve().parents[2]
OLD = "78:45:58:0b:bc:76"
NEW = "78:45:58:0b:bc:77"
CLIENT = "1361"

SITE_CLIENT = {
    "identification": {"id": "site-1361"},
    "ucrm": {"client": {"id": CLIENT, "name": "Ba, Amadou"}, "service": {"id": "501"}},
}
SITE_OTHER = {
    "identification": {"id": "site-1369"},
    "ucrm": {"client": {"id": "1369", "name": "Ba, Amadou"}, "service": {"id": "502"}},
}


def _station(mac: str, *, uid: str, site: str | None = "site-1361", status: str = "disconnected"):
    return {
        "identification": {
            "id": uid, "mac": mac, "type": "airMax", "role": "station", "model": "LBE-5AC-Gen2",
            "site": {"id": site} if site else None,
        },
        "overview": {"status": status, "wirelessMode": "sta-ptmp"},
    }


class FakeUISP:
    def __init__(self, devices: list[dict], sites: list[dict] | None = None, *, delete_raises=None):
        self.devices = devices
        self.sites = [SITE_CLIENT, SITE_OTHER] if sites is None else sites
        self.deleted: list[str] = []
        self.delete_raises = delete_raises

    async def fetch_devices(self, role=None):
        return self.devices

    async def fetch_sites(self):
        return self.sites

    async def delete_device(self, device_id):
        if self.delete_raises:
            raise self.delete_raises
        self.deleted.append(device_id)
        return True


class FakeSession:
    def __init__(self, uisp: FakeUISP):
        self.uisp = uisp
        self.deleted: list = []

    async def delete(self, obj):
        # UISP d'abord : au moment où la base supprime, le contrôleur l'a fait.
        assert self.uisp.deleted or not any(
            uisp_assignment_service._mac_of(d) == obj.mac_address for d in self.uisp.devices
        ), "la base a été supprimée AVANT UISP"
        self.deleted.append(obj)

    async def flush(self):
        pass


def _lr(mac: str, **kw) -> Lr:
    lr = Lr(name=f"LR {mac[-2:]}", mac_address=mac, device_type="lr", status=kw.pop("status", "down"))
    lr.client_blocked = kw.pop("client_blocked", False)
    lr.blocked_categories = kw.pop("blocked_categories", None)
    lr.block_adult_content = kw.pop("block_adult_content", False)
    lr.uisp_crm_client_id = kw.pop("uisp_crm_client_id", None)
    lr.client_blocked_reason = None
    return lr


def _wire(monkeypatch, uisp: FakeUISP, local: dict, assign=None):
    monkeypatch.setattr(uisp_assignment_service, "_client", lambda: uisp)

    async def by_mac(_session, mac):
        return local.get(mac)

    monkeypatch.setattr(svc, "_local_device_by_mac", by_mac)
    calls: list[dict] = []

    async def fake_assign(session, mac, crm_client_id, crm_service_id=None, reassign=False, started=None):
        calls.append({"mac": mac, "crm_client_id": crm_client_id, "crm_service_id": crm_service_id})
        result = assign or {"assigned": True, "pending_registration": False, "retry_after_seconds": None}
        return {"steps": [], "client_name": "Ba, Amadou", "crm_service_id": crm_service_id, **result}

    monkeypatch.setattr(uisp_assignment_service, "assign_device_to_crm_client", fake_assign)
    return calls


# ═══ Suppression ════════════════════════════════════════════════════════════


async def test_delete_removes_from_uisp_then_database(monkeypatch):
    uisp = FakeUISP([_station(OLD, uid="dev-old")])
    lr = _lr(OLD)
    _wire(monkeypatch, uisp, {OLD: lr})
    session = FakeSession(uisp)

    report = await svc.delete_antenna(session, "78-45-58-0b-bc-76")

    assert uisp.deleted == ["dev-old"]
    assert session.deleted == [lr]
    assert report["deleted"] and report["removed_from_uisp"] and report["removed_from_database"]


async def test_delete_unknown_everywhere_is_404_not_a_fake_success(monkeypatch):
    _wire(monkeypatch, FakeUISP([]), {})
    with pytest.raises(svc.AntennaError) as err:
        await svc.delete_antenna(FakeSession(FakeUISP([])), OLD)
    assert err.value.error_code == "device_not_found"


async def test_delete_refuses_an_infra_device(monkeypatch):
    uisp = FakeUISP([])
    rocket = Rocket(name="A2-AT1-NORD", mac_address=OLD, device_type="rocket")
    _wire(monkeypatch, uisp, {OLD: rocket})
    with pytest.raises(svc.AntennaError) as err:
        await svc.delete_antenna(FakeSession(uisp), OLD)
    assert err.value.error_code == "not_a_client_antenna"
    assert uisp.deleted == []


async def test_delete_refuses_an_af60_known_only_to_uisp(monkeypatch):
    """Un AF60 annonce role=station à un bout de chaque lien : c'est le modèle qui tranche."""
    af60 = _station(OLD, uid="dev-af60")
    af60["identification"].update(type="airFiber", model="AF60-LR")
    uisp = FakeUISP([af60])
    _wire(monkeypatch, uisp, {})
    with pytest.raises(svc.AntennaError) as err:
        await svc.delete_antenna(FakeSession(uisp), OLD)
    assert err.value.error_code == "not_a_client_antenna"
    assert uisp.deleted == []


@pytest.mark.parametrize("ours,theirs", [("up", "disconnected"), ("down", "active")])
async def test_delete_refuses_an_online_antenna_unless_forced(monkeypatch, ours, theirs):
    uisp = FakeUISP([_station(OLD, uid="dev-old", status=theirs)])
    _wire(monkeypatch, uisp, {OLD: _lr(OLD, status=ours)})
    with pytest.raises(svc.AntennaError) as err:
        await svc.delete_antenna(FakeSession(uisp), OLD)
    assert err.value.error_code == "device_online"
    assert uisp.deleted == []

    report = await svc.delete_antenna(FakeSession(uisp), OLD, force=True)
    assert report["deleted"]


async def test_delete_refuses_a_blocked_client_even_with_force(monkeypatch):
    uisp = FakeUISP([_station(OLD, uid="dev-old")])
    _wire(monkeypatch, uisp, {OLD: _lr(OLD, client_blocked=True)})
    with pytest.raises(svc.AntennaError) as err:
        await svc.delete_antenna(FakeSession(uisp), OLD, force=True)
    assert err.value.error_code == "client_blocked"
    assert uisp.deleted == []


async def test_uisp_refusal_leaves_the_database_untouched(monkeypatch):
    uisp = FakeUISP([_station(OLD, uid="dev-old")], delete_raises=uisp_service.UISPAuthError("403"))
    _wire(monkeypatch, uisp, {OLD: _lr(OLD)})
    session = FakeSession(uisp)
    with pytest.raises(uisp_service.UISPAuthError):
        await svc.delete_antenna(session, OLD)
    assert session.deleted == []


# ═══ Remplacement ═══════════════════════════════════════════════════════════


async def test_replace_assigns_new_then_deletes_old_on_the_same_service(monkeypatch):
    uisp = FakeUISP([_station(OLD, uid="dev-old"), _station(NEW, uid="dev-new", site=None)])
    old_lr = _lr(OLD)
    calls = _wire(monkeypatch, uisp, {OLD: old_lr})
    session = FakeSession(uisp)

    report = await svc.replace_antenna(session, CLIENT, OLD, NEW)

    # Le service de l'ancienne antenne est repris : pas de 409 multiple_services.
    assert calls == [{"mac": NEW, "crm_client_id": CLIENT, "crm_service_id": "501"}]
    assert uisp.deleted == ["dev-old"] and session.deleted == [old_lr]
    assert report["replaced"] and report["new_assigned"]


async def test_replace_keeps_the_old_antenna_while_the_new_one_is_pending(monkeypatch):
    uisp = FakeUISP([_station(OLD, uid="dev-old")])
    _wire(monkeypatch, uisp, {OLD: _lr(OLD)}, assign={
        "assigned": False, "pending_registration": True, "retry_after_seconds": 30,
    })
    session = FakeSession(uisp)

    report = await svc.replace_antenna(session, CLIENT, OLD, NEW)

    assert not report["replaced"] and report["pending_registration"]
    assert report["retry_after_seconds"] == 30
    assert uisp.deleted == [] and session.deleted == []


async def test_replace_refuses_an_old_antenna_owned_by_another_client(monkeypatch):
    uisp = FakeUISP([_station(OLD, uid="dev-old", site="site-1369")])
    calls = _wire(monkeypatch, uisp, {OLD: _lr(OLD)})
    with pytest.raises(svc.AntennaError) as err:
        await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW, force=True)
    assert err.value.error_code == "old_device_not_owned"
    assert err.value.extra["current_crm_client_id"] == "1369"
    assert calls == [] and uisp.deleted == []


async def test_replace_falls_back_on_our_record_for_ownership(monkeypatch):
    uisp = FakeUISP([])
    _wire(monkeypatch, uisp, {OLD: _lr(OLD, uisp_crm_client_id=CLIENT)})
    report = await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW)
    assert report["replaced"] and report["removed_from_database"]


async def test_replace_refuses_when_ownership_cannot_be_established(monkeypatch):
    uisp = FakeUISP([])
    _wire(monkeypatch, uisp, {OLD: _lr(OLD)})
    with pytest.raises(svc.AntennaError) as err:
        await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW)
    assert err.value.error_code == "old_device_owner_unknown"


@pytest.mark.parametrize("kw,code", [
    ({"client_blocked": True}, "client_blocked"),
    ({"blocked_categories": ["tiktok"]}, "content_filter_active"),
    ({"block_adult_content": True}, "content_filter_active"),
])
async def test_replace_refuses_a_blocked_or_filtered_client(monkeypatch, kw, code):
    uisp = FakeUISP([_station(OLD, uid="dev-old")])
    calls = _wire(monkeypatch, uisp, {OLD: _lr(OLD, **kw)})
    with pytest.raises(svc.AntennaError) as err:
        await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW, force=True)
    assert err.value.error_code == code
    assert calls == []


async def test_replace_refuses_an_online_old_antenna_unless_forced(monkeypatch):
    uisp = FakeUISP([_station(OLD, uid="dev-old", status="active")])
    _wire(monkeypatch, uisp, {OLD: _lr(OLD)})
    with pytest.raises(svc.AntennaError) as err:
        await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW)
    assert err.value.error_code == "device_online"
    assert (await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW, force=True))["replaced"]


async def test_replace_replay_after_success_is_a_success(monkeypatch):
    """Ancienne déjà supprimée, nouvelle déjà chez ce client : le rejeu réussit."""
    uisp = FakeUISP([_station(NEW, uid="dev-new")])
    _wire(monkeypatch, uisp, {})
    report = await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW)
    assert report["replaced"] and uisp.deleted == []


async def test_replace_with_an_unknown_old_antenna_is_404(monkeypatch):
    uisp = FakeUISP([_station(NEW, uid="dev-new", site=None)])
    _wire(monkeypatch, uisp, {})
    with pytest.raises(svc.AntennaError) as err:
        await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW)
    assert err.value.error_code == "old_device_not_found"


async def test_old_delete_failure_after_assignment_says_new_is_assigned(monkeypatch):
    uisp = FakeUISP([_station(OLD, uid="dev-old")], delete_raises=TimeoutError("lent"))
    _wire(monkeypatch, uisp, {OLD: _lr(OLD)})
    with pytest.raises(svc.AntennaError) as err:
        await svc.replace_antenna(FakeSession(uisp), CLIENT, OLD, NEW)
    assert err.value.error_code == "old_device_delete_failed"
    assert err.value.extra == {"new_assigned": True}


async def test_same_mac_is_refused():
    with pytest.raises(svc.AntennaError) as err:
        await svc.replace_antenna(None, CLIENT, OLD, OLD.upper())
    assert err.value.error_code == "same_device"


# ═══ Contrat, clé, proxy ════════════════════════════════════════════════════


def test_every_service_error_code_is_in_the_contract():
    source = (REPO_ROOT / "backend" / "app" / "services" / "antenna_lifecycle_service.py").read_text(
        encoding="utf-8",
    )
    raised = set(re.findall(r'^\s+"([a-z_]+)",\s*$', source, re.M))
    assert raised, "aucun code relevé — le motif de recherche ne correspond plus au source"
    assert raised <= set(antennas.ERROR_CODES), raised - set(antennas.ERROR_CODES)


def test_antenna_routes_carry_only_the_ticketing_key():
    for path in ("/api/v1/antennas/delete", "/api/v1/antennas/replace"):
        route = next(r for r in api_router.routes if getattr(r, "path", None) == path)
        names = [d.dependency.__name__ for d in route.dependencies
                 if d.dependency.__name__ != "permission_guard"]
        assert names == ["require_ticketing_client"], (path, names)


def test_ticketing_key_opens_nothing_else(monkeypatch, settings):
    key = "ticketing-key-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    monkeypatch.setattr(settings, "ticketing_api_key", key, raising=False)
    monkeypatch.setattr(settings, "api_key", "master-key-aaaaaaaaaaaaaaaaaaaa", raising=False)
    assert deps._ticketing_api_key_matches(key) is True
    for other in (deps._api_key_matches, deps._fai_api_key_matches,
                  deps._uisp_assign_api_key_matches, deps._content_block_api_key_matches):
        assert other(key) is False
    monkeypatch.setattr(settings, "ticketing_api_key", "", raising=False)
    assert deps._ticketing_api_key_matches("") is False


def test_production_refuses_ticketing_key_equal_to_master():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="TICKETING_API_KEY must differ"):
        Settings(app_env="production", postgres_password="a-strong-password",
                 api_key="same-value-123", ticketing_api_key="same-value-123")


def test_the_replace_budget_fits_under_the_proxy_timeout():
    conf = REPO_ROOT / "nginx" / "nginx.conf"
    if not conf.exists():
        pytest.skip("nginx.conf hors de portée (tests lancés dans le conteneur backend)")
    block = re.search(r"location \^~ /api/v1/antennas/ \{(.*?)\}", conf.read_text(encoding="utf-8"), re.S)
    assert block, "location /api/v1/antennas/ introuvable dans nginx.conf"
    timeout = int(re.search(r"proxy_read_timeout\s+(\d+)s;", block.group(1)).group(1))
    assert timeout >= uisp_assignment_service.CALL_BUDGET_S + svc.DELETE_STEP_BUDGET_S + 5
