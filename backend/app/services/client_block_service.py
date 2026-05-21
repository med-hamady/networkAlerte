"""Client internet block — SSH-enforced on the client LR, two flavours.

Mechanisms (Lr.block_mode)
--------------------------
A client sits behind its LR; the supervisor reaches the LR *through the radio*,
not the client-facing path, so both mechanisms below leave it manageable:

  - ``full``          : shut the LR's LAN port (`lan_interface`). Total cut.
                        The interface to shut is device-specific and the SSH
                        layer refuses one that would carry the management/SSH
                        path (see ssh_service._collect_forbidden_ifaces) — on
                        airMAX it's eth0, on LTU eth0.1, never the static guess.
  - ``whatsapp_only`` : an iptables allowlist on the LR (DNS + Meta/WhatsApp
                        ranges RETURN, the rest DROP) so the client keeps
                        WhatsApp (to reach support / pay) while the rest of the
                        internet is cut. Touches no interface → cannot lock the
                        supervisor out. Caveat: Meta IP space is shared, so
                        Facebook/Instagram also pass (documented, accepted).

Intent vs enforcement
---------------------
`Lr.client_blocked` is the operator's *intent*, `block_mode` the flavour, and
`client_block_enforced_at` the last time it was actually (re)asserted. They
deliberately decouple:

  - Block while the LR is briefly unreachable → intent persists, enforcement
    pending; `enforce_blocked_clients` (scheduler job) keeps retrying.
  - LR reboots → port back UP / iptables flushed; the enforcement job
    re-asserts the active mode within one interval → a block survives a reboot.

This is the real mechanism behind the removed no-op `devices.is_suspended`
flag: a stored boolean with nothing enforcing it was useless.
"""

import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Lr
from app.services import ssh_service

logger = logging.getLogger(__name__)

# Block flavours — persisted on Lr.block_mode.
MODE_FULL = "full"
MODE_WHATSAPP = "whatsapp_only"
VALID_MODES = (MODE_FULL, MODE_WHATSAPP)

# Per-family default client LAN interface, field-verified 2026-05-19:
#   LTU LR family terminates the customer on a VLAN sub-interface (eth0.1 → br1).
#     Their physical eth0 carries the management bridge (eth0.2 → br0), so
#     shutting eth0 would lock the supervisor out — the dynamic guard refuses
#     it, but the block then simply fails until lan_interface is corrected.
#   airMAX LiteBeam family terminates the customer on the plain eth0; the radio
#     ath0 carries the management bridge, so eth0 is safe to shut.
# Single source of truth: discovery_service sets this at Lr creation, and the
# accompanying migration m4e5f6a7b8c9 backfills existing rows the same way.
_LTU_VARIANTS = frozenset({"ltu_lr", "ltu_instant", "ltu_lite"})


def default_lan_interface(model_variant: str) -> str:
    """Return the right client LAN interface for a freshly discovered LR.

    LTU variants → ``eth0.1`` (VLAN sub-interface), airMAX/anything else →
    ``eth0``. The operator can still override via PUT /devices/{id} after a
    site-specific verification (`ip -o addr show` + `brctl show` on the LR).
    """
    return "eth0.1" if model_variant in _LTU_VARIANTS else "eth0"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _has_ssh(lr: Lr) -> bool:
    return bool(lr.ssh_username and lr.ssh_password)


def _pin_fp(lr: Lr, ok: bool, observed_fp: str | None) -> None:
    """Pin the host key on first-seen (TOFU). Caller commits."""
    if ok and observed_fp and lr.ssh_host_fingerprint != observed_fp:
        lr.ssh_host_fingerprint = observed_fp


def _promote_password(lr: Lr, primary: str, used: str | None) -> None:
    """Auto-heal: persist the fallback password that just authenticated.

    Called after every SSH operation that supports fallbacks. When ``used``
    differs from ``primary`` the LR was running on an old password — record
    the working one so the next cycle authenticates on the first try.
    """
    if used and used != primary:
        logger.info(
            "client_block: LR '%s' (%s) — fallback SSH password succeeded, "
            "promoting on LR row.",
            lr.name, lr.ip_address,
        )
        lr.ssh_password = used


async def _set_full(lr: Lr, cut: bool) -> tuple[bool, str]:
    """Shut (cut=True) / restore (cut=False) the LR's LAN port over SSH."""
    settings = get_settings()
    primary_pw = lr.ssh_password
    ok, msg, observed_fp, used_pw = await ssh_service.set_lan_interface(
        host=lr.ip_address,
        port=lr.ssh_port or 22,
        username=lr.ssh_username,
        password=primary_pw,
        interface=lr.lan_interface,
        bring_up=not cut,
        expected_fingerprint=lr.ssh_host_fingerprint,
        fallback_passwords=settings.lr_fallback_password_list,
    )
    _pin_fp(lr, ok, observed_fp)
    _promote_password(lr, primary_pw, used_pw)
    return ok, msg


async def _set_whatsapp(lr: Lr, on: bool) -> tuple[bool, str]:
    """Install (on=True) / remove (on=False) the 3-layer WhatsApp-only filter.

    The mechanism mixes DNAT + dnsmasq deny + iptables filter — see
    ``ssh_service._set_whatsapp_only_sync``. Both ``allow_cidrs`` (Meta IP
    allowlist) and ``deny_domains`` (FB/IG/etc to DNS-poison) come from
    settings so an operator can tune them without redeploying.
    """
    settings = get_settings()
    primary_pw = lr.ssh_password
    ok, msg, observed_fp, used_pw = await ssh_service.set_whatsapp_only(
        host=lr.ip_address,
        port=lr.ssh_port or 22,
        username=lr.ssh_username,
        password=primary_pw,
        enable=on,
        allow_cidrs=settings.whatsapp_allow_cidr_list,
        deny_domains=settings.blocked_domains_whatsapp_only_list,
        expected_fingerprint=lr.ssh_host_fingerprint,
        fallback_passwords=settings.lr_fallback_password_list,
    )
    _pin_fp(lr, ok, observed_fp)
    _promote_password(lr, primary_pw, used_pw)
    return ok, msg


async def _assert_block(lr: Lr) -> tuple[bool, str]:
    """Re-assert the block per lr.block_mode (single SSH round-trip).

    This is the hot path used by the enforcement job every cycle — it only
    enforces the *active* mechanism, it does not clean the other one.
    """
    if lr.block_mode == MODE_WHATSAPP:
        return await _set_whatsapp(lr, on=True)
    return await _set_full(lr, cut=True)


async def _clear_block(lr: Lr) -> tuple[bool, str]:
    """Fully restore internet — undo *both* mechanisms (idempotent).

    Operator action, not the hot loop: doing both (port up + filter removed)
    guarantees a clean state even if block_mode was switched while blocked.
    """
    up_ok, up_msg = await _set_full(lr, cut=False)
    wa_ok, wa_msg = await _set_whatsapp(lr, on=False)
    if up_ok and wa_ok:
        return True, "Port LAN remonté et filtre WhatsApp retiré."
    return False, f"Port LAN: {up_msg} | Filtre WhatsApp: {wa_msg}"


def _resolve_mode(mode: str | None) -> str:
    """Validate the requested mode, falling back to the configured default."""
    if mode in VALID_MODES:
        return mode
    default = get_settings().client_block_default_mode
    return default if default in VALID_MODES else MODE_FULL


async def _neutralize_other(lr: Lr) -> None:
    """Best-effort: undo the mechanism the *other* mode would have left.

    Makes a mode switch (full ↔ whatsapp_only) clean. Failures are logged but
    never fail the block — the chosen mechanism is what matters; a stale
    artifact of the other one is harmless (port already up / no iptables rule).
    """
    if lr.block_mode == MODE_WHATSAPP:
        ok, msg = await _set_full(lr, cut=False)  # ensure port not left down
    else:
        ok, msg = await _set_whatsapp(lr, on=False)  # ensure filter removed
    if not ok:
        logger.info(
            "block_client: nettoyage de l'autre mécanisme non concluant "
            "pour LR '%s' (id=%d) : %s — sans conséquence",
            lr.name, lr.id, msg,
        )


async def block_client(
    session: AsyncSession, lr: Lr, reason: str | None, mode: str | None = None
) -> tuple[bool, str]:
    """Cut a client's internet on its LR — mode 'full' or 'whatsapp_only'.

    Records the block intent + flavour, then tries to enforce it immediately.
    If the LR is unreachable the intent is still persisted and the enforcement
    job retries — so the return `ok` reflects *enforcement*, not intent.
    Refuses outright when the LR has no SSH credentials: an unenforceable block
    is exactly the trap we're avoiding.
    """
    if not _has_ssh(lr):
        return (
            False,
            f"Le LR {lr.name} n'a pas d'identifiants SSH — impossible de "
            f"couper le client. Configure ssh_username/ssh_password via "
            f"PUT /api/v1/devices/{lr.id}.",
        )

    resolved = _resolve_mode(mode)
    already = lr.client_blocked
    lr.client_blocked = True
    lr.block_mode = resolved
    if not already:
        lr.client_blocked_at = _now()
    lr.client_blocked_reason = (reason or "").strip() or None

    await _neutralize_other(lr)
    ok, msg = await _assert_block(lr)
    label = "WhatsApp autorisé" if resolved == MODE_WHATSAPP else "coupure totale"
    if ok:
        lr.client_block_enforced_at = _now()
        await session.commit()
        logger.warning(
            "CLIENT BLOCK appliqué — LR '%s' (id=%d, %s) mode=%s — motif: %s",
            lr.name, lr.id, lr.ip_address, resolved,
            lr.client_blocked_reason or "(non précisé)",
        )
        return True, f"Client {lr.name} bloqué ({label}). {msg}"

    await session.commit()
    logger.warning(
        "CLIENT BLOCK enregistré mais NON appliqué — LR '%s' (id=%d) mode=%s : "
        "%s — le job de renforcement réessaiera",
        lr.name, lr.id, resolved, msg,
    )
    return (
        False,
        f"Blocage ({label}) enregistré pour {lr.name} mais NON appliqué "
        f"({msg}). Le job de renforcement réessaiera automatiquement dès que "
        f"le LR sera joignable.",
    )


async def unblock_client(session: AsyncSession, lr: Lr) -> tuple[bool, str]:
    """Restore a client's internet by bringing its LR's LAN port back up.

    Intent is cleared first so the enforcement job stops re-cutting. If the
    SSH bring-up fails the port may stay down until the operator retries (the
    LR is normally reachable via the radio, so this is rare).
    """
    was_blocked = lr.client_blocked
    lr.client_blocked = False
    lr.client_blocked_reason = None
    lr.client_blocked_at = None
    lr.client_block_enforced_at = None

    if not _has_ssh(lr):
        await session.commit()
        return (
            False,
            f"Intention de blocage levée pour {lr.name}, mais sans identifiants "
            f"SSH l'accès n'a pas pu être rétabli sur le LR. Configure les "
            f"credentials puis relance le déblocage.",
        )

    ok, msg = await _clear_block(lr)
    await session.commit()
    if ok:
        logger.warning(
            "CLIENT UNBLOCK — LR '%s' (id=%d, %s) accès rétabli",
            lr.name, lr.id, lr.ip_address,
        )
        return True, f"Accès internet rétabli pour {lr.name}. {msg}"

    logger.warning(
        "CLIENT UNBLOCK — LR '%s' (id=%d) : intention levée mais accès non "
        "entièrement rétabli : %s",
        lr.name, lr.id, msg,
    )
    suffix = "" if was_blocked else " (le client n'était pas marqué bloqué)"
    return (
        False,
        f"Déblocage enregistré pour {lr.name} mais l'accès n'a pas pu être "
        f"entièrement rétabli ({msg}). Relance le déblocage quand le LR sera "
        f"joignable.{suffix}",
    )


async def enforce_blocked_clients(session: AsyncSession) -> int:
    """Re-assert the active block on every LR marked blocked.

    Idempotent per mode: re-shutting a down port / re-applying the same
    iptables chain is a no-op. The point is reboot recovery — a rebooted LR
    comes back with its port UP and its iptables flushed, and this re-asserts
    the block within one cycle — plus retrying blocks that couldn't be enforced
    at click time. Returns the count successfully (re)enforced this pass.
    """
    result = await session.execute(select(Lr).where(Lr.client_blocked.is_(True)))
    blocked = list(result.scalars().all())
    if not blocked:
        return 0

    enforced = 0
    for lr in blocked:
        if not _has_ssh(lr):
            logger.warning(
                "enforce_blocked_clients: LR '%s' (id=%d) bloqué mais sans "
                "identifiants SSH — blocage non garanti",
                lr.name, lr.id,
            )
            continue
        ok, msg = await _assert_block(lr)
        if ok:
            lr.client_block_enforced_at = _now()
            enforced += 1
            logger.info(
                "enforce_blocked_clients: LR '%s' (id=%d) blocage maintenu "
                "(mode=%s)",
                lr.name, lr.id, lr.block_mode,
            )
        else:
            logger.warning(
                "enforce_blocked_clients: LR '%s' (id=%d) non renforcé "
                "(mode=%s) : %s",
                lr.name, lr.id, lr.block_mode, msg,
            )
        await session.commit()

    return enforced
