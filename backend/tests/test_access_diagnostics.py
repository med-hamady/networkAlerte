"""Diagnostics d'accès : catégorisation SSH + les deux requêtes de la page.

1. `classify_probe_ssh_status` traduit le résultat brut de la sonde en une
   catégorie persistée (`lrs.ssh_status`). Le piège couvert : un exec qui
   timeout APRÈS une auth réussie n'est PAS un refus (used_pw non nul → "ok").
2. Les deux requêtes du service ne remontent que les bonnes lignes : un LR down
   ne « refuse » pas le SSH (il est éteint), et un LR déjà connu de UISP n'est
   pas « hors UISP ».
"""

import datetime

from app.models.device import Lr
from app.services import access_diagnostics_service as svc
from app.services import ssh_service


def _now():
    return datetime.datetime.now(datetime.UTC)


# ── 1. Catégorisation (pur, sans DB) ────────────────────────────────────────


def test_auth_failure_is_a_refusal():
    assert ssh_service.classify_probe_ssh_status(
        False, None, "Authentication failed against 10.0.0.1",
    ) == (ssh_service.SSH_STATUS_AUTH_FAILED, "Authentication failed against 10.0.0.1")


def test_connection_refused_means_ssh_disabled():
    cat, _ = ssh_service.classify_probe_ssh_status(
        False, None, "[Errno 111] Connection refused",
    )
    assert cat == ssh_service.SSH_STATUS_DISABLED


def test_host_key_mismatch_is_its_own_category():
    cat, _ = ssh_service.classify_probe_ssh_status(
        False, None, "Host key mismatch for 10.0.0.1: expected X, got Y",
    )
    assert cat == ssh_service.SSH_STATUS_HOST_KEY_MISMATCH


def test_success_is_ok_and_clears_error():
    assert ssh_service.classify_probe_ssh_status(True, "pw", "8.8.8.8 avg=12 ms") == (
        ssh_service.SSH_STATUS_OK, None,
    )


def test_auth_ok_but_exec_timeout_is_not_a_refusal():
    """used_pw non nul = la session s'est authentifiée → "ok", jamais un refus.

    Sans ce cas, un LR sain dont une commande a simplement traîné serait affiché
    « refuse le SSH » à tort.
    """
    cat, err = ssh_service.classify_probe_ssh_status(False, "pw", "exit-status timeout")
    assert cat == ssh_service.SSH_STATUS_OK
    assert err is None


def test_plain_timeout_is_unreachable_not_a_refusal():
    cat, _ = ssh_service.classify_probe_ssh_status(False, None, "timed out")
    assert cat == ssh_service.SSH_STATUS_UNREACHABLE
    assert cat not in ssh_service.SSH_REFUSAL_STATUSES


# ── 2. Requêtes du service (vraie DB) ───────────────────────────────────────


def _lr(db, name, **kw):
    lr = Lr(name=name, model_variant="litebeam_5ac", **kw)
    db.add(lr)
    return lr


async def test_ssh_refused_lists_only_up_refusals(db):
    refused = _lr(db, "refuse", status="up", ssh_status="auth_failed",
                  ssh_error="Authentication failed", ssh_checked_at=_now())
    _lr(db, "ok", status="up", ssh_status="ok")
    # Un LR down qui refusait : ce n'est plus un refus, il est éteint.
    _lr(db, "down-refuse", status="down", ssh_status="auth_failed")
    # up mais juste injoignable en SSH (radio) → pas un refus.
    _lr(db, "unreachable", status="up", ssh_status="unreachable")
    await db.flush()

    rows = await svc.get_ssh_refusing_lrs(db)

    names = {r["name"] for r in rows}
    assert names == {"refuse"}
    assert rows[0]["ssh_status"] == "auth_failed"
    assert rows[0]["id"] == refused.id


async def test_radio_only_not_in_uisp(db):
    seen = _lr(db, "radio-orphan", status="up",
               last_discovered_at=_now(), uisp_synced_at=None)
    # Connu de UISP → pas « hors UISP ».
    _lr(db, "in-uisp", status="up", last_discovered_at=_now(), uisp_synced_at=_now())
    # Jamais vu par le radio → pas concerné.
    _lr(db, "never-radio", status="unknown", last_discovered_at=None, uisp_synced_at=None)
    await db.flush()

    rows = await svc.get_radio_only_not_in_uisp(db)

    names = {r["name"] for r in rows}
    assert names == {"radio-orphan"}
    assert rows[0]["id"] == seen.id
