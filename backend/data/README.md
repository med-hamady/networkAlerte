# Offline data files (ASN resolution)

The NetFlow collector resolves each destination IP to its operator/CDN (ASN). Two
sources, tried in order — drop the files here (this dir is bind-mounted to
`/app/data`), then restart the collector (`dc restart netflow-collector`).

## 1. iptoasn — BGP-derived (PRIMARY, recommended)

Free, built from the global routing table (https://iptoasn.com/) — far more
complete than GeoLite2 for the long tail (small/regional/African networks,
freshly announced prefixes). This is what fixes most of the "Indéterminé" band.

Download the two datasets and place them here **keeping the names**:

```bash
# IPv4 (main) + IPv6
curl -L -o ip2asn-v4.tsv.gz https://iptoasn.com/data/ip2asn-v4.tsv.gz
curl -L -o ip2asn-v6.tsv.gz https://iptoasn.com/data/ip2asn-v6.tsv.gz
```

Paths: `GEOIP` / `IPTOASN_V4_PATH=/app/data/ip2asn-v4.tsv.gz`,
`IPTOASN_V6_PATH=/app/data/ip2asn-v6.tsv.gz`. The collector reads `.tsv` or
`.tsv.gz`. Refresh every few weeks (rebuilt daily upstream).

> If the server has no outbound Internet (DNS/firewall), download on your PC and
> copy them over:
> `scp ip2asn-v4.tsv.gz ip2asn-v6.tsv.gz a2@10.135.3.25:/opt/a2project/backend/data/`

## 2. MaxMind GeoLite2-ASN (FALLBACK)

Used only when iptoasn has no answer. Free account at
https://www.maxmind.com/en/geolite2/signup → download **GeoLite2 ASN** (`.mmdb`)
→ place as `GeoLite2-ASN.mmdb` here (`GEOIP_ASN_DB_PATH`).

When neither source resolves an IP, it aggregates under ASN "Indéterminé". The
collector logs a sample of unresolved IPs each cycle
(`NetFlow 'Indéterminé' sample IPs …`) to help decide what to add.
