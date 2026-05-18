"""Diagnostic — dump the real LTU `linkQuality` structure from a live device.

Purpose: confirm the JSON key holding the modulation data-rate index
("Nx" in the dashboard) so `ltu_api_service._rate_idx` can be pinned to
the exact path instead of probing several shapes.

Run ON THE PRODUCTION SERVER (it needs network access to the Rocket and
the real credentials). No DB access — credentials are passed as args.

    # inside the backend container
    docker compose exec backend python scripts/ltu_dump_linkquality.py \
        <rocket_ip> <username> <password> [port]

    # or directly on the host venv
    python scripts/ltu_dump_linkquality.py <rocket_ip> <username> <password>

Paste the full output back so the parser key can be confirmed/fixed.
"""

import asyncio
import json
import sys

from app.services.ltu_api_service import LTUApiClient, _rate_idx


def _dump_link_quality(label: str, lq: object) -> None:
    print(f"\n=== {label} ===")
    if not isinstance(lq, dict):
        print(f"  (not a dict: {type(lq).__name__})")
        return
    print(f"  keys: {sorted(lq.keys())}")
    print("  full linkQuality JSON:")
    print(json.dumps(lq, indent=2, default=str))
    print(f"  _rate_idx(lq,'dl') -> {_rate_idx(lq, 'dl')}")
    print(f"  _rate_idx(lq,'ul') -> {_rate_idx(lq, 'ul')}")


async def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    host, user, pwd = sys.argv[1], sys.argv[2], sys.argv[3]
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 443

    raw = await LTUApiClient(host, user, pwd, port).fetch_stats()
    if raw is None:
        print(f"FAILED — no stats from {host}:{port} (auth/network/TLS).")
        return 1

    print(f"Top-level keys: {sorted(raw.keys())}")
    wireless = raw.get("wireless")
    peers = wireless.get("peers") if isinstance(wireless, dict) else None
    if not isinstance(peers, list) or not peers:
        print("No wireless.peers in response — dumping raw JSON:")
        print(json.dumps(raw, indent=2, default=str)[:8000])
        return 0

    for i, peer in enumerate(peers):
        if not isinstance(peer, dict):
            continue
        common = peer.get("common") or {}
        ident = common.get("identification") or {}
        print(
            f"\n########## peer[{i}] "
            f"host={common.get('hostname')} mac={ident.get('mac')} ##########"
        )
        local = peer.get("local")
        if isinstance(local, list) and local:
            _dump_link_quality(
                f"peer[{i}] local[0].linkQuality (AP side)",
                local[0].get("linkQuality"),
            )
        remote = peer.get("remote")
        if isinstance(remote, list) and remote:
            _dump_link_quality(
                f"peer[{i}] remote[0].linkQuality (CPE side)",
                remote[0].get("linkQuality"),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
