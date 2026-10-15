"""Message WhatsApp aux clients (page /broadcast).

Ce que ces tests verrouillent :
  - le numéro est lu dans le NOM du LR, jamais deviné ;
  - les catégories sont celles de /access (actifs / bloqués / hors supervision) ;
  - la file envoie un message à la fois, s'arrête à son budget de temps, et
    termine l'envoi quand plus rien n'attend ;
  - « relancer les échecs » ne vise QUE les échecs — jamais un client qui a
    déjà reçu le message ;
  - un envoi arrêté n'envoie plus rien.

La file est testée sur SQLite en mémoire : elle ne touche que les deux tables
de l'envoi.
"""

import datetime

import pytest

pytest.importorskip("aiosqlite")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models.client_broadcast import ClientBroadcast, ClientBroadcastRecipient  # noqa: E402
from app.services import client_broadcast_service as svc  # noqa: E402

# ---------------------------------------------------------------------------
# Le numéro vit dans le nom du LR
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "phone"),
    [
        ("44910449- Habib Khoumeini", "44910449"),
        ("48191327-Selma", "48191327"),
        ("20034411 Vatimetou Sidi", "20034411"),
        ("46538176FATOUSY", "46538176"),
        ("22236086261-Toutou", "36086261"),      # indicatif collé
        ("Client 34567890 x", "34567890"),       # pas en tête
        ("123456789-x", None),                    # 9 chiffres : un contrat, pas un numéro
        ("54910449-x", None),                     # 5 n'est pas un préfixe mobile
        ("A2-CT1-EST", None),
        ("LTU-LR", None),
        (None, None),
    ],
)
def test_phone_is_read_from_the_lr_name(name, phone):
    assert svc.extract_phone(name) == phone


# ---------------------------------------------------------------------------
# Les catégories sont celles de /access
# ---------------------------------------------------------------------------

_RECENT = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)
_OLD = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=400)


def test_an_unblocked_supervised_client_is_active():
    assert svc.lr_categories(False, "10.135.3.4", _RECENT) == {"active"}


def test_a_blocked_client_is_never_active():
    assert svc.lr_categories(True, "10.135.3.4", _RECENT) == {"blocked"}


def test_out_of_supervision_is_not_active():
    """Sans IP et muet pour UISP : ni « actif » ni rien d'autre que hors supervision."""
    assert svc.lr_categories(False, None, _OLD) == {"out_of_supervision"}


def test_a_blocked_client_can_also_be_out_of_supervision():
    """Les catégories se recouvrent comme les onglets de /access : l'union le gère."""
    assert svc.lr_categories(True, None, None) == {"blocked", "out_of_supervision"}


def test_unknown_or_empty_audience_is_refused():
    with pytest.raises(svc.BroadcastError):
        svc.normalize_audiences([])
    with pytest.raises(svc.BroadcastError):
        svc.normalize_audiences(["everyone"])
    assert svc.normalize_audiences(["blocked", "active", "active"]) == ["active", "blocked"]


# ---------------------------------------------------------------------------
# La file d'envoi
# ---------------------------------------------------------------------------


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            ClientBroadcast.metadata.create_all,
            tables=[ClientBroadcast.__table__, ClientBroadcastRecipient.__table__],
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _broadcast(session, phones, *, status="running", message="Panne en cours"):
    b = ClientBroadcast(message=message, audiences="active", status=status)
    session.add(b)
    await session.flush()
    session.add_all(
        ClientBroadcastRecipient(broadcast_id=b.id, phone=p, name=f"{p}-client",
                                 status="pending", attempts=0)
        for p in phones
    )
    await session.commit()
    return b


async def _statuses(session, broadcast_id):
    rows = (await session.execute(
        select(ClientBroadcastRecipient.phone, ClientBroadcastRecipient.status)
        .where(ClientBroadcastRecipient.broadcast_id == broadcast_id)
        .order_by(ClientBroadcastRecipient.id)
    )).all()
    return dict(rows)


class _FakeSender:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, to, text):
        self.calls.append((to, text))
        if to.removeprefix("+222") in self.failing:
            return False, "HTTP 500 : boom"
        return True, "id-1"


async def _no_sleep(_s):
    return None


async def _drain(session, sender, *, budget=1000.0, delay=4.0, clock=None):
    return await svc.process_queue(
        session, budget_s=budget, delay_s=delay, send=sender, sleep=_no_sleep,
        clock=clock or (lambda: 0.0),
    )


async def test_queue_sends_each_number_once_with_the_country_code(session):
    b = await _broadcast(session, ["44910449", "36086261"])
    sender = _FakeSender()

    assert await _drain(session, sender) == 2
    assert sender.calls == [("+22244910449", "Panne en cours"),
                            ("+22236086261", "Panne en cours")]
    assert await _statuses(session, b.id) == {"44910449": "sent", "36086261": "sent"}
    await session.refresh(b)
    assert b.status == "done" and b.finished_at is not None


async def test_a_failure_is_recorded_with_its_reason(session):
    b = await _broadcast(session, ["44910449", "36086261"])
    await _drain(session, _FakeSender(failing={"36086261"}))

    failed = (await session.execute(
        select(ClientBroadcastRecipient).where(ClientBroadcastRecipient.status == "failed")
    )).scalar_one()
    assert failed.phone == "36086261"
    assert failed.error == "HTTP 500 : boom"
    assert failed.attempts == 1
    await session.refresh(b)
    assert b.status == "done"


async def test_the_queue_stops_at_its_time_budget(session):
    """Un passage s'arrête avant de déborder son intervalle ; le reste attend le suivant."""
    b = await _broadcast(session, ["44910449", "36086261", "20034411"])
    now = [0.0]

    async def advancing_sleep(seconds):
        now[0] += seconds

    sender = _FakeSender()
    # Budget 6 s, 4 s entre deux messages : envoi à t=0, puis à t=4 ; un 3e
    # partirait à t=8, au-delà du budget → il attend le passage suivant.
    handled = await svc.process_queue(
        session, budget_s=6.0, delay_s=4.0, send=sender,
        sleep=advancing_sleep, clock=lambda: now[0],
    )
    assert handled == 2
    statuses = await _statuses(session, b.id)
    assert list(statuses.values()) == ["sent", "sent", "pending"]
    await session.refresh(b)
    assert b.status == "running"  # pas terminé : il reste quelqu'un en file

    await _drain(session, sender)
    assert len(sender.calls) == 3


async def test_retry_resends_only_the_failures(session):
    b = await _broadcast(session, ["44910449", "36086261"])
    await _drain(session, _FakeSender(failing={"36086261"}))
    await session.refresh(b)

    assert await svc.retry_failed(session, b) == 1
    await session.commit()
    sender = _FakeSender()
    await _drain(session, sender)

    # Celui qui avait déjà reçu le message ne le reçoit pas une seconde fois.
    assert sender.calls == [("+22236086261", "Panne en cours")]
    assert await _statuses(session, b.id) == {"44910449": "sent", "36086261": "sent"}


async def test_retry_is_refused_while_the_broadcast_is_running(session):
    b = await _broadcast(session, ["44910449"])
    with pytest.raises(svc.BroadcastError, match="en cours"):
        await svc.retry_failed(session, b)


async def test_retry_without_failures_is_refused(session):
    b = await _broadcast(session, ["44910449"])
    await _drain(session, _FakeSender())
    await session.refresh(b)
    with pytest.raises(svc.BroadcastError, match="Aucun échec"):
        await svc.retry_failed(session, b)


async def test_a_cancelled_broadcast_sends_nothing_more(session):
    b = await _broadcast(session, ["44910449", "36086261"])
    assert await svc.cancel(session, b) == 2
    await session.commit()

    sender = _FakeSender()
    assert await _drain(session, sender) == 0
    assert sender.calls == []
    assert set((await _statuses(session, b.id)).values()) == {"cancelled"}


async def test_broadcasts_are_sent_one_after_the_other(session):
    first = await _broadcast(session, ["44910449"], message="un")
    second = await _broadcast(session, ["36086261"], message="deux")
    sender = _FakeSender()
    await _drain(session, sender)
    assert [text for _, text in sender.calls] == ["un", "deux"]
    for b in (first, second):
        await session.refresh(b)
        assert b.status == "done"


# ---------------------------------------------------------------------------
# Deux canaux, deux files indépendantes
# ---------------------------------------------------------------------------


async def _two_channel_broadcast(session, phones):
    b = ClientBroadcast(message="Panne", audiences="active", channels="whatsapp,sms",
                        status="running")
    session.add(b)
    await session.flush()
    session.add_all(
        ClientBroadcastRecipient(broadcast_id=b.id, channel=ch, phone=p, name=p,
                                 status="pending", attempts=0)
        for ch in ("whatsapp", "sms") for p in phones
    )
    await session.commit()
    return b


async def _channel_statuses(session, broadcast_id, channel):
    rows = (await session.execute(
        select(ClientBroadcastRecipient.phone, ClientBroadcastRecipient.status)
        .where(ClientBroadcastRecipient.broadcast_id == broadcast_id,
               ClientBroadcastRecipient.channel == channel)
    )).all()
    return dict(rows)


def test_unknown_or_empty_channel_is_refused():
    with pytest.raises(svc.BroadcastError):
        svc.normalize_channels([])
    with pytest.raises(svc.BroadcastError):
        svc.normalize_channels(["email"])
    assert svc.normalize_channels(["sms", "whatsapp"]) == ["whatsapp", "sms"]


async def test_each_channel_only_drains_its_own_queue(session):
    """Le job SMS n'envoie que les SMS : il n'attend pas la file WhatsApp, ni ne la vide."""
    b = await _two_channel_broadcast(session, ["44910449", "36086261"])
    sms = _FakeSender()
    assert await svc.process_queue(session, channel="sms", budget_s=1000, delay_s=1,
                                   send=sms, sleep=_no_sleep, clock=lambda: 0.0) == 2
    assert await _channel_statuses(session, b.id, "sms") == {"44910449": "sent", "36086261": "sent"}
    assert set((await _channel_statuses(session, b.id, "whatsapp")).values()) == {"pending"}
    await session.refresh(b)
    # Le WhatsApp n'est pas parti : l'envoi n'est PAS terminé.
    assert b.status == "running"

    await _drain(session, _FakeSender())
    await session.refresh(b)
    assert b.status == "done"


async def test_retrying_a_failed_sms_does_not_resend_the_whatsapp(session):
    b = await _two_channel_broadcast(session, ["44910449"])
    await _drain(session, _FakeSender())
    await svc.process_queue(session, channel="sms", budget_s=1000, delay_s=1,
                            send=_FakeSender(failing={"44910449"}), sleep=_no_sleep,
                            clock=lambda: 0.0)
    await session.refresh(b)
    assert await svc.retry_failed(session, b) == 1
    await session.commit()

    whatsapp = _FakeSender()
    await _drain(session, whatsapp)
    assert whatsapp.calls == []  # le WhatsApp déjà reçu ne repart pas
    sms = _FakeSender()
    await svc.process_queue(session, channel="sms", budget_s=1000, delay_s=1,
                            send=sms, sleep=_no_sleep, clock=lambda: 0.0)
    assert sms.calls == [("+22244910449", "Panne")]


# ---------------------------------------------------------------------------
# La passerelle SMS : succès = « Message Submitted », rien d'autre
# ---------------------------------------------------------------------------

import httpx  # noqa: E402

from app.services import sms_service  # noqa: E402

_OK_HTML = """<HTML><HEAD><TITLE>Message Submitted</TITLE></HEAD>
<BODY><p>Message Submitted<pre><small>
MessageID=SAR-+22248783201-67205d00-7-1, Recipient=+22248783201
MessageID=SAR-+22248783201-67205d00-7-2, Recipient=+22248783201
</small></pre></BODY></HTML>"""


@pytest.fixture
def gateway(monkeypatch):
    """Remplace le réseau par un gestionnaire, et configure la passerelle."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "sms_gateway_url", "http://gateway.test:8800")
    monkeypatch.setattr(settings, "sms_gateway_username", "user")
    monkeypatch.setattr(settings, "sms_gateway_password", "secret")
    seen: list[httpx.Request] = []
    state = {"handler": None}

    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        def handle(request):
            seen.append(request)
            return state["handler"](request)
        kwargs["transport"] = httpx.MockTransport(handle)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(sms_service.httpx, "AsyncClient", factory)
    return state, seen


async def test_sms_success_is_message_submitted_and_counts_segments(gateway):
    state, seen = gateway
    state["handler"] = lambda req: httpx.Response(200, text=_OK_HTML)
    ok, detail = await sms_service.send_sms("+22248783201", "مرحبا")
    assert ok
    assert detail.startswith("2 segment(s)")
    body = seen[0].content.decode()
    assert "Phone=%2B22248783201" in body and "SMSCRoute=Mattel" in body
    assert seen[0].headers["Authorization"].startswith("Basic ")
    assert "expect" not in {k.lower() for k in seen[0].headers}


async def test_sms_200_without_the_marker_is_a_failure(gateway):
    state, _ = gateway
    state["handler"] = lambda req: httpx.Response(200, text="<HTML><BODY>Invalid Route</BODY></HTML>")
    ok, detail = await sms_service.send_sms("+22248783201", "x")
    assert not ok and "Invalid Route" in detail


async def test_sms_timeout_says_it_may_have_been_sent(gateway):
    """Un délai dépassé n'est pas un échec certain : l'opérateur doit le savoir avant de relancer."""
    state, _ = gateway

    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)

    state["handler"] = boom
    ok, detail = await sms_service.send_sms("+22248783201", "x")
    assert not ok and "PEUT-ÊTRE" in detail


async def test_sms_not_configured_sends_nothing(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "sms_gateway_url", "")
    ok, detail = await sms_service.send_sms("+22248783201", "x")
    assert not ok and "non configuré" in detail


# ---------------------------------------------------------------------------
# Service arrêté (CLIENT_BROADCAST_ENABLED=false)
# ---------------------------------------------------------------------------


@pytest.fixture
def service_stopped(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "client_broadcast_enabled", False)


async def test_stopped_service_sends_nothing_and_keeps_the_queue(session, service_stopped):
    """Un envoi en cours est SUSPENDU : rien ne part, rien n'est annulé ni clos."""
    b = await _broadcast(session, ["44910449", "36086261"])
    sender = _FakeSender()
    assert await _drain(session, sender) == 0
    assert sender.calls == []
    assert set((await _statuses(session, b.id)).values()) == {"pending"}
    await session.refresh(b)
    assert b.status == "running"


async def test_stopped_service_refuses_new_broadcasts(session, service_stopped):
    with pytest.raises(svc.BroadcastDisabledError, match="temporairement arrêté"):
        await svc.create_broadcast(session, audiences=["active"], message="x", created_by=None)


async def test_stopped_service_refuses_retries(session, service_stopped):
    b = await _broadcast(session, ["44910449"], status="done")
    with pytest.raises(svc.BroadcastDisabledError):
        await svc.retry_failed(session, b)


async def test_the_queue_resumes_where_it_stopped(session, monkeypatch):
    from app.core.config import get_settings

    b = await _broadcast(session, ["44910449"])
    monkeypatch.setattr(get_settings(), "client_broadcast_enabled", False)
    await _drain(session, _FakeSender())
    monkeypatch.setattr(get_settings(), "client_broadcast_enabled", True)
    sender = _FakeSender()
    assert await _drain(session, sender) == 1
    await session.refresh(b)
    assert b.status == "done"
