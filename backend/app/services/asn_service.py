"""IP → ASN/operator resolution for the NetFlow collector.

Each NetFlow public endpoint is resolved to its autonomous system (ASN) and the
operator's name so the ``/traffic`` views can rank traffic by operator/CDN.

Two data sources, tried in order:

1. **iptoasn (BGP-derived, primary)** — the ``ip2asn-v4.tsv[.gz]`` / ``ip2asn-v6``
   datasets from https://iptoasn.com (built from the global routing table). Far
   more complete than GeoLite2 for the long tail (small / regional / African
   networks, freshly announced prefixes) — which is exactly what was showing up
   as "Indéterminé". Loaded once into sorted arrays and looked up by bisection.
2. **MaxMind GeoLite2-ASN (fallback)** — used when iptoasn has no answer (or its
   files are absent), e.g. for an address family we didn't load.

If neither resolves an IP, it aggregates under ASN "Indéterminé". A small static
map gives well-known CDN ASNs a friendly commercial label on top of either source.

Refresh the iptoasn TSVs periodically (they're rebuilt daily); drop the new files
at the configured paths and restart the collector.
"""

from __future__ import annotations

import bisect
import gzip
import ipaddress
import logging
import os
import sys

logger = logging.getLogger(__name__)

# Friendly operator/CDN labels for the ASNs that matter for cache requests.
# Keyed by ASN; overrides the raw source name in the UI.
_ASN_FRIENDLY: dict[int, str] = {
    32934: "Facebook / Instagram",
    15169: "Google / YouTube",
    36040: "Google (GGC)",
    19527: "Google",
    2906: "Netflix",
    40027: "Netflix (OCA)",
    13335: "Cloudflare",
    16509: "Amazon / AWS",
    16550: "Amazon CloudFront",
    20940: "Akamai",
    8075: "Microsoft",
    32590: "Valve / Steam",
    54113: "Fastly",
    13414: "Twitter / X",
    714: "Apple",
    46489: "Twitch",
}

# A loaded iptoasn table: parallel, start-sorted lists (starts, ends, asns, names).
_Table = tuple[list[int], list[int], list[int], list[str]]

_v4: _Table | None = None
_v6: _Table | None = None
_maxmind = None
_loaded = False


def _load_iptoasn(path: str) -> _Table | None:
    """Parse an iptoasn TSV (optionally gzipped) into start-sorted arrays.

    Columns: range_start, range_end, AS_number, country, AS_description.
    ASN 0 = "not routed" → skipped. Descriptions are interned (many consecutive
    ranges share one AS) to keep memory low."""
    if not path or not os.path.exists(path):
        return None
    opener = gzip.open if path.endswith(".gz") else open
    starts: list[int] = []
    ends: list[int] = []
    asns: list[int] = []
    names: list[str] = []
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 5:
                    continue
                try:
                    asn = int(parts[2])
                except ValueError:
                    continue
                if asn == 0:  # not routed
                    continue
                try:
                    si = int(ipaddress.ip_address(parts[0]))
                    ei = int(ipaddress.ip_address(parts[1]))
                except ValueError:
                    continue
                starts.append(si)
                ends.append(ei)
                asns.append(asn)
                names.append(sys.intern(parts[4]))
    except OSError:
        logger.exception("Failed to read iptoasn dataset at %s", path)
        return None
    if not starts:
        return None
    # iptoasn ships ascending by range_start; sort defensively only if needed.
    if any(starts[i] > starts[i + 1] for i in range(len(starts) - 1)):
        order = sorted(range(len(starts)), key=starts.__getitem__)
        starts = [starts[i] for i in order]
        ends = [ends[i] for i in order]
        asns = [asns[i] for i in order]
        names = [names[i] for i in order]
    logger.info("iptoasn dataset loaded from %s (%d prefixes)", path, len(starts))
    return starts, ends, asns, names


def _maxmind_reader():
    """Open the GeoLite2-ASN mmdb fallback (or None)."""
    from app.core.config import get_settings

    path = get_settings().geoip_asn_db_path
    if not path or not os.path.exists(path):
        return None
    try:
        import maxminddb

        reader = maxminddb.open_database(path)
        logger.info("GeoLite2-ASN fallback loaded from %s", path)
        return reader
    except Exception:  # pragma: no cover - missing lib / corrupt file
        logger.exception("Failed to open GeoLite2-ASN database at %s", path)
        return None


def _ensure_loaded() -> None:
    global _v4, _v6, _maxmind, _loaded
    if _loaded:
        return
    _loaded = True
    from app.core.config import get_settings

    settings = get_settings()
    _v4 = _load_iptoasn(settings.iptoasn_v4_path)
    _v6 = _load_iptoasn(settings.iptoasn_v6_path)
    _maxmind = _maxmind_reader()
    if _v4 is None and _v6 is None and _maxmind is None:
        logger.warning(
            "No ASN source available (iptoasn TSV + GeoLite2-ASN both absent) — "
            "all destinations will aggregate under 'Indéterminé'. Provide "
            "ip2asn-v4.tsv.gz (see backend/data/README.md).",
        )


def _lookup(table: _Table, ip_int: int) -> tuple[int | None, str | None]:
    starts, ends, asns, names = table
    i = bisect.bisect_right(starts, ip_int) - 1
    if i >= 0 and ip_int <= ends[i]:
        return asns[i], names[i]
    return None, None


def resolve_asn(ip: str) -> tuple[int | None, str | None]:
    """Return ``(asn, operator_name)`` for an IP, or ``(None, None)``.

    iptoasn (BGP) first, MaxMind fallback second. The operator name prefers a
    friendly CDN label, else the source's AS description."""
    _ensure_loaded()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None, None

    table = _v6 if addr.version == 6 else _v4
    if table is not None:
        asn, name = _lookup(table, int(addr))
        if asn is not None:
            return asn, _ASN_FRIENDLY.get(asn, name)

    if _maxmind is not None:
        try:
            rec = _maxmind.get(ip)
        except (ValueError, KeyError):
            rec = None
        if rec:
            asn = rec.get("autonomous_system_number")
            org = rec.get("autonomous_system_organization")
            if asn is not None:
                return asn, _ASN_FRIENDLY.get(asn, org)
            if org:
                return None, org
    return None, None
