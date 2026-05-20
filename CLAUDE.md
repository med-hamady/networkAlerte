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
│   └── alert_constants.py   # Source unique de vérité : Severity, AlertChannel, alert_type keys (22 types)
├── api/
│   ├── router.py            # Montage des routers avec prefix /api/v1 + auth API key
│   ├── deps.py              # verify_api_key — authentification par header X-API-Key
│   └── endpoints/
│       ├── health.py              # GET /health (public — test DB inclus)
│       ├── devices.py             # CRUD + diagnostics SSH/ping sur /devices
│       ├── incidents.py           # GET/PATCH /incidents
│       ├── notifications.py       # GET /notifications (historique alertes)
│       ├── notification_channels.py  # CRUD /notification-channels
│       ├── alert_policies.py      # CRUD /alert-policies
│       └── system.py              # GET/POST /system (infos système, test notifications)
├── models/                  # SQLAlchemy ORM (Base avec id, created_at, updated_at)
│   ├── device.py            # Équipements supervisés (+ parent_id hiérarchie, policy_overrides JSON)
│   ├── device_metric.py     # Métriques time-series
│   ├── incident.py          # Incidents (open/acknowledged/resolved + probable_cause)
│   ├── alert.py             # Notifications envoyées (audit trail)
│   ├── alert_state.py       # Compteurs d'anti-flapping persistés en DB (survit aux redémarrages)
│   ├── power_status_log.py  # Relevés UISP Power (voltage, current, power)
│   └── notification_channel.py  # Canaux d'alerte configurés via API
├── schemas/                 # Pydantic — validation I/O API
│   ├── device.py
│   ├── incident.py
│   ├── alert.py
│   ├── notification_channel.py
│   └── alert_policy.py
├── services/
│   ├── device_service.py           # CRUD devices
│   ├── poller.py                   # Ping ICMP async (asyncio subprocess)
│   ├── incident_service.py         # Création/résolution/déduplication d'incidents
│   ├── notification_service.py     # Routage et envoi des notifications (Slack/webhook/email)
│   ├── email_service.py            # Envoi SMTP HTML + plain text
│   ├── snmp_service.py             # SNMP : LTU radio (ath0/eth0) + Switch (ports 1..N)
│   ├── uisp_power_service.py       # API REST UISP Power (voltage, current, batterie)
│   ├── ltu_api_service.py          # API HTTP LTU Rocket (signal, CCQ, CINR, CPE peers)
│   ├── ssh_service.py              # SSH via paramiko : check_ssh_access, ping_targets_via_ssh, set_lan_interface, set_whatsapp_only, garde-fou _collect_forbidden_ifaces
│   ├── client_block_service.py     # Blocage client 2 modes (full / whatsapp_only) + enforcement
│   ├── alert_engine.py             # Orchestrateur : évalue règles, gère AlertState, ouvre/résout incidents
│   ├── alert_rules.py              # Règles d'alerte pure Python (sans DB) — 10+ règles
│   ├── alert_correlation.py        # Corrélation de causes (ex: rocket_down causé par switch_down)
│   ├── alert_formatter.py          # Formatage messages Slack/email par type d'alerte
│   ├── alert_policy.py             # Politiques : quel canal pour quel alert_type
│   ├── digest_service.py           # Regroupement des warnings en digest 15 min
│   └── notification_channel_service.py  # CRUD canaux via DB
├── tasks/
│   ├── scheduler.py         # Init APScheduler, start/stop lifecycle
│   └── jobs.py              # 7 jobs planifiés (voir tableau ci-dessous)
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
- **Scheduler lifecycle** : lié au lifespan FastAPI. En production avec plusieurs workers, un seul worker doit tourner le scheduler (ou utiliser un job store persistant).
- **alert_constants.py** : source unique de vérité pour les `alert_type` strings. Ne jamais redéfinir ces constantes dans d'autres modules.
- **AlertState** : les compteurs anti-flapping sont persistés en DB (pas in-memory) pour survivre aux redémarrages, sauf les compteurs de ping qui restent in-memory (`_failure_counts` dans jobs.py).
- **Authentification** : toutes les routes sauf `/health` sont protégées par `verify_api_key` (header `X-API-Key`).

## Variables d'environnement importantes

> Les **credentials des équipements** (LTU Rocket, LTU LR SSH, UISP Power) ne
> sont **pas** dans le `.env` : ils sont stockés par device dans la table
> `devices` (colonnes `ssh_username`, `ssh_password`, `ssh_port`,
> `uisp_power_username`, `uisp_power_password`, `uisp_power_port`).
> Configuration via `PUT /api/v1/devices/{id}` ou le formulaire UI.

| Variable | Rôle |
|---|---|
| `APP_ENV` | `development` (reload) ou `production` (workers, pas de reload) |
| `POSTGRES_HOST` | Hôte PostgreSQL |
| `POSTGRES_PORT` | Port PostgreSQL (défaut 5432) |
| `POSTGRES_USER` | Utilisateur DB |
| `POSTGRES_PASSWORD` | Mot de passe DB |
| `POSTGRES_DB` | Nom de la base |
| `SCHEDULER_ENABLED` | Active/désactive APScheduler |
| `DEBUG` | Mode debug SQLAlchemy |
| `LOG_LEVEL` | Niveau de log (INFO, DEBUG, WARNING) |
| `API_KEY` | Clé d'authentification API (header X-API-Key) |
| `SNMP_DEFAULT_COMMUNITY` | Community SNMP par défaut (ex: public) |
| `SNMP_PORT` | Port SNMP (défaut 161) |
| `SNMP_TIMEOUT` | Timeout SNMP en secondes |
| `SWITCH_MAX_PORTS` | Nombre de ports à scanner sur le switch |
| `SWITCH_ROCKET_PORT_INDEX` | Index du port switch connecté au Rocket (0 = désactivé) |
| `SWITCH_PORT_MIN_SPEED_MBPS` | Vitesse minimale attendue sur ce port (défaut 1000 Mbps) |
| `TRANSIT_PROBE_IPS` | IPs à pinger depuis le LTU LR (ex: `1.1.1.1,8.8.8.8`) |
| `TRANSIT_PROBE_INTERVAL` | Intervalle sonde transit (secondes) |
| `TRANSIT_PROBE_THRESHOLD` | Cycles consécutifs KO avant incident transit |
| `SLACK_WEBHOOK_URL` | Webhook Slack pour les notifications |
| `WEBHOOK_URL` | Webhook générique (JSON POST) |
| `SMTP_HOST` | Serveur SMTP pour les emails |
| `SMTP_PORT` | Port SMTP |
| `SMTP_USERNAME` | Identifiant SMTP |
| `SMTP_PASSWORD` | Mot de passe SMTP |
| `SMTP_FROM` | Adresse expéditeur |
| `SMTP_TO` | Destinataire(s) emails |
| `WARNING_DIGEST_MINUTES` | Intervalle digest warnings (défaut 15 min) |
| `PING_DOWN_THRESHOLD` | Pings consécutifs échoués avant incident (défaut 3) |
| `SIGNAL_WARN_DBM` | Seuil signal warning (défaut -70 dBm) |
| `SIGNAL_CRIT_DBM` | Seuil signal critical (défaut -80 dBm) |
| `SIGNAL_TOLERANCE_DBM` | Marge de tolérance signal — l'incident `signal_low` n'ouvre qu'à `seuil − tolérance` (défaut 5 dBm ; 0 = strict) |
| `CCQ_WARN_PCT` | Seuil CCQ warning (défaut 75%) |
| `CCQ_CRIT_PCT` | Seuil CCQ critical (défaut 50%) |
| `CCQ_TOLERANCE_PCT` | Bande d'hystérésis CCQ DL+UL — ouvre à `seuil − tol`, résout au seuil nominal (défaut 5% ; 0 = strict) |
| `CINR_WARN_DB` | Seuil CINR warning (défaut 20 dB) |
| `CINR_CRIT_DB` | Seuil CINR critical (défaut 10 dB) |
| `CINR_TOLERANCE_DB` | Bande d'hystérésis CINR DL+UL — ouvre à `seuil − tol`, résout au seuil nominal (défaut 3 dB ; 0 = strict) |
| `BATTERY_WARNING_PCT` | Seuil batterie warning (défaut 25%) |
| `BATTERY_CRITICAL_PCT` | Seuil batterie critical (défaut 10%) |
| `CLIENT_BLOCK_ENFORCEMENT_ENABLED` | Active le job qui ré-applique le blocage client (défaut true) |
| `CLIENT_BLOCK_ENFORCE_INTERVAL` | Intervalle de ré-application du blocage client en secondes (défaut 120) |
| `CLIENT_BLOCK_DEFAULT_MODE` | Mode de blocage par défaut : `full` (coupure totale) ou `whatsapp_only` (défaut `full`) |
| `WHATSAPP_ALLOW_CIDRS` | Plages IPv4 laissées joignables en mode `whatsapp_only` (Meta AS32934, séparées par virgule) |
| `BLOCKED_DOMAINS_WHATSAPP_ONLY` | Domaines FB/IG/Messenger/Threads résolus en `0.0.0.0` par dnsmasq du LR en mode `whatsapp_only` (séparés par virgule) — neutralise le leak FB/IG via les IP Meta partagées |

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
- [x] **Notifications** (Slack webhook + webhook générique + email SMTP HTML) — `notification_service.py` + `email_service.py`
- [x] **SNMP Ubiquiti** — `snmp_service.py` (radio ath0/eth0 + switch ports 1..N)
- [x] **UISP Power polling** — `uisp_power_service.py` (voltage, current, power, batterie)
- [x] **API HTTP LTU Rocket** — `ltu_api_service.py` (signal, CCQ, CINR, TX/RX rates, CPE peers, distance)
- [x] **Sonde transit SSH** — `ssh_service.py` + `lr_transit_probe_job` (ping internet depuis LTU LR via SSH)
- [x] **Moteur de règles d'alerte** — `alert_rules.py` (10+ règles : signal, CCQ, CINR, capacité, erreurs, interfaces, CPE, throughput anomaly EMA)
- [x] **Alert engine** — `alert_engine.py` (évalue règles, gère AlertState DB, ouvre/résout incidents, appelle corrélation)
- [x] **AlertState persisté en DB** — compteurs anti-flapping survivent aux redémarrages (sauf ping = in-memory)
- [x] **Corrélation de causes** — `alert_correlation.py` (ex: rocket_down causé par switch_down, avec corrélation temporelle)
- [x] **23 alert_types** centralisés — `core/alert_constants.py`
- [x] **Détection anomalies radio** — signal dBm, CCQ, CINR, capacité lien, taux d'erreurs
- [x] **Détection anomalies power** — batterie + voltage hors plage (20–56 V)
- [x] **Détection port switch** — port DOWN ou vitesse < 1000 Mbps
- [x] **Digest warnings** — `digest_service.py` + `warning_digest_job` (regroupement 15 min)
- [x] **Auto-découverte LTU LR** — le job LTU API lit les CPE peers du Rocket et établit la hiérarchie parent/enfant automatiquement
- [x] **Authentification API** — API key via header `X-API-Key` (`app/api/deps.py`)
- [x] **Canaux de notification via DB** — CRUD `/api/v1/notification-channels`
- [x] **Politiques d'alerte** — CRUD `/api/v1/alert-policies` (routage alert_type → canal)
- [x] **Formatage des alertes** — `alert_formatter.py` (messages Slack/email contextualisés par type)
- [x] **API incidents** — `GET/PATCH /api/v1/incidents` (filtres status/severity/device_id/alert_type)
- [x] **Enregistrement alertes** — table `alerts` alimentée à chaque notification (audit trail)
- [x] **Blocage internet client (2 modes)** — SSH sur le LR. Mode `full` : shutdown du port LAN (`lan_interface`). Mode `whatsapp_only` : **3 couches** sur le LR pour vraiment séparer WhatsApp de FB/IG (qui partagent les IP Meta) : (1) DNAT en `iptables -t nat PREROUTING` redirigeant tout DNS du sous-réseau client vers le dnsmasq du LR (anti-bypass `8.8.8.8`), (2) entrées `address=/<domaine>/0.0.0.0` ajoutées à `/etc/dnsmasq.conf` pour FB/IG/Messenger/Threads (résolus en `0.0.0.0` → connexion immédiate impossible), (3) chaîne `CLIENTBLOCK` sur `FORWARD` autorisant DNS + plages Meta (`WHATSAPP_ALLOW_CIDRS`), `DROP` le reste. **Quirk terrain (airOS 8) : `kill -HUP dnsmasq` n'applique pas les `address=` — il faut `killall dnsmasq` (airOS le respawn).** Mode persisté (`block_mode`) + `client_blocked` en DB + job `client_block_enforcement_job` qui ré-applique le mode actif toutes les 120 s (survit au reboot du LR — airOS régénère `/etc/dnsmasq.conf` au boot, l'enforcement remet le bloc dans la minute). **Garde-fou dynamique du mode `full`** : avant un shutdown, `ssh_service._collect_forbidden_ifaces` calcule en direct sur le LR les interfaces du chemin SSH/route par défaut (+ membres de bridge, parents VLAN) et refuse de les couper. **Défaut `lan_interface` par famille** : `client_block_service.default_lan_interface(model_variant)` → `eth0.1` (LTU) / `eth0` (airMAX), appliqué à la création par `discovery_service` et backfillé par la migration `m4e5f6a7b8c9`. Remplace l'ancien `is_suspended` (flag no-op supprimé)
- [x] **Dashboard frontend** — Next.js avec pages : devices, notification-channels

### Jobs planifiés actifs
| Job | Intervalle | Rôle |
|---|---|---|
| `heartbeat_job` | 60s | Sanity check scheduler |
| `device_ping_job` | 30s | Ping ICMP tous les devices (anti-flap 3 cycles, corrélation après) |
| `snmp_poll_job` | 60s | Métriques SNMP LTU radio (ath0/eth0) + Switch (ports) → alert engine |
| `power_poll_job` | 30s | API REST UISP Power (voltage, batterie) |
| `ltu_api_poll_job` | 60s | API HTTP LTU Rocket (signal, CCQ, CINR, CPE auto-discovery) → alert engine |
| `lr_transit_probe_job` | 60s | SSH → LTU LR → ping internet (1.1.1.1, 8.8.8.8) — détecte coupure transit |
| `warning_digest_job` | 15 min | Regroupe les warnings en un seul message pour éviter la fatigue d'alerte |
| `client_block_enforcement_job` | 120s | Ré-applique le blocage actif (port LAN ou filtre WhatsApp, selon `block_mode`) sur chaque LR `client_blocked` (survit au reboot du LR) |
| `lr_topology_check_job` | 60 min | Détecte mode routeur vs bridge sur chaque LR (via SSH) ; ouvre un incident `lr_bridge_mode_misconfig` (warning) si bridge → le blocage n'est pas opérationnel sur ce LR tant qu'il n'est pas repassé en routeur |

### Device types reconnus
| `device_type` | Polling |
|---|---|
| `ltu_rocket` | Ping + SNMP (ath0/eth0) + API HTTP (signal, CCQ, CINR, CPE peers, distance) |
| `ltu_lr` | Ping + SNMP + Sonde transit SSH (ping internet depuis le device) |
| `uisp_switch` | Ping + SNMP standard (ports, vitesse, erreurs) |
| `uisp_power` | Ping + API REST (voltage, current, power, batterie) |

### 24 Alert types
| Catégorie | alert_type | Déclencheur |
|---|---|---|
| Disponibilité | `rocket_down` | Ping LTU Rocket échoue ×3 |
| Disponibilité | `lr_down` | Ping LTU LR échoue ×3 |
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
| Power | `uisp_power_unreachable` | API UISP Power injoignable |
| Power | `battery_low_warning` | Batterie < 25% |
| Power | `battery_low_critical` | Batterie < 10% |
| Power | `voltage_anomaly` | Voltage < 20 V ou > 56 V |
| Switch | `switch_port_down` | Port switch connecté au Rocket = DOWN |
| Switch | `switch_port_speed_low` | Port UP mais vitesse < 1000 Mbps |
| Transit | `transit_unavailable` | (réservé) |
| Transit | `lr_no_transit` | SSH OK mais ping internet échoue depuis LTU LR |
| Lien client | `lr_link_substandard` | Incident **consolidé** per-LR : ≥1 plancher franchi (potentiel < 60 %, capacité totale < 60 Mbps, débit RX local/distant < ×6) sur 5 cycles — critique |
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
| GET | `/api/v1/notifications` | Oui | Historique des alertes envoyées |
| GET | `/api/v1/notification-channels` | Oui | Liste canaux de notification |
| POST | `/api/v1/notification-channels` | Oui | Créer un canal |
| PUT | `/api/v1/notification-channels/{id}` | Oui | Modifier un canal |
| DELETE | `/api/v1/notification-channels/{id}` | Oui | Supprimer un canal |
| GET | `/api/v1/alert-policies` | Oui | Liste politiques d'alerte |
| POST | `/api/v1/alert-policies` | Oui | Créer une politique (alert_type → canal) |
| PUT | `/api/v1/alert-policies/{id}` | Oui | Modifier une politique |
| DELETE | `/api/v1/alert-policies/{id}` | Oui | Supprimer une politique |
| GET | `/api/v1/system` | Oui | Infos système (version, uptime scheduler) |

### Frontend Next.js
| Page | Chemin | Contenu |
|---|---|---|
| Devices | `/devices` | Liste avec statut, dernière vue, métriques, modal détail |
| Anomalies détectées | `/incidents` | Anomalies actuellement détectées (lecture seule, résolution automatique) |
| Archive | `/incidents/archive` | Historique des incidents auto-résolus |
| Notification Channels | `/notification-channels` | Gestion des canaux Slack/email/webhook |

### À implémenter (prochaines phases)
- [ ] Tests unitaires et d'intégration
- [ ] Config nginx pour la production (reverse proxy)

## Déploiement production (serveur physique)

Le système est prévu pour être déployé sur un serveur physique après validation maquette.

### Points d'attention pour la production
- Mettre `APP_ENV=production` dans le `.env` du serveur → uvicorn sans `--reload`, avec workers
- **APScheduler + plusieurs workers** : utiliser `--workers 1` en production pour éviter les jobs dupliqués. Si passage à plusieurs workers nécessaire, ajouter un job store PostgreSQL ou Redis.
- Séparer les volumes Docker pour les données PostgreSQL sur un stockage persistant.
- Mettre en place un reverse proxy (nginx ou Caddy) devant uvicorn.
- Remplacer les mots de passe et l'`API_KEY` par des valeurs fortes dans `.env`.
- Logs : rediriger stdout vers un aggregateur (Loki, ELK, ou simple fichier rotatif).

### Commandes de déploiement type
```bash
# Sur le serveur
git pull
cp .env.example .env  # puis éditer avec les vraies valeurs
APP_ENV=production docker compose up -d --build
docker compose logs -f backend
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
