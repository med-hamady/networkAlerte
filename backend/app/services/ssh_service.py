"""
SSH diagnostic helpers — synchronous paramiko wrapped in asyncio.to_thread().

Host-key handling
-----------------
We do NOT trust paramiko's `AutoAddPolicy` (which silently accepts any host
key, leaving the supervisor open to MITM on the LAN segment). The fingerprint
check lives inline in `_open_transport`:

  - The fingerprint of the key the device just presented is always recorded
    and returned as the third tuple element.
  - When the caller pinned `expected_fingerprint`, a mismatch raises
    `_FingerprintMismatchError` and aborts the connect.
  - When the caller passes `expected_fingerprint=None` (TOFU — Trust On First
    Use), the key is accepted once and the observed value is returned so the
    caller can persist it on the Device row for next time.

Why we bypass SSHClient
-----------------------
`SSHClient.connect()` always uses `auth_password(fallback=True)`, which probes
`auth_none` before sending the password. Several airOS dropbear builds reject
the subsequent password attempt after that probe — OpenSSH works because it
never sends `auth_none`. We open a `Transport` ourselves and call
`auth_password(fallback=False)` directly.
"""

import asyncio
import base64
import contextlib
import hashlib
import ipaddress
import json
import logging
import re
import shlex
import socket
import threading

import paramiko

logger = logging.getLogger(__name__)


def _fingerprint(key: paramiko.PKey) -> str:
    """Return the OpenSSH-style SHA256 fingerprint of a paramiko PKey."""
    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


class _FingerprintMismatchError(paramiko.SSHException):
    """Raised when the host key fingerprint does not match the pinned one."""


def _open_transport(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
    timeout: int = 6,
    fallback_passwords: list[str] | None = None,
) -> tuple[paramiko.Transport, str, str]:
    """Open an authenticated SSH Transport and return (transport, fp, used_pw).

    We bypass `SSHClient.connect()` because it always calls `auth_password` with
    `fallback=True`, which makes paramiko probe an `auth_none` first. Several
    airOS dropbear builds reject the subsequent password attempt after that
    probe — OpenSSH works because it never sends `auth_none`. Calling
    `auth_password(fallback=False)` directly skips the probe and authenticates
    on the first try.

    Fallback ladder
    ---------------
    When ``fallback_passwords`` is provided and the primary ``password`` auth
    fails with ``AuthenticationException``, we close the transport and retry
    against a fresh transport using each fallback in order. The third tuple
    element is the password that actually authenticated — callers compare it
    to the primary and persist it on the LR row when it differs, so old LRs
    auto-heal to the right password after one successful cycle.

    Any non-auth error (timeout, host-key mismatch, network) raises straight
    away — we only ladder through fallbacks on real auth failures.
    """
    candidates: list[str] = [password]
    if fallback_passwords:
        for fp in fallback_passwords:
            if fp and fp not in candidates:
                candidates.append(fp)

    last_auth_exc: paramiko.AuthenticationException | None = None
    for idx, candidate in enumerate(candidates):
        sock = socket.create_connection((host, port), timeout=timeout)
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=timeout)
            server_key = transport.get_remote_server_key()
            observed = _fingerprint(server_key)
            if expected_fingerprint is None:
                # Only warn on the first attempt — successive retries land on the
                # same host so re-logging adds noise without information.
                if idx == 0:
                    logger.warning(
                        "SSH TOFU: accepting first-seen host key %s for %s — "
                        "verify out-of-band and persist this value on the Device row",
                        observed, host,
                    )
            elif observed != expected_fingerprint:
                raise _FingerprintMismatchError(
                    f"Host key mismatch for {host}: "
                    f"expected {expected_fingerprint}, got {observed}",
                )
            transport.auth_password(username, candidate, fallback=False)
        except paramiko.AuthenticationException as exc:
            transport.close()
            last_auth_exc = exc
            continue
        except Exception:
            transport.close()
            raise
        if idx > 0:
            logger.warning(
                "SSH auth: primary password rejected on %s — fallback #%d "
                "succeeded. LR should be updated to use the working password.",
                host, idx,
            )
        return transport, observed, candidate

    # All candidates exhausted without authenticating.
    raise last_auth_exc or paramiko.AuthenticationException(
        f"SSH authentication failed against {host} (no passwords supplied)"
    )


def _exec(transport: paramiko.Transport, command: str, timeout: int) -> int:
    """Run a command on a new channel and return its exit code (-1 on timeout).

    Same unbounded-wait trap as :func:`_exec_capture` — ``settimeout`` does not
    cover ``recv_exit_status()``, which waits on an Event with NO timeout
    ("will hang indefinitely", per paramiko's own docstring). A peer whose radio
    link dies mid-session never sends its exit-status and parks this thread for
    good. Here that would freeze ``client_block_enforcement_job`` (it re-asserts
    every client block every 120 s, over SSH, and gates paying customers'
    internet), and ``lan_discovery``.

    Returns **-1** rather than raising, deliberately: paramiko itself returns -1
    for "no exit status provided", and every caller already reads a non-zero code
    as "command failed" → ``(False, msg)`` → which
    ``client_block_service._structural_failure`` classifies as **transient**
    (it only matches auth / host-key), i.e. *keep retrying*. That is exactly the
    right verdict for a dead session — and it needs no change at any call site,
    which is what we want on the path that cuts a customer's internet. Raising
    instead would propagate uncaught: none of the five call sites wraps _exec.
    """
    channel = transport.open_session()
    try:
        channel.settimeout(timeout)
        channel.exec_command(command)
        if not channel.status_event.wait(timeout):
            logger.warning(
                "SSH exec: exit-status non reçu après %ss — session probablement "
                "morte (lien radio tombé en cours) — commande: %s",
                timeout, command,
            )
            return -1
        return channel.recv_exit_status()
    finally:
        channel.close()


# Plafond dur d'une session de sonde, une fois le SSH établi et authentifié.
# Budget réel : board.info 5 s + wstalist 8 s + ping 20 s ≈ 33 s → 60 s est large.
_PROBE_SESSION_HARD_LIMIT_S = 60


def _start_session_watchdog(
    transport: paramiko.Transport, host: str, limit: int
) -> threading.Timer:
    """Arme un timer qui ferme de force le transport après ``limit`` s.

    Garantie de sortie du thread. Pourquoi un watchdog plutôt que des timeouts :
    paramiko a des attentes qu'on ne **peut pas** borner par paramètre.
    ``Channel.exec_command()`` appelle ``_wait_for_event()`` → ``self.event.wait()``
    — sans timeout, et sans moyen d'en passer un. Elle ne rend la main que si le
    transport meurt. Or un LR dont le lien radio tombe garde son TCP vivant côté
    serveur : le transport reste ``active`` et le thread attend pour toujours.

    Fermer le transport depuis un autre thread met ``active=False``, ce qui
    débloque **toutes** ces attentes d'un coup (open_channel, _wait_for_event,
    recv_exit_status) : elles lèvent, l'appelant remonte, le thread du pool est
    rendu. C'est le seul levier qui couvre les attentes qu'on ne maîtrise pas.

    Terrain (2026-07-16) : après avoir borné ``recv_exit_status``, il restait
    **1 LR sur 696** qui ne revenait jamais et retenait tout le fan-out jusqu'à la
    deadline globale de 600 s. C'était l'une de ces attentes-là.

    L'appelant DOIT faire ``.cancel()`` dans son ``finally`` (sinon le transport
    d'une session saine serait fermé sous ses pieds au bout de ``limit``).
    """
    def _force_close() -> None:
        logger.warning(
            "SSH watchdog: session %s bloquée > %ss — fermeture forcée du "
            "transport (lien radio probablement tombé en cours de session).",
            host, limit,
        )
        with contextlib.suppress(Exception):
            transport.close()

    timer = threading.Timer(limit, _force_close)
    timer.daemon = True
    timer.start()
    return timer


class SshExecTimeoutError(TimeoutError):
    """Le pair n'a jamais renvoyé l'exit-status de la commande.

    Sous-classe ``TimeoutError`` **volontairement** : tous les ``except Exception``
    / ``except OSError`` déjà en place continuent de l'attraper exactement comme
    avant, donc aucun appelant existant ne change de comportement. Le type dédié
    n'existe que pour les appelants qui doivent la distinguer d'un vrai échec de
    commande — cf. :func:`_measure_latency_via_ssh_sync`, où « pas d'exit-status »
    veut dire « je ne sais pas », surtout pas « le client n'a pas de transit ».
    """


def _exec_capture(
    transport: paramiko.Transport, command: str, timeout: int
) -> tuple[int, str]:
    """Run a command and return (exit_code, stdout stripped).

    ``timeout`` bounds BOTH the read and the exit-status wait — see below. It is
    a wall-clock bound per step, not for the whole call.
    """
    # `timeout=` est indispensable : sans lui, open_channel compare
    # `start_ts + None` et part en TypeError dès que le pair tarde.
    channel = transport.open_session(timeout=timeout)
    try:
        channel.settimeout(timeout)
        channel.exec_command(command)  # cf. _probe_session_watchdog : attente non bornée
        out = channel.makefile("rb").read().decode("utf-8", errors="replace")
        # `settimeout` covers recv() — it does NOT cover recv_exit_status(),
        # which waits on an Event with NO timeout. Paramiko's own docstring says
        # it "will hang indefinitely". So a peer that goes silent right after the
        # command was sent (a radio link dropping mid-session — routine here)
        # parks this thread forever.
        #
        # That is not a local nuisance: lr_internet_probe_job gathers over ALL
        # ~800 LRs and only persists once every probe returned, so ONE such LR
        # froze the whole cycle. Field evidence (2026-07-16): cycles at 3263 s /
        # 3690 s / 3898 s against a 95-450 s norm, which stalled the latency
        # metric for an hour at a time.
        #
        # status_event is what paramiko itself waits on; we wait on it with a
        # bound, then read the status it guards.
        if not channel.status_event.wait(timeout):
            raise SshExecTimeoutError(
                f"exit status non reçu après {timeout}s — commande: {command}"
            )
        return channel.recv_exit_status(), out.strip()
    finally:
        channel.close()


def _parse_board_model(text: str) -> str | None:
    """Extract the hardware model from ``/etc/board.info`` (airOS).

    The file is a set of ``key=value`` lines; the human model is ``board.name``
    (e.g. "LiteBeam M5"), with ``board.shortname`` (e.g. "LBE-M5") as fallback.
    Present on both airOS-M (M5) and airOS-8 (AC) — unlike the HTTP status.cgi,
    which the older M-series firmware does not serve the way the AC ones do.
    """
    if not text:
        return None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, val = line.partition("=")
            fields[key.strip()] = val.strip()
    for key in ("board.name", "board.shortname", "board.hwmodel", "board.model"):
        val = fields.get(key)
        if val:
            return val
    return None


def _read_board_model(transport: paramiko.Transport, timeout: int = 5) -> str | None:
    """Read ``/etc/board.info`` on an already-open transport and return the
    device model string, or None. Never raises — a failure here must not affect
    the caller's primary operation (this rides on an existing SSH session)."""
    try:
        code, out = _exec_capture(transport, "cat /etc/board.info", timeout)
        if code == 0 and out:
            return _parse_board_model(out)
    except Exception as exc:  # noqa: BLE001 — best-effort, never break the caller
        logger.debug("read board.info failed — %s", exc)
    return None


def _num(val: object) -> float | None:
    """Best-effort float conversion; None on any non-numeric input."""
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# Keys filled from `wstalist` — same names as ltu_api_service / airos_api_service
# so the alert engine, DeviceMetric persistence and the frontend modal all work
# unchanged.
#
# ⚠ PÉRIMÈTRE VOLONTAIREMENT RÉDUIT (2026-07-21) : depuis que les LR airMAX sont
# pollés via leur AP, celui-ci est la source de la CAPACITÉ, du DÉBIT et des
# COMPTEURS D'OCTETS — y compris pour les M5. `wstalist` ne garde que ce que
# l'AP ne sait PAS donner d'un M5 :
#   - `ccq_pct`  : l'AP n'expose aucun CCQ par station (pas de clé `ccq`).
#   - `cinr_db`  : l'AP annonce un CINR de 3 dB pour un M5 dont le SNR réel est
#     de 25 dB (son bloc `airmax` est inexploitable sur une station airOS 6,
#     comme son `linkscore` à 0). S'y fier ferait passer TOUS les M5 sous le
#     seuil critique de 10 dB → alertes massives et fausses.
#   - `noise_dbm`, `signal_dbm` : mesurés au CPE.
#
# NE PAS y remettre capacité / débit / compteurs : les deux sources écriraient
# les mêmes clés. Pour la capacité c'est une valeur qui oscille ; pour les
# compteurs c'est pire — ce sont deux compteurs cumulés d'ORIGINES DIFFÉRENTES
# (celui de l'AP pour cette station vs celui du M5 depuis SON boot), et
# `consumption_service` en calcule des deltas par `LAG()` : les entrelacer
# produirait des sauts délirants dans la consommation facturée.
_WSTALIST_METRIC_KEYS = (
    "signal_dbm", "noise_dbm", "cinr_db", "ccq_pct",
    "remote_signal_dbm", "uptime_seconds",
    # Compteurs cumulés : SOURCE DE LA CONSOMMATION, inchangée depuis toujours.
    # Ils restent ici et surtout PAS côté AP : le compteur de l'AP pour cette
    # station a une autre origine (55 Gio vs 2 Gio mesurés sur un même client),
    # et basculer de source ferait facturer l'écart au client.
    "radio_rx_bytes", "radio_tx_bytes",
)


def _parse_wstalist_metrics(text: str) -> dict[str, float | None]:
    """Parse ``wstalist`` JSON (airOS station list) into standard radio keys.

    On a CPE the list has one entry: the AP the station is linked to. Returns an
    all-None dict when there is no station / bad JSON.

    This is the metric source for airOS-M LRs (LiteBeam M5) whose firmware does
    not serve the HTTP status.cgi the AC LiteBeams do. airMAX-M has no Link
    Potential / Total Capacity concept (the ``airmax.quality/capacity`` block
    reads 0), so only the base radio indicators are filled. CINR is derived as
    signal − noise floor (SNR), matching ``snmp_service.collect_airmax_metrics``.
    """
    result: dict[str, float | None] = dict.fromkeys(_WSTALIST_METRIC_KEYS)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return result
    if not isinstance(data, list) or not data:
        return result
    sta = data[0]
    if not isinstance(sta, dict):
        return result

    result["signal_dbm"]   = _num(sta.get("signal"))
    result["noise_dbm"]    = _num(sta.get("noisefloor"))
    result["ccq_pct"]      = _num(sta.get("ccq"))
    result["uptime_seconds"] = _num(sta.get("uptime"))

    # Compteurs du CPE lui-même (interface radio) : `rx` = ce qu'il reçoit =
    # DOWNLOAD du client, conforme à la convention de `consumption_service`.
    stats = sta.get("stats")
    if isinstance(stats, dict):
        result["radio_rx_bytes"] = _num(stats.get("rx_bytes"))
        result["radio_tx_bytes"] = _num(stats.get("tx_bytes"))

    remote = sta.get("remote")
    if isinstance(remote, dict):
        result["remote_signal_dbm"] = _num(remote.get("signal"))

    # CINR ≈ SNR = signal − noise floor (dB).
    if result["signal_dbm"] is not None and result["noise_dbm"] is not None:
        result["cinr_db"] = round(result["signal_dbm"] - result["noise_dbm"], 1)
    return result


def _read_wstalist_metrics(
    transport: paramiko.Transport, timeout: int = 8
) -> dict[str, float | None] | None:
    """Run ``wstalist`` on an already-open transport and parse its radio metrics.

    Returns the metric dict (possibly all-None if no station), or None when the
    command itself failed. Never raises — this rides on an existing SSH session
    and must not break the caller's primary operation."""
    try:
        code, out = _exec_capture(transport, "wstalist", timeout)
        if code == 0 and out:
            return _parse_wstalist_metrics(out)
    except Exception as exc:  # noqa: BLE001 — best-effort, never break the caller
        logger.debug("read wstalist failed — %s", exc)
    return None


# Cheap static pre-check — obvious never-shut interfaces (radio/loopback).
# This is only a first line of defence: it is NOT sufficient on its own. Field
# verification proved the real trap is device-specific — on an LTU LR the
# management plane rides eth0 (VLAN eth0.2 → br0), so shutting `eth0` (which a
# static list happily allows) locks the supervisor out, while on an airMAX
# LiteBeam the same eth0 is the safe client port. The authoritative guard is
# `_collect_forbidden_ifaces`, computed live from the device before any cut.
_PROTECTED_IFACES = frozenset({"ath0", "wlan0", "wifi0", "wlan1", "lo"})


class _ProtectedInterfaceError(ValueError):
    """Raised when asked to shut an interface that carries radio/management."""


def _read_admin_up(transport: paramiko.Transport, interface: str) -> bool | None:
    """Return the interface's admin (IFF_UP) state, or None if undeterminable.

    Reads /sys/class/net/<if>/flags — a hex bitmask whose bit 0 (0x1) is
    IFF_UP, i.e. the *administrative* state set by `ip link set`. Unlike
    operstate this does not depend on a cable being plugged, so it correctly
    reflects an admin-down port with nothing connected.
    """
    iface = shlex.quote(interface)
    try:
        code, out = _exec_capture(
            transport, f"cat /sys/class/net/{iface}/flags", timeout=8
        )
    except Exception:
        return None
    if code != 0 or not out:
        return None
    try:
        return bool(int(out, 16) & 0x1)
    except ValueError:
        return None


_VLAN_RE = re.compile(r"^(.+)\.\d+$")


def _vlan_parent(name: str) -> str | None:
    """Return the physical parent of a VLAN sub-interface (eth0.2 → eth0)."""
    m = _VLAN_RE.match(name)
    return m.group(1) if m else None


def _bridge_members(transport: paramiko.Transport, bridge: str) -> set[str]:
    """List a bridge's member interfaces via sysfs (works on old airOS kernels)."""
    iface = shlex.quote(bridge)
    try:
        code, out = _exec_capture(
            transport, f"ls /sys/class/net/{iface}/brif 2>/dev/null", timeout=8
        )
    except Exception:
        return set()
    if code != 0 or not out:
        return set()
    return {tok for tok in out.split() if tok}


def _collect_forbidden_ifaces(transport: paramiko.Transport) -> set[str]:
    """Compute, live from the device, the interfaces that must never be shut.

    The supervisor must keep reaching the LR after the cut. The management
    path is whatever carries (a) the IP the SSH session landed on and (b) the
    default route. We expand each to its bridge members and VLAN physical
    parents, because downing a physical port also downs its VLANs and any
    bridge built on them. Returns an empty set if the device output can't be
    parsed; the caller then relies on the static denylist plus the operator's
    per-device `lan_interface` (set after the field verification step) as the
    remaining safety controls.
    """
    script = (
        'echo "SSHC=$SSH_CONNECTION"; '
        "echo ADDR; ip -o -4 addr show 2>/dev/null; "
        "echo DEF; ip route show default 2>/dev/null"
    )
    try:
        code, out = _exec_capture(transport, script, timeout=10)
    except Exception:
        return set()
    if code != 0 or not out:
        return set()

    mgmt_ip: str | None = None
    ip_to_iface: dict[str, str] = {}
    default_dev: str | None = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("SSHC="):
            parts = line[5:].split()
            # $SSH_CONNECTION = "<client_ip> <client_port> <server_ip> <server_port>"
            if len(parts) >= 3:
                mgmt_ip = parts[2]
        elif " inet " in line:
            toks = line.split()
            # "13: br0    inet 10.135.7.237/16 brd ... scope global br0"
            if len(toks) >= 4 and toks[2] == "inet":
                ip_to_iface[toks[3].split("/")[0]] = toks[1]
        elif line.startswith("default ") and " dev " in line:
            t = line.split()
            default_dev = t[t.index("dev") + 1]

    critical: set[str] = set()
    if mgmt_ip and mgmt_ip in ip_to_iface:
        critical.add(ip_to_iface[mgmt_ip])
    if default_dev:
        critical.add(default_dev)
    if not critical:
        return set()

    forbidden = set(critical)
    for c in critical:
        forbidden |= _bridge_members(transport, c)
        parent = _vlan_parent(c)
        if parent:
            forbidden.add(parent)
    # A bridge member that is itself a VLAN drags its physical parent down too
    # (LTU: br0 ← eth0.2 ← eth0 — shutting eth0 kills management).
    for member in list(forbidden):
        parent = _vlan_parent(member)
        if parent:
            forbidden.add(parent)
    return forbidden


# ── Contrôle d'identité avant toute action de blocage ────────────────────────

_IDENT_CMD = "cat /sys/class/net/*/address 2>/dev/null"

# Préfixe du message de refus. Les appelants le reconnaissent par CETTE
# constante, jamais en cherchant des mots dans la phrase : le libellé est
# destiné à un opérateur et peut être reformulé sans casser la logique.
IDENTITY_REFUSAL_PREFIX = "Identité refusée :"


def _device_macs(transport: paramiko.Transport, timeout: int = 10) -> set[str]:
    """MAC de TOUTES les interfaces de l'équipement joint, en minuscules.

    On compare sur l'ENSEMBLE des interfaces plutôt que sur une seule : le nom
    de l'interface radio dépend de la famille (ath0 sur airOS, autre ailleurs)
    et notre `mac_address` vient de la liste des stations de l'AP, donc de la
    radio. Chercher la MAC attendue parmi toutes les interfaces est robuste
    sans rien supposer du nommage.
    """
    try:
        code, out = _exec_capture(transport, _IDENT_CMD, timeout)
    except Exception as exc:  # noqa: BLE001 — l'invérifiable ne doit pas bloquer
        logger.debug("identité : lecture des MAC impossible (%s)", exc)
        return set()
    if code != 0:
        return set()
    return {
        line.strip().lower()
        for line in (out or "").splitlines()
        if len(line.strip()) == 17 and line.strip().count(":") == 5
    }


def identity_refusal(
    transport: paramiko.Transport, expected_mac: str | None, timeout: int = 10
) -> str | None:
    """`None` si on peut agir, sinon le motif du refus.

    Une fiche identifie son client par sa **MAC**, mais la session SSH part sur
    son **IP** — et une IP a pu être redonnée à un autre abonné par le DHCP
    pendant que celui-ci était éteint. Sans ce contrôle, une coupure demandée
    pour le client A tombait sur le client B, qui paie.

    ⚠️ **Invérifiable = on laisse passer.** Un firmware sans `/sys/class/net`
    rendrait sinon TOUT blocage impossible sur cette famille d'équipements —
    une panne bien pire que le risque couvert. On ne refuse que sur une preuve
    positive : des MAC lisibles, et la MAC attendue absente.

    Coût : une commande sur la session DÉJÀ ouverte. Ce qui est cher sur ces
    radios, c'est la poignée de main SSH (saturation au-delà d'environ 150
    simultanées), pas une lecture dans /sys.
    """
    if not expected_mac:
        return None
    macs = _device_macs(transport, timeout)
    if not macs:
        logger.debug("identité invérifiable (aucune MAC lisible) — action autorisée")
        return None
    if expected_mac.strip().lower() in macs:
        return None
    return (
        f"{IDENTITY_REFUSAL_PREFIX} l'équipement joint annonce {sorted(macs)}, pas la "
        f"MAC attendue {expected_mac}. L'IP de la fiche est périmée (bail DHCP "
        f"réattribué) — agir aurait touché un AUTRE abonné."
    )


def _set_iface_state_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    interface: str,
    bring_up: bool,
    expected_fingerprint: str | None,
    fallback_passwords: list[str] | None,
    expected_mac: str | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """SSH into the device and bring `interface` admin up or down.

    Idempotent: re-applying the same state is harmless (this is what lets the
    enforcement job re-assert a block every cycle / after an LR reboot). Tries
    `ip link` first, falls back to busybox `ifconfig`. Verifies via the admin
    flag, not operstate, so it stays correct on an unplugged port.

    Returns (ok, message, observed_fp, used_password). The 4th element is the
    password that actually authenticated — caller compares it to the primary
    to detect a fallback hit and persist the working password on the LR.
    """
    if not bring_up and interface in _PROTECTED_IFACES:
        return (
            False,
            f"Interface protégée '{interface}' — refus : couper cette interface "
            f"déconnecterait le superviseur du LR (radio/loopback).",
            None,
            None,
        )

    try:
        transport, observed, used_pw = _open_transport(
            host, port, username, password, expected_fingerprint,
            fallback_passwords=fallback_passwords,
        )
    except _FingerprintMismatchError as exc:
        logger.error("set_iface host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None, None
    except Exception as exc:
        logger.debug("set_iface SSH connect failed — %s — %s", host, exc)
        return False, str(exc), None, None

    # Identité : la fiche cible une MAC, la session part sur une IP. Refuser
    # avant d'agir évite de couper un abonné qui a hérité de l'adresse.
    refusal = identity_refusal(transport, expected_mac)
    if refusal is not None:
        logger.warning("Action de blocage refusée sur %s — %s", host, refusal)
        return False, refusal, observed, used_pw

    action = "up" if bring_up else "down"
    iface = shlex.quote(interface)
    try:
        # Authoritative guard — only when *cutting*. Bringing a port back up
        # can never lock us out, so unblock is never gated by this.
        if not bring_up:
            forbidden = _collect_forbidden_ifaces(transport)
            if interface in forbidden:
                return (
                    False,
                    f"Interface '{interface}' refusée : sur ce LR elle porte le "
                    f"plan de management/route par défaut (chemin SSH du "
                    f"superviseur : {sorted(forbidden)}). La couper "
                    f"verrouillerait l'accès au LR. Vérifie lan_interface — "
                    f"sur LTU c'est typiquement eth0.1, pas eth0.",
                    observed,
                    used_pw,
                )
        code = _exec(
            transport, f"ip link set dev {iface} {action}", timeout=12
        )
        if code != 0:
            # busybox airOS builds may lack `ip` — fall back to ifconfig.
            code = _exec(transport, f"ifconfig {iface} {action}", timeout=12)
        if code != 0:
            return (
                False,
                f"Échec de la commande de mise {action} de {interface} "
                f"(code {code}) — vérifier que l'utilisateur SSH est root.",
                observed,
                used_pw,
            )
        admin_up = _read_admin_up(transport, interface)
        if admin_up is not None and admin_up != bring_up:
            return (
                False,
                f"Commande acceptée mais {interface} toujours "
                f"{'DOWN' if bring_up else 'UP'} — état non appliqué.",
                observed,
                used_pw,
            )
        state_str = "UP" if bring_up else "DOWN"
        verified = "" if admin_up is None else " (vérifié)"
        return (
            True,
            f"Interface {interface} mise {state_str}{verified}.",
            observed,
            used_pw,
        )
    finally:
        transport.close()


async def set_lan_interface(
    host: str,
    port: int,
    username: str,
    password: str,
    interface: str,
    bring_up: bool,
    expected_fingerprint: str | None = None,
    fallback_passwords: list[str] | None = None,
    expected_mac: str | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """SSH into the LR and bring its LAN port admin up/down — non-blocking.

    Returns (ok, message, observed_fingerprint, used_password). Refuses
    protected interfaces (radio/management) so an operator error can't lock
    the supervisor out. ``used_password`` is the password that actually
    authenticated — when it differs from ``password`` the caller should
    persist it on the LR row.
    """
    return await asyncio.to_thread(
        _set_iface_state_sync,
        host, port, username, password, interface, bring_up,
        expected_fingerprint, fallback_passwords, expected_mac,
    )


# Dedicated iptables chain — a named chain makes the whatsapp_only block
# idempotent (flush+rebuild) and cleanly removable, and keeps it from
# entangling with whatever FORWARD rules the airOS/LTU firmware ships.
_WA_CHAIN = "CLIENTBLOCK"
# busybox/airOS non-login shells often miss /sbin in PATH where iptables lives.
_IPT_PATH = "PATH=$PATH:/sbin:/usr/sbin"


_DOMAIN_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.\-]*$")
_DNSMASQ_CONF = "/etc/dnsmasq.conf"
_DNS_BLOCK_BEGIN = "CLIENTBLOCK_BEGIN"
_DNS_BLOCK_END = "CLIENTBLOCK_END"

# Per-category content filter (independent of whatsapp_only). Uses its OWN
# dnsmasq marker pair so the two mechanisms never clobber each other's block in
# /etc/dnsmasq.conf. DNS-poison only (no iptables DROP chain) — the client keeps
# the rest of the internet; only the selected services resolve to 0.0.0.0.
_CONTENT_DNS_BEGIN = "CONTENTBLOCK_BEGIN"
_CONTENT_DNS_END = "CONTENTBLOCK_END"

# Direction of the content filter (see _set_content_block_sync).
_CONTENT_MODE_DENY = "denylist"    # allow everything except the listed services
_CONTENT_MODE_ALLOW = "allowlist"  # block everything except the listed services

def _detect_client_context(
    transport: paramiko.Transport,
) -> tuple[str | None, str | None]:
    """Return (client_subnet, lr_gateway_ip) — e.g. ('172.16.0.0/24', '172.16.0.1').

    The client sits behind the LR's gateway IP. We pick the RFC1918 address
    that is NOT on the management interface (the one carrying the SSH session)
    — on both observed topologies that is the customer LAN (172.16.0.1/24 on
    LTU br1, on airMAX eth0). (None, None) if it can't be determined.
    """
    script = 'echo "SSHC=$SSH_CONNECTION"; ip -o -4 addr show 2>/dev/null'
    try:
        code, out = _exec_capture(transport, script, timeout=10)
    except Exception:
        return None, None
    if code != 0 or not out:
        return None, None

    mgmt_ip: str | None = None
    candidates: list[tuple[str, str]] = []  # (iface, cidr)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("SSHC="):
            parts = line[5:].split()
            if len(parts) >= 3:
                mgmt_ip = parts[2]
        elif " inet " in line:
            toks = line.split()
            if len(toks) >= 4 and toks[2] == "inet":
                candidates.append((toks[1], toks[3]))

    mgmt_iface: str | None = None
    for iface, cidr in candidates:
        if mgmt_ip and cidr.split("/")[0] == mgmt_ip:
            mgmt_iface = iface
            break

    for iface, cidr in candidates:
        if iface == mgmt_iface:
            continue
        try:
            net = ipaddress.ip_interface(cidr)
        except ValueError:
            continue
        if net.ip.is_private and not net.ip.is_loopback:
            return str(net.network), str(net.ip)
    return None, None


def _set_whatsapp_only_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    enable: bool,
    allow_cidrs: list[str],
    deny_domains: list[str],
    expected_fingerprint: str | None,
    fallback_passwords: list[str] | None,
    expected_mac: str | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """Install/remove the WhatsApp-only restriction on the LR (3 layers).

    The Meta IP allowlist alone is insufficient — WhatsApp shares Meta's IP
    space with Facebook/Instagram, so allowing the Meta CIDRs lets FB/IG
    through. To actually separate them on stock airOS 8 (which has no L7
    matchers — no ``string``, no ``connmark``), we combine three layers:

      1. ``iptables -t nat PREROUTING`` — DNAT every DNS query from the
         client subnet to the LR's own dnsmasq. A client that hardcodes
         8.8.8.8 still lands on the LR resolver; bypass closed.
      2. ``/etc/dnsmasq.conf`` — append ``address=/<domain>/0.0.0.0`` for
         FB/IG/Messenger/etc. so they resolve to an unreachable address
         while ``*.whatsapp.net`` and ``*.whatsapp.com`` resolve normally.
         Field quirk (airOS 8 dnsmasq, 2026-05-19): ``kill -HUP`` does NOT
         pick up new ``address=`` directives — we must ``killall dnsmasq``
         and let airOS respawn it (which it does within a second).
      3. ``iptables FORWARD`` — CLIENTBLOCK chain RETURNs DNS + Meta CIDRs,
         DROPs the rest. Catches direct-IP attempts that skipped DNS.

    Idempotent end-to-end: the enforcement job re-asserts every cycle, so a
    LR reboot (which regenerates ``/etc/dnsmasq.conf``) recovers in the next
    pass. Touches no interface, so the supervisor cannot be locked out.
    """
    try:
        transport, observed, used_pw = _open_transport(
            host, port, username, password, expected_fingerprint,
            fallback_passwords=fallback_passwords,
        )
    except _FingerprintMismatchError as exc:
        logger.error("whatsapp_only host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None, None
    except Exception as exc:
        logger.debug("whatsapp_only SSH connect failed — %s — %s", host, exc)
        return False, str(exc), None, None

    # Identité : la fiche cible une MAC, la session part sur une IP. Refuser
    # avant d'agir évite de couper un abonné qui a hérité de l'adresse.
    refusal = identity_refusal(transport, expected_mac)
    if refusal is not None:
        logger.warning("Action de blocage refusée sur %s — %s", host, refusal)
        return False, refusal, observed, used_pw

    try:
        subnet, lr_ip = _detect_client_context(transport)
        if subnet is None or lr_ip is None:
            return (
                False,
                "Sous-réseau client introuvable sur le LR — impossible de "
                "poser le filtre WhatsApp. Vérifie que le LR route bien un "
                "réseau client privé.",
                observed,
                used_pw,
            )

        # Validate inputs before shelling out — never trust caller arrays.
        valid_cidrs: list[str] = []
        for c in allow_cidrs:
            c = c.strip()
            try:
                ipaddress.ip_network(c, strict=False)
            except ValueError:
                continue
            valid_cidrs.append(c)
        valid_domains: list[str] = [
            d.strip() for d in deny_domains
            if d and _DOMAIN_RE.match(d.strip())
        ]

        net_q = shlex.quote(subnet)
        lr_q = shlex.quote(lr_ip)
        chain = _WA_CHAIN
        cidrs_str = " ".join(shlex.quote(c) for c in valid_cidrs)
        domains_str = " ".join(shlex.quote(d) for d in valid_domains)
        begin = _DNS_BLOCK_BEGIN
        end = _DNS_BLOCK_END
        conf = shlex.quote(_DNSMASQ_CONF)

        if enable:
            # Build the enable script. Three layers in order: DNAT → dnsmasq → filter.
            script = (
                f"{_IPT_PATH}; "
                f"SUBNET={net_q}; LR_IP={lr_q}; CHAIN={chain}; "
                f'DOMAINS="{domains_str}"; CIDRS="{cidrs_str}"; '
                # 1) DNAT — capture DNS bypass attempts
                f"for p in udp tcp; do "
                f"  iptables -t nat -C PREROUTING -s $SUBNET -p $p --dport 53 "
                f"  ! -d $LR_IP -j DNAT --to-destination $LR_IP 2>/dev/null "
                f"  || iptables -t nat -I PREROUTING 1 -s $SUBNET -p $p "
                f"  --dport 53 ! -d $LR_IP -j DNAT --to-destination $LR_IP; "
                f"done; "
                # 2) dnsmasq — NXDOMAIN-like answers for FB/IG/etc
                f"if ! grep -q {begin} {conf}; then "
                f"  echo '' >> {conf}; "
                f"  echo '# >>> {begin} (auto) >>>' >> {conf}; "
                f"  for d in $DOMAINS; do "
                f'    echo "address=/$d/0.0.0.0" >> {conf}; '
                f"  done; "
                f"  echo '# <<< {end} <<<' >> {conf}; "
                # killall (NOT SIGHUP) — field-verified necessity on airOS 8
                f"  killall dnsmasq 2>/dev/null || true; "
                f"fi; "
                # 3) Filter — Meta CIDR allowlist + DROP
                f"iptables -N $CHAIN 2>/dev/null; "
                f"iptables -F $CHAIN; "
                f"iptables -A $CHAIN -p udp --dport 53 -j RETURN; "
                f"iptables -A $CHAIN -p tcp --dport 53 -j RETURN; "
                f"for c in $CIDRS; do "
                f"  iptables -A $CHAIN -d $c -j RETURN; "
                f"done; "
                f"iptables -A $CHAIN -j DROP; "
                f"iptables -C FORWARD -s $SUBNET -j $CHAIN 2>/dev/null "
                f"|| iptables -I FORWARD 1 -s $SUBNET -j $CHAIN"
            )
            verify = (
                f"{_IPT_PATH}; "
                f"iptables -C FORWARD -s {net_q} -j {chain} && "
                f"grep -q {begin} {conf}"
            )
        else:
            # Reverse all three layers — idempotent.
            script = (
                f"{_IPT_PATH}; "
                f"SUBNET={net_q}; LR_IP={lr_q}; CHAIN={chain}; "
                # Filter
                f"while iptables -D FORWARD -s $SUBNET -j $CHAIN 2>/dev/null; "
                f"do :; done; "
                f"iptables -F $CHAIN 2>/dev/null; "
                f"iptables -X $CHAIN 2>/dev/null; "
                # DNAT
                f"for p in udp tcp; do "
                f"  while iptables -t nat -D PREROUTING -s $SUBNET -p $p "
                f"  --dport 53 ! -d $LR_IP -j DNAT --to-destination $LR_IP "
                f"  2>/dev/null; do :; done; "
                f"done; "
                # dnsmasq
                f"sed -i '/{begin}/,/{end}/d' {conf} 2>/dev/null; "
                f"killall dnsmasq 2>/dev/null || true; "
                f"true"
            )
            verify = (
                f"{_IPT_PATH}; "
                f"! iptables -C FORWARD -s {net_q} -j {chain} 2>/dev/null && "
                f"! grep -q {begin} {conf}"
            )

        code = _exec(transport, f"sh -c {shlex.quote(script)}", timeout=25)
        if code != 0 and enable:
            return (
                False,
                f"Échec de l'application du filtre WhatsApp-only (code {code}) — "
                f"vérifier que l'utilisateur SSH est root et qu'iptables existe.",
                observed,
                used_pw,
            )
        vcode = _exec(transport, f"sh -c {shlex.quote(verify)}", timeout=12)
        if vcode != 0:
            state = "posé" if enable else "retiré"
            return (
                False,
                f"Commande acceptée mais le filtre WhatsApp-only n'a pas été "
                f"{state} correctement (vérification KO).",
                observed,
                used_pw,
            )
        if enable:
            msg = (
                f"Filtre WhatsApp-only appliqué sur {subnet} : DNS redirigé vers "
                f"{lr_ip}, {len(valid_domains)} domaine(s) FB/IG bloqué(s) en "
                f"DNS, {len(valid_cidrs)} plage(s) Meta autorisée(s) en IP."
            )
        else:
            msg = (
                f"Filtre WhatsApp-only retiré sur {subnet} (DNS, dnsmasq, "
                f"iptables : tout rétabli)."
            )
        return True, msg, observed, used_pw
    finally:
        transport.close()


async def set_whatsapp_only(
    host: str,
    port: int,
    username: str,
    password: str,
    enable: bool,
    allow_cidrs: list[str],
    deny_domains: list[str],
    expected_fingerprint: str | None = None,
    fallback_passwords: list[str] | None = None,
    expected_mac: str | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """SSH into the LR and apply/remove the 3-layer WhatsApp-only restriction.

    See ``_set_whatsapp_only_sync`` for the mechanism. ``allow_cidrs`` are the
    Meta IP ranges left reachable; ``deny_domains`` are DNS names resolved to
    0.0.0.0 by the LR's dnsmasq to neutralise FB/IG which would otherwise pass
    via the IP allowlist (they share Meta's IP space).

    Returns (ok, message, observed_fp, used_password).
    """
    return await asyncio.to_thread(
        _set_whatsapp_only_sync,
        host, port, username, password,
        enable, allow_cidrs, deny_domains, expected_fingerprint,
        fallback_passwords, expected_mac,
    )


def _set_content_block_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    domains: list[str],
    keep_dnat: bool,
    expected_fingerprint: str | None,
    fallback_passwords: list[str] | None,
    mode: str = _CONTENT_MODE_DENY,
    allow_resolver: str = "8.8.8.8",
    expected_mac: str | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """Apply a per-category content filter on the LR — DNS-only, declarative.

    Two directions, both driven by the same ``domains`` union:

      - ``denylist``  : the client keeps full internet, only ``domains`` resolve
                        to 0.0.0.0 (``address=/<domain>/0.0.0.0``).
      - ``allowlist`` : the reverse — a catch-all ``address=/#/0.0.0.0`` poisons
                        *every* name, and each allowed domain is excepted with
                        ``server=/<domain>/<resolver>`` so it resolves normally.
                        dnsmasq matches the most specific rule, so the per-domain
                        exceptions win over the ``#`` wildcard.

    Two layers:

      1. ``iptables -t nat PREROUTING`` DNAT — force the client subnet's DNS to
         the LR's own dnsmasq so a hardcoded 8.8.8.8 can't bypass the poison.
         This rule is *shared* with the whatsapp_only mode (identical match), so
         ``keep_dnat`` tells us whether another mechanism still needs it when we
         clear the content filter.
      2. ``/etc/dnsmasq.conf`` — a CONTENTBLOCK-marked block, then
         ``killall dnsmasq`` (NOT SIGHUP — airOS 8 quirk).

    ⚠ ``allowlist`` is inherently weaker than ``denylist``: blocking "everything
    else" by DNS cannot stop a client that dials a raw IP or resolves over DoH,
    and the catch-all also poisons the *LR's own* name resolution (management is
    unaffected — the supervisor reaches the LR by IP). Blocking a named service
    is exact; allowing only a named service is best-effort.

    Declarative: the desired ``domains`` set is fingerprinted into the BEGIN
    marker, so an unchanged filter is a pure no-op (no rewrite, no dnsmasq
    restart) while a changed selection — or a LR reboot that regenerated
    dnsmasq.conf — is rebuilt on the next pass. That guard matters: the
    enforcement job calls this every 120s. An empty ``domains`` list removes the
    filter entirely. Touches no interface and installs no DROP rule → cannot
    lock the supervisor out.

    Returns (ok, message, observed_fp, used_password).
    """
    try:
        transport, observed, used_pw = _open_transport(
            host, port, username, password, expected_fingerprint,
            fallback_passwords=fallback_passwords,
        )
    except _FingerprintMismatchError as exc:
        logger.error("content_block host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None, None
    except Exception as exc:
        logger.debug("content_block SSH connect failed — %s — %s", host, exc)
        return False, str(exc), None, None

    # Identité : la fiche cible une MAC, la session part sur une IP. Refuser
    # avant d'agir évite de couper un abonné qui a hérité de l'adresse.
    refusal = identity_refusal(transport, expected_mac)
    if refusal is not None:
        logger.warning("Action de blocage refusée sur %s — %s", host, refusal)
        return False, refusal, observed, used_pw

    try:
        subnet, lr_ip = _detect_client_context(transport)
        if subnet is None or lr_ip is None:
            return (
                False,
                "Sous-réseau client introuvable sur le LR — impossible de poser "
                "le filtre de contenu. Vérifie que le LR route bien un réseau "
                "client privé (mode routeur).",
                observed,
                used_pw,
            )

        valid_domains: list[str] = [
            d.strip() for d in domains if d and _DOMAIN_RE.match(d.strip())
        ]

        net_q = shlex.quote(subnet)
        lr_q = shlex.quote(lr_ip)
        domains_str = " ".join(shlex.quote(d) for d in valid_domains)
        begin = _CONTENT_DNS_BEGIN
        end = _CONTENT_DNS_END
        conf = shlex.quote(_DNSMASQ_CONF)
        enable = bool(valid_domains)

        # Fingerprint of the desired domain set, stamped into the BEGIN marker.
        # The enforcement job runs every 120s: without this, every cycle would
        # rewrite the block and `killall dnsmasq`, cutting the client's DNS
        # every 2 minutes. Grepping for the exact stamp makes an unchanged
        # filter a pure no-op, while a *changed* category selection produces a
        # different stamp and is therefore re-applied (which is why we stamp a
        # hash rather than just grepping for the plain marker).
        # The mode is part of the stamp: the same domain set means the exact
        # OPPOSITE policy in allowlist vs denylist, so a direction switch must
        # produce a different digest and force a rewrite.
        digest = hashlib.sha1(
            f"{mode}|{','.join(sorted(valid_domains))}".encode()
        ).hexdigest()[:12]
        begin_tag = f"{begin} {digest}"
        tag_q = shlex.quote(begin_tag)
        marker_q = shlex.quote(begin)

        if enable:
            # The only difference between the two directions is what we write
            # inside the marked block; DNAT, stamping and restart are identical.
            if mode == _CONTENT_MODE_ALLOW:
                dns_lines = (
                    # Catch-all first: every name → 0.0.0.0 …
                    f"  echo 'address=/#/0.0.0.0' >> {conf}; "
                    # … then the exceptions, which dnsmasq prefers (most specific).
                    f"  for d in $DOMAINS; do "
                    f'    echo "server=/$d/$RESOLVER" >> {conf}; '
                    f"  done; "
                )
            else:
                dns_lines = (
                    f"  for d in $DOMAINS; do "
                    f'    echo "address=/$d/0.0.0.0" >> {conf}; '
                    f"  done; "
                )
            script = (
                f"{_IPT_PATH}; "
                f"SUBNET={net_q}; LR_IP={lr_q}; RESOLVER={shlex.quote(allow_resolver)}; "
                f'DOMAINS="{domains_str}"; '
                # 1) DNAT — capture DNS bypass attempts (shared, idempotent)
                f"for p in udp tcp; do "
                f"  iptables -t nat -C PREROUTING -s $SUBNET -p $p --dport 53 "
                f"  ! -d $LR_IP -j DNAT --to-destination $LR_IP 2>/dev/null "
                f"  || iptables -t nat -I PREROUTING 1 -s $SUBNET -p $p "
                f"  --dport 53 ! -d $LR_IP -j DNAT --to-destination $LR_IP; "
                f"done; "
                # 2) dnsmasq — rebuild the block ONLY when the desired set changed
                f"if ! grep -q {tag_q} {conf} 2>/dev/null; then "
                f"  sed -i '/{begin}/,/{end}/d' {conf} 2>/dev/null; "
                f"  echo '' >> {conf}; "
                f"  echo '# >>> {begin_tag} (auto) >>>' >> {conf}; "
                f"{dns_lines}"
                f"  echo '# <<< {end} <<<' >> {conf}; "
                # killall (NOT SIGHUP) — field-verified necessity on airOS 8
                f"  killall dnsmasq 2>/dev/null || true; "
                f"fi"
            )
            verify = f"grep -q {tag_q} {conf}"
        else:
            # Clear the content filter — remove its dnsmasq block, and the shared
            # DNAT only if no other mechanism (whatsapp_only) still needs it.
            dnat_removal = (
                ""
                if keep_dnat
                else (
                    "for p in udp tcp; do "
                    "  while iptables -t nat -D PREROUTING -s $SUBNET -p $p "
                    "  --dport 53 ! -d $LR_IP -j DNAT --to-destination $LR_IP "
                    "  2>/dev/null; do :; done; "
                    "done; "
                )
            )
            script = (
                f"{_IPT_PATH}; "
                f"SUBNET={net_q}; LR_IP={lr_q}; "
                # Only touch dnsmasq (and restart it) if a block is actually there
                f"if grep -q {marker_q} {conf} 2>/dev/null; then "
                f"  sed -i '/{begin}/,/{end}/d' {conf} 2>/dev/null; "
                f"  killall dnsmasq 2>/dev/null || true; "
                f"fi; "
                f"{dnat_removal}"
                f"true"
            )
            verify = f"! grep -q {marker_q} {conf}"

        code = _exec(transport, f"sh -c {shlex.quote(script)}", timeout=25)
        if code != 0 and enable:
            return (
                False,
                f"Échec de l'application du filtre de contenu (code {code}) — "
                f"vérifier que l'utilisateur SSH est root et qu'iptables existe.",
                observed,
                used_pw,
            )
        vcode = _exec(transport, f"sh -c {shlex.quote(verify)}", timeout=12)
        if vcode != 0:
            state = "posé" if enable else "retiré"
            return (
                False,
                f"Commande acceptée mais le filtre de contenu n'a pas été {state} "
                f"correctement (vérification KO).",
                observed,
                used_pw,
            )
        if enable and mode == _CONTENT_MODE_ALLOW:
            msg = (
                f"Filtre « autoriser uniquement » appliqué sur {subnet} : tout est "
                f"résolu en 0.0.0.0 sauf {len(valid_domains)} domaine(s) autorisé(s), "
                f"DNS redirigé vers {lr_ip}."
            )
        elif enable:
            msg = (
                f"Filtre de contenu appliqué sur {subnet} : {len(valid_domains)} "
                f"domaine(s) résolus en 0.0.0.0, DNS redirigé vers {lr_ip}."
            )
        else:
            msg = f"Filtre de contenu retiré sur {subnet}."
        return True, msg, observed, used_pw
    finally:
        transport.close()


async def set_content_block(
    host: str,
    port: int,
    username: str,
    password: str,
    domains: list[str],
    keep_dnat: bool = False,
    expected_fingerprint: str | None = None,
    fallback_passwords: list[str] | None = None,
    mode: str = _CONTENT_MODE_DENY,
    allow_resolver: str = "8.8.8.8",
    expected_mac: str | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """SSH into the LR and apply/remove a per-category DNS content filter.

    ``domains`` is the union of the selected categories' domains (empty = clear
    the filter, whatever the mode). ``mode`` picks the direction: ``denylist``
    (block those domains, allow the rest) or ``allowlist`` (block everything,
    allow only those). ``keep_dnat`` should be True when a ``whatsapp_only``
    block is also active on the LR, so clearing the content filter doesn't tear
    down the shared DNS-redirect rule. See ``_set_content_block_sync``.

    Returns (ok, message, observed_fp, used_password).
    """
    return await asyncio.to_thread(
        _set_content_block_sync,
        host, port, username, password,
        domains, keep_dnat, expected_fingerprint,
        fallback_passwords, mode, allow_resolver, expected_mac,
    )


def _ssh_check_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
    fallback_passwords: list[str] | None,
) -> tuple[bool, str, str | None, str | None]:
    try:
        transport, observed, used_pw = _open_transport(
            host, port, username, password, expected_fingerprint,
            fallback_passwords=fallback_passwords,
        )
        transport.close()
        logger.debug("SSH check OK — %s:%d (fp=%s)", host, port, observed)
        return True, "OK", observed, used_pw
    except _FingerprintMismatchError as exc:
        logger.error("SSH host-key mismatch — %s:%d — %s", host, port, exc)
        return False, str(exc), None, None
    except Exception as exc:
        logger.debug("SSH check failed — %s:%d — %s", host, port, exc)
        return False, str(exc), None, None


def _ping_via_ssh_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
    fallback_passwords: list[str] | None,
) -> tuple[bool, str, str | None, str | None]:
    try:
        transport, observed, used_pw = _open_transport(
            host, port, username, password, expected_fingerprint,
            fallback_passwords=fallback_passwords,
        )
    except _FingerprintMismatchError as exc:
        logger.error("Ping-via-SSH host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None, None
    except Exception as exc:
        logger.debug("Ping-via-SSH failed — %s — %s", host, exc)
        return False, str(exc), None, None

    try:
        # 5 paquets de 56 octets (payload → paquet ICMP de 64 o), et on affiche
        # min/moy/max du RTT (résumé de fin de ping), pas seulement le dernier
        # paquet. busybox (airOS) comme iputils impriment la ligne récap
        # "round-trip min/avg/max = …". Fallback sur le RTT du dernier paquet si
        # le récap n'est pas parsé (défensif).
        exit_code, out = _exec_capture(transport, "ping -c 5 -s 56 -W 2 8.8.8.8", timeout=15)
        ok = exit_code == 0
        if ok:
            stats = _PING_STATS_RE.search(out)
            if stats:
                mn, avg, mx = (float(stats.group(i)) for i in (1, 2, 3))
                msg = f"min {mn:.1f} / moy {avg:.1f} / max {mx:.1f} ms"
            else:
                times = _PING_TIME_RE.findall(out)
                msg = f"Latence {float(times[-1]):.1f} ms" if times else "Joignable"
        else:
            msg = "Non joignable"
        logger.debug("Ping-via-SSH %s → %s (exit %d)", host, "OK" if ok else "KO", exit_code)
        return ok, msg, observed, used_pw
    except Exception as exc:
        logger.debug("Ping-via-SSH exec failed — %s — %s", host, exc)
        return False, str(exc), observed, used_pw
    finally:
        transport.close()


def _ping_targets_via_ssh_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    targets: list[str],
    expected_fingerprint: str | None,
    fallback_passwords: list[str] | None,
) -> tuple[bool, str, str | None, str | None]:
    """Open one SSH session and try to ping each target IP in order.

    Returns (True, target, observed_fingerprint, used_password) as soon as
    one target is reachable, or (False, message, fp, used_pw) otherwise.
    """
    try:
        transport, observed, used_pw = _open_transport(
            host, port, username, password, expected_fingerprint,
            fallback_passwords=fallback_passwords,
        )
    except _FingerprintMismatchError as exc:
        logger.error("ping_targets_via_ssh host-key mismatch %s — %s", host, exc)
        return False, str(exc), None, None
    except Exception as exc:
        logger.debug("ping_targets_via_ssh: SSH connect failed %s — %s", host, exc)
        return False, str(exc), None, None

    try:
        for target in targets:
            exit_code = _exec(transport, f"ping -c 2 -W 3 {shlex.quote(target)}", timeout=12)
            logger.debug("ping_targets_via_ssh %s → %s exit=%d", host, target, exit_code)
            if exit_code == 0:
                return True, target, observed, used_pw
        return False, f"Aucune cible joignable parmi {targets}", observed, used_pw
    finally:
        transport.close()


async def check_ssh_access(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None = None,
    fallback_passwords: list[str] | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """Return (ok, message, observed_fingerprint, used_password) — non-blocking."""
    return await asyncio.to_thread(
        _ssh_check_sync, host, port, username, password, expected_fingerprint,
        fallback_passwords,
    )


async def check_ping_via_ssh(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None = None,
    fallback_passwords: list[str] | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """SSH into device, run ping 8.8.8.8, return (reachable, message, fp, used_pw)."""
    return await asyncio.to_thread(
        _ping_via_ssh_sync, host, port, username, password, expected_fingerprint,
        fallback_passwords,
    )


async def ping_targets_via_ssh(
    host: str,
    port: int,
    username: str,
    password: str,
    targets: list[str],
    expected_fingerprint: str | None = None,
    fallback_passwords: list[str] | None = None,
) -> tuple[bool, str, str | None, str | None]:
    """SSH into device and try each target IP in order.

    Returns (ok, target_or_msg, observed_fp, used_password)."""
    return await asyncio.to_thread(
        _ping_targets_via_ssh_sync, host, port, username, password, targets,
        expected_fingerprint, fallback_passwords,
    )


# busybox ping (airOS) prints "round-trip min/avg/max = 1.2/3.4/5.6 ms".
# iputils prints "rtt min/avg/max/mdev = …". Same field order, single regex.
_PING_AVG_RE = re.compile(
    r"(?:round-trip|rtt)\s+min/avg/max(?:/mdev)?\s*=\s*"
    r"[\d.]+/([\d.]+)/[\d.]+"
)

# Same summary line but capturing all three values (min, avg, max) for the
# check-ping diagnostic, which reports the full min/moy/max, not just the avg.
_PING_STATS_RE = re.compile(
    r"(?:round-trip|rtt)\s+min/avg/max(?:/mdev)?\s*=\s*"
    r"([\d.]+)/([\d.]+)/([\d.]+)"
)

# RTT par paquet : "... time=23.4 ms" (iputils) / "time=23.4 ms" (busybox),
# parfois "time<1 ms". Sert à afficher la latence instantanée du test ping.
_PING_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)")


def _measure_latency_via_ssh_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    target: str,
    count: int,
    expected_fingerprint: str | None,
    fallback_passwords: list[str] | None,
    collect_radio: bool = False,
    collect_model: bool = False,
) -> tuple[
    bool, bool, float | None, str, str | None, str | None, str | None,
    dict[str, float | None] | None,
]:
    """Open SSH on the LR and ping `target` `count` times to measure avg RTT.

    Returns ``(ssh_ok, ping_ok, avg_rtt_ms, message, observed_fp, used_pw,
    board_model, radio_metrics)``, a 3-state result so the caller can
    disambiguate :

      - ``ssh_ok=False``                                  → device offline
        (handled by device_ping_job — caller should skip).
      - ``ssh_ok=True, ping_ok=False``                    → device reachable
        but target unreachable (= no transit). avg is None.
      - ``ssh_ok=True, ping_ok=True, avg_rtt_ms=float``   → all good, evaluate
        latency against threshold.
      - ``ssh_ok=True, ping_ok=True, avg_rtt_ms=None``    → ping ran but RTT
        line couldn't be parsed (defensive, shouldn't normally happen).

    ``board_model`` is the ``/etc/board.info`` model string (e.g. "LiteBeam M5"),
    read on the same session so the caller can correct a mis-inferred
    model_variant — it is the only model source for airOS-M LRs (M5), which do
    not serve the HTTP status.cgi the AC firmware does. None when unreadable.

    ``radio_metrics`` is filled from ``wstalist`` only when ``collect_radio`` is
    set (M5 LRs, whose radio metrics have no other source) — a dict of the same
    keys as the HTTP poll, or None when not collected / command failed.
    """
    try:
        # 12 s (vs 6 s par défaut) : sur les liens radio avec perte, le kex SSH
        # peut dépasser 6 s — un solo sur un LR à 60 % de perte mesuré à 6,0 s,
        # pile à la limite. Le timeout serré coupait des poignées de main qui
        # auraient abouti, d'où des « No existing session » sous concurrence.
        transport, observed, used_pw = _open_transport(
            host, port, username, password, expected_fingerprint,
            timeout=12,
            fallback_passwords=fallback_passwords,
        )
    except _FingerprintMismatchError as exc:
        logger.error("measure_latency host-key mismatch %s — %s", host, exc)
        return False, False, None, str(exc), None, None, None, None
    except Exception as exc:
        logger.debug("measure_latency SSH connect failed %s — %s", host, exc)
        return False, False, None, str(exc), None, None, None, None

    # Garantie de sortie du thread : cf. _probe_session_watchdog. Les timeouts de
    # commande ci-dessous ne couvrent PAS toutes les attentes de paramiko, et un
    # thread bloqué ici retient tout le fan-out de la sonde.
    watchdog = _start_session_watchdog(transport, host, _PROBE_SESSION_HARD_LIMIT_S)
    try:
        # Hardware model, read on the same session (cheap local file) so an
        # airMAX LR mis-inferred as the wrong variant self-heals — this is the
        # only model source for M5 LRs that don't answer the HTTP API.
        # UNIQUEMENT pour les airMAX : eux seuls exploitent ce modèle (correction
        # M5 vs 5AC côté caller). Le lire sur les ~557 LTU LR coûtait un canal SSH
        # + exec + attente d'exit-status par tour, pour un résultat jeté — du temps
        # pris sur le budget du tour, donc sur la cadence de la mesure de latence.
        model = _read_board_model(transport) if collect_model else None
        # Radio metrics via wstalist — only for M5 LRs (no HTTP status.cgi).
        radio = _read_wstalist_metrics(transport) if collect_radio else None

        # -s 56 : payload 56 o (paquet ICMP 64 o), aligné sur le diagnostic
        # check-ping. Le nombre de paquets vient de lr_latency_ping_count.
        cmd = f"ping -c {int(count)} -s 56 -W 3 {shlex.quote(target)}"
        # busybox ping prints the summary on stdout; we need its content,
        # not just the exit code, so use _exec_capture.
        timeout_s = max(15, int(count) * 3 + 5)
        code, out = _exec_capture(transport, cmd, timeout=timeout_s)
        if code != 0:
            # SSH OK but ping failed entirely — caller treats this as no-transit.
            return (
                True, False, None,
                f"ping {target} exit={code}",
                observed, used_pw, model, radio,
            )
        if not out:
            return (
                True, False, None,
                f"ping {target}: stdout vide",
                observed, used_pw, model, radio,
            )
        m = _PING_AVG_RE.search(out)
        if not m:
            return (
                True, True, None,
                f"ping {target} OK mais RTT non parsé",
                observed, used_pw, model, radio,
            )
        try:
            avg = float(m.group(1))
        except ValueError:
            return (
                True, True, None,
                f"ping {target}: valeur RTT invalide",
                observed, used_pw, model, radio,
            )
        return (
            True, True, avg,
            f"{target} avg={avg:.1f} ms",
            observed, used_pw, model, radio,
        )
    except SshExecTimeoutError as exc:
        # Le ping a bien été lancé mais le LR n'a jamais renvoyé son exit-status :
        # on ne SAIT PAS s'il a du transit. Le rendre en `ping_ok=False` (ce que
        # ferait le `except Exception` ci-dessous) inventerait une conclusion —
        # ça ferait monter le compteur AT_LR_NO_TRANSIT et purgerait la latence
        # du LR sur la foi d'une mesure ratée.
        #
        # `ssh_ok=False` est le canal « indéterminé » du contrat de retour : le
        # caller saute le LR (pas d'évaluation de transit) et le reprend au cycle
        # suivant. Si ça devient chronique, le backoff SSH par LR espacera les
        # tentatives, ce qui est exactement le bon traitement pour un LR dont la
        # session meurt en route.
        logger.debug("measure_latency exit-status timeout %s — %s", host, exc)
        return False, False, None, str(exc), observed, used_pw, None, None
    except Exception as exc:
        logger.debug("measure_latency exec failed %s — %s", host, exc)
        return True, False, None, str(exc), observed, used_pw, None, None
    finally:
        watchdog.cancel()
        transport.close()


async def measure_latency_via_ssh(
    host: str,
    port: int,
    username: str,
    password: str,
    target: str,
    count: int = 5,
    expected_fingerprint: str | None = None,
    fallback_passwords: list[str] | None = None,
    collect_radio: bool = False,
    collect_model: bool = False,
) -> tuple[
    bool, bool, float | None, str, str | None, str | None, str | None,
    dict[str, float | None] | None,
]:
    """SSH into device and measure avg RTT of `count` pings to `target`.

    Returns ``(ssh_ok, ping_ok, avg_rtt_ms, message, observed_fp, used_pw,
    board_model, radio_metrics)``.
    """
    return await asyncio.to_thread(
        _measure_latency_via_ssh_sync,
        host, port, username, password, target, count,
        expected_fingerprint, fallback_passwords, collect_radio, collect_model,
    )


# ── Traffic shaper (per-client subscription plan / "forfait") ────────────────
#
# The customer's plan is NOT in any HTTP API (LTU /statistics, airOS status.cgi
# carry only live radio/throughput). It is provisioned on the LR itself as an
# airOS *traffic shaper*: a per-interface rate cap stored as flat `tshaper.*`
# keys in /tmp/system.cfg. On a CPE the radio interface is the uplink (toward
# the AP / internet) and the wired interface faces the customer LAN, so an
# egress (output) shaper maps to a direction:
#   LAN egress (output) → toward customer = download
#   WAN egress (output) → toward AP       = upload
# (ingress/input is the mirror of each). Field-confirmed on a LiteBeam 5AC
# (router mode): ath0 output=10100 kbit/s = 10 Mbps up, eth0 output=20200 = 20
# Mbps down → a 20/10 plan.
_RADIO_IFACE_PREFIXES = ("ath", "wlan", "wifi", "rai")
_SHAPER_DIRECTION = {
    ("lan", "output"): "download",
    ("lan", "input"):  "upload",
    ("wan", "output"): "upload",
    ("wan", "input"):  "download",
}


def _iface_role(devname: str) -> str:
    """Classify a shaper interface as the radio uplink ('wan') or wired LAN ('lan').

    A VLAN sub-interface (eth0.1) keeps its physical parent's role, so the
    prefix test on the radio names is enough — anything not radio is treated as
    customer-facing wired.
    """
    name = devname.strip().lower()
    if any(name.startswith(p) for p in _RADIO_IFACE_PREFIXES):
        return "wan"
    return "lan"


def parse_tshaper_config(raw: str) -> dict:
    """Parse airOS ``tshaper.*`` config lines into a per-client plan (rate caps).

    Returns::

        {
          "shaper_enabled": bool,        # tshaper.status
          "download_mbps": float | None, # cap toward the customer
          "upload_mbps":   float | None, # cap toward the internet
          "rules": [ {devname, role, direction, rate_kbps, rate_mbps}, ... ],
        }

    download/upload stay None when no enabled rule maps to that direction.
    Rates are airOS kbit/s → Mbit/s (rounded to 0.1).
    """
    kv: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("tshaper.") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        kv[key.strip()] = val.strip()

    result: dict = {
        "shaper_enabled": kv.get("tshaper.status") == "enabled",
        "download_mbps": None,
        "upload_mbps": None,
        "rules": [],
    }

    # Group the flat keys by rule index: tshaper.<idx>.<sub> = val
    by_idx: dict[str, dict[str, str]] = {}
    for key, val in kv.items():
        parts = key.split(".")
        if len(parts) >= 3 and parts[1].isdigit():
            by_idx.setdefault(parts[1], {})[".".join(parts[2:])] = val

    for idx in sorted(by_idx, key=int):
        rule = by_idx[idx]
        if rule.get("status") != "enabled":
            continue
        devname = rule.get("devname")
        if not devname:
            continue
        role = _iface_role(devname)
        for flow in ("output", "input"):
            if rule.get(f"{flow}.status") != "enabled":
                continue
            try:
                rate_kbps = int(float(rule.get(f"{flow}.rate")))
            except (TypeError, ValueError):
                continue
            direction = _SHAPER_DIRECTION[(role, flow)]
            rate_mbps = round(rate_kbps / 1000.0, 1)
            result["rules"].append({
                "devname": devname, "role": role, "direction": direction,
                "rate_kbps": rate_kbps, "rate_mbps": rate_mbps,
            })
            # First enabled rule for a direction wins (there is normally one).
            key_mbps = f"{direction}_mbps"
            if result[key_mbps] is None:
                result[key_mbps] = rate_mbps

    return result


def parse_system_location(raw: str) -> tuple[float | None, float | None]:
    """Parse ``system.latitude`` / ``system.longitude`` from airOS config lines.

    These are the coordinates PROVISIONED on the device, **not a GPS fix**: even
    on an LTU-LR — which does run ``ubnt-gps-reader`` on /dev/ttyAMA0 — the live
    fix is void (``gps_info`` = ``,,V,,...``, 0 satellites, ``gpsFixed=0``), so
    this value is whatever the operator/UISP wrote in. Field-checked 2026-07-17.

    All three firmware families carry these keys — LTU (``afltu``), airMAX AC
    (``WA``) and LiteBeam M5 (``XW``/airOS-M). A missing key, or one present but
    EMPTY (``system.latitude=``, seen on the M5), means that individual device
    was never provisioned, not that its family can't hold one — verified on a
    LiteBeam 5AC with ``system.latitude=18.135`` and on an LTU-Lite with none.
    (None, None) is therefore a normal, per-device outcome.

    Do NOT read ``mca-status`` instead: its meaning flips per family. On airMAX
    it mirrors this config value, but on LTU it reports the *live GPS* — which
    is 0.000000 on every unit here, since none has a fix.

    Both values are required: a half-filled pair is meaningless → (None, None).
    """
    kv: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("system.") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        kv[key.strip()] = val.strip()

    try:
        lat = float(kv["system.latitude"])
        lon = float(kv["system.longitude"])
    except (KeyError, ValueError):
        return None, None
    return lat, lon


def _read_traffic_shaper_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
    fallback_passwords: list[str] | None,
) -> tuple[bool, dict | None, str, str | None, str | None]:
    """SSH into the LR and read what /tmp/system.cfg provisions on it.

    Returns ``(ok, plan|None, message, observed_fp, used_pw)``. ``plan`` has the
    shape documented on :func:`parse_tshaper_config` (the rate caps) PLUS the
    ``latitude``/``longitude`` keys from :func:`parse_system_location` — both
    live in the same config file, so one grep on one session gets them.

    ok=False only on a connect/auth/read failure — a reachable LR with no shaper
    returns ok=True and all-None rate caps (no forfait provisioned), and the
    coordinates are still reported in that case.
    """
    try:
        transport, observed, used_pw = _open_transport(
            host, port, username, password, expected_fingerprint,
            fallback_passwords=fallback_passwords,
        )
    except _FingerprintMismatchError as exc:
        logger.error("read_traffic_shaper host-key mismatch %s — %s", host, exc)
        return False, None, str(exc), None, None
    except Exception as exc:
        logger.debug("read_traffic_shaper SSH connect failed %s — %s", host, exc)
        return False, None, str(exc), None, None

    try:
        # tshaper config is flat key=value in the running config. Cover both
        # filenames airOS uses across firmware (system.cfg / running.cfg).
        # grep exits 1 on no match — not an error, just an unshaped LR.
        # Same file also carries the provisioned GPS coordinates (system.latitude
        # / system.longitude) — read both in ONE grep on this session rather than
        # opening a second SSH connection per LR just for two lines.
        _code, out = _exec_capture(
            transport,
            "grep -hE '^tshaper|^system\\.(latitude|longitude)=' "
            "/tmp/system.cfg /tmp/running.cfg 2>/dev/null",
            timeout=10,
        )
        plan = parse_tshaper_config(out)
        plan["latitude"], plan["longitude"] = parse_system_location(out)
        if not plan["shaper_enabled"] and not plan["rules"]:
            return (
                True, plan,
                "Aucun traffic shaper configuré sur ce LR — pas de forfait posé "
                "sur l'équipement.",
                observed, used_pw,
            )
        return (
            True, plan,
            "Forfait lu depuis le traffic shaper du LR.",
            observed, used_pw,
        )
    except Exception as exc:
        logger.debug("read_traffic_shaper exec failed %s — %s", host, exc)
        return False, None, str(exc), observed, used_pw
    finally:
        transport.close()


async def read_traffic_shaper(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None = None,
    fallback_passwords: list[str] | None = None,
) -> tuple[bool, dict | None, str, str | None, str | None]:
    """SSH into the LR and read its airOS traffic-shaper rate caps (the forfait).

    Returns ``(ok, plan|None, message, observed_fp, used_pw)`` — see
    :func:`_read_traffic_shaper_sync`.
    """
    return await asyncio.to_thread(
        _read_traffic_shaper_sync, host, port, username, password,
        expected_fingerprint, fallback_passwords,
    )
