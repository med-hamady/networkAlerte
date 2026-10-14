"""
Contrôle du solde CRM avant un déblocage — `crm_balance_service` + les deux routes.

Décisions opérateur (2026-10-14) verrouillées ici :
  * dû > 150 ⇒ refus (150 tout rond passe) ;
  * contrôle SEULEMENT pour un client à un seul abonnement (hors terminés) ;
  * CRM injoignable / illisible / clé absente ⇒ refus (fermé par défaut) ;
  * abonné sans id CRM ⇒ débloqué sans contrôle ;
  * aucun moyen de forcer ;
  * un refus ne touche à RIEN (ni base, ni LR, ni routeur).
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


@pytest.fixture(autouse=True)
def _crm_settings(monkeypatch):
    monkeypatch.setattr(settings, "unblock_balance_check_enabled", True)
    monkeypatch.setattr(settings, "crm_api_url", "https://crm.test/crm/api/v1.0")
    monkeypatch.setattr(settings, "crm_api_key", "app-key")
    monkeypatch.setattr(settings, "unblock_max_outstanding", 150.0)


def _lr(crm_id: str | None = "1361") -> SimpleNamespace:
    return SimpleNamespace(
        id=7, name="LR test", mac_address="1c:6a:1b:b8:79:b0",
        uisp_crm_client_id=crm_id, block_mode="full",
    )


def _crm(handler):
    """Fait répondre le CRM par `handler(request) -> httpx.Response`."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return patch.object(crm_balance_service.httpx, "AsyncClient", factory)


def _svc(*statuses, client_id=1361):
    return [{"id": 100 + i, "clientId": client_id, "status": st} for i, st in enumerate(statuses)]


def _fake(services=None, client=None):
    """Faux CRM : `services` / `client` = réponse httpx, ou callable(request).

    `client=None` : le solde ne doit PAS être lu (le handler lève s'il l'est).
    """
    services = services if services is not None else httpx.Response(200, json=_svc(1))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Auth-App-Key"] == "app-key"
        if request.url.path.endswith("/clients/services"):
            assert request.url.params["clientId"] == "1361"
            target = services
        else:
            assert request.url.path.endswith("/clients/1361")
            if client is None:
                raise AssertionError("le solde ne doit pas être lu")
            target = client
        return target(request) if callable(target) else target
    return handler


def _owes(amount, services=None):
    return _fake(services, httpx.Response(200, json={"id": 1361, "accountOutstanding": amount}))


# ── Verdicts : client à un seul abonnement ─────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [0, 0.0, 149.99, 150])
async def test_balance_at_or_below_threshold_is_allowed(amount):
    with _crm(_owes(amount)):
        check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.allowed
    assert check.outstanding == float(amount)


@pytest.mark.asyncio
async def test_balance_above_threshold_is_refused():
    with _crm(_owes(150.01)):
        check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.verdict == "outstanding_balance"
    assert check.outstanding == 150.01
    assert "150.01" in check.message


@pytest.mark.asyncio
async def test_suspended_single_service_is_checked():
    """Un client coupé a souvent son abonnement SUSPENDU (3) : il compte."""
    with _crm(_owes(980, httpx.Response(200, json=_svc(3)))):
        check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.verdict == "outstanding_balance"


@pytest.mark.asyncio
async def test_ended_services_do_not_count():
    """Un actif + des terminés (2) / obsolètes (5) = UN seul abonnement."""
    with _crm(_owes(980, httpx.Response(200, json=_svc(1, 2, 5, 2)))):
        check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.verdict == "outstanding_balance"
    assert check.service_count == 1


# ── Client à plusieurs abonnements (ou aucun) : pas de contrôle ────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("statuses", [(1, 1), (1, 3), (1, 1, 1), (), (2,), (2, 5)])
async def test_not_exactly_one_service_skips_the_balance(statuses):
    """Le solde n'est même pas lu (le faux CRM lève s'il l'était)."""
    with _crm(_fake(httpx.Response(200, json=_svc(*statuses)))):
        check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.allowed
    assert check.outstanding is None
    assert check.service_count == sum(1 for st in statuses if st not in (2, 5))


@pytest.mark.asyncio
async def test_services_of_another_client_are_ignored():
    """Garde-fou : seuls les services de CE client comptent."""
    other = _svc(1, client_id=999)
    with _crm(_owes(980, httpx.Response(200, json=_svc(1) + other))):
        check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.verdict == "outstanding_balance"


# ── Sans id CRM / CRM muet ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_client_without_crm_id_is_allowed_without_calling_the_crm():
    def boom(request):  # pragma: no cover — ne doit pas être appelé
        raise AssertionError("le CRM ne doit pas être interrogé")
    with _crm(boom):
        for crm_id in (None, "", "  "):
            assert (await crm_balance_service.check_unblock_allowed(_lr(crm_id))).allowed


_FAILURES = [
    lambda r: (_ for _ in ()).throw(httpx.ConnectError("down", request=r)),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow", request=r)),
    lambda r: httpx.Response(500),
    lambda r: httpx.Response(401),
    lambda r: httpx.Response(404),
    lambda r: httpx.Response(200, text="<html>"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    *_FAILURES,
    lambda r: httpx.Response(200, json={"id": 1361}),
    lambda r: httpx.Response(200, json=[{"clientId": 1361}]),
])
async def test_unknown_service_count_is_refused(failure):
    """Fermé par défaut : on ne SAIT pas si le client relève du contrôle."""
    with _crm(_fake(failure)):
        check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.verdict == "crm_unavailable"
    assert "abonnements" in check.message


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    *_FAILURES,
    lambda r: httpx.Response(200, json={"id": 1361}),
    lambda r: httpx.Response(200, json={"accountOutstanding": "abc"}),
])
async def test_unknown_balance_is_refused(failure):
    """Fermé par défaut : on ne SAIT pas que le client est à jour."""
    with _crm(_fake(client=failure)):
        check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.verdict == "crm_unavailable"
    assert not check.allowed


@pytest.mark.asyncio
async def test_missing_app_key_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "crm_api_key", "")
    check = await crm_balance_service.check_unblock_allowed(_lr())
    assert check.verdict == "crm_unavailable"


def test_crm_url_defaults_to_the_controller(monkeypatch):
    monkeypatch.setattr(settings, "crm_api_url", "")
    monkeypatch.setattr(settings, "uisp_base_url", "https://13.62.145.152/")
    assert crm_balance_service._crm_base_url() == "https://13.62.145.152/crm/api/v1.0"


# ── Les deux routes ─────────────────────────────────────────────────────────

_REFUSED = crm_balance_service.BalanceCheck(
    "outstanding_balance", "Déblocage refusé : doit 300.00", "1361", 300.0, 150.0,
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
    assert exc.value.detail["error_code"] == check.verdict
    assert exc.value.detail["outstanding"] == check.outstanding
    unblock.assert_not_awaited()
    assert log.call_args.args[0] == "BALANCE_KO"
    assert log.call_args.kwargs["user"] == "a@b"


@pytest.mark.asyncio
async def test_fai_unblock_allowed_goes_through():
    lr = _lr()
    ok = crm_balance_service.BalanceCheck("allowed", "ok")
    with patch.object(fai_ep, "_lookup_lr", AsyncMock(return_value=lr)), \
         patch.object(crm_balance_service, "check_unblock_allowed", AsyncMock(return_value=ok)), \
         patch.object(fai_ep.client_block_service, "unblock_client",
                      AsyncMock(return_value=(True, "rétabli", None))) as unblock, \
         patch.object(fai_ep.fai_audit, "log_action"), \
         patch.object(fai_ep, "_result", return_value="RESULT"):
        assert await fai_ep.fai_unblock(fai_ep.FaiUnblockRequest(mac=lr.mac_address), db=None) == "RESULT"
    unblock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dashboard_unblock_is_checked_too():
    lr = Lr(id=7, name="LR test", mac_address="1c:6a:1b:b8:79:b0", uisp_crm_client_id="1361")
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
