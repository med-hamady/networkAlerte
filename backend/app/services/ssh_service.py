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
import hashlib
import ipaddress
import logging
import re
import shlex
import socket

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
) -> tuple[paramiko.Transport, str]:
    """Open an authenticated SSH Transport and return it with the observed fp.

    We bypass `SSHClient.connect()` because it always calls `auth_password` with
    `fallback=True`, which makes paramiko probe an `auth_none` first. Several
    airOS dropbear builds reject the subsequent password attempt after that
    probe — OpenSSH works because it never sends `auth_none`. Calling
    `auth_password(fallback=False)` directly skips the probe and authenticates
    on the first try.
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=timeout)
        server_key = transport.get_remote_server_key()
        observed = _fingerprint(server_key)
        if expected_fingerprint is None:
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
        transport.auth_password(username, password, fallback=False)
    except Exception:
        transport.close()
        raise
    return transport, observed


def _exec(transport: paramiko.Transport, command: str, timeout: int) -> int:
    """Run a command on a new channel and return its exit code."""
    channel = transport.open_session()
    try:
        channel.settimeout(timeout)
        channel.exec_command(command)
        return channel.recv_exit_status()
    finally:
        channel.close()


def _exec_capture(
    transport: paramiko.Transport, command: str, timeout: int
) -> tuple[int, str]:
    """Run a command and return (exit_code, stdout stripped)."""
    channel = transport.open_session()
    try:
        channel.settimeout(timeout)
        channel.exec_command(command)
        out = channel.makefile("rb").read().decode("utf-8", errors="replace")
        return channel.recv_exit_status(), out.strip()
    finally:
        channel.close()


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


def _set_iface_state_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    interface: str,
    bring_up: bool,
    expected_fingerprint: str | None,
) -> tuple[bool, str, str | None]:
    """SSH into the device and bring `interface` admin up or down.

    Idempotent: re-applying the same state is harmless (this is what lets the
    enforcement job re-assert a block every cycle / after an LR reboot). Tries
    `ip link` first, falls back to busybox `ifconfig`. Verifies via the admin
    flag, not operstate, so it stays correct on an unplugged port.
    """
    if not bring_up and interface in _PROTECTED_IFACES:
        return (
            False,
            f"Interface protégée '{interface}' — refus : couper cette interface "
            f"déconnecterait le superviseur du LR (radio/loopback).",
            None,
        )

    try:
        transport, observed = _open_transport(
            host, port, username, password, expected_fingerprint
        )
    except _FingerprintMismatchError as exc:
        logger.error("set_iface host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("set_iface SSH connect failed — %s — %s", host, exc)
        return False, str(exc), None

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
            )
        admin_up = _read_admin_up(transport, interface)
        if admin_up is not None and admin_up != bring_up:
            return (
                False,
                f"Commande acceptée mais {interface} toujours "
                f"{'DOWN' if bring_up else 'UP'} — état non appliqué.",
                observed,
            )
        state_str = "UP" if bring_up else "DOWN"
        verified = "" if admin_up is None else " (vérifié)"
        return (
            True,
            f"Interface {interface} mise {state_str}{verified}.",
            observed,
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
) -> tuple[bool, str, str | None]:
    """SSH into the LR and bring its LAN port admin up/down — non-blocking.

    Returns (ok, message, observed_fingerprint). Refuses protected interfaces
    (radio/management) so an operator error can't lock the supervisor out.
    """
    return await asyncio.to_thread(
        _set_iface_state_sync,
        host, port, username, password, interface, bring_up, expected_fingerprint,
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
) -> tuple[bool, str, str | None]:
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
        transport, observed = _open_transport(
            host, port, username, password, expected_fingerprint
        )
    except _FingerprintMismatchError as exc:
        logger.error("whatsapp_only host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("whatsapp_only SSH connect failed — %s — %s", host, exc)
        return False, str(exc), None

    try:
        subnet, lr_ip = _detect_client_context(transport)
        if subnet is None or lr_ip is None:
            return (
                False,
                "Sous-réseau client introuvable sur le LR — impossible de "
                "poser le filtre WhatsApp. Vérifie que le LR route bien un "
                "réseau client privé.",
                observed,
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
            )
        vcode = _exec(transport, f"sh -c {shlex.quote(verify)}", timeout=12)
        if vcode != 0:
            state = "posé" if enable else "retiré"
            return (
                False,
                f"Commande acceptée mais le filtre WhatsApp-only n'a pas été "
                f"{state} correctement (vérification KO).",
                observed,
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
        return True, msg, observed
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
) -> tuple[bool, str, str | None]:
    """SSH into the LR and apply/remove the 3-layer WhatsApp-only restriction.

    See ``_set_whatsapp_only_sync`` for the mechanism. ``allow_cidrs`` are the
    Meta IP ranges left reachable; ``deny_domains`` are DNS names resolved to
    0.0.0.0 by the LR's dnsmasq to neutralise FB/IG which would otherwise pass
    via the IP allowlist (they share Meta's IP space).
    """
    return await asyncio.to_thread(
        _set_whatsapp_only_sync,
        host, port, username, password,
        enable, allow_cidrs, deny_domains, expected_fingerprint,
    )


def _ssh_check_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
) -> tuple[bool, str, str | None]:
    try:
        transport, observed = _open_transport(host, port, username, password, expected_fingerprint)
        transport.close()
        logger.debug("SSH check OK — %s:%d (fp=%s)", host, port, observed)
        return True, "OK", observed
    except _FingerprintMismatchError as exc:
        logger.error("SSH host-key mismatch — %s:%d — %s", host, port, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("SSH check failed — %s:%d — %s", host, port, exc)
        return False, str(exc), None


def _ping_via_ssh_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None,
) -> tuple[bool, str, str | None]:
    try:
        transport, observed = _open_transport(host, port, username, password, expected_fingerprint)
    except _FingerprintMismatchError as exc:
        logger.error("Ping-via-SSH host-key mismatch — %s — %s", host, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("Ping-via-SSH failed — %s — %s", host, exc)
        return False, str(exc), None

    try:
        exit_code = _exec(transport, "ping -c 2 -W 2 8.8.8.8", timeout=10)
        ok = exit_code == 0
        logger.debug("Ping-via-SSH %s → %s (exit %d)", host, "OK" if ok else "KO", exit_code)
        return ok, "Joignable" if ok else "Non joignable", observed
    except Exception as exc:
        logger.debug("Ping-via-SSH exec failed — %s — %s", host, exc)
        return False, str(exc), observed
    finally:
        transport.close()


def _ping_targets_via_ssh_sync(
    host: str,
    port: int,
    username: str,
    password: str,
    targets: list[str],
    expected_fingerprint: str | None,
) -> tuple[bool, str, str | None]:
    """
    Open one SSH session and try to ping each target IP in order.
    Returns (True, target, observed_fingerprint) as soon as one target
    is reachable, or (False, message, observed_fp) otherwise.
    """
    try:
        transport, observed = _open_transport(host, port, username, password, expected_fingerprint)
    except _FingerprintMismatchError as exc:
        logger.error("ping_targets_via_ssh host-key mismatch %s — %s", host, exc)
        return False, str(exc), None
    except Exception as exc:
        logger.debug("ping_targets_via_ssh: SSH connect failed %s — %s", host, exc)
        return False, str(exc), None

    try:
        for target in targets:
            exit_code = _exec(transport, f"ping -c 2 -W 3 {shlex.quote(target)}", timeout=12)
            logger.debug("ping_targets_via_ssh %s → %s exit=%d", host, target, exit_code)
            if exit_code == 0:
                return True, target, observed
        return False, f"Aucune cible joignable parmi {targets}", observed
    finally:
        transport.close()


async def check_ssh_access(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None = None,
) -> tuple[bool, str, str | None]:
    """Return (ok, message, observed_fingerprint) — non-blocking."""
    return await asyncio.to_thread(
        _ssh_check_sync, host, port, username, password, expected_fingerprint,
    )


async def check_ping_via_ssh(
    host: str,
    port: int,
    username: str,
    password: str,
    expected_fingerprint: str | None = None,
) -> tuple[bool, str, str | None]:
    """SSH into device, run ping 8.8.8.8, return (reachable, message, fp)."""
    return await asyncio.to_thread(
        _ping_via_ssh_sync, host, port, username, password, expected_fingerprint,
    )


async def ping_targets_via_ssh(
    host: str,
    port: int,
    username: str,
    password: str,
    targets: list[str],
    expected_fingerprint: str | None = None,
) -> tuple[bool, str, str | None]:
    """SSH into device and try each target IP in order."""
    return await asyncio.to_thread(
        _ping_targets_via_ssh_sync, host, port, username, password, targets, expected_fingerprint,
    )
