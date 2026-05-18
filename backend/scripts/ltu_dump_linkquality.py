"""Diagnostic — dump a live LTU peer to find the "Link Potential" source.

History:
  v1 confirmed the data-rate index lives in ``linkQuality.mcs``
     (txRate/rxRate) and capacity in ``linkQuality.capacity``.
  v2 (this) — the dashboard "LINK POTENTIAL %" is NOT capacity.combined
     /combinedIdeal (proven: combined constant while potential swings).
     This dump prints the FULL peer object plus every key whose name
     hints at a quality/potential/ideal field, plus candidate formulas,
     so the real source can be pinned.

Run ON THE PRODUCTION SERVER, ideally WHILE the device dashboard is open
so the printed values can be compared to the live "LINK POTENTIAL %" at
the SAME instant.

    docker compose -f docker-compose.yml -f docker-compose.prod.yml exec \
        backend python scripts/ltu_dump_linkquality.py \
        <rocket_ip> <username> <password> [peer_substring]

``peer_substring`` (optional, case-insensitive) limits the FULL dump to
the matching peer (host or MAC), e.g. ``Aicha`` — keeps output readable
and lets you capture one peer at the exact dashboard moment. Without it,
every peer is dumped fully.
"""

import asyncio
import json
import sys

from app.services.ltu_api_service import LTUApiClient, _mcs_rate

_HINT_TOKENS = ("potential", "ideal", "score", "quality", "grade", "margin", "expected")


def _walk_hint_keys(obj: object, path: str = "") -> list[tuple[str, object]]:
    """Recursively collect (path, value) for keys whose name hints at a
    quality/potential metric, plus any scalar value in [0, 100] that could
    be the dashboard's percentage."""
    out: list[tuple[str, object]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            kl = str(k).lower()
            if any(tok in kl for tok in _HINT_TOKENS):
                out.append((p, v if not isinstance(v, (dict, list)) else json.dumps(v)))
            out.extend(_walk_hint_keys(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_walk_hint_keys(v, f"{path}[{i}]"))
    return out


def _candidates(lq: dict) -> None:
    """Print formula candidates for the dashboard LINK POTENTIAL %."""
    if not isinstance(lq, dict):
        return
    cap = lq.get("capacity") or {}
    c, ci = cap.get("combined"), cap.get("combinedIdeal")
    if isinstance(c, (int, float)) and isinstance(ci, (int, float)) and ci:
        print(f"  [cand] combined/combinedIdeal*100 = {c / ci * 100:.1f}%  (current formula — expected WRONG)")
    sig, isig = lq.get("signal"), lq.get("idealSignal")
    if isinstance(sig, (int, float)) and isinstance(isig, (int, float)):
        print(f"  [cand] signal={sig} idealSignal={isig} gap={sig - isig} dB")
    spc, ispc = lq.get("signalPerChain"), lq.get("idealSignalPerChain")
    if isinstance(spc, list) and isinstance(ispc, list):
        print(f"  [cand] signalPerChain={spc} idealSignalPerChain={ispc}")
    ls = lq.get("linkScore")
    if isinstance(ls, dict):
        print(f"  [cand] linkScore={ls}")
    mcs = lq.get("mcs")
    if isinstance(mcs, dict):
        print(f"  [cand] mcs txIdx={mcs.get('txIdx')}/{mcs.get('txIdxIdeal')} "
              f"rxIdx={mcs.get('rxIdx')}/{mcs.get('rxIdxIdeal')} "
              f"txRate={mcs.get('txRate')} rxRate={mcs.get('rxRate')}")


def _dump_side(label: str, side: object) -> None:
    print(f"\n=== {label} ===")
    if not isinstance(side, list) or not side:
        print(f"  (no data: {type(side).__name__})")
        return
    entry = side[0]
    print("  FULL side[0] JSON:")
    print(json.dumps(entry, indent=2, default=str))
    lq = entry.get("linkQuality") if isinstance(entry, dict) else None
    if isinstance(lq, dict):
        print(f"  _mcs_rate txRate -> {_mcs_rate(lq, 'txRate')} | rxRate -> {_mcs_rate(lq, 'rxRate')}")
        _candidates(lq)


async def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    host, user, pwd = sys.argv[1], sys.argv[2], sys.argv[3]
    peer_filter = sys.argv[4].lower() if len(sys.argv) > 4 else None

    raw = await LTUApiClient(host, user, pwd, 443).fetch_stats()
    if raw is None:
        print(f"FAILED — no stats from {host}:443 (auth/network/TLS).")
        return 1

    print(f"Top-level keys: {sorted(raw.keys())}")
    wireless = raw.get("wireless")
    peers = wireless.get("peers") if isinstance(wireless, dict) else None
    if not isinstance(peers, list) or not peers:
        print("No wireless.peers — raw JSON head:")
        print(json.dumps(raw, indent=2, default=str)[:8000])
        return 0

    for i, peer in enumerate(peers):
        if not isinstance(peer, dict):
            continue
        common = peer.get("common") or {}
        ident = common.get("identification") or {}
        host_name = str(common.get("hostname") or "")
        mac = str(ident.get("mac") or "")
        if peer_filter and peer_filter not in host_name.lower() and peer_filter not in mac.lower():
            continue

        print(f"\n########## peer[{i}] host={host_name} mac={mac} ##########")
        hints = _walk_hint_keys(peer)
        if hints:
            print("  -- keys hinting at potential/quality/ideal --")
            for p, v in hints:
                print(f"     {p} = {v}")
        _dump_side(f"peer[{i}] local[0] (AP side)", peer.get("local"))
        _dump_side(f"peer[{i}] remote[0] (CPE side)", peer.get("remote"))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
