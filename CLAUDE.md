# CLAUDE.md — Network Supervisor

Système de supervision et d'alerting réseau orienté équipements UISP/Ubiquiti.
Développé d'abord sur maquette de simulation, puis déployé sur serveur physique en production.

## Contexte métier

L'entreprise n'a pas de visibilité en temps réel sur son réseau. Ce système doit détecter
proactivement les pannes, dégradations et anomalies d'alimentation, et alerter l'équipe.

## Topologie de simulation (maquette)

```
PC local / Serveur → (RJ45) → UISP Switch → (RJ45) → LTU Rocket
                                                            ↕ (Lien radio)
                             → (RJ45) → UISP Power     LTU LR
```

Équipements cibles : LTU Rocket, LTU LR, UISP Switch, UISP Power.

## Stack technique

| Couche | Technologie |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy async |
| Frontend | Next.js (React, Tailwind CSS) |
| Base de données | PostgreSQL 16 |
| Migrations | Alembic (mode async, asyncpg) |
| Scheduler | APScheduler (AsyncIOScheduler) |
| Réseau | pysnmp-lextudio, paramiko, httpx |
| Infra | Docker Compose (3 containers) |
| Qualité | Ruff, pre-commit |

## Architecture du backend

```
backend/app/
├── main.py                  # App factory + lifespan (démarrage scheduler)
├── core/
│   ├── config.py            # Settings via pydantic-settings + computed fields
│   ├── logging.py           # Logging structuré vers stdout
│   ├── exceptions.py        # AppException + handlers globaux
│   └── alert_constants.py   # Source unique de vérité : Severity, AlertChannel, alert_type keys (20 types)
├── api/
│   ├── router.py            # Montage des routers avec prefix /api/v1 + auth API key
│   ├── deps.py              # verify_api_key — authentification par header X-API-Key
│   └── endpoints/
│       ├── health.py              # GET /health (public — test DB inclus)
│       ├── devices.py             # CRUD + diagnostics SSH/ping sur /devices
│       ├── incidents.py           # GET/PATCH /incidents
│       ├── system.py              # GET/POST /system (infos système, /system/test-whatsapp)
│       └── uisp.py                # POST /uisp/sync (import infra depuis le contrôleur UISP, ?dry_run=true pour prévisualiser)
├── models/                  # SQLAlchemy ORM (Base avec id, created_at, updated_at)
│   ├── device.py            # Équipements supervisés (+ parent_id hiérarchie, policy_overrides JSON)
│   ├── device_metric.py     # Métriques time-series
│   ├── incident.py          # Incidents (open/acknowledged/resolved)
│   ├── alert_state.py       # Compteurs d'anti-flapping persistés en DB (survit aux redémarrages)
│   └── power_status_log.py  # Relevés UISP Power (voltage, current, power)
├── schemas/                 # Pydantic — validation I/O API
│   ├── device.py
│   └── incident.py
├── services/
│   ├── device_service.py           # CRUD devices
│   ├── poller.py                   # Ping ICMP async (asyncio subprocess)
│   ├── incident_service.py         # Création/résolution/déduplication d'incidents
│   ├── notification_service.py     # Dispatch des notifications — **WhatsApp (Ultramsg) est l'UNIQUE transport** (l'envoi d'email a été retiré du projet). _deliver / digest / security routent vers WhatsApp. **Liste blanche `WHATSAPP_ALERT_TYPES`** (chokepoint dans `_dispatch`) : seules 5 anomalies sont poussées, tout le reste ouvre l'incident en DB mais n'est notifié nulle part
│   ├── whatsapp_service.py         # Envoi WhatsApp via Ultramsg (POST /{instance}/messages/chat → groupe WHATSAPP_GROUP_ID). httpx async, jamais raise (False sur échec)
│   ├── snmp_service.py             # SNMP : LTU radio (ath0/eth0) + Switch (ports 1..N)
│   ├── uisp_power_service.py       # API REST UISP Power (voltage, current, batterie)
│   ├── ltu_api_service.py          # API HTTP LTU Rocket (signal, CCQ, CINR, CPE peers)
│   ├── ssh_service.py              # SSH via paramiko : check_ssh_access, ping_targets_via_ssh, set_lan_interface, set_whatsapp_only, garde-fou _collect_forbidden_ifaces, fallback de mot de passe (_open_transport essaie LR_FALLBACK_SSH_PASSWORDS sur AuthenticationException, retourne le mdp utilisé → promu sur le LR)
│   ├── client_block_service.py     # Blocage client 2 modes (full / whatsapp_only) + enforcement
│   ├── alert_engine.py             # Orchestrateur : évalue règles, gère AlertState, ouvre/résout incidents
│   ├── alert_rules.py              # Règles d'alerte pure Python (sans DB) — 10+ règles
│   ├── alert_formatter.py          # Formatage messages WhatsApp/log par type d'alerte
│   ├── alert_policy.py             # Registre interne : politique (canal/groupable/recovery/immédiat) par alert_type — plus exposé en API
│   ├── digest_service.py           # Regroupement des warnings en digest 15 min
│   ├── uisp_service.py             # Client REST contrôleur UISP/UNMS (login → token, GET /devices) — read-only
│   ├── uisp_sync_service.py        # Import auto depuis UISP : INFRA (classify→upsert name/IP/site, creds par convention à la CRÉATION) + STATIONS clientes via sync_uisp_stations (LR abonnés dans `lrs`, colonnes uisp_* mode/statut, identité MAC, gated UISP_STATION_SYNC_ENABLED) ; aucun delete/deactivate
│   ├── netflow_service.py          # Collecteur NetFlow (asyncio UDP) : décode v1/v5/v9/IPFIX (lib `netflow`). Attribue chaque flux à son **extrémité PUBLIQUE** (source en download, destination en upload ; l'extrémité INTERNE = client/WAN défini par NETFLOW_INTERNAL_PREFIXES), résout l'ASN (asn_service), agrège en mémoire par (asn, opérateur) avec **down_bytes/up_bytes** et flush dans `traffic_dest_stats`. Process long dédié (RUN_MODE=collector), PAS un job APScheduler
│   ├── asn_service.py              # IP → (ASN, opérateur). PRIMAIRE : datasets BGP **iptoasn.com** (`ip2asn-v4/v6.tsv.gz`, sorted arrays + bisect, bien plus complets que GeoLite2 pour la longue traîne). FALLBACK : MaxMind GeoLite2-ASN (.mmdb). + map statique de labels CDN. Lazy load ; aucune source = tout sous "Indéterminé"
│   └── traffic_service.py          # 2 roll-ups : `get_top_destinations` (VOLUME down/up/total par ASN sur 24h/7j/30j) + `get_throughput` (DÉBIT Gb/s = bytes÷bucket s, dernier bucket, descendant/montant + part). Alimente /traffic
├── tasks/
│   ├── scheduler.py         # Init APScheduler, start/stop lifecycle
│   ├── runner.py            # Entrée du container scheduler standalone (RUN_MODE=scheduler)
│   ├── collector_runner.py # Entrée du container NetFlow collecteur (RUN_MODE=collector) : lance netflow_service.run_collector, gate NETFLOW_COLLECTOR_ENABLED (idle si off)
│   └── jobs.py              # jobs planifiés (voir tableau ci-dessous)
├── db/
│   ├── base.py              # DeclarativeBase avec id/created_at/updated_at
│   └── session.py           # Engine async + get_db() + async_session_factory()
└── utils/                   # Helpers partagés
```

## Patterns clés — à respecter

- **Async partout** : FastAPI + SQLAlchemy async + asyncpg. Ne pas introduire de code synchrone dans les endpoints ou services.
- **Service layer** : la logique métier va dans `services/`, jamais directement dans les endpoints.
- **Dependency injection** : sessions DB via `Depends(get_db)` dans les endpoints, `async_session_factory()` context manager dans les jobs scheduler.
- **Pydantic validation** : tout I/O API passe par des schemas dans `schemas/`.
- **Config via env** : toutes les variables de config dans `.env`, lues par `Settings` (pydantic-settings). `database_url` est un `@computed_field` construit depuis les `POSTGRES_*` vars.
- **Alembic async** : migrations via `async_engine_from_config` avec asyncpg. Créer une migration après chaque changement de modèle.
- **Scheduler lifecycle** : lié au lifespan FastAPI en dev (un seul container). En prod, le scheduler tourne dans un **container dédié** (`RUN_MODE=scheduler`, entrée `app/tasks/runner.py`) et le `backend` a `SCHEDULER_ENABLED=false` → uvicorn peut scaler à plusieurs workers sans dupliquer les jobs.
- **alert_constants.py** : source unique de vérité pour les `alert_type` strings. Ne jamais redéfinir ces constantes dans d'autres modules.
- **AlertState** : les compteurs anti-flapping sont persistés en DB (pas in-memory) pour survivre aux redémarrages, sauf les compteurs de ping qui restent in-memory (`_failure_counts` dans jobs.py).
- **Authentification** : toutes les routes sauf `/health` sont protégées par `verify_api_key` (header `X-API-Key`).

## Variables d'environnement importantes

> Les **credentials des équipements** (LTU Rocket, LTU LR SSH, UISP Power) ne
> sont **pas** dans le `.env` : ils sont stockés par device dans la table
> `devices` (colonnes `ssh_username`, `ssh_password`, `ssh_port`,
> `uisp_power_username`, `uisp_power_password`, `uisp_power_port`).
> Configuration via `PUT /api/v1/devices/{id}` ou le formulaire UI.
>
> **Fallback de mot de passe SSH** : `LR_FALLBACK_SSH_PASSWORDS` (env, CSV) liste
> les anciens mots de passe essayés quand le `ssh_password` du LR échoue en auth.
> Quand un fallback réussit, le `ssh_password` du LR est mis à jour avec le mot
> de passe qui marche (auto-réparation, log INFO). S'applique à toutes les
> opérations SSH sur LR (sonde transit, ping, blocage client, topologie,
> découverte LAN, diagnostics check-ssh/check-ping).

| Variable | Rôle |
|---|---|
| `APP_ENV` | `development` (reload) ou `production` (workers, pas de reload) |
| `RUN_MODE` | `api` (défaut — uvicorn + migrations) ou `scheduler` (process scheduler standalone, voir `app/tasks/runner.py`). Utilisé par le container `scheduler` en prod. |
| `UVICORN_WORKERS` | Nombre de workers uvicorn en prod (défaut 1). Ne dépasser 1 **que** si le scheduler tourne dans son container dédié (`SCHEDULER_ENABLED=false` côté backend), sinon les jobs s'exécutent N fois. |
| `POSTGRES_HOST` | Hôte PostgreSQL |
| `POSTGRES_PORT` | Port PostgreSQL (défaut 5432) |
| `POSTGRES_USER` | Utilisateur DB |
| `POSTGRES_PASSWORD` | Mot de passe DB |
| `POSTGRES_DB` | Nom de la base |
| `SCHEDULER_ENABLED` | Active/désactive APScheduler |
| `DEBUG` | Mode debug SQLAlchemy |
| `LOG_LEVEL` | Niveau de log (INFO, DEBUG, WARNING) |
| `API_KEY` | Clé d'authentification API (header X-API-Key) |
| `LR_FALLBACK_SSH_PASSWORDS` | Mots de passe SSH de fallback pour les LR (CSV) essayés quand le `ssh_password` stocké échoue ; le mdp qui marche est promu sur le LR. Défaut `A2HQ@4321` |
| `SNMP_DEFAULT_COMMUNITY` | Community SNMP par défaut (ex: public) |
| `SNMP_PORT` | Port SNMP (défaut 161) |
| `SNMP_TIMEOUT` | Timeout SNMP en secondes |
| `SWITCH_MAX_PORTS` | Nombre de ports à scanner sur le switch |
| `SWITCH_ROCKET_PORT_INDEX` | Index du port switch connecté au Rocket (0 = désactivé) |
| `SWITCH_PORT_MIN_SPEED_MBPS` | Vitesse minimale attendue sur ce port (défaut 1000 Mbps) |
| `LR_LATENCY_TARGET` | Cible du ping LR → Internet (défaut `8.8.8.8`). Sert à la fois à la détection de transit et à la mesure de latence |
| `LR_LATENCY_PING_COUNT` | Nombre de pings utilisés pour la moyenne RTT (défaut 5) |
| `LR_LATENCY_CRITICAL_MS` | Seuil critique de latence LR → Internet en ms (défaut 100 ; incident critique si avg ≥ seuil) |
| `LR_LATENCY_FAILURE_THRESHOLD` | Cycles consécutifs ≥ seuil avant ouverture de `lr_latency_high` (défaut 3 ≈ 3 min) |
| `LR_LATENCY_INTERVAL` | Intervalle de la sonde LR → Internet (secondes, défaut 60) |
| `TRANSIT_PROBE_THRESHOLD` | Cycles consécutifs sans transit avant ouverture de `lr_no_transit` (défaut 2) |
| `SLACK_WEBHOOK_URL` | Webhook Slack pour les notifications |
| `WEBHOOK_URL` | Webhook générique (JSON POST) |
| `WHATSAPP_ENABLED` | Active le canal WhatsApp (Ultramsg) — **unique transport d'alerting** (l'envoi d'email a été retiré du projet) (défaut `false`) |
| `WHATSAPP_BASE_URL` | URL de base Ultramsg (défaut `https://api.ultramsg.com`) |
| `WHATSAPP_INSTANCE_ID` | Id d'instance Ultramsg (ex. `instance12345`) |
| `WHATSAPP_TOKEN` | Token de l'instance Ultramsg |
| `WHATSAPP_GROUP_ID` | Id du **groupe** WhatsApp destinataire (forme `1203630xxxxxxx@g.us`) |
| `WARNING_DIGEST_MINUTES` | Intervalle digest warnings (défaut 15 min) |
| `DEVICE_METRICS_RETENTION_DAYS` | Fenêtre de rétention des `device_metrics` historiques (défaut 90 j ; couvre les matviews conso 30 j avec marge — seuls les compteurs bytes sont encore historisés) |
| `DEVICE_METRICS_RETENTION_INTERVAL_MINUTES` | Intervalle du `device_metrics_retention_job` (défaut 360 = 6 h) |
| `DEVICE_METRICS_RETENTION_BATCH_SIZE` | Taille de batch de la purge (défaut 50000 lignes/DELETE) |
| `PING_DOWN_THRESHOLD` | Pings consécutifs échoués avant incident (défaut 3) |
| `SIGNAL_WARNING_DBM` | Seuil signal warning (défaut -75 dBm — un signal entre -75 et -80 = warning) |
| `SIGNAL_CRITICAL_DBM` | Seuil signal critical (défaut -80 dBm — strictement sous -80 = critique) |
| `SIGNAL_TOLERANCE_DBM` | Marge de tolérance signal — l'incident `signal_low` n'ouvre qu'à `seuil − tolérance` (défaut 0 dBm — strict ; mettre 2-5 si flapping autour du seuil) |
| `LR_LINK_POTENTIAL_MIN_PCT_LTU` | Plancher link_potential pour les LR LTU (défaut 50 %) |
| `LR_LINK_POTENTIAL_MIN_PCT_AIRMAX` | Plancher link_potential pour les LR airMAX/Litebeam (défaut 40 %) |
| `LR_TOTAL_CAPACITY_MIN_MBPS` | Plancher capacité totale du lien (défaut 60 Mbps) |
| `LR_RX_RATE_CRITICAL_IDX_LTU` | LTU : critical strict si rate local/remote < ×6 (pas de warning) |
| `LR_RX_RATE_WARNING_IDX_AIRMAX` | airMAX : warning si rate local/remote < ×6 (défaut 6.0) |
| `LR_RX_RATE_CRITICAL_IDX_AIRMAX` | airMAX : critical si rate local/remote < ×4 (défaut 4.0) |
| `CCQ_WARN_PCT` | Seuil CCQ warning (défaut 75%) |
| `CCQ_CRIT_PCT` | Seuil CCQ critical (défaut 50%) |
| `CCQ_TOLERANCE_PCT` | Bande d'hystérésis CCQ DL+UL — ouvre à `seuil − tol`, résout au seuil nominal (défaut 5% ; 0 = strict) |
| `CINR_WARN_DB` | Seuil CINR warning (défaut 20 dB) |
| `CINR_CRIT_DB` | Seuil CINR critical (défaut 10 dB) |
| `CINR_TOLERANCE_DB` | Bande d'hystérésis CINR DL+UL — ouvre à `seuil − tol`, résout au seuil nominal (défaut 3 dB ; 0 = strict) |
| `BATTERY_INTERNAL_CRITICAL_PCT` | Seuil batterie **interne** (Li-Ion UPS) du UISP Power → `battery_internal_low` critique (défaut **50%**) |
| `BATTERY_EXTERNAL_CRITICAL_PCT` | Seuil batterie **externe** (banc plomb) du UISP Power → `battery_external_low` critique (défaut **30%**) |
| `BATTERY_WARNING_PCT` / `BATTERY_CRITICAL_PCT` | ⚠️ Legacy — plus utilisés (ancienne alerte batterie unique remplacée par interne/externe) |
| `FLAP_THRESHOLD_24H` | Coupures (incidents de dispo) au-delà desquelles un device est jugé instable → `device_flapping` (défaut 3) |
| `FLAP_WINDOW_HOURS` | Fenêtre glissante de comptage du flapping (défaut 24 h) |
| `FLAP_CHECK_INTERVAL_MINUTES` | Intervalle du `flap_detection_job` (défaut 10 min) |
| `NETWORK_HIGH_LATENCY_PCT` | % de clients (LR up) en latence élevée au-delà duquel `network_latency_aggregate_job` alerte sur WhatsApp (défaut 20) |
| `NETWORK_LATENCY_MIN_SAMPLE` | Taille d'échantillon minimale (LR avec relevé) avant d'évaluer la latence réseau (défaut 10) |
| `NETWORK_LATENCY_CHECK_INTERVAL_MINUTES` | Intervalle du `network_latency_aggregate_job` (défaut **1440 min = 24 h** — contrôle quotidien) |
| `ROCKET_SATURATION_REPORT_ENABLED` | Active le `rocket_saturation_report_job` (rapport PDF quotidien des Rockets saturés sur WhatsApp ; défaut `true`) |
| `ROCKET_SATURATION_REPORT_HOUR` | Heure quotidienne du `rocket_saturation_report_job` (défaut `7` = **07:00 UTC** ; Mauritanie GMT → 07:00 locale). Le job tourne aussi **1× au démarrage** du scheduler (déploiement) |
| `SITE_INFRA_MAX` | Nombre **max d'équipements infra par site** (défaut **14**). Compte les **Rockets + AF60 + PTP LiteBeam** (exclut switches, UISP Power et LR clients). Sert au `site_infra_report_job` et à la section « Capacité infra par site » de `/capacity` |
| `SITE_INFRA_REPORT_ENABLED` | Active le `site_infra_report_job` (rapport PDF quotidien capacité infra par site sur WhatsApp ; défaut `true`) |
| `SITE_INFRA_REPORT_HOUR` | Heure quotidienne du `site_infra_report_job` (défaut `7` = **07:00 UTC**). Le job tourne aussi **1× au démarrage** du scheduler (déploiement) |
| `CLIENT_BLOCK_ENFORCEMENT_ENABLED` | Active le job qui ré-applique le blocage client (défaut true) |
| `CLIENT_BLOCK_ENFORCE_INTERVAL` | Intervalle de ré-application du blocage client en secondes (défaut 120) |
| `CLIENT_BLOCK_DEFAULT_MODE` | Mode de blocage par défaut : `full` (coupure totale) ou `whatsapp_only` (défaut `full`) |
| `WHATSAPP_ALLOW_CIDRS` | Plages IPv4 laissées joignables en mode `whatsapp_only` (Meta AS32934, séparées par virgule) |
| `BLOCKED_DOMAINS_WHATSAPP_ONLY` | Domaines FB/IG/Messenger/Threads résolus en `0.0.0.0` par dnsmasq du LR en mode `whatsapp_only` (séparés par virgule) — neutralise le leak FB/IG via les IP Meta partagées |
| `UISP_SYNC_ENABLED` | Active le job d'import inventaire depuis le contrôleur UISP (défaut `false`) |
| `UISP_BASE_URL` | URL du contrôleur UISP (ex. `https://13.62.145.152`) |
| `UISP_API_TOKEN` | Token API UISP (préféré ; sinon `UISP_USERNAME`/`UISP_PASSWORD`) |
| `UISP_USERNAME` / `UISP_PASSWORD` | Login web UISP (fallback si pas de token) |
| `UISP_VERIFY_TLS` | Vérif TLS du contrôleur (défaut `false` — cert auto-signé) |
| `UISP_SYNC_HOUR` | Heure quotidienne du `uisp_sync_job` (défaut `7` = **07:00 UTC** ; la Mauritanie est GMT/UTC+0 → 07:00 locale). Le job tourne aussi **1× au démarrage** du scheduler (déploiement) |
| `UISP_REQUEST_TIMEOUT` | Timeout HTTP des appels UISP en s (défaut 30) |
| `UISP_IGNORED_SITES` | Sites UISP à exclure du sync (ni créés ni màj). **Séparateur `;`** (les noms de sites contiennent des virgules, ex. `Bureau, A2`), insensible à la casse. Pour les sites bureautiques dont un switch LAN serait vu comme infra |
| `UISP_STATION_SYNC_ENABLED` | Active l'import des **stations clientes** (LR abonnés) depuis `GET /nms/api/v2.1/devices?role=station` dans la table `lrs`, sur le même `uisp_sync_job` (après l'infra). Apporte le **mode (routeur/bridge)** + le **statut « dernier état connu »** UISP de chaque client → `/access` reste complet/exact même quand un Rocket/LR est down. Écrit **uniquement** les colonnes `uisp_*` (jamais `topology_mode`/`rocket_id`/IP/blocage — propriété de `discovery_service`). Identité = **MAC** (converge avec la découverte radio). AF60 (backhaul) exclus. Importe le **roster complet** (UISP ne retourne que les stations provisionnées). Défaut `false` |
| `UISP_ROCKET_SSH_USERNAME` / `UISP_ROCKET_SSH_PASSWORD_TEMPLATE` | Creds posés sur un Rocket créé par le sync. `{site}` = code extrait du nom de site UISP (`A2 SNDE`→`SNDE`). Défaut `ubnt` / `A2{site}@4321$A2` |
| `UISP_POWER_API_USERNAME` / `UISP_POWER_API_PASSWORD` | Creds API posés sur un UISP Power créé par le sync (défaut `ubnt` / `A2@uispp2025`) |
| `UISP_AF60_SSH_USERNAME` / `UISP_AF60_SSH_PASSWORD` | Creds API posés sur un AF60 créé par le sync (défaut `ubnt` / `A2F60@4321`) |
| `NETFLOW_COLLECTOR_ENABLED` | Active le collecteur NetFlow (container `netflow-collector`, RUN_MODE=collector). Le container existe pour ça ; `false` = il idle (défaut `false`) |
| `NETFLOW_LISTEN_PORT` | Port UDP d'écoute du collecteur (défaut 2055). Publié **uniquement sur l'IP LAN** via `docker-compose.lan.yml`, **jamais 0.0.0.0** ; restreindre la source au routeur au firewall (NetFlow non authentifié). Sur le MikroTik : exporter vers `${LAN_BIND_IP}:2055` |
| `NETFLOW_FLUSH_INTERVAL_SECONDS` | Fréquence d'écriture de l'agrégat mémoire → `traffic_dest_stats` (défaut **60**) |
| `NETFLOW_BUCKET_MINUTES` | Fenêtre agrégée (défaut **1** min → débit « live » en Gb/s ; le débit = bytes ÷ bucket s) |
| `NETFLOW_INTERNAL_PREFIXES` | Préfixes traités comme **INTERNES** (notre côté). L'extrémité interne d'un flux = le client ; l'autre = l'opérateur Internet attribué. CSV de CIDR — **DOIT inclure RFC1918/CGNAT ET tout notre bloc public** (les clients ont des IP publiques dans `102.215.95.0/24`, pas seulement le /30 WAN), sinon les flux **descendants** (opérateur → client 102.215.95.x) sont vus opérateur↔opérateur et ignorés (download à 0). Le collecteur logue par cycle `down/up/skip_both_public/skip_lan` + un échantillon `src→dst` des flux rejetés pour révéler l'adressage à couvrir |
| `IPTOASN_V4_PATH` / `IPTOASN_V6_PATH` | Datasets **BGP iptoasn.com** (IP→ASN+opérateur), source ASN **primaire** (bien plus complète que GeoLite2 pour la longue traîne). Défaut `/app/data/ip2asn-v4.tsv.gz` / `-v6`. Voir `backend/data/README.md` |
| `GEOIP_ASN_DB_PATH` | Base MaxMind GeoLite2-ASN (.mmdb), **fallback** quand iptoasn ne répond pas. Défaut `/app/data/GeoLite2-ASN.mmdb`. Aucune source = tout agrégé sous "Indéterminé" |
| `TRAFFIC_STATS_RETENTION_DAYS` | Rétention batchée de `traffic_dest_stats` (défaut 90 ; `traffic_stats_retention_job`) |

## État d'implémentation

### Terminé
- [x] Structure complète du projet
- [x] FastAPI + lifespan + exception handlers
- [x] Config via env (pydantic-settings, computed fields)
- [x] PostgreSQL + SQLAlchemy async
- [x] Alembic + migrations
- [x] CRUD complet `/api/v1/devices` + endpoints diagnostics SSH/ping
- [x] Health check `/api/v1/health` (public, test DB inclus)
- [x] APScheduler + **7 jobs planifiés**
- [x] **Docker Compose** — 3 containers : postgres + backend + **frontend Next.js**
- [x] Entrypoint auto-migrations + dev/prod modes
- [x] Ruff + pre-commit
- [x] **Ping ICMP async** — `app/services/poller.py`
- [x] **Incidents automatiques** avec déduplication — `app/services/incident_service.py`
- [x] **Notifications** (WhatsApp Ultramsg — unique transport ; envoi email retiré du projet) — `notification_service.py` + `whatsapp_service.py`
- [x] **SNMP Ubiquiti** — `snmp_service.py` (radio ath0/eth0 + switch ports 1..N)
- [x] **UISP Power polling** — `uisp_power_service.py` (voltage, current, power, batterie)
- [x] **API HTTP LTU Rocket** — `ltu_api_service.py` (signal, CCQ, CINR, TX/RX rates, CPE peers, distance)
- [x] **Sonde LR → Internet** — `ssh_service.py` + `lr_internet_probe_job` (un seul SSH/LR/cycle : ping vers Google, deux signaux en sortie — `lr_no_transit` binaire et `lr_latency_high` continue)
- [x] **Moteur de règles d'alerte** — `alert_rules.py` (10+ règles : signal, CCQ, CINR, capacité, erreurs, interfaces, CPE, throughput anomaly EMA)
- [x] **Alert engine** — `alert_engine.py` (évalue règles, gère AlertState DB, ouvre/résout incidents)
- [x] **AlertState persisté en DB** — compteurs anti-flapping survivent aux redémarrages (sauf ping = in-memory)
- [x] **21 alert_types** centralisés — `core/alert_constants.py`
- [x] **Détection anomalies radio** — signal dBm, CCQ, CINR, capacité lien, taux d'erreurs
- [x] **Détection anomalies power** — batterie + voltage hors plage (20–56 V)
- [x] **Détection port switch** — port DOWN ou vitesse < 1000 Mbps
- [x] **Digest warnings** — `digest_service.py` + `warning_digest_job` (regroupement 15 min)
- [x] **Auto-découverte LTU LR** — le job LTU API lit les CPE peers du Rocket et établit la hiérarchie parent/enfant automatiquement
- [x] **Authentification API** — API key via header `X-API-Key` (`app/api/deps.py`)
- [x] **Notifications — WhatsApp (Ultramsg) remplace l'email** — depuis le 2026-06-11 le canal résolu depuis l'env est **WhatsApp** (`WHATSAPP_ENABLED` + `WHATSAPP_INSTANCE_ID` + `WHATSAPP_TOKEN` + `WHATSAPP_GROUP_ID`) : tout le pipeline d'incidents (immédiat + digest + sécurité) part vers le **groupe WhatsApp** via `whatsapp_service` (`POST /{instance}/messages/chat` Ultramsg). **L'envoi d'email a été entièrement retiré du projet (2026-06-16)** : `email_service`, l'endpoint `/system/test-email`, le job d'instabilité ping (`ping_instability` + son email), la config SMTP et la dépendance `aiosmtplib` sont supprimés. Diagnostic restant : `POST /api/v1/system/test-whatsapp`. Le registre `alert_policy.py` reste interne ; ses jeux de canaux pointent tous sur `AlertChannel.WHATSAPP`. **Restriction (2026-06-11)** : WhatsApp ne pousse QUE les alert_types de la liste blanche `WHATSAPP_ALERT_TYPES` (`alert_constants`) : `switch_port_speed_low`+`switch_port_down`, `device_flapping`, **`battery_internal_low` (Li-Ion UPS < 50%) + `battery_external_low` (banc plomb < 30%)**, `af60_link_substandard`+`af60_link_down` (lien P2P dégradé = capacité < **1.95 Gb/s**, cf. `af60_total_capacity_min_mbps`), **équipement injoignable** `rocket_down`+`switch_down`+`device_unreachable`+`airmax_down` (un UISP Power down est couvert par `device_unreachable` ; `uisp_power_unreachable` plus émis, pour éviter le doublon), plus la latence réseau (envoi direct du `network_latency_aggregate_job`). **UISP Power notifie = 2 alertes batterie + down** (voltage / ancienne alerte batterie unique retirés ; **coupure secteur `mains_power_lost` conservée et affichée dans /incidents mais NON notifiée**). Toute autre anomalie (qualité radio, voltage, coupure secteur, **sécurité**, découverte LR…) ouvre/résout son incident en DB mais **n'est notifiée nulle part**. Le chokepoint est `notification_service._dispatch` (+ `notify_security_event` + collecte du digest). (Historique 2026-06-09 : avant WhatsApp, l'email était env-only `SMTP_ENABLED`+`NOTIFICATION_EMAILS` ; `notification_channels`/`/notification-channels` et `/alert-policies` supprimés.)
- [x] **Formatage des alertes** — `alert_formatter.py` (messages WhatsApp contextualisés par type)
- [x] **API incidents** — `GET/PATCH /api/v1/incidents` (filtres status/severity/device_id/alert_type)
- [x] **Résolution = suppression** — pas d'archive : à la résolution, `incident_service.resolve_incidents` **hard-delete** l'incident. **Exception** : les types de **disponibilité** (`AVAILABILITY_ALERT_TYPES` dans `alert_constants` = `rocket_down`, `switch_down`, `device_unreachable`, `uisp_power_unreachable`, `airmax_down`) sont conservés en `status=resolved` car le **Journal des coupures** (`network_uptime_service`) reconstruit l'historique + la dispo % depuis leur `resolved_at`. La notification de résolution part quand même pour les incidents purgés (objets encore en mémoire). La page `/incidents/archive` et le lien sidebar ont été supprimés.
- [x] **Pas d'audit trail des notifications** — la table `alerts` et la page `/notifications` ont été **supprimées** (2026-06-09, migration `a8b9c0d1e2f3`). Les notifications sont toujours **envoyées** mais aucune ligne d'audit n'est persistée.
- [x] **Blocage internet client (2 modes)** — SSH sur le LR. Mode `full` : shutdown du port LAN (`lan_interface`). Mode `whatsapp_only` : **3 couches** sur le LR pour vraiment séparer WhatsApp de FB/IG (qui partagent les IP Meta) : (1) DNAT en `iptables -t nat PREROUTING` redirigeant tout DNS du sous-réseau client vers le dnsmasq du LR (anti-bypass `8.8.8.8`), (2) entrées `address=/<domaine>/0.0.0.0` ajoutées à `/etc/dnsmasq.conf` pour FB/IG/Messenger/Threads (résolus en `0.0.0.0` → connexion immédiate impossible), (3) chaîne `CLIENTBLOCK` sur `FORWARD` autorisant DNS + plages Meta (`WHATSAPP_ALLOW_CIDRS`), `DROP` le reste. **Quirk terrain (airOS 8) : `kill -HUP dnsmasq` n'applique pas les `address=` — il faut `killall dnsmasq` (airOS le respawn).** Mode persisté (`block_mode`) + `client_blocked` en DB + job `client_block_enforcement_job` qui ré-applique le mode actif toutes les 120 s (survit au reboot du LR — airOS régénère `/etc/dnsmasq.conf` au boot, l'enforcement remet le bloc dans la minute). **Garde-fou dynamique du mode `full`** : avant un shutdown, `ssh_service._collect_forbidden_ifaces` calcule en direct sur le LR les interfaces du chemin SSH/route par défaut (+ membres de bridge, parents VLAN) et refuse de les couper. **Défaut `lan_interface` par famille** : `client_block_service.default_lan_interface(model_variant)` → `eth0.1` (LTU) / `eth0` (airMAX), appliqué à la création par `discovery_service` et backfillé par la migration `m4e5f6a7b8c9`. Remplace l'ancien `is_suspended` (flag no-op supprimé)
- [x] **Dashboard frontend** — Next.js avec pages : devices, incidents, etc.

### Jobs planifiés actifs
| Job | Intervalle | Rôle |
|---|---|---|
| `heartbeat_job` | 60s | Sanity check scheduler |
| `device_ping_job` | 30s | Ping ICMP tous les devices via **un seul process `fping`** (`poller.ping_hosts_bulk` → `{ip: reachable}`), au lieu d'un sous-process `ping` par device. À 600+ devices, le `gather` de N `ping` spawnait des centaines de process simultanés qui se starvaient → faux « down » de masse + cycle qui débordait 30 s. `fping` pingue tout le parc en parallèle dans 1 process (~2-5 s), coût **plat** quelle que soit la taille du parc. Fallback `ping` par hôte borné si `fping` absent. Tolérance perte : `fping -r 2` (joignable si ≥1 réponse, comme l'ancien `ping -c 2`) pour l'ICMP rate-limité Ubiquiti. **Statut `down` seulement au seuil anti-flap** (`ping_down_threshold`=3), jamais sur un seul paquet perdu — sinon un Rocket qui route le trafic + répond à son API s'affichait « HORS LIGNE » et sortait des polls API/SNMP (qui filtrent `status=up`). L'incident `*_down` suit le même seuil. Requiert `fping` dans l'image (Dockerfile). |
| `snmp_poll_job` | 60s | Métriques SNMP LTU radio (ath0/eth0) + Switch (ports) → alert engine. **Concurrent** : Phase 1 fetch SNMP (+ découverte airMAX) de tous les rockets/switches en parallèle (sémaphore `snmp_concurrency=30`), Phase 2 persist/alert/ports en série DB. Avant : série → un tour dépassait 60 s, aggravé par les timeouts des airMAX SNMP-off qui s'additionnaient. Persistance via `persist_device_metrics` (cf. **Politique device_metrics** ci-dessous) : seules les métriques de `HISTORY_METRICS` sont empilées, le reste (tout le switch, noise, rates…) est écrasé en place (1 ligne/`(device_id, metric_name)`). Au 1er cycle après bascule d'une métrique en collapse, le DELETE absorbe son backlog historique, dans le scheduler — surtout PAS dans une migration de démarrage (un bulk delete bloquait le healthcheck backend, cf. no-op `u2a3b4c5d6e7`). |
| `power_poll_job` | 30s | API REST UISP Power (voltage, batterie) |
| `ltu_api_poll_job` | 60s | API HTTP LTU Rocket (signal, CCQ, CINR, CPE auto-discovery) → alert engine + check topologie via `peer.remote.netMode` (router/bridge) par LR, sans SSH. **Concurrent** : Phase 1 fetch tous les Rockets en parallèle (sémaphore `_LTU_POLL_CONCURRENCY=10` + deadline global `_LTU_POLL_DEADLINE_S=40s`), Phase 2 persist/découverte/alerting en série DB. Avant : série → un tour dépassait 60 s → cycles sautés (`max instances reached`) → découverte en retard de plusieurs min. |
| `airos_api_poll_job` | 60s | API HTTP airOS (`login.cgi`+`status.cgi`) sur **chaque LR airMAX** (LiteBeam) à son IP → métriques de lien (link_potential, total_capacity, rate idx, signal, CINR…), auto-rename via `host.hostname`, + check topologie via `host.netrole` (router/bridge). Remplace le SNMP pour ces LR. Requiert `ssh_username`/`ssh_password` (creds airOS). **Concurrent** : Phase 1 fetch status.cgi de tous les LiteBeam en parallèle (sémaphore `airos_concurrency=12`), Phase 2 persist/alert/topo en série DB. Avant : série → à beaucoup de LR airMAX (découverts dès l'activation SNMP du Rocket parent), un tour dépassait 250 s. |
| `lr_internet_probe_job` | 60s | SSH sur **chaque LR** avec credentials → `ping -c 5` vers `LR_LATENCY_TARGET` (8.8.8.8). Détecte à la fois la perte de transit (`lr_no_transit` après 2 cycles KO) et la dégradation de latence (`lr_latency_high` si avg ≥ 100 ms sur 3 cycles) |
| `warning_digest_job` | 15 min | Regroupe les warnings en un seul message pour éviter la fatigue d'alerte |
| `client_block_enforcement_job` | 120s | Ré-applique le blocage actif (port LAN ou filtre WhatsApp, selon `block_mode`) sur chaque LR `client_blocked` (survit au reboot du LR) |
| `client_consumption_matview_refresh_job` | 15 min | `REFRESH MATERIALIZED VIEW CONCURRENTLY client_consumption_30d` — pré-calcule la somme des deltas de compteurs bytes sur 30 j (download/upload par CPE). Avant : l'endpoint `/clients/consumption?period=30d` transférait des millions de samples vers Python pour faire la boucle `_sum_positive_deltas` → ~36 s en prod. Maintenant : delta calculé en SQL via `LAG()` + `CASE`, et 30d servi depuis la vue. |
| `client_consumption_7d_refresh_job` | 15 min | `REFRESH MATERIALIZED VIEW CONCURRENTLY client_consumption_7d` — même pattern que le matview 30 j mais borné à 7 j. La période 7 j à elle seule clockait ~13 s sur le live SQL (seq scan + external sort 30 MB) ; le matview la fait passer à <100 ms. Matview séparé car l'agrégat 30 j est un seul SUM qui ne peut pas être soustrait à une fenêtre plus étroite. 24h reste en SQL live (true rolling window, ~2 s acceptable) ; lifetime aussi (sera adressé via la rétention 90 j). |
| `device_metrics_retention_job` | 6 h | Purge `device_metrics` plus vieux que `DEVICE_METRICS_RETENTION_DAYS` (défaut 90 j) en **batches** (`DELETE … WHERE id IN (SELECT id … LIMIT n)`, boucle jusqu'à épuisement) — jamais une grosse transaction (cf. leçon `u2a3b4c5d6e7`). Seules les métriques de `HISTORY_METRICS` accumulent encore des lignes ; le reste est déjà collapsé par `persist_device_metrics`. Crée aussi `ix_device_metrics_collected_at` via `CREATE INDEX CONCURRENTLY IF NOT EXISTS` (dans le scheduler, hors path de démarrage — cf. no-op `w4c5d6e7f8a9`) pour que la purge soit un index range scan. |
| `traffic_stats_retention_job` | `TRAFFIC_STATS_RETENTION_INTERVAL_MINUTES` (6 h) | Purge `traffic_dest_stats` plus vieux que `TRAFFIC_STATS_RETENTION_DAYS` (90 j) en **batches** (même pattern que `device_metrics_retention_job`). Groupe scheduler **fast**. La collecte elle-même tourne dans le container **`netflow-collector`** (hors APScheduler). |
| `uisp_sync_job` | **Cron quotidien `UISP_SYNC_HOUR`:00 UTC** (défaut 07:00 ; Mauritanie GMT → 07:00 locale) + **1× au démarrage** (`next_run_time=now` → import dès le déploiement) | **Désactivé par défaut** (`UISP_SYNC_ENABLED=false`). Importe les équipements d'**infra** (Rocket LTU/airMAX role=ap, switches `uisps`/blackBox, UISP Power `uispp`, AF60* P2P) depuis `GET /nms/api/v2.1/devices` du contrôleur UISP. Mapping `classify_device(type, role, model)` ; identité = **MAC** (sinon IP, sinon (type,nom)). Met à jour **name/IP/site(location)** ; pose les **creds par convention famille/site à la création** (jamais d'écrasement). **Abonnés (LTU-LR/LiteBeam station)** : ignorés par l'import **infra**, mais importés dans `lrs` par `sync_uisp_stations` (après l'infra) si `UISP_STATION_SYNC_ENABLED` — apporte le mode routeur/bridge + statut UISP (colonnes `uisp_*` seules, identité MAC, AF60 exclus, **roster complet**) pour que `/access` reste complet même Rocket/LR down. **Aucun delete/deactivate**. Voir `uisp_sync_service`. |
| `flap_detection_job` | `FLAP_CHECK_INTERVAL_MINUTES` (10 min) | Détecte les équipements d'**infra instables** (flapping). Compte par device les **incidents de disponibilité** (`AVAILABILITY_ALERT_TYPES`, conservés en DB après résolution) avec `detected_at` sur les dernières `FLAP_WINDOW_HOURS` ; au-delà de `FLAP_THRESHOLD_24H` (3) → ouvre `device_flapping` (critique → WhatsApp), résout sinon. **UISP Power exclus** (`device_type=="uisp_power"` filtré dans la requête : leurs up/down sur coupure secteur sont normaux). Infra-only par nature (un LR down n'est jamais un incident). |
| `network_latency_aggregate_job` | `NETWORK_LATENCY_CHECK_INTERVAL_MINUTES` (**1440 min = 24 h**) | **Contrôle quotidien** réseau-wide : part des LR `up` dont le dernier `lr_latency_ms` ≥ seuil latence 100 ms (`lr_health_service.network_latency_summary`, réutilise `_fetch_latest_latency`). Si > `NETWORK_HIGH_LATENCY_PCT` (20%) et échantillon ≥ `NETWORK_LATENCY_MIN_SAMPLE` (10) → **message WhatsApp direct** (PAS un incident : un Incident exige un device_id). **Pas de flag/rétabli** : rapport quotidien qui n'envoie que si la condition est remplie. |
| `rocket_saturation_report_job` | **Cron quotidien `ROCKET_SATURATION_REPORT_HOUR`:00 UTC** (défaut 07:00 ; Mauritanie GMT → 07:00 locale) + **1× au démarrage** (`next_run_time=now` → rapport dès le déploiement) | **Rapport PDF quotidien** des **Rockets saturés** envoyé en **document WhatsApp**. Réutilise `network_capacity_service.get_network_capacity` ; un Rocket est saturé quand ses **clients installés ≥ capacité max** (= condition `rocket_client_overload`). `saturation_report_service` génère le PDF (lib `fpdf2`, tableau Site/Rocket/Famille/Clients/Max/Charge/Largeur, trié par charge décroissante), `whatsapp_service.send_whatsapp_document` l'upload en base64 sur Ultramsg `/messages/document`. **Envoi systématique** (même si liste vide = PDF « aucun saturé », caption ✅), contrairement à la latence. Gated `ROCKET_SATURATION_REPORT_ENABLED`. Groupe scheduler **fast** (léger, pas de SSH). Dépend des clients installés → nécessite `UISP_STATION_SYNC_ENABLED`. |
| `site_infra_report_job` | **Cron quotidien `SITE_INFRA_REPORT_HOUR`:00 UTC** (défaut 07:00) + **1× au démarrage** (`next_run_time=now`) | **Rapport PDF quotidien** de la **capacité infra par site** envoyé en **document WhatsApp**. `site_infra_service` compte par `site` (colonne dénormalisée) les équipements d'infra **Rockets + AF60 + PTP LiteBeam** (`INFRA_COUNTED_TYPES` ; **exclut switches, UISP Power, LR clients**) et calcule la marge vs `SITE_INFRA_MAX` (14) : **+N** places libres / **-N** dépassement. PDF via `fpdf2` (tableau Site/Équip./Max/Marge, dépassements en rouge, triés dépassement d'abord). **Envoi systématique** (caption ✅ si aucun dépassement). Gated `SITE_INFRA_REPORT_ENABLED`. Groupe scheduler **fast**. La même donnée est exposée par `network_capacity_service` → `/network-capacity` (clé `infra`) → section « Capacité infra par site » de `/capacity`. |

#### Politique device_metrics (history vs latest) — `persist_device_metrics` dans `jobs.py`
Tous les jobs de polling persistent leurs métriques via `persist_device_metrics(session, device_id, metrics, unit_map)`. Règle unique : si le `metric_name` est dans `HISTORY_METRICS`, on **empile** une ligne par cycle (série temporelle conservée) ; sinon on **écrase en place** (1 ligne par `(device_id, metric_name)` via DELETE+INSERT). `HISTORY_METRICS` = les **seules** métriques relues comme série par un consommateur, c.-à-d. **uniquement les compteurs bytes** :
- `peer_tx_bytes`, `peer_rx_bytes`, `radio_rx_bytes`, `radio_tx_bytes` → deltas `LAG()` de `consumption_service` (24h/7j/30j).

Tout le reste est collapsé (latest-only). **Les métriques radio (`signal_dbm`, `cinr_db`, `ccq_pct`, `link_potential_pct`, `total_capacity_mbps`, `local/remote_rx_rate_idx`) sont aussi collapsées** depuis que les 2 sections 30 j du rapport (« santé liens clients » + « qualité radio ») ont été retirées : plus aucun lecteur d'historique radio. La page « Liaisons clients » tourne en LIVE (`get_live_link_health`, fetch direct LTU/airOS), pas sur `device_metrics`. La matview `lr_health_metric_stats_30d` + son job de refresh ont été supprimés (migration `x5d6e7f8a9b0`). L'alert engine lit ses baselines (EMA throughput, deltas d'erreurs) depuis `AlertState`, **jamais** depuis `device_metrics` → collapser ne casse aucune alerte. Sans cette politique, un seul UISP Power empilait ~25 métriques toutes les 30 s (~70k lignes/jour) que rien ne relit.

### Device types reconnus
| `device_type` | Polling |
|---|---|
| `ltu_rocket` | Ping + SNMP (ath0/eth0) + API HTTP (signal, CCQ, CINR, CPE peers, distance) |
| `ltu_lr` | Ping + SNMP + Sonde transit SSH (ping internet depuis le device) |
| `uisp_switch` | Ping + SNMP standard (ports, vitesse, erreurs) |
| `uisp_power` | Ping + API REST (voltage, current, power, batterie) |

### Page /incidents = INFRASTRUCTURE uniquement (suppression côté client, 2026-06-09)
La page `/incidents` ne montre que les incidents **d'infrastructure**. Les incidents **côté client** ne sont **ni créés ni stockés** (purge DB via migration `z7f8a9b0c1d2`). Le découpage est **par device** (`rule_category`), **pas par alert_type** : les types radio (`signal_low`, `ccq_low`, `cinr_low`, `capacity_low`, `radio_link_degraded`, `high_rx_tx_errors`, `throughput_anomaly`) se déclenchent à la fois sur les **Rockets de base station** (infra → gardés) et sur les **LR abonnés** (client → supprimés), donc filtrer sur la string `alert_type` masquerait de vraies alertes infra. Le garde-fou unique est `incident_service.is_suppressed_incident(device, alert_type)`, appelé en tête de `open_incident` (retourne `(None, False)` sans rien créer) — tous les appelants ne déréférencent l'incident que sous `if is_new`, donc un `None` est sûr. **`airmax_down` est infra** (Rocket airMAX = AP de base, pas le LiteBeam abonné). Exceptions explicites (cf. `alert_constants`) :
- `CLIENT_KEPT_ALERT_TYPES = {}` — **vide** (plus aucune exception « gardé même sur un LR »).
- `INFRA_DEVICE_SUPPRESSED_ALERT_TYPES = {cpe_disconnected, rocket_client_overload, lr_bridge_mode_misconfig}` — supprimés **toujours**, même sur un device infra : `cpe_disconnected` (un CPE qui disparaît = churn côté abonné, pas notre panne) ; **`rocket_client_overload` (saturation Rocket) est géré par la page `/capacity`** et **`lr_bridge_mode_misconfig` (LR en bridge) par la page `/access`** (politique 2026-06-25 — purge DB via migration `l9a0b1c2d3e4`) : ces deux-là sont surfacés sur leur page dédiée, jamais comme incident.

Conséquence : plus aucune notification ni ligne `alerts` pour les alertes client (signal/ccq/cinr/capacity sur LR, `lr_link_substandard`, `lr_no_transit`, `lr_latency_high`, `lr_discovered`/`lr_ip_changed`/`lr_reassigned`, `cpe_disconnected`). Les jobs continuent de sonder les LR (latence/transit/SSH) et d'incrémenter leurs `AlertState` ; seul l'incident final est court-circuité.

### 26 Alert types
| Catégorie | alert_type | Déclencheur |
|---|---|---|
| Disponibilité | `rocket_down` | Ping LTU Rocket échoue ×3 |
| Disponibilité | `switch_down` | Ping Switch échoue ×3 |
| Disponibilité | `device_unreachable` | Ping device générique échoue ×3 |
| Interface | `radio_interface_down` | SNMP : ath0 OperStatus=DOWN |
| Interface | `eth0_down` | SNMP : eth0 OperStatus=DOWN |
| Interface | `cpe_disconnected` | API LTU : aucun CPE connecté |
| Radio | `signal_low` | Signal < seuil warning ou critical |
| Radio | `cinr_low` | CINR < seuil warning ou critical |
| Radio | `ccq_low` | CCQ < seuil warning ou critical |
| Radio | `radio_link_degraded` | Combinaison signal + CCQ dégradés |
| Performance | `capacity_low` | Débit réel / idéal < seuil |
| Performance | `high_rx_tx_errors` | Taux d'erreurs delta > seuil |
| Performance | `throughput_anomaly` | Débit < EMA × facteur (détection anomalie) |
| Charge AP | `rocket_client_overload` | Rocket de base station saturé : clients connectés ≥ seuil. Seuil = **formule** par famille : base à 10 MHz + `rocket_overload_clients_per_10mhz` (défaut 5) clients par tranche de +10 MHz. Bases : LTU 15, airMAX 10 (configurables, page Seuils). Donc LTU 10→15 / 20→20 / 30→25… ; airMAX 10→10 / 20→15 / 40→25… Largeur auto-détectée en direct (arrondie au multiple de 10 MHz) : LTU via API `wireless.radios[0].channelWidth.tx`, airMAX via airOS `status.cgi` `wireless.chanbw` (lu dans `snmp_poll_job`, requiert les creds airOS sur la fiche). Clients = `len(all_peers)` (LTU) / stations SNMP `airmax_peers` (airMAX). Largeur < 10 MHz → pas de seuil → pas d'incident. **Override manuel par Rocket** : `rockets.max_clients_override` (Integer nullable, migration `f3a4b5c6d7e8`) — quand posé, il **remplace entièrement** la formule (s'applique même sans largeur connue) ; éditable depuis la page **Capacité** (drill-down par Rocket, colonne « Capacité max » → bouton « modifier », vide = retour auto). Injecté dans les métriques de la règle par `alert_engine` (comme `is_backhaul`) ; `_rocket_overload_threshold(settings, airmax, width, override)`. Préservé par le sync UISP. Critique, anti-flap 3 cycles |
| Disponibilité | `device_flapping` | Équipement d'infra qui flappe : > `FLAP_THRESHOLD_24H` (3) incidents de disponibilité sur `FLAP_WINDOW_HOURS` (24 h). **UISP Power exclus** (leurs cycles up/down sur coupure secteur sont normaux → couverts par `mains_power_lost`). Critique. **Pas** un type de disponibilité (se résout/purge normalement). `flap_detection_job` |
| Power | `battery_internal_low` | **Batterie INTERNE (Li-Ion UPS) < `BATTERY_INTERNAL_CRITICAL_PCT` (50%)** → critique + notif immédiate. Pas de message de rétabli (fermeture silencieuse). `power_poll_job` |
| Power | `battery_external_low` | **Batterie EXTERNE (banc plomb) < `BATTERY_EXTERNAL_CRITICAL_PCT` (30%)** → critique + notif immédiate. Pas de message de rétabli. `power_poll_job` |
| Power | `uisp_power_unreachable` | ⚠️ **Plus émis depuis 2026-06-11** : un UISP Power down est couvert par `device_unreachable` (ping). Type conservé pour le journal/legacy ; le job ferme silencieusement les incidents legacy. |
| Power | `battery_low_warning` / `battery_low_critical` | ⚠️ **Plus émis depuis 2026-06-11** (remplacés par `battery_internal_low` / `battery_external_low`). Fermés silencieusement par le job. |
| Power | `voltage_anomaly` | ⚠️ **Plus émis depuis 2026-06-11** (politique UISP Power : seules les 2 alertes batterie + down). Fermé silencieusement. |
| Power | `mains_power_lost` | Coupure secteur (SOMELEC) : UISP Power passé sur batterie (≥ `MAINS_LOSS_THRESHOLD` cycles). **Affiché dans /incidents mais NON notifié** (hors `WHATSAPP_ALERT_TYPES`). `power_poll_job` / `_evaluate_mains_power` |
| Switch | `switch_port_down` | Port switch connecté au Rocket = DOWN |
| Switch | `switch_port_speed_low` | Port UP mais vitesse < 1000 Mbps |
| Transit | `transit_unavailable` | (réservé) |
| Transit | `lr_no_transit` | SSH OK mais ping internet échoue depuis LTU LR |
| Transit | `lr_latency_high` | Latence moyenne LR → `8.8.8.8` ≥ `LR_LATENCY_CRITICAL_MS` (défaut 100 ms) sur 3 cycles → critique |
| Lien client | `lr_link_substandard` | Incident **consolidé** per-LR — seuils par famille radio. LTU : potentiel < 50 % / capacité < 60 Mbps / RX < ×6 → critical. airMAX : potentiel < 40 % / capacité < 60 Mbps / RX < ×4 → critical, 4 ≤ RX < 6 → warning. Anti-flap : 5 cycles. |
| Config | `lr_bridge_mode_misconfig` | LR détecté en mode bridge (au lieu de routeur) → le blocage client ne peut pas fonctionner ; l'opérateur doit reconfigurer le LR en routeur via airOS |

### API Endpoints
| Méthode | Chemin | Auth | Description |
|---|---|---|---|
| GET | `/api/v1/health` | Non | Health check + test DB |
| GET | `/api/v1/devices` | Oui | Liste des équipements |
| POST | `/api/v1/devices` | Oui | Ajouter un équipement |
| GET | `/api/v1/devices/{id}` | Oui | Détail équipement |
| PUT | `/api/v1/devices/{id}` | Oui | Modifier équipement |
| DELETE | `/api/v1/devices/{id}` | Oui | Supprimer équipement |
| GET | `/api/v1/devices/{id}/metrics/latest` | Oui | Dernières métriques (dashboard) |
| POST | `/api/v1/devices/{id}/check-ssh` | Oui | Test SSH vers le device |
| POST | `/api/v1/devices/{id}/check-ping` | Oui | Ping internet via SSH depuis le device |
| POST | `/api/v1/devices/{id}/block-client` | Oui | Bloque l'accès internet du client via SSH — body `mode`: `full` (shutdown port LAN) ou `whatsapp_only` (filtre iptables WhatsApp+DNS) |
| POST | `/api/v1/devices/{id}/unblock-client` | Oui | Rétablit l'accès internet complet du client (port LAN remonté + filtre WhatsApp retiré) |
| GET | `/api/v1/incidents` | Oui | Liste incidents (filtres: status, severity, device_id, alert_type) — lecture seule |
| GET | `/api/v1/incidents/{id}` | Oui | Détail incident — lecture seule |
| GET | `/api/v1/system` | Oui | Infos système (version, uptime scheduler) |
| POST | `/api/v1/system/test-whatsapp` | Oui | Diagnostic WhatsApp (Ultramsg) — envoie un message de test au groupe `WHATSAPP_GROUP_ID` |
| POST | `/api/v1/uisp/sync` | Oui | Import des équipements d'infra depuis le contrôleur UISP (`?dry_run=true` = prévisualisation sans écriture). Renvoie un résumé (créés/màj/ignorés + échantillon) |
| GET | `/api/v1/network-capacity` | Oui | Capacité clients : par famille (LTU/airMAX) et par site, clients connectés (`peer_count`) vs max (seuil `rocket_client_overload`). Rockets sans largeur connue exclus des totaux (`unknown`). `network_capacity_service`. Inclut aussi la clé **`infra`** (`site_infra_service.get_site_infra_capacity`) : budget d'équipements infra par site (Rockets+AF60+PTP) vs `SITE_INFRA_MAX`, avec marge `remaining` signée |
| GET | `/api/v1/traffic/top-destinations` | Oui | **Volume** Internet par opérateur/CDN (ASN) sur `?period=24h\|7d\|30d` : SUM(down/up) GROUP BY asn depuis `traffic_dest_stats`, trié par total + part %. `traffic_service.get_top_destinations` |
| GET | `/api/v1/traffic/throughput` | Oui | **Débit** (Gb/s) par opérateur sur le dernier bucket : descendant/montant Mbps + part du download. Montre le partage de la bande passante WAN en direct. `traffic_service.get_throughput` |
| GET | `/api/v1/traffic/throughput-history` | Oui | **Historique de débit** descendant par opérateur sur `?period=1h\|6h\|24h` : re-bin des buckets 1 min (top-N opérateurs + « Autres »), séries alignées pour un graphe d'aires empilées. `traffic_service.get_throughput_history` (SQL `date_bin`) |

### Frontend Next.js
| Page | Chemin | Contenu |
|---|---|---|
| Devices | `/devices` | Liste avec statut, dernière vue, métriques, modal détail |
| Anomalies détectées | `/incidents` | Anomalies actuellement détectées (lecture seule, résolution automatique) |
| Capacité du réseau | `/capacity` | 2 cercles (LTU/airMAX) consommé vs disponible sur tout le réseau + barres par site (LTU/airMAX séparés) ; clic site → table Rockets (connectés/max + largeur). Donut SVG custom (pas de lib de charts). Inclut la section **« Capacité infra par site »** (table Site/Équip. infra/Max/Marge, marge +N vert / -N rouge) alimentée par la clé `infra` de `/network-capacity` |
| Destinations Internet | `/traffic` | 3 sections : **Débit en direct** (descendant/montant Gb/s + partage par opérateur, `/traffic/throughput`, refresh 30 s), **Débit descendant par opérateur** (graphe d'aires empilées SVG sur 1h/6h/24h, `/traffic/throughput-history`) et **Volume** (par opérateur sur 24h/7j/30j, down/up/total + part, `/traffic/top-destinations`). Repère les candidats à un serveur de cache. **Vide tant que `NETFLOW_COLLECTOR_ENABLED=false` ou que le routeur n'exporte pas vers le collecteur** |

### À implémenter (prochaines phases)
- [ ] Tests unitaires et d'intégration
- [ ] Config nginx pour la production (reverse proxy)

## Déploiement production (serveur physique)

Le système est prévu pour être déployé sur un serveur physique après validation maquette.

### Points d'attention pour la production
- Mettre `APP_ENV=production` dans le `.env` du serveur → uvicorn sans `--reload`, avec workers
- **Scheduler isolé en prod** : `docker-compose.prod.yml` ajoute un container dédié `scheduler` (`RUN_MODE=scheduler`, `SCHEDULER_ENABLED=true`) qui exécute APScheduler en process séparé. Le `backend` tourne avec `SCHEDULER_ENABLED=false` et peut scaler à `UVICORN_WORKERS>1` sans dupliquer les jobs (sinon chaque worker démarrerait son propre scheduler → SSH/alertes en double). Les migrations Alembic restent gérées par le container `backend` ; le `scheduler` attend `backend: service_healthy` avant de démarrer.
- **Collecteur NetFlow isolé** : un container dédié `netflow-collector` (`RUN_MODE=collector`, entrée `app/tasks/collector_runner.py`) écoute le NetFlow exporté par le MikroTik (UDP) — un listener permanent, pas un job APScheduler. Off par défaut (`NETFLOW_COLLECTOR_ENABLED=false` → idle). Le port UDP n'est publié que sur l'IP LAN via `docker-compose.lan.yml` (`${LAN_BIND_IP}:2055/udp`), **jamais 0.0.0.0** ; **verrouiller la source au MikroTik au firewall** (NetFlow non authentifié). Déposer `backend/data/GeoLite2-ASN.mmdb` (cf. `backend/data/README.md`) pour les noms d'opérateurs.
- Séparer les volumes Docker pour les données PostgreSQL sur un stockage persistant.
- Mettre en place un reverse proxy (nginx ou Caddy) devant uvicorn.
- Remplacer les mots de passe et l'`API_KEY` par des valeurs fortes dans `.env`.
- Logs : rediriger stdout vers un aggregateur (Loki, ELK, ou simple fichier rotatif).
- **Auth UI** : le dashboard est protégé par login + sessions serveur (`auth_service.py`, cookie `supervisor_session` HttpOnly+Secure+SameSite=Lax, toutes les routes derrière `require_user_or_api_key`). Créer le premier compte admin après le 1er déploiement : `LAN_BIND_IP=10.135.3.25 docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lan.yml exec backend python scripts/create_admin.py` (cf. **Commandes de déploiement type** — toujours les 3 `-f` + `LAN_BIND_IP`).
- **Exposition réseau** : nginx est bindé `127.0.0.1` uniquement → l'IP publique reste accessible **seulement par tunnel SSH** (`ssh -L 8443:127.0.0.1:443 a2@<serveur>` → `https://localhost:8443/`). Pour un **accès LAN direct** (réseau interne d'entreprise, pas de tunnel), composer en plus `docker-compose.lan.yml` avec `LAN_BIND_IP` = l'IP LAN du serveur : nginx ajoute alors un binding sur cette IP **seulement** (jamais `0.0.0.0`), donc l'interface publique reste non-exposée. L'accès LAN se fait en HTTPS (`https://<LAN_BIND_IP>/`, avertissement de certificat à accepter une fois). **Ne jamais binder `0.0.0.0`** (incident 2026-05-17).

### Commandes de déploiement type

> **Serveur de prod = `10.135.3.25` (sur le LAN, derrière le FortiGate 40F).**
> Le déploiement STANDARD compose **3 fichiers** (`docker-compose.yml` +
> `.prod.yml` + `.lan.yml`) avec **`LAN_BIND_IP=10.135.3.25`** — c'est ce qui
> donne l'accès LAN direct `https://10.135.3.25/` (l'IP publique reste
> tunnel-only). `docker-compose.lan.yml` impose `LAN_BIND_IP` (`:?`) : sans lui
> le `up` échoue. ⚠️ **TOUTE** commande `docker compose` sur cette stack (`up`,
> `logs`, `exec`, `restart`, `down`…) doit reprendre les **3 `-f` + `LAN_BIND_IP`**,
> sinon le binding LAN saute. Astuce : `export LAN_BIND_IP=10.135.3.25` puis un
> alias `dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lan.yml'`.

```bash
# Sur le serveur (10.135.3.25)
git pull
cp .env.example .env  # 1re fois seulement, puis éditer (APP_ENV=production, secrets…)

export LAN_BIND_IP=10.135.3.25
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lan.yml'

# Déploiement standard (6 conteneurs : postgres + backend + frontend +
# scheduler[fast] + scheduler-heavy + netflow-collector). Le backend
# (RUN_MODE=api) applique les migrations Alembic au démarrage.
dc up -d --build
dc logs -f backend            # suivre les migrations + le démarrage

# Créer le premier compte admin (une fois)
dc exec backend python scripts/create_admin.py
```

## Commandes utiles

```bash
# Démarrer l'environnement local
docker compose up --build

# Vérifier la santé de l'API
curl http://localhost:8000/api/v1/health

# Ajouter un équipement à superviser (avec API key)
curl -X POST http://localhost:8000/api/v1/devices \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <votre_api_key>" \
  -d '{"name":"LTU Rocket","ip_address":"192.168.1.10","device_type":"ltu_rocket"}'

# Suivre les logs en temps réel
docker compose logs -f backend

# Créer une migration après changement de modèle
docker compose exec backend alembic revision --autogenerate -m "description"

# Appliquer les migrations manuellement
docker compose exec backend alembic upgrade head

# Linter
docker compose exec backend ruff check app/
docker compose exec backend ruff format app/
```
