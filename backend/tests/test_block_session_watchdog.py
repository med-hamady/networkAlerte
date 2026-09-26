"""Une session de BLOCAGE bloquée est fermée de force et rend un échec propre.

Mesuré en prod le 2026-09-26 (scripts/diag_jobs.py) : client_block_enforcement_job
a tenu un tour de 3644 s et en a sauté 121 sur 6 h. Les trois chemins SSH de
blocage (port LAN, WhatsApp seul, filtre de contenu) n'avaient pas le chien de
garde de la sonde : une attente paramiko non bornable sur UN LR gelait tout le
fan-out.

Ces tests simulent une session qui ne rend jamais la main tant que le transport
n'est pas fermé — exactement le comportement de ``exec_command`` sur un lien
radio tombé.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.services import ssh_service


class _FakeTransport:
    def __init__(self) -> None:
        self.closed = threading.Event()

    def close(self) -> None:
        self.closed.set()


@pytest.fixture
def transport(monkeypatch):
    t = _FakeTransport()
    monkeypatch.setattr(
        ssh_service, "_open_transport", lambda *a, **k: (t, "fp", "pw"),
    )
    monkeypatch.setattr(ssh_service, "identity_refusal", lambda *a, **k: None)
    monkeypatch.setattr(ssh_service, "_BLOCK_SESSION_HARD_LIMIT_S", 0.2)
    return t


def _hang_until_closed(t: _FakeTransport):
    def _hang(*_a, **_k):
        # Plafond de sécurité du test : sans chien de garde on échoue ici
        # au lieu de pendre la suite de tests.
        if not t.closed.wait(5):
            raise AssertionError("session jamais fermée : aucun chien de garde")
        raise EOFError("transport fermé")
    return _hang


def _run_iface():
    return ssh_service._set_iface_state_sync(
        "10.0.0.1", 22, "ubnt", "pw", "eth0.1", False, None, None, expected_mac="aa",
    )


def _run_whatsapp():
    return ssh_service._set_whatsapp_only_sync(
        "10.0.0.1", 22, "ubnt", "pw", True, [], [], None, None, expected_mac="aa",
    )


def _run_content():
    return ssh_service._set_content_block_sync(
        "10.0.0.1", 22, "ubnt", "pw", ["tiktok.com"], False, None, None, expected_mac="aa",
    )


@pytest.mark.parametrize(
    ("runner", "hang_on"),
    [
        (_run_iface, "_collect_forbidden_ifaces"),
        (_run_whatsapp, "_detect_client_context"),
        (_run_content, "_detect_client_context"),
    ],
)
def test_hung_block_session_is_force_closed_and_fails_cleanly(
    monkeypatch, transport, runner, hang_on,
):
    monkeypatch.setattr(ssh_service, hang_on, _hang_until_closed(transport))

    started = time.monotonic()
    result = runner()

    assert time.monotonic() - started < 3
    ok, msg = result[0], result[1]
    assert ok is False
    # « bloquée » et pas auth/clé d'hôte : _structural_failure la classe donc
    # transitoire → rejouée au cycle suivant, jamais abandonnée.
    assert "fermée de force" in msg
    assert transport.closed.is_set()


@pytest.mark.parametrize(
    ("runner", "hang_on"),
    [
        (_run_iface, "_collect_forbidden_ifaces"),
        (_run_whatsapp, "_detect_client_context"),
        (_run_content, "_detect_client_context"),
    ],
)
def test_other_session_errors_still_propagate(monkeypatch, transport, runner, hang_on):
    """Seule la fermeture forcée est convertie ; le reste remonte comme avant."""
    monkeypatch.setattr(ssh_service, "_BLOCK_SESSION_HARD_LIMIT_S", 30)

    def _boom(*_a, **_k):
        raise RuntimeError("autre panne")

    monkeypatch.setattr(ssh_service, hang_on, _boom)
    with pytest.raises(RuntimeError, match="autre panne"):
        runner()
    assert transport.closed.is_set()


def test_identity_refusal_closes_the_session(monkeypatch, transport):
    """Les branches de refus d'identité laissaient la session SSH ouverte."""
    monkeypatch.setattr(ssh_service, "identity_refusal", lambda *a, **k: "MAC absente")
    for runner in (_run_iface, _run_whatsapp, _run_content):
        transport.closed.clear()
        ok, msg = runner()[:2]
        assert ok is False and msg == "MAC absente"
        assert transport.closed.is_set()
