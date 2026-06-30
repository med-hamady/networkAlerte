# Offline data files

## GeoLite2-ASN.mmdb (IP → ASN / operator)

The NetFlow collector resolves each destination IP to its operator/CDN using
MaxMind's free **GeoLite2-ASN** database. The file is **not committed** (MaxMind
licence) — download it and drop it here as `GeoLite2-ASN.mmdb`:

1. Create a free account at https://www.maxmind.com/en/geolite2/signup
2. Download **GeoLite2 ASN** (`.mmdb` format).
3. Place it at `backend/data/GeoLite2-ASN.mmdb` (this path is bind-mounted to
   `/app/data/GeoLite2-ASN.mmdb`, the default `GEOIP_ASN_DB_PATH`).
4. Restart the collector: `dc restart netflow-collector`.

Refresh it every few weeks (ASN assignments change). When the file is absent the
collector still runs — destinations just aggregate under ASN "unknown".
