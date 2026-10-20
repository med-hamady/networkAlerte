"""
Contrôle du solde CRM avant un déblocage — `crm_balance_service` + les deux routes.

Règle (décision opérateur du 2026-10-20) : on débloque si
    solde dû − prix des AUTRES abonnements du client encore coupés ≤ 150.
Verrouillé ici :
  * l'exemple opérateur (3 abonnements à 1000) ;
  * solde ≤ 150 ⇒ débloqué sans rien lire d'autre ;
  * prix différents ⇒ rattachement par site UISP, introuvable ⇒ refus ;
  * le nombre compté comme coupé ne dépasse jamais abonnements − 1 ;
  * CRM / UISP injoignable, illisible, clé absente ⇒ refus (fermé par défaut) ;
  * abonné sans id CRM ⇒ débloqué sans contrôle ;
  * aucun moyen de forcer ; un refus ne touche à RIEN.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.api.endpoints import devices as devices_ep
from app.api.endpoints import fai as fai_ep
from app.core.config import get_settings
from app.models.device import Lr
from app.services import crm_balance_service

settings = get_settings()

_MAC = "1c:6a:1b:b8:79:b0"
_B1, _B2 = "aa:aa:aa:aa:aa:01", "aa:aa:aa:aa:aa:02"


@pytest.fixture(autouse=True)
def _crm_settings(monkeypatch):
    monkeypatch.setattr(settings, "unblock_balance_check_enabled", True)
    monkeypatch.setattr(settings, "crm_api_url", "https://crm.test/crm/api/v1.0")
    monkeypatch.setattr(settings, "crm_api_key", "app-key")
    monkeypatch.setattr(settings, "unblock_max_outstanding", 150.0)


def _lr(crm_id: str | None = "1361") -> SimpleNamespace:
    return SimpleNamespace(
        id=7, name="LR test", mac_address=_MAC,
        uisp_crm_client_id=crm_id, block_mode="full",
    )


def _svc(sid, price=1000, site=None, client_id=1361):
    return {"id": sid, "clientId": client_id, "status": 1, "totalPrice": price,
            "unmsClientSiteId": site}


def _crm(outstanding, services=()):
    """Faux CRM. `outstanding` / `services` = valeur, ou callable(request)."""
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Auth-App-Key"] == "app-key"
        if request.url.path.endswith("/clients/services"):
            assert request.url.params["clientId"] == "1361"
            target = services if callable(services) else list(services)
        elif request.url.path.endswith("/clients/1361"):
            target = outstanding if callable(outstanding) else {"id": 1361, "accountOutstanding": outstanding}
        else:  # pragma: no cover
            raise AssertionError(f"appel inattendu {request.url}")
        return target(request) if callable(target) else httpx.Response(200, json=target)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return patch.object(crm_balance_service.httpx, "AsyncClient", factory)


def _blocked(*macs):
    """Les AUTRES antennes coupées du client, telles que la base les rend."""
    return patch.object(crm_balance_service, "other_blocked_macs", AsyncMock(return_value=list(macs)))


@pytest.fixture(autouse=True)
def _uisp_sites():
    with patch.object(crm_balance_service, "_station_sites", AsyncMock(return_value={})) as mock:
        yield mock


async def _check():
    return await crm_balance_service.check_unblock_allowed(None, _lr())


_THREE = [_svc(1), _svc(2), _svc(3)]


# ── L'exemple opérateur : 3 abonnements à 1000 ─────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("outstanding,others_blocked,allowed", [
    (3000, (_B1, _B2), False),   # rien payé : 3000 − 2000 = 1000
    (2000, (_B1, _B2), True),    # un payé, on débloque le 1er : 2000 − 2000 = 0
    (2000, (_B1,), False),       # 1 actif, on demande le 2e : 2000 − 1000 = 1000
    (1000, (_B1,), True),        # deux payés, on demande le 2e : 1000 − 1000 = 0
    (1000, (), False),           # deux payés, 2 actifs, on demande le 3e : 1000 − 0
])
async def test_operator_example(outstanding, others_blocked, allowed):
    with _crm(outstanding, _THREE), _blocked(*others_blocked):
        check = await _check()
    assert check.allowed is allowed
    assert check.blocked_services == len(others_blocked)


@pytest.mark.asyncio
async def test_single_service_client_only_its_balance_counts():
    with _crm(980, [_svc(1, 990)]), _blocked():
        check = await _check()
    assert check.verdict == "outstanding_balance"
    assert check.covered == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("outstanding,allowed", [(0, True), (150, True), (150.01, False)])
async def test_threshold(outstanding, allowed):
    with _crm(outstanding, [_svc(1)]), _blocked():
        assert (await _check()).allowed is allowed


@pytest.mark.asyncio
async def test_small_balance_reads_nothing_else():
    def boom(request):  # pragma: no cover — ne doit pas être appelé
        raise AssertionError("les abonnements ne doivent pas être lus")
    with _crm(100, boom), _blocked(_B1) as blocked:
        assert (await _check()).allowed
    blocked.assert_not_awaited()


# ── Garde-fous du comptage ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_rows_never_cover_more_than_services_minus_one():
    """3 fiches coupées pour un client à 2 abonnements : une seule compte."""
    with _crm(2000, [_svc(1), _svc(2)]), _blocked(_B1, _B2, "aa:aa:aa:aa:aa:03"):
        check = await _check()
    assert check.blocked_services == 1
    assert check.verdict == "outstanding_balance"


@pytest.mark.asyncio
async def test_services_of_another_client_are_ignored():
    with _crm(2000, [_svc(1), _svc(9, client_id=999)]), _blocked(_B1):
        check = await _check()
    assert check.blocked_services == 0
    assert check.verdict == "outstanding_balance"


# ── Prix différents : rattachement par site UISP ───────────────────────────

_MIXED = [_svc(1, 990, "s1"), _svc(2, 990, "s2"), _svc(3, 1190, "s3")]


@pytest.mark.asyncio
async def test_different_prices_use_each_blocked_antenna_price(_uisp_sites):
    _uisp_sites.return_value = {_B1: "s3"}
    with _crm(1190, _MIXED), _blocked(_B1):
        check = await _check()
    assert check.covered == 1190
    assert check.allowed


@pytest.mark.asyncio
async def test_cheaper_blocked_antenna_does_not_cover_more_than_its_price(_uisp_sites):
    _uisp_sites.return_value = {_B1: "s1"}
    with _crm(1190, _MIXED), _blocked(_B1):
        check = await _check()
    assert check.covered == 990
    assert check.verdict == "outstanding_balance"


@pytest.mark.asyncio
async def test_two_rows_on_the_same_service_count_once(_uisp_sites):
    _uisp_sites.return_value = {_B1: "s3", _B2: "s3"}
    with _crm(2380, _MIXED), _blocked(_B1, _B2):
        check = await _check()
    assert check.blocked_services == 1


@pytest.mark.asyncio
async def test_equal_prices_need_no_uisp_lookup(_uisp_sites):
    with _crm(2000, _THREE), _blocked(_B1, _B2):
        assert (await _check()).allowed
    _uisp_sites.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmappable_blocked_antenna_is_refused(_uisp_sites):
    _uisp_sites.return_value = {}
    with _crm(1190, _MIXED), _blocked(_B1):
        check = await _check()
    assert check.verdict == "crm_unavailable"
    assert "introuvable" in check.message


@pytest.mark.asyncio
async def test_uisp_unreachable_is_refused(_uisp_sites):
    _uisp_sites.side_effect = httpx.ConnectError("down")
    with _crm(1190, _MIXED), _blocked(_B1):
        assert (await _check()).verdict == "crm_unavailable"


# ── Sans id CRM / CRM muet ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_without_crm_id_is_allowed_without_calling_the_crm():
    def boom(request):  # pragma: no cover
        raise AssertionError("le CRM ne doit pas être interrogé")
    with _crm(boom, boom):
        for crm_id in (None, "", "  "):
            assert (await crm_balance_service.check_unblock_allowed(None, _lr(crm_id))).allowed


_FAILURES = [
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(401),
    lambda r: httpx.Response(404),
    lambda r: httpx.Response(200, text="<html>"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [*_FAILURES, lambda r: httpx.Response(200, json={"id": 1361})])
async def test_unreadable_balance_is_refused(failure):
    with _crm(failure), _blocked():
        assert (await _check()).verdict == "crm_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    *_FAILURES,
    lambda r: httpx.Response(200, json={"id": 1}),
    lambda r: httpx.Response(200, json=[{"id": 1, "clientId": 1361, "totalPrice": "abc"},
                                        {"id": 2, "clientId": 1361, "totalPrice": 1000}]),
])
async def test_unreadable_services_are_refused(failure):
    with _crm(2000, failure), _blocked(_B1):
        assert (await _check()).verdict == "crm_unavailable"


@pytest.mark.asyncio
async def test_missing_app_key_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "crm_api_key", "")
    assert (await _check()).verdict == "crm_unavailable"


def test_crm_url_defaults_to_the_controller(monkeypatch):
    monkeypatch.setattr(settings, "crm_api_url", "")
    monkeypatch.setattr(settings, "uisp_base_url", "https://13.62.145.152/")
    assert crm_balance_service._crm_base_url() == "https://13.62.145.152/crm/api/v1.0"


# ── Les deux routes ─────────────────────────────────────────────────────────

_REFUSED = crm_balance_service.BalanceCheck(
    "outstanding_balance", "Déblocage refusé : doit 2000.00", "1361", 2000.0, 150.0, 1000.0, 1,
)
_UNAVAILABLE = crm_balance_service.BalanceCheck(
    "crm_unavailable", "Déblocage refusé : CRM injoignable", "1361", None, 150.0,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("check,status", [(_REFUSED, 409), (_UNAVAILABLE, 503)])
async def test_fai_unblock_refusal_touches_nothing(check, status):
    lr = _lr()
    with (
        patch.object(fai_ep, "_lookup_lr", AsyncMock(return_value=lr)),
        patch.object(crm_balance_service, "check_unblock_allowed", AsyncMock(return_value=check)),
        patch.object(fai_ep.client_block_service, "unblock_client", AsyncMock()) as unblock,
        patch.object(fai_ep.fai_audit, "log_action") as log,
        pytest.raises(HTTPException) as exc,
    ):
        await fai_ep.fai_unblock(fai_ep.FaiUnblockRequest(mac=lr.mac_address, user="a@b"), db=None)
    assert exc.value.status_code == status
    detail = exc.value.detail
    assert detail["error_code"] == check.verdict
    assert detail["outstanding"] == check.outstanding
    assert detail["blocked_services"] == check.blocked_services
    assert detail["covered"] == check.covered
    unblock.assert_not_awaited()
    assert log.call_args.args[0] == "BALANCE_KO"
    assert log.call_args.kwargs["user"] == "a@b"


@pytest.mark.asyncio
async def test_fai_unblock_allowed_goes_through():
    lr = _lr()
    ok = crm_balance_service.BalanceCheck("allowed", "ok")
    with (
        patch.object(fai_ep, "_lookup_lr", AsyncMock(return_value=lr)),
        patch.object(crm_balance_service, "check_unblock_allowed", AsyncMock(return_value=ok)),
        patch.object(fai_ep.client_block_service, "unblock_client",
                     AsyncMock(return_value=(True, "rétabli", None))) as unblock,
        patch.object(fai_ep.fai_audit, "log_action"),
        patch.object(fai_ep, "_result", return_value="RESULT"),
    ):
        assert await fai_ep.fai_unblock(fai_ep.FaiUnblockRequest(mac=lr.mac_address), db=None) == "RESULT"
    unblock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dashboard_unblock_is_checked_too():
    lr = Lr(id=7, name="LR test", mac_address=_MAC, uisp_crm_client_id="1361")
    with (
        patch.object(devices_ep.device_service, "get_device", AsyncMock(return_value=lr)),
        patch.object(crm_balance_service, "check_unblock_allowed", AsyncMock(return_value=_REFUSED)),
        patch.object(devices_ep.client_block_service, "unblock_client", AsyncMock()) as unblock,
        patch.object(devices_ep.fai_audit, "log_action"),
        pytest.raises(HTTPException) as exc,
    ):
        await devices_ep.unblock_client(7, db=None)
    assert exc.value.status_code == 409
    assert exc.value.detail == _REFUSED.message  # chaîne : le dashboard l'affiche telle quelle
    unblock.assert_not_awaited()


def test_no_force_option_exists():
    """Décision opérateur : aucun moyen de passer outre le solde."""
    assert set(fai_ep.FaiUnblockRequest.model_fields) == {"mac", "user"}
