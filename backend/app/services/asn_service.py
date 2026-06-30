"""IP → ASN/operator resolution for the NetFlow collector.

Each NetFlow destination IP is resolved to its autonomous system (ASN) and the
operator's name using the offline MaxMind **GeoLite2-ASN** database, so the
``/traffic`` page can rank traffic by operator/CDN (Facebook, Google/YouTube,
Netflix…) — the format cache programs (GGC/FNA/OCA) care about.

The reader is opened lazily and cached. If the ``.mmdb`` file is missing the
collector still works: every public IP resolves to ``(None, None)`` and is
aggregated under ASN "unknown". A small static map gives well-known CDN ASNs a
friendly commercial label on top of MaxMind's legal-entity name.

GeoLite2-ASN is free (requires a MaxMind account) and should be refreshed
periodically; drop the new file at ``GEOIP_ASN_DB_PATH`` and restart the
collector.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Friendly operator/CDN labels for the ASNs that matter for cache requests.
# Keyed by ASN; value overrides MaxMind's organisation string in the UI.
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

_reader = None          # lazily opened maxminddb.Reader
_reader_loaded = False  # True once we've attempted to open it (success or not)


def _get_reader():
    """Open and cache the GeoLite2-ASN reader; return None if unavailable."""
    global _reader, _reader_loaded
    if _reader_loaded:
        return _reader
    _reader_loaded = True

    from app.core.config import get_settings

    path = get_settings().geoip_asn_db_path
    if not path or not os.path.exists(path):
        logger.warning(
            "GeoLite2-ASN database not found at %s — destinations will aggregate "
            "under ASN 'unknown'. Provide the .mmdb to label operators/CDNs.",
            path,
        )
        return None
    try:
        import maxminddb

        _reader = maxminddb.open_database(path)
        logger.info("GeoLite2-ASN database loaded from %s", path)
    except Exception:  # pragma: no cover - defensive, missing lib / corrupt file
        logger.exception("Failed to open GeoLite2-ASN database at %s", path)
        _reader = None
    return _reader


def resolve_asn(ip: str) -> tuple[int | None, str | None]:
    """Return ``(asn, operator_name)`` for a destination IP.

    ``(None, None)`` when the database is unavailable or has no record for the
    IP. The operator name prefers a friendly CDN label, falling back to
    MaxMind's autonomous-system-organisation string.
    """
    reader = _get_reader()
    if reader is None:
        return None, None
    try:
        rec = reader.get(ip)
    except (ValueError, KeyError):  # malformed IP / lookup error
        return None, None
    if not rec:
        return None, None
    asn = rec.get("autonomous_system_number")
    org = rec.get("autonomous_system_organization")
    if asn is None:
        return None, org
    return asn, _ASN_FRIENDLY.get(asn, org)
