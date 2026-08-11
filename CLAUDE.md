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
│   ├── device.py            # Équipements supervisés (+ parent_id hiérarchie, policy_overrides JSON, **`uplink_switch_id`/`uplink_switch_port`/`uplink_detected_at`** = sur quel port de switch l'équipement est physiquement câblé, auto-détecté depuis la FDB du switch, jamais saisi à la main ; les deux NULL = câblage inconnu ⇒ port non surveillé)
│   ├── device_metric.py     # Métriques time-series
│   ├── incident.py          # Incidents (open/acknowledged/resolved)
│   ├── alert_state.py       # Compteurs d'anti-flapping persistés en DB (survit aux redémarrages)
│   ├── lr_metric_sample.py # Historique des COURBES de la fiche équipement en buckets (largeur `LR_METRIC_HISTORY_BUCKET_SECONDS`, défaut 60 s ; 1 ligne/(device_id, **metric_name**, bucket_start), avg/min/max/sample_count). Une courbe par métrique (latence, capacité du lien, capacités DL/UL, débits DL/UL). Table DÉDIÉE et pas `device_metrics` : empiler les polls ferait ~1M lignes/jour (cf. l'épisode de bloat) — le bucket ramène à 1440 lignes/jour/(device, métrique) à 60 s. **Le coût est ∝ au nombre de métriques de `GRAPH_METRICS`**
│   ├── power_status_log.py  # Relevés UISP Power (voltage, current, power)
│   └── site_link.py         # **Câblage INTER-SITES** (backhauls), rapatrié 1×/jour depuis les data-links UISP. 1 ligne = 1 **lien physique** (2 radios entre les mêmes sites = 2 lignes ; le regroupement par paire est fait à la lecture). Porte les **MAC** des deux bouts (c'est par elles que la lecture rejoint notre inventaire) + les noms UISP (une extrémité peut ne pas être supervisée : le switch UniFi du HQ porte les 3 liaisons fibre de la racine). ⚠️ **La SANTÉ n'y est PAS** : statut/capacité/potentiel sont relus en direct à l'affichage — les figer ici afficherait l'état d'hier
├── schemas/                 # Pydantic — validation I/O API
│   ├── device.py
│   └── incident.py
├── services/
│   ├── device_service.py           # CRUD devices
│   ├── poller.py                   # Ping ICMP async (asyncio subprocess)
│   ├── incident_service.py         # Création/résolution/déduplication d'incidents
│   ├── notification_service.py     # Dispatch des notifications — **WhatsApp (Ultramsg) est l'UNIQUE transport** (l'envoi d'email a été retiré du projet). _deliver / digest / security routent vers WhatsApp. **Liste blanche `WHATSAPP_ALERT_TYPES`** (chokepoint dans `_dispatch`) : seules 5 anomalies sont poussées, tout le reste ouvre l'incident en DB mais n'est notifié nulle part
│   ├── whatsapp_service.py         # Envoi WhatsApp via Ultramsg (POST /{instance}/messages/chat → groupe WHATSAPP_GROUP_ID). httpx async, jamais raise (False sur échec)
│   ├── snmp_service.py             # SNMP : LTU radio (ath0/eth0) + Switch (ports 1..N) + `resolve_mac_ports` (FDB BRIDGE-MIB : sur quel port une MAC connue est apprise)
│   ├── switch_port_service.py      # **Quel équipement supervisé sur quel port de switch** — détecté, jamais saisi à la main. Source PRIMAIRE = les **data-links du contrôleur UISP** (`detect_from_uisp`) : nos switches n'exposent **pas** BRIDGE-MIB et n'émettent **pas** de LLDP (vérifié terrain), mais UISP connaît le câblage. La FDB SNMP (`detect_all`) ne reste qu'en fallback pour un switch qu'UISP ne couvre pas. C'est ce qui rend les règles `switch_port_down`/`switch_port_speed_low` opérantes : elles étaient gated sur `rocket_port_index`, colonne qu'AUCUN code ne renseignait (NULL partout → aucune alerte de port n'a jamais pu partir sur aucun switch). Écrit `devices.uplink_switch_id/_port`. Voir **Surveillance des ports de switch**
│   ├── uisp_power_service.py       # API REST UISP Power (voltage, current, batterie)
│   ├── ltu_api_service.py          # API HTTP LTU Rocket (signal, CCQ, CINR, CPE peers)
│   ├── uisp_assignment_service.py  # **Association équipement ↔ client CRM** : reçoit une MAC + un id CRM, rien d'autre (transposition du formulaire UISP « unknown → choisir le client »). Pose la clé d'abord si l'équipement est absent du contrôleur. ⚠️ Le **site** est une plomberie INTERNE jamais exposée : UISP rattache à un site, et c'est le site qui porte `ucrm.client.id` — la traduction id CRM → site est notre travail. **Seul chemin d'ÉCRITURE vers le contrôleur** (token API en écriture requis, sinon 403)
│   ├── uisp_enrollment_service.py  # Enrôlement UISP d'un CPE : pose la clé du contrôleur par SSH (unitaire + lot sur la liste « absents de UISP »), garde-fou MAC, concurrence SSH bornée, écrit `lrs.uisp_enrolled_at`. **Aucun enforcement** (un enrôlement est ponctuel — le rejouer dé-enrôlerait). Voir **Enrôlement UISP**
│   ├── ssh_service.py              # SSH via paramiko : check_ssh_access, ping_targets_via_ssh, set_lan_interface, set_whatsapp_only, **set_uisp_key** (enrôlement UISP), garde-fou _collect_forbidden_ifaces, fallback de mot de passe (_open_transport essaie LR_FALLBACK_SSH_PASSWORDS sur AuthenticationException, retourne le mdp utilisé → promu sur le LR)
│   ├── client_block_service.py     # Blocage client 2 modes (full / whatsapp_only) + enforcement
│   ├── alert_engine.py             # Orchestrateur : évalue règles, gère AlertState, ouvre/résout incidents
│   ├── alert_rules.py              # Règles d'alerte pure Python (sans DB) — 10+ règles
│   ├── alert_formatter.py          # Formatage messages WhatsApp/log par type d'alerte. `_DESCRIPTION_ALERT_TYPES` = les types dont la **description** est rendue en ligne supplémentaire, parce qu'elle porte le seul contenu actionnable : batteries UISP Power (charge % + autonomie) et **ports de switch** (quels ports, et quel équipement au bout — les champs structurés ne nomment que le switch, inutile sur une unité 24 ports)
│   ├── alert_policy.py             # Registre interne : politique (canal/groupable/recovery/immédiat) par alert_type — plus exposé en API
│   ├── digest_service.py           # Regroupement des warnings en digest 15 min
│   ├── lr_metric_history_service.py # Historique des courbes de la fiche (table `lr_metric_samples`). **`GRAPH_METRICS`** = l'allowlist des métriques traçables (latence, `total_capacity_mbps`, `link_potential_pct`, `dl_capacity_mbps`, `ul_capacity_mbps`, `dl_throughput_mbps`, `ul_throughput_mbps`) avec label/unité/seuil — **ajouter une clé ici suffit à rendre une métrique traçable** (pas de migration, pas de table). Le seuil peut être une chaîne (seuil unique) ou un **dict par famille radio** (`link_potential_pct` : 50 % LTU / 40 % airMAX) résolu par `threshold_setting_for(spec, device)`, qui réutilise `alert_rules._AIRMAX_LR_VARIANTS` — **importé, jamais recopié** : la ligne tracée doit être celle qui déclenche l'alerte. **ÉCRITURE** : `record_sample` est appelé depuis `persist_device_metrics` (le chokepoint de TOUS les polls → couvre sonde SSH, airOS, fan-out LTU, wstalist M5 d'un coup) et replie la valeur dans un **bucket** (60 s par défaut) par upsert (moyenne glissante recalculée EN SQL + `least`/`greatest` sur min/max). **LECTURE** : `get_history` sert 24h à la résolution native et re-binne les fenêtres larges via `date_bin` (moyenne **pondérée par `sample_count`**). `available_metrics` = les courbes que CE device possède (onglets de la modale). **Trous NON comblés** : un bucket sans relevé est absent, jamais ramené à 0
│   ├── uisp_service.py             # Client REST contrôleur UISP/UNMS (login → token, GET /devices) — read-only
│   ├── uisp_sync_service.py        # Import auto depuis UISP : INFRA (classify→upsert name/IP/site, creds par convention à la CRÉATION, **jamais de delete**) + STATIONS clientes via sync_uisp_stations (LR abonnés dans `lrs`, colonnes uisp_* mode/statut, identité MAC, gated UISP_STATION_SYNC_ENABLED). ⚠️ **Le sync des STATIONS SUPPRIME** pour rester synchro avec UISP : un LR **issu de UISP** (`uisp_synced_at` renseigné) dont la MAC a disparu du roster = déprovisionné dans UISP → `session.delete` (cascade). Un client **découvert par radio seul** (`uisp_synced_at` NULL) n'est JAMAIS supprimé (propriété de discovery_service). **Garde-fou** : pass de suppression sautée si le roster revient VIDE (échec de fetch — ne jamais prendre un payload vide pour « tout le monde déprovisionné »)
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
| `MANAGEMENT_IP_CIDRS` | **Allowlist du plan d'adressage de management** (CSV de CIDR, défaut `10.135.0.0/16`). La découverte n'écrit `devices.ip_address` que si l'IP annoncée y appartient. Une radio annonce AUSSI son LAN (`192.168.10.1`, `192.168.1.20`, `172.16.0.1` = valeurs d'usine airOS), une APIPA `169.254.x` ou `0.0.0.0` : ces adresses sont les **mêmes sur des dizaines de CPE** alors que `ip_address` est **UNIQUE** → chaque écriture **volait** la ligne du détenteur précédent (`_release_ip_if_held` le laisse sans IP et en `status="unknown"`, donc hors du sweep de ping). Un CPE éteignait ainsi un **autre** client sain. Vide = filtre désactivé. `discovery_service.is_management_ip` / `pick_management_ip` |
| `OUT_OF_SUPERVISION_DAYS` | Seuil « **hors supervision** » pour un LR (défaut **7** j). Un LR **sans IP** est hors du `_ping_sweep` (qui filtre `ip_address IS NOT NULL`) → plus rien ne mesure son état ; si en plus **UISP** ne l'a pas vu depuis ce délai (`uisp_last_seen` nul = silence), **les deux sources se taisent** : ni panne constatée, ni accès actif. Exclu du décompte « Accès actif » de `/access` + badge ambre **HORS SUPERVISION** (au lieu du rouge « INCONNU », lu à tort comme une panne — 124 lignes sur ~1000 en prod le 2026-07-22). **Aucune suppression** : la découverte les récupère seules dès qu'un AP les rapporte avec une IP du plan. Règle écrite 2× : `schemas/device.is_out_of_supervision` (fiche) et `fn_access_clients` (SQL, migration `cc3d4e5f6a7b`) — les garder d'accord |
| `IP_CLEANUP_ENABLED` / `IP_CLEANUP_INTERVAL_HOURS` / `IP_CLEANUP_RADIO_HOURS` | Nettoyage périodique (défaut **12 h**) des IP de LR que **plus aucune source ne confirme** (`ip_hygiene_service`, job `unverified_ip_cleanup`, groupe **fast**) : IP retirée + `status='unknown'`. ⚠️ C'est un **FILET** — la protection réelle contre une action sur le mauvais abonné est le **contrôle d'identité MAC** avant chaque blocage (voir ci-dessous). Rien n'est supprimé : la découverte rend son IP à la ligne dès qu'un AP la rapporte. Script équivalent à la demande : `scripts/clear_unverified_ips.py` (dry-run par défaut) |
| `UISP_IP_TRUST_HOURS` | Fenêtre de confiance de l'IP annoncée par UISP pour une station qu'il ne voit pas en ligne (défaut **24 h**). Une **fenêtre**, pas un booléen `active` : une panne d'1 h ne périme pas un bail DHCP, 3 semaines si |
| `SWITCH_SNMP_INTERVAL_SECONDS` | Intervalle du poll SNMP des **switches** (défaut 60 s), séparé de `SNMP_INTERVAL_SECONDS` qui ne concerne plus que les radios. Voir `switch_snmp_poll_job` |
| `SNMP_DEFAULT_COMMUNITY` | Community SNMP par défaut (ex: public) |
| `SNMP_PORT` | Port SNMP (défaut 161) |
| `SNMP_TIMEOUT` | Timeout SNMP en secondes |
| `SWITCH_MAX_PORTS` | **Fenêtre de scan SNMP** du switch, pas son nombre de ports : `collect_switch_port_metrics` lit les ifIndex `1..max_ports` et **rien au-delà n'est mesuré** — un port hors fenêtre est invisible même quand il tombe. **Défaut 30** depuis le 2026-07-30 (migration `f7b5396b55ea`, relève tous les switches existants, jamais à la baisse) : couvre les 24 RJ45 **+ les 4 cages SFP+** d'un `UISP-S-Pro` (ifIndex 1..28) avec un peu de marge. Avant, à 16, des ports réels n'étaient pas mesurés (PK1 porte un AF60 sur le port 18 ; le SFP fibre de CT1/ARF1 est à l'index 25 et avait dû être exposé à la main par migration). ⚠️ **Élargir la fenêtre ne crée AUCUNE alerte** : les règles n'évaluent que les ports de `watched_ports` + `rocket_port_index`, jamais « tous les ports scannés ». Aussi **relevé automatiquement** si la détection prouve qu'un équipement supervisé est au-delà (plafond 64, = celui du formulaire) |
| `SWITCH_ROCKET_PORT_INDEX` | Index du port switch connecté au Rocket (0 = désactivé). ⚠️ **Override manuel** : la désignation normale est **auto-détectée** (voir `SWITCH_PORT_MAPPING_ENABLED`). Renseigné automatiquement quand la détection ne trouve qu'**un seul** Rocket derrière le switch ; jamais écrasé s'il a été saisi |
| `SWITCH_PORT_MIN_SPEED_MBPS` | Vitesse minimale attendue sur un port surveillé (défaut 1000 Mbps) |
| `SWITCH_PORT_MAPPING_ENABLED` / `SWITCH_PORT_MAPPING_INTERVAL_MINUTES` | Détection automatique de **quel équipement est câblé sur quel port** (`switch_port_mapping_job`, défaut activé, 60 min). Sans elle, la surveillance des ports ne couvre que `rocket_port_index` — c.-à-d. **rien**, puisque rien ne le renseignait. Voir **Surveillance des ports de switch** |
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
| `TOPOLOGY_SYNC_ENABLED` / `TOPOLOGY_SYNC_HOUR` | Sync quotidien du **câblage** inter-sites (data-links UISP → table `site_links`), défaut `true` / `7` (= **07:30 UTC**, à `:30` pour ne pas se superposer au `uisp_sync` de la même heure). Tourne aussi **1× au démarrage** (sinon la page reste vide jusqu'au lendemain après un premier déploiement). ⚠️ Ne synchronise **que le câblage** : la santé des liaisons reste lue en direct. Groupe scheduler **heavy** |
| `TOPOLOGY_TRAFFIC_MIN_MBPS` | Débit (descendant + montant) au-dessus duquel une liaison inter-sites est jugée **écoulante** → verte sur `/topology` ; en dessous elle est debout mais **inerte** → jaune (défaut **0,1** Mb/s). Il ne s'agit pas de juger la charge mais de distinguer « ça passe » de « ça ne passe pas ». ⚠️ Une liaison **sans relevé** de débit (les liaisons fibre : un switch n'expose aucun débit en SNMP) reste **verte** — « pas mesuré » n'est pas « pas de trafic » |
| `TOPOLOGY_ROOT_SITE` | Site racine du graphe inter-sites de `/topology` (défaut `A2 HQ`). **Ne se déduit pas** : le lien Internet→HQ n'est pas un data-link, le contrôleur ignore quel site fait face à l'amont. Site absent du graphe ⇒ repli sur le site de plus haut degré, **annoncé** dans `root_source` (un repli silencieux se lirait comme une déduction) |
| `UISP_IGNORED_SITES` | Sites UISP à exclure du sync (ni créés ni màj). **Séparateur `;`** (les noms de sites contiennent des virgules, ex. `Bureau, A2`), insensible à la casse. Pour les sites bureautiques dont un switch LAN serait vu comme infra |
| `UISP_STATION_SYNC_ENABLED` | Active l'import des **stations clientes** (LR abonnés) depuis `GET /nms/api/v2.1/devices?role=station` dans la table `lrs`, sur le même `uisp_sync_job` (après l'infra). Apporte le **mode (routeur/bridge)** + le **statut « dernier état connu »** UISP de chaque client → `/access` reste complet/exact même quand un Rocket/LR est down. Écrit les colonnes `uisp_*`, **jamais** `topology_mode` ni l'état de blocage. ⚠️ **`rocket_id`/`location`/`ip_address` sont PARTAGÉS** avec `discovery_service` depuis le 2026-07-22, sous une règle d'arbitrage unique : **la source qui a vu la station le plus récemment gagne** (`_adopt_uisp_attribution`, compare `uisp_last_seen` à `last_discovered_at`). Raison : le rattachement radio lit la liste des stations d'un AP, donc ne corrige QUE les clients **allumés** — un client qui déménage puis tombe restait figé sur son ancien AP, son ancien site et son ancienne IP (morte → « hors ligne » pour toujours), alors que son propre `uisp_ap_name` portait déjà la bonne réponse. ⚠️ **L'AP se reprend, l'IP presque jamais** : pour une station **déconnectée**, l'IP annoncée par UISP n'est qu'un **dernier état connu** que le DHCP a pu réattribuer (au 1er passage réel, UISP a rendu `10.135.3.159` pour **trois** abonnés déconnectés). L'IP n'est donc reprise que si UISP voit la station **active** **ou** l'a vue depuis moins de **`UISP_IP_TRUST_HOURS`** (défaut 24 h — une **fenêtre**, pas un booléen : une panne d'1 h ne périme pas un bail DHCP, 3 semaines si), qu'elle est dans `MANAGEMENT_IP_CIDRS`, et qu'elle est **libre** — jamais volée à un autre détenteur (ni en base, ni à une station déjà servie dans le même passage : `claimed_ips`). Un conflit incrémente `ip_conflict` et laisse les deux lignes intactes : seul le radio voit le terrain. Identité = **MAC** (converge avec la découverte radio). AF60 (backhaul) exclus. Importe le **roster complet** (UISP ne retourne que les stations provisionnées). ⚠️ **SUPPRESSION pour rester synchro** : à la fin du passage, tout LR **déjà vu par UISP** (`uisp_synced_at` renseigné — colonne écrite nulle part ailleurs) dont la MAC n'est plus dans le roster est **déprovisionné dans UISP** → **`session.delete`** (cascade métriques/incidents/historique ; le journal FAI est un fichier par MAC, préservé). Supprimé **même si `client_blocked`** (déprovisionné = plus servi). Un client **découvert par radio seul** (`uisp_synced_at` NULL) n'est **jamais** supprimé (propriété de `discovery_service` — l'effacer déclencherait une recréation en boucle). **Garde-fou anti-catastrophe** : la passe de suppression est **entièrement sautée si le roster revient vide** (`fetch_devices` lève sur erreur transport, mais un payload vide/malformé serait sinon lu comme « tout le monde déprovisionné » et purgerait tout le parc). Défaut `false` |
| `UISP_WRITE_API_TOKEN` | Token UISP **séparé, en écriture**, réservé à l'association équipement ↔ client CRM (`POST /uisp/assign`). Tout le reste — dont le `uisp_sync_job`, qui parcourt ~1300 équipements **sans surveillance** — garde `UISP_API_TOKEN` en **lecture seule** : aucun job de fond ne peut alors modifier le contrôleur, quoi qu'il arrive, et le token d'écriture est révocable sans interrompre la supervision. Vide = repli sur `UISP_API_TOKEN` |
| `UISP_DEVICE_KEY` | **Clé d'enrôlement du contrôleur** (UISP → Paramètres → Équipements), forme `wss://<hôte>:443+<jeton>+<option TLS>`. **UNE seule valeur pour tout le parc** — c'est un identifiant du contrôleur, pas de l'équipement. Posée sur un CPE par `ssh_service.set_uisp_key` pour le faire apparaître dans l'inventaire (cf. **Enrôlement UISP** ci-dessous). Vide = enrôlement indisponible (l'API répond 409 au lieu d'écrire une config vide) |
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
| `CLIENT_CONSUMPTION_REFRESH_HOUR` | Heure UTC du recalcul **quotidien** des matviews de consommation (défaut `3` ; le 7 j suit à +1 h). ⚠️ **Ne pas repasser en intervalle court** : ce REFRESH relit `device_metrics` (6,8 Go / 20 M lignes) et prend > 19 min — planifié toutes les 15 min il tournait EN PERMANENCE et saturait l'E/S, ce qui faisait ramper la phase 2 de la sonde LR (~40 min/tour) et rendait `ltu_api_poll` à 0/60 Rockets (incident 2026-07-20) |
| `TRAFFIC_STATS_RETENTION_DAYS` | Rétention batchée de `traffic_dest_stats` (défaut 90 ; `traffic_stats_retention_job`) |
| `LR_METRIC_HISTORY_BUCKET_SECONDS` | Largeur d'un bucket de l'historique des courbes (défaut **60** s = un point par relevé de poll, la résolution max que les données permettent). 300 divise le volume par ~3. **Le bucket n'est PAS le facteur limitant pour la latence** : sa sonde tourne toutes les 3 min (`LR_LATENCY_INTERVAL=180` en prod) et un tour dure 100-480 s → un client produit une mesure toutes les 3-8 min quoi qu'on règle ici. Changer la valeur ne réécrit pas les lignes existantes |
| `LR_METRIC_HISTORY_RETENTION_DAYS` | Rétention batchée de `lr_metric_samples` (historique des courbes de la fiche, défaut **30** j ; `lr_latency_retention_job`). Coût ∝ au nombre de métriques ET à la largeur de bucket : à 60 s, ~800 LR × 1440 buckets/j × N métriques ≈ **110 M lignes** à 30 j — surveiller l'autovacuum |
| `LR_METRIC_HISTORY_RETENTION_INTERVAL_MINUTES` | Intervalle du `lr_latency_retention_job` (défaut 360 = 6 h) |

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
- [x] **Détection port switch** — port DOWN ou vitesse < 1000 Mbps, sur **tous** les ports portant un équipement supervisé. ⚠️ **Ces deux règles n'avaient jamais pu se déclencher** avant le 2026-07-30 : elles étaient gardées sur `rocket_port_index`, colonne qu'aucun code ne renseignait (NULL partout). Le port de chaque équipement est désormais **auto-détecté** depuis la table d'apprentissage MAC du switch (`switch_port_service` + `switch_port_mapping_job`). Voir **Surveillance des ports de switch**
- [x] **Digest warnings** — `digest_service.py` + `warning_digest_job` (regroupement 15 min)
- [x] **Auto-découverte LTU LR** — le job LTU API lit les CPE peers du Rocket et établit la hiérarchie parent/enfant automatiquement
- [x] **Authentification API** — API key via header `X-API-Key` (`app/api/deps.py`)
- [x] **Notifications — WhatsApp (Ultramsg) remplace l'email** — depuis le 2026-06-11 le canal résolu depuis l'env est **WhatsApp** (`WHATSAPP_ENABLED` + `WHATSAPP_INSTANCE_ID` + `WHATSAPP_TOKEN` + `WHATSAPP_GROUP_ID`) : tout le pipeline d'incidents (immédiat + digest + sécurité) part vers le **groupe WhatsApp** via `whatsapp_service` (`POST /{instance}/messages/chat` Ultramsg). **L'envoi d'email a été entièrement retiré du projet (2026-06-16)** : `email_service`, l'endpoint `/system/test-email`, le job d'instabilité ping (`ping_instability` + son email), la config SMTP et la dépendance `aiosmtplib` sont supprimés. Diagnostic restant : `POST /api/v1/system/test-whatsapp`. Le registre `alert_policy.py` reste interne ; ses jeux de canaux pointent tous sur `AlertChannel.WHATSAPP`. **Restriction (2026-06-11)** : WhatsApp ne pousse QUE les alert_types de la liste blanche `WHATSAPP_ALERT_TYPES` (`alert_constants`) : `switch_port_speed_low`+`switch_port_down`, `device_flapping`, **`battery_internal_low` (Li-Ion UPS < 50%) + `battery_external_low` (banc plomb < 30%)**, `af60_link_substandard`+`af60_link_down` (lien P2P dégradé = capacité < **1.95 Gb/s**, cf. `af60_total_capacity_min_mbps`), **équipement injoignable** `rocket_down`+`switch_down`+`device_unreachable`+`airmax_down` (un UISP Power down est couvert par `device_unreachable` ; `uisp_power_unreachable` plus émis, pour éviter le doublon), plus la latence réseau (envoi direct du `network_latency_aggregate_job`). **UISP Power notifie = 2 alertes batterie + down** (voltage / ancienne alerte batterie unique retirés ; **coupure secteur `mains_power_lost` conservée et affichée dans /incidents mais NON notifiée**). Toute autre anomalie (qualité radio, voltage, coupure secteur, **sécurité**, découverte LR…) ouvre/résout son incident en DB mais **n'est notifiée nulle part**. Le chokepoint est `notification_service._dispatch` (+ `notify_security_event` + collecte du digest). (Historique 2026-06-09 : avant WhatsApp, l'email était env-only `SMTP_ENABLED`+`NOTIFICATION_EMAILS` ; `notification_channels`/`/notification-channels` et `/alert-policies` supprimés.)
- [x] **Formatage des alertes** — `alert_formatter.py` (messages WhatsApp contextualisés par type)
- [x] **API incidents** — `GET/PATCH /api/v1/incidents` (filtres status/severity/device_id/alert_type)
- [x] **Résolution = suppression** — pas d'archive : à la résolution, `incident_service.resolve_incidents` **hard-delete** l'incident. **Exception** : les types de **disponibilité** (`AVAILABILITY_ALERT_TYPES` dans `alert_constants` = `rocket_down`, `switch_down`, `device_unreachable`, `uisp_power_unreachable`, `airmax_down`) sont conservés en `status=resolved` car le **Journal des coupures** (`network_uptime_service`) reconstruit l'historique + la dispo % depuis leur `resolved_at`. La notification de résolution part quand même pour les incidents purgés (objets encore en mémoire). La page `/incidents/archive` et le lien sidebar ont été supprimés.
- [x] **Pas d'audit trail des notifications** — la table `alerts` et la page `/notifications` ont été **supprimées** (2026-06-09, migration `a8b9c0d1e2f3`). Les notifications sont toujours **envoyées** mais aucune ligne d'audit n'est persistée.
- [x] **Contrôle d'identité avant blocage** (2026-07-22) — une fiche cible une **MAC**, mais la session SSH part sur une **IP** que le DHCP a pu redonner à un autre abonné. `ssh_service.identity_refusal` lit les MAC de toutes les interfaces de l'équipement joint (une commande sur la session **déjà ouverte** — le coût sur ces radios, c'est la poignée de main, pas la lecture) et **refuse d'agir** si la MAC attendue n'y est pas. Câblé sur les **trois** chemins (`set_lan_interface`, `set_whatsapp_only`, `set_content_block`). ⚠️ **Invérifiable = autorisé** : un firmware sans `/sys/class/net` rendrait sinon tout blocage impossible sur cette famille — on ne refuse que sur preuve positive. Le refus est **structurel** (le job d'enforcement sort la ligne de sa boucle, `block_unenforceable_reason` posé) et journalisé sous l'action dédiée **`IDENT_KO`** (« Identité refusée », violet sur `/fai-journal`) — distincte d'`ABANDON` : ici il n'y a rien à réparer sur l'équipement, c'est la fiche qui est périmée.
- [x] **Blocage internet client (2 modes)** — SSH sur le LR. Mode `full` : shutdown du port LAN (`lan_interface`). Mode `whatsapp_only` : **3 couches** sur le LR pour vraiment séparer WhatsApp de FB/IG (qui partagent les IP Meta) : (1) DNAT en `iptables -t nat PREROUTING` redirigeant tout DNS du sous-réseau client vers le dnsmasq du LR (anti-bypass `8.8.8.8`), (2) entrées `address=/<domaine>/0.0.0.0` ajoutées à `/etc/dnsmasq.conf` pour FB/IG/Messenger/Threads (résolus en `0.0.0.0` → connexion immédiate impossible), (3) chaîne `CLIENTBLOCK` sur `FORWARD` autorisant DNS + plages Meta (`WHATSAPP_ALLOW_CIDRS`), `DROP` le reste. **Quirk terrain (airOS 8) : `kill -HUP dnsmasq` n'applique pas les `address=` — il faut `killall dnsmasq` (airOS le respawn).** Mode persisté (`block_mode`) + `client_blocked` en DB + job `client_block_enforcement_job` qui ré-applique le mode actif toutes les 120 s (survit au reboot du LR — airOS régénère `/etc/dnsmasq.conf` au boot, l'enforcement remet le bloc dans la minute). **Garde-fou dynamique du mode `full`** : avant un shutdown, `ssh_service._collect_forbidden_ifaces` calcule en direct sur le LR les interfaces du chemin SSH/route par défaut (+ membres de bridge, parents VLAN) et refuse de les couper. **Défaut `lan_interface` par famille** : `client_block_service.default_lan_interface(model_variant)` → `eth0.1` (LTU) / `eth0` (airMAX), appliqué à la création par `discovery_service` et backfillé par la migration `m4e5f6a7b8c9`. Remplace l'ancien `is_suspended` (flag no-op supprimé)
- [x] **Dashboard frontend** — Next.js avec pages : devices, incidents, etc.

### Jobs planifiés actifs
| Job | Intervalle | Rôle |
|---|---|---|
| `heartbeat_job` | 60s | Sanity check scheduler |
| `infra_ping_job` | `ping_interval_seconds` (30s) | Ping ICMP de l'**INFRA** (Rockets base / switches / UISP Power / AF60) — groupe scheduler **fast**. Le SEUL des deux à ouvrir/résoudre les incidents `*_down`. Params fping fiables (`ping_infra_retries`=2, `ping_infra_timeout_ms`=1200) sur un petit lot. Voir **Fiabilité du ping** ci-dessous. |
| `client_ping_job` | `client_ping_interval_seconds` (60s) | Ping ICMP des **LR clients** — **container dédié `scheduler-ping-lr`** (groupe `ping-lr`), isolé de `fast`. Un LR down = panne côté abonné → **aucun incident infra**, seul le statut bascule (+ purge de ses incidents ouverts, devenus du bruit). Sondé moins souvent que l'infra. |

#### ⚠️ Interblocages `device_metrics` — ordre de verrouillage (2026-08-11)

**103 `deadlock detected` en 24 h** relevés en prod. Le mécanisme : le sweep de
ping mute `status`/`last_seen` de ~200 équipements **par transaction** (commit par
paquets), pendant que les jobs de poll écrivent les mêmes lignes `devices`. Deux
transactions qui prennent ces verrous dans un **ordre différent** forment un
cycle, et Postgres en tue une.

Or aucun des deux côtés n'avait d'ordre stable : le `select(Device)` du sweep
n'avait **aucun `order_by`**, et les phases 2 des polls itéraient `fetched`, dont
l'ordre suit l'**achèvement des requêtes réseau** — donc change à chaque tour.

**Règle à tenir : toute boucle SÉQUENTIELLE qui écrit des lignes `devices` les
parcourt par `id` croissant.** C'est la discipline classique d'ordre de
verrouillage : si tout le monde prend dans le même sens, aucun cycle ne peut se
former. Appliqué au sweep de ping (`order_by(Device.id)`) et aux 5 phases 2
(`sorted(...)`), verrouillé par
`tests/test_snmp_poll_device_isolation.py::test_every_write_loop_takes_its_locks_in_the_same_order`.

⚠️ **Exception : un persist CONCURRENT** (`asyncio.gather` du job airOS) n'est pas
concerné — trier un générateur ne sérialise rien. Sa protection est structurelle :
UNE session par équipement, donc une transaction qui ne tient jamais qu'une ligne
et ne peut pas former de cycle seule.

⚠️ Ça **réduit** sans éliminer : un interblocage reste possible (une transaction
de poll couvre un Rocket ET ses LR, dont les id ne sont pas contigus). L'isolation
par équipement de la phase 2 reste donc indispensable — elle transforme un
conflit en une ligne de log au lieu d'une perte de cycle.

#### Fiabilité du ping (les deux sweeps) — `_ping_sweep` dans `jobs.py`
Le sweep se fait en **un seul process `fping`** (`poller.ping_hosts_bulk` → `{ip: reachable}`) plutôt qu'un sous-process `ping` par device : à 600+ devices, le `gather` de N `ping` spawnait des centaines de process qui se starvaient → faux « down » de masse + cycle qui débordait. Coût **plat** quelle que soit la taille du parc. Requiert `fping` dans l'image (Dockerfile) ; fallback `ping` par hôte borné s'il manque.

⚠️ **Le fping groupé n'est qu'un PRÉ-FILTRE : il ne décide JAMAIS seul qu'un device est down.** Le CPU de management des radios Ubiquiti rate-limite l'ICMP, et ce rate-limit frappe **tout le burst d'un coup** → les sondes d'un même hôte partent dans la même rafale et se font jeter ensemble, à chaque cycle. Un LR sain, joignable en HTTPS, restait ainsi « HORS LIGNE » (constaté 2026-07-17). Donc tout hôte que le fping déclare injoignable est **re-pingé ISOLÉMENT** hors burst (`_reconfirm_unreachable`, `ping -c 2 -W 2`) avant d'être compté KO : une radio saine répond du premier coup. Coût **nul** quand tout répond. Le log `X/Y suspect(s) répondent au ping isolé` mesure les faux down rattrapés.

Réglages **séparés par famille**, aucun budget partagé (`ping_infra_reconfirm_*` / `ping_client_reconfirm_*`) : régler les LR ne peut pas dégrader la mesure de l'infra.

⚠️ **Un device sans IP sort du sweep** (`ip_address IS NOT NULL` dans la requête) — donc **plus rien n'écrit son `status`**, qui reste figé sur sa dernière valeur. Un LR dont l'IP est libérée au churn DHCP (`discovery_service._release_ip_if_held`) alors qu'il était `up` s'affichait ainsi « EN LIGNE » **indéfiniment**, avec une `last_seen` qui vieillissait (constaté 2026-07-21, dernière vue à 17 h). Corrigé : nuller l'IP remet le `status` à **`unknown`** et purge son compteur `_ping_failures` ; l'UI rend `unknown` en **rouge** (badge INCONNU) et non plus en bleu neutre. Les lignes déjà figées sont rattrapées par la migration `aa1b2c3d4e5f`. **Règle générale : tout chemin qui retire un device du sweep doit écrire son statut, sinon il ment pour toujours.**

**Statut `down` seulement au seuil anti-flap** (`ping_down_threshold`=3), jamais sur un seul paquet perdu — sinon un Rocket qui route le trafic + répond à son API s'affichait « HORS LIGNE » et sortait des polls API/SNMP (qui filtrent `status=up`). L'incident `*_down` suit le même seuil.
| `snmp_poll_job` | 60s | Métriques SNMP des **RADIOS** (Rockets LTU/airMAX, ath0/eth0) → alert engine. ⚠️ **Les switches ont leur PROPRE job** depuis le 2026-08-11 (`switch_snmp_poll_job`) — voir ci-dessous. Les deux appellent le même corps `_run_snmp_poll(device_types, label)` ; seule la sélection change. **Concurrent** : Phase 1 fetch SNMP (+ découverte airMAX) de tous les rockets/switches en parallèle (sémaphore `snmp_concurrency=30`), Phase 2 persist/alert/ports en série DB. Avant : série → un tour dépassait 60 s, aggravé par les timeouts des airMAX SNMP-off qui s'additionnaient. Persistance via `persist_device_metrics` (cf. **Politique device_metrics** ci-dessous) : seules les métriques de `HISTORY_METRICS` sont empilées, le reste (tout le switch, noise, rates…) est écrasé en place (1 ligne/`(device_id, metric_name)`). Au 1er cycle après bascule d'une métrique en collapse, le DELETE absorbe son backlog historique, dans le scheduler — surtout PAS dans une migration de démarrage (un bulk delete bloquait le healthcheck backend, cf. no-op `u2a3b4c5d6e7`). |
| `power_poll_job` | 30s | API REST UISP Power (voltage, batterie) |
| `ltu_api_poll_job` | 60s | API HTTP LTU Rocket (signal, CCQ, CINR, CPE auto-discovery) → alert engine + check topologie via `peer.remote.netMode` (router/bridge) par LR, sans SSH. **Concurrent** : Phase 1 fetch tous les Rockets en parallèle (sémaphore `_LTU_POLL_CONCURRENCY=10` + deadline global `_LTU_POLL_DEADLINE_S=40s`), Phase 2 persist/découverte/alerting en série DB. Avant : série → un tour dépassait 60 s → cycles sautés (`max instances reached`) → découverte en retard de plusieurs min. |
| `airos_api_poll_job` | 60s | **Poll par l'AP pour les M5 SEULEMENT** (2026-07-21) : interroge chaque **Rocket airMAX** (`status.cgi`), dont `wireless.sta[]` rend tous ses abonnés d'un coup, et n'en retient que les **LiteBeam M5** (`parse_airos_ap_stations` → fan-out par **MAC**). Le M5 y gagne ce qu'il ne sait pas produire : une **capacité en Mb/s** (il n'expose qu'un taux PHY), un potentiel, sa distance, son netrole et son hostname. ⚠️ **Les 5AC gardent leur poll HTTP direct** : leur **consommation** vient des compteurs du CPE (`sta[0].stats`) depuis toujours, et le compteur que l'AP tient pour une station est un cumul d'une **autre origine** (**55,46 Gio vu par l'AP contre 2,03 Gio vu par le CPE**, même client même instant) — y basculer ferait **facturer l'écart**. Comme le poll direct doit tourner pour les compteurs, il fournit déjà tout le reste. ⚠️ **Étiquettes** : `dl_*`/`ul_*` sont **absolues** (identiques des deux côtés) mais `rx`/`tx` sont **relatives à qui répond** et sont **croisées** côté AP. ⚠️ L'AP n'émet **ni compteurs** (conso) **ni CINR pour une station airOS 6** (il annonce 3 dB quand le SNR réel est de 25 → tous les M5 sous le seuil critique) ; leur CINR/CCQ vient du SSH `wstalist`, leur débit est **dérivé** de leurs compteurs. ⚠️ **Deux portées à ne pas confondre** (2026-07-22) : le fan-out des **métriques** reste M5-only, mais la **réconciliation d'identité** (`reconcile_peers` sur `mac` + `remote.ipaddr` + `hostname` + `platform`) s'applique à **TOUTES** les stations de l'AP, 5AC comprises. C'est le seul chemin qui suive un abonné qui **roame** vers un autre AP : le poll direct le vise à son **ancienne IP** (morte) et ne tourne que sur les LR `status="up"` → une fois `down` il ne repasse **jamais** up, et `discover_airmax_peers` (SNMP, souvent éteint sur ces radios) est le seul autre à savoir re-rattacher. Le sync UISP voit le bon AP (`uisp_ap_name`) mais n'écrit **que** les colonnes `uisp_*`. Sans cette réconciliation un 5AC roamé restait affiché **hors ligne sur son ancien site avec son ancienne IP**, indéfiniment |
| `lr_internet_probe_job` | 60s | SSH sur **chaque LR** avec credentials → `ping -c 5` vers `LR_LATENCY_TARGET` (8.8.8.8). Détecte à la fois la perte de transit (`lr_no_transit` après 2 cycles KO) et la dégradation de latence (`lr_latency_high` si avg ≥ 100 ms sur 3 cycles). Le RTT part dans `persist_device_metrics`, qui l'écrit **deux fois** : en `device_metrics.lr_latency_ms` (collapse = dernière valeur, lu par `/lr-health`) **et**, `lr_latency_ms` étant dans `GRAPH_METRICS`, dans `lr_metric_samples` (bucket 5 min = la SÉRIE du graphe client). Pas de transit → **rien** n'est écrit dans l'historique et la métrique collapse est purgée → le graphe montre un trou, pas un 0. **Effet de bord (2026-07-23)** : comme la session SSH a déjà lieu, il **persiste l'issue SSH** dans `lrs.ssh_status`/`ssh_error`/`ssh_checked_at` via `ssh_service.classify_probe_ssh_status(ssh_ok, used_pw, msg)` (`ok`/`auth_failed`/`ssh_disabled`/`host_key_mismatch`/`unreachable`) — `used_pw` non nul = auth OK même si exec timeout (pas un refus). Alimente la page « Diagnostics d'accès » |
| `warning_digest_job` | 15 min | Regroupe les warnings en un seul message pour éviter la fatigue d'alerte |
| `client_block_enforcement_job` | 120s | Ré-applique le blocage actif (port LAN ou filtre WhatsApp, selon `block_mode`) sur chaque LR `client_blocked` (survit au reboot du LR) |
| `client_consumption_matview_refresh_job` | **Cron quotidien `CLIENT_CONSUMPTION_REFRESH_HOUR`:00 UTC** (défaut 03:00) | `REFRESH MATERIALIZED VIEW CONCURRENTLY client_consumption_30d` — pré-calcule la somme des deltas de compteurs bytes sur 30 j (download/upload par CPE). Avant : l'endpoint `/clients/consumption?period=30d` transférait des millions de samples vers Python pour faire la boucle `_sum_positive_deltas` → ~36 s en prod. Maintenant : delta calculé en SQL via `LAG()` + `CASE`, et 30d servi depuis la vue. |
| `client_consumption_7d_refresh_job` | **Cron quotidien à `CLIENT_CONSUMPTION_REFRESH_HOUR`+1:00 UTC** (défaut 04:00 — décalé pour ne pas se disputer le disque avec le 30 j) | `REFRESH MATERIALIZED VIEW CONCURRENTLY client_consumption_7d` — même pattern que le matview 30 j mais borné à 7 j. La période 7 j à elle seule clockait ~13 s sur le live SQL (seq scan + external sort 30 MB) ; le matview la fait passer à <100 ms. Matview séparé car l'agrégat 30 j est un seul SUM qui ne peut pas être soustrait à une fenêtre plus étroite. 24h reste en SQL live (true rolling window, ~2 s acceptable) ; la **plage de dates personnalisée** de `/clients` tourne aussi en SQL live borné (`collected_at ∈ [start, end)`, pas de matview possible pour une fenêtre arbitraire). |
| `unverified_ip_cleanup_job` | `IP_CLEANUP_INTERVAL_HOURS` (12 h) | Retire l'IP des LR que plus aucune source ne confirme (ni UISP actif/récent, ni radio récent, ou IP hors plan) → `status='unknown'`. Groupe **fast**. `ip_hygiene_service.run_cleanup` |
| `traffic_stats_retention_job` | `TRAFFIC_STATS_RETENTION_INTERVAL_MINUTES` (6 h) | Purge `traffic_dest_stats` plus vieux que `TRAFFIC_STATS_RETENTION_DAYS` (90 j) en **batches** (`DELETE … WHERE id IN (SELECT id … LIMIT n)`, jamais une grosse transaction). Groupe scheduler **fast**. La collecte elle-même tourne dans le container **`netflow-collector`** (hors APScheduler). **NB : il n'y a plus de rétention sur `device_metrics`** — les compteurs bytes de conso sont conservés indéfiniment (plage de dates `/clients` sans limite ; surveiller disque/autovacuum). |
| `lr_latency_retention_job` | `LR_METRIC_HISTORY_RETENTION_INTERVAL_MINUTES` (6 h) | Purge `lr_metric_samples` plus vieux que `LR_METRIC_HISTORY_RETENTION_DAYS` (30 j) en **batches** (`DELETE … WHERE id IN (SELECT id … LIMIT n)`, jamais une grosse transaction). Groupe scheduler **fast**. Même forme que `traffic_stats_retention_job` |
| `site_topology_sync_job` | **Cron quotidien `TOPOLOGY_SYNC_HOUR`:30 UTC** (défaut 07:30) + **1× au démarrage** | Rapatrie le **câblage inter-sites** (`GET /data-links` + `/devices` + `/sites`) dans la table **`site_links`** → alimente `/topology`. Avant ce job, la page interrogeait le contrôleur **à chaque affichage** (~1300 équipements + ~1400 sites + ~1300 liens) et son `refreshInterval` le rejouait **toutes les 2 min**. Ne touche **pas** à la santé des liaisons (lue en direct). Remplacement intégral de la table, **sauté si aucune liaison n'est résolue** (anti-purge). Gated `TOPOLOGY_SYNC_ENABLED`. Groupe **heavy**. Voir **Topologie inter-sites** |
| `switch_snmp_poll_job` | `SWITCH_SNMP_INTERVAL_SECONDS` (60 s) | **Métriques SNMP des SWITCHES** (état/vitesse de chaque port, compteurs, lien fibre) → règles `switch_port_down`, `switch_port_speed_low`, `fiber_link_down`. Groupe scheduler **`poll-switch`**, dans son **conteneur dédié** `scheduler-poll-switch`. ⚠️ **Séparé pour une raison vécue** : les switches partageaient le tour des ~100 radios et, leur collecte étant la plus lente (28 ports), ils étaient traités **EN DERNIER** — tout incident amont les privait de leur écriture. Constaté le 2026-08-11 : **14 h sans une seule métrique de switch**, donc ces trois alertes aveugles, pendant que le job « tournait » normalement pour les radios. Le parc n'en compte qu'une quinzaine : un tour dure quelques secondes. |
| `switch_port_mapping_job` | `SWITCH_PORT_MAPPING_INTERVAL_MINUTES` (60 min) + **1× au démarrage** | Détecte **quel équipement supervisé est câblé sur quel port** et écrit `devices.uplink_switch_id/_port` — la source de vérité de « quels ports surveiller » pour `snmp_poll_job`. **2 sources dans l'ordre** : (1) les **data-links du contrôleur UISP** (`portN`/`0/N`, la seule qui fonctionne sur notre matériel — 1 appel API pour tout le parc, index **vérifié** contre l'`ifDescr` du switch), (2) la **FDB BRIDGE-MIB** en fallback, **uniquement** sur les switches qu'UISP ne couvre pas. Contrôleur injoignable = WARNING, la passe FDB et les attributions en base survivent. Groupe **heavy**. Voir **Surveillance des ports de switch** |
| `uisp_sync_job` | **Cron quotidien `UISP_SYNC_HOUR`:00 UTC** (défaut 07:00 ; Mauritanie GMT → 07:00 locale) + **1× au démarrage** (`next_run_time=now` → import dès le déploiement) | **Désactivé par défaut** (`UISP_SYNC_ENABLED=false`). Importe les équipements d'**infra** (Rocket LTU/airMAX role=ap, switches `uisps`/blackBox, UISP Power `uispp`, AF60* P2P) depuis `GET /nms/api/v2.1/devices` du contrôleur UISP. Mapping `classify_device(type, role, model)` ; identité = **MAC** (sinon IP, sinon (type,nom)). Met à jour **name/IP/site(location)** ; pose les **creds par convention famille/site à la création** (jamais d'écrasement). **Abonnés (LTU-LR/LiteBeam station)** : ignorés par l'import **infra**, mais importés dans `lrs` par `sync_uisp_stations` (après l'infra) si `UISP_STATION_SYNC_ENABLED` — apporte le mode routeur/bridge + statut UISP (colonnes `uisp_*` seules, identité MAC, AF60 exclus, **roster complet**) pour que `/access` reste complet même Rocket/LR down. **Infra : aucun delete.** **Stations : suppression des LR issus de UISP (`uisp_synced_at` renseigné) absents du roster** (déprovisionnés dans UISP), même bloqués ; jamais les clients radio-seuls ; passe sautée si roster vide. Voir `uisp_sync_service`. |
| `flap_detection_job` | `FLAP_CHECK_INTERVAL_MINUTES` (10 min) | Détecte les équipements d'**infra instables** (flapping). Compte par device les **incidents de disponibilité** (`AVAILABILITY_ALERT_TYPES`, conservés en DB après résolution) avec `detected_at` sur les dernières `FLAP_WINDOW_HOURS` ; au-delà de `FLAP_THRESHOLD_24H` (3) → ouvre `device_flapping` (critique → WhatsApp), résout sinon. **UISP Power exclus** (`device_type=="uisp_power"` filtré dans la requête : leurs up/down sur coupure secteur sont normaux). Infra-only par nature (un LR down n'est jamais un incident). |
| `network_latency_aggregate_job` | `NETWORK_LATENCY_CHECK_INTERVAL_MINUTES` (**1440 min = 24 h**) | **Contrôle quotidien** réseau-wide : part des LR `up` dont le dernier `lr_latency_ms` ≥ seuil latence 100 ms (`lr_health_service.network_latency_summary`, réutilise `_fetch_latest_latency`). Si > `NETWORK_HIGH_LATENCY_PCT` (20%) et échantillon ≥ `NETWORK_LATENCY_MIN_SAMPLE` (10) → **message WhatsApp direct** (PAS un incident : un Incident exige un device_id). **Pas de flag/rétabli** : rapport quotidien qui n'envoie que si la condition est remplie. |
| `rocket_saturation_report_job` | **Cron quotidien `ROCKET_SATURATION_REPORT_HOUR`:00 UTC** (défaut 07:00 ; Mauritanie GMT → 07:00 locale) + **1× au démarrage** (`next_run_time=now` → rapport dès le déploiement) | **Rapport PDF quotidien** des **Rockets saturés** envoyé en **document WhatsApp**. Réutilise `network_capacity_service.get_network_capacity` ; un Rocket est saturé quand ses **clients installés ≥ capacité max** (= condition `rocket_client_overload`). `saturation_report_service` génère le PDF (lib `fpdf2`, tableau Site/Rocket/Famille/Clients/Max/Charge/Largeur, trié par charge décroissante), `whatsapp_service.send_whatsapp_document` l'upload en base64 sur Ultramsg `/messages/document`. **Envoi systématique** (même si liste vide = PDF « aucun saturé », caption ✅), contrairement à la latence. Gated `ROCKET_SATURATION_REPORT_ENABLED`. Groupe scheduler **fast** (léger, pas de SSH). Dépend des clients installés → nécessite `UISP_STATION_SYNC_ENABLED`. |
| `site_infra_report_job` | **Cron quotidien `SITE_INFRA_REPORT_HOUR`:00 UTC** (défaut 07:00) + **1× au démarrage** (`next_run_time=now`) | **Rapport PDF quotidien** de la **capacité infra par site** envoyé en **document WhatsApp**. `site_infra_service` compte par `site` (colonne dénormalisée) les équipements d'infra **Rockets + AF60 + PTP LiteBeam** (`INFRA_COUNTED_TYPES` ; **exclut switches, UISP Power, LR clients**) et calcule la marge vs `SITE_INFRA_MAX` (14) : **+N** places libres / **-N** dépassement. PDF via `fpdf2` (tableau Site/Équip./Max/Marge, dépassements en rouge, triés dépassement d'abord). **Envoi systématique** (caption ✅ si aucun dépassement). Gated `SITE_INFRA_REPORT_ENABLED`. Groupe scheduler **fast**. La même donnée est exposée par `network_capacity_service` → `/network-capacity` (clé `infra`) → section « Capacité infra par site » de `/capacity`. |

#### Surveillance des ports de switch — quel équipement sur quel port (2026-07-30)

Les deux règles de port (`switch_port_down`, `switch_port_speed_low`) **n'ont
jamais pu se déclencher sur aucun switch**, depuis le début. Elles étaient
gardées par `if port_idx > 0` avec `port_idx = uisp_switches.rocket_port_index`
— une colonne qu'**aucun code ne renseignait** (ni le sync UISP, ni la
découverte, ni un formulaire) : NULL partout, donc garde toujours fausse, donc
ni le contrôle DOWN ni le contrôle « UP mais sous 1 Gb/s » n'ont jamais été
évalués. Les deux types sont pourtant dans `WHATSAPP_ALERT_TYPES` : le canal
était prêt, la mesure aussi (`port_N_up` / `port_N_speed_mbps` étaient collectés
à chaque cycle SNMP) — seule la **désignation du port** manquait.

**Remplir la colonne à la main n'aurait pas suffi** : un site porte jusqu'à
`SITE_INFRA_MAX` (14) équipements infra derrière un seul switch, et une colonne
ne peut désigner qu'un port. On ne désigne donc plus : **on détecte le câblage**.

##### Comment (`switch_port_service` + `snmp_service.resolve_mac_ports`)

La table d'apprentissage MAC du switch (**BRIDGE-MIB**) dit sur quel port chaque
MAC a été apprise, et on connaît déjà la MAC de chaque équipement supervisé
(c'est son **identité** — cf. découverte / sync UISP). Le croisement donne
`port → équipement` pour tout port qui porte quelque chose qu'on supervise.
Résultat écrit sur `devices.uplink_switch_id` / `uplink_switch_port` /
`uplink_detected_at` (migration `ee5f6a7b8c9d`, FK `ON DELETE SET NULL` —
supprimer un switch ne doit pas cascader sur les Rockets qui y sont branchés).
`uplink_switch_port` est un **ifIndex IF-MIB**, la même numérotation que les
métriques `port_N_*` — sinon la détection désignerait un port que le poll ne
mesure pas.

- **GET ciblés, pas un walk** : la FDB d'un switch de site contient **toutes**
  les MAC clientes bridgées à travers les Rockets (des milliers de lignes, un
  aller-retour GETNEXT chacune). Comme la FDB est indexée **par** la MAC et
  qu'on sait lesquelles nous intéressent, c'est **1 GET par MAC connue**
  (~14 pour un site complet). Un walk **borné** (`_FDB_WALK_MAX_ROWS=2000`) ne
  sert que de repli quand rien ne résout (FDB exposée en Q-BRIDGE sur un VLAN
  qu'on ne devine pas) — il distingue « firmware muet » de « aucune de nos MAC
  derrière ce switch ».
- **Deux dispositions de table**, dans l'ordre : `dot1dTpFdbPort` (indexée par
  les 6 octets de MAC) puis `dot1qTpFdbPort` (indexée `<vlan>.<MAC>`, essayée
  sur **VLAN 1**, le VLAN de management non taggé dans l'immense majorité).
- ⚠️ **`dot1dBasePort` n'est PAS l'ifIndex** : les deux tables rendent un numéro
  de port de bridge, traduit par `dot1dBasePortIfIndex`. Traduction
  indisponible ⇒ on garde le numéro tel quel (identiques sur la plupart des
  switches) ; une erreur ne peut que mal attribuer un port, ce que les
  garde-fous d'ambiguïté ci-dessous écartent.
- **Périmètre = le site du switch** (`Device.site`), équipements avec MAC, types
  `CABLED_DEVICE_TYPES` (Rockets, AF60, PTP LiteBeam, UISP Power, switches).
  **Les LR sont exclus** : ce sont des radios abonnées joignables par les ondes,
  jamais câblées à un switch de site — et il y en a ~1000, ce qui ferait
  exploser le budget de GET. Un switch **sans site** est sauté : périmètre
  indéterminable.

##### La règle à ne pas casser : une attribution ne s'efface jamais sur une absence

Un switch **fait vieillir une MAC hors de sa FDB en quelques minutes** après que
le port est tombé. Donc « la MAC a disparu » et « le lien vient de mourir » sont
**la même observation** — et la seconde est exactement le moment où l'alerte doit
partir. Effacer sur absence rendrait la fonctionnalité aveugle au seul instant
qui compte. Une attribution n'est écrasée que par une **observation positive plus
récente** (l'équipement a répondu sur un autre port, ou derrière un autre
switch). Même logique au niveau du switch : un switch **injoignable** (filtré sur
`status == "up"`) n'a rien à dire ce tour-ci, ses attributions restent intactes.

**L'exception est aussi une preuve positive** : un port derrière lequel le switch
annonce **plusieurs** équipements supervisés est un **uplink ou un switch
chaîné**, pas le port de l'un d'eux — nommer l'un des deux enverrait l'opérateur
au mauvais équipement (et, sur une chaîne, vers un port qui n'est même pas dans
ce switch). Ce port est donc **non attribué**, et une attribution antérieure y
est **effacée** : ici le switch affirme activement quelque chose, il ne se tait
pas.

##### Interactions

- **`fiber_port_index` est exclu** de l'attribution : le lien fibre a déjà sa
  règle dédiée `fiber_link_down`, le surveiller deux fois double-alerterait.
- **`max_ports` est relevé automatiquement** si un équipement est prouvé
  **au-delà** de la plage scannée (plafond `MAX_PORT_INDEX=64`) : sinon
  `port_N_up` n'est jamais collecté et le port reste **invisible** — le trou
  exact qui avait caché le SFP fibre à l'index 25.
- **`rocket_port_index` reste un override manuel**, désormais utile : renseigné
  automatiquement **seulement** s'il est NULL **et** qu'il n'y a qu'**un seul**
  Rocket derrière le switch (sinon la valeur serait arbitraire) ; **jamais
  écrasé** s'il a été saisi, et évalué **en plus** des ports auto-détectés (il
  vaut même sans aucune MAC apprise).

##### Forme de l'alerte (changée)

Un incident **par switch et par type** (la déduplication porte sur
`device_id + alert_type`), listant **tous** les ports fautifs **et l'équipement
câblé sur chacun** : « 2 port(s) DOWN sur SW-AT1 — port 3 (Rocket AT1-Nord) ;
port 7 (UISP Power AT1) ». Les deux types sont ajoutés à
`_DESCRIPTION_ALERT_TYPES` (`alert_formatter`) pour que la **description passe
dans le message WhatsApp** : les champs structurés ne nomment que le switch, ce
qui n'est pas actionnable sur une unité 24 ports.

- `port_N_up` absent (hors plage de scan, ou pas de réponse) ⇒ **rien n'est
  affirmé**, ni ouverture ni résolution.
- **`ifSpeed = 0` est ignoré** (les cages SFP le font) : un débit **inconnu**
  n'est pas un débit **dégradé**.

##### ⚠️ VERDICT TERRAIN 2026-07-30 : inapplicable sur les switches UISP

Mesuré sur le parc réel, la détection **ne trouve rien** (16 switches, **0 port
attribué**) — et le code n'est pas en cause. Deux constats indépendants, chacun
vérifié sur le matériel :

1. **Les switches UISP n'implémentent pas BRIDGE-MIB.** Tout le sous-arbre
   `1.3.6.1.2.1.17` répond `NoSuchObject` : ni `dot1dBaseNumPorts`, ni
   `dot1dTpFdbPort`, ni `dot1qTpFdbPort`, ni `dot1dBasePortIfIndex`. Le MIB privé
   Ubiquiti (`1.3.6.1.4.1.41112`) est **absent aussi**. Le SNMP fonctionne
   parfaitement par ailleurs (`sysDescr`, `sysName`, 36 `ifDescr`) : ces firmwares
   n'exposent **que IF-MIB**. Vérifié sur les **deux** familles du parc —
   `UISP Switch` (`at1-uisp-s-a1d`, 10.135.2.108) et `UISP Switch Pro`
   (`ct1-uisp-s-pro-65a` 10.135.2.31, `arf1-uisp-s-pro-409` 10.135.2.209) — donc
   ce n'est pas une généralisation depuis un seul équipement.
2. **Le switch n'émet pas de LLDP/CDP**, donc la radio ne peut pas apprendre son
   port par le câble. Vérifié en capturant 45 s sur `eth0` d'une Rocket LTU
   (10.135.144.1, `aflturocket v2.4.1`) : `tcpdump 'ether proto 0x88cc or ether
   dst 01:00:0c:cc:cc:cc'` rend un pcap de **24 octets = son en-tête, zéro
   trame**. Le firmware LTU n'a d'ailleurs aucun démon LLDP (`lldpcli`/`lldpctl`/
   `lldpd` absents ; `system.cfg` porte `discovery.cdp.status=disabled`).

Le mot du câblage n'est donc **ni dans le switch, ni sur le câble**. La détection
par FDB est **conservée en fallback** (elle est correcte, et ne coûte plus rien
puisqu'elle ne tourne que sur les switches qu'UISP ne couvre pas) pour un futur
switch d'un autre fabricant qui exposerait BRIDGE-MIB.

##### La source qui marche : les data-links du contrôleur UISP

`GET /nms/api/v2.1/data-links` — **UISP connaît le câblage** (ses propres agents
le rapportent). Le bout switch d'un lien `ethernet` porte le numéro de port :

```
device: "TS1-UISP-S 5C5"  role=switch
interface: name="port6"  deviceName="0/6"  description="EST"  currentSpeed="1000-full"
```

`switch_port_service.detect_from_uisp` est donc la **source PRIMAIRE**, et
`fetch_data_links()` était déjà là (utilisé par le sync des stations). Un seul
appel couvre tout le parc. Mesuré le 2026-07-30 : **151 liens `ethernet`**, dont
**121 exploitables** sur les 17 switches UISP.

⚠️ **Trois formes de nom de port, une seule digne de confiance** :

| Forme | Nb | Matériel | Décision |
|---|---|---|---|
| `portN` + `deviceName` `0/N` | 121 | switches **UISP** | **exploitée** : `N` = ifIndex |
| `ethN`, `deviceName` absent | 13 | switches **UniFi** (`HQ-USW`, `Linskis`, `SNDE-LinkSys`) | **refusée** |
| `sfp+N` | 1 | `USW-Pro-Aggregation` → Edge router | hors parc |

Le refus des `ethN` n'est pas de la prudence gratuite : **l'indexation UniFi n'est
pas déductible du payload**, et le payload se contredit sur un même switch —
`eth0` y est libellé `port1` (base 0) mais `eth14` est libellé `port14` (base 1).
Deviner ferait **nommer le mauvais port physique dans une alerte critique**. On
journalise en WARNING et on n'attribue pas.

**La numérotation est VÉRIFIÉE, pas supposée** : chaque index attribué doit
répondre à `ifDescr` sur le switch (`snmp_service.fetch_if_descrs`), donc être un
vrai ifIndex, dans la même numérotation que les métriques `port_N_*`. Distinction
qui compte : un switch qui ne répond à **aucun** index est *muet* → on garde ses
attributions (stickiness) ; un switch qui répond mais **nie un index précis** →
cette attribution-là est refusée (`rejected_ports`, WARNING). C'est ce qui
transforme une hypothèse sur le nommage UISP en contrôle.

⚠️ **La question des cages SFP est sans objet** : aucun switch UISP n'a
d'équipement au-delà de `port18`. Les `TenGigabitEthernet` (ifIndex 25-28)
portent la fibre vers du matériel que UISP ne gère pas — donc aucun data-link —
et c'est déjà couvert à la main par `fiber_port_index`.

⚠️ **Les libellés de port UISP ne sont PAS fiables et ne servent à rien ici** :
`CT1-UISP-S-Pro port2` est étiqueté « SUD » alors qu'il porte `A2-CT1-EST`. Ils
n'entrent ni dans l'identification (qui est la **MAC**, des deux côtés, comme
partout dans le projet) ni dans les messages d'alerte — un libellé faux enverrait
l'opérateur au mauvais mât.

**Liens ignorés** : un lien dont **zéro** bout est un de nos switches (AF60 câblé
direct à un Rocket, 16 cas) et un lien dont **les deux** bouts sont nos switches
(uplink inter-switch : chaque bout est une affirmation valable, mais un device n'a
qu'une colonne d'uplink, donc choisir serait arbitraire).

##### Les ports d'UISP Power sont mappés mais JAMAIS surveillés

`UNWATCHED_DEVICE_TYPES = ("uisp_power",)`. Le port de management d'un UISP Power
est du **Fast Ethernet** : 100 Mb/s est sa vitesse **nominale**, pas une
dégradation. Mesuré sur le parc le 2026-07-30 : **11 des 13 ports sous le
gigabit étaient des UISP Power** — la règle aurait alerté en permanence sur du
matériel sain et **enterré les 4 vraies trouvailles** (`A2-AT2-SUD1`,
`A2-NR1-NORD`, `A2-SM1-OUEST`, `F60 CT1-NR1` bloqués à 100 Mb/s alors qu'ils
devraient être en gigabit). Son port qui tombe n'a pas besoin de règle non plus :
l'équipement cesse alors de répondre au ping et `device_unreachable` le couvre —
le même raisonnement qui a retiré `uisp_power_unreachable` pour éviter le doublon.

Le câblage reste **enregistré** (`devices.uplink_switch_port` est renseigné, donc
l'opérateur voit ce qui est sur chaque port) ; seule la **surveillance** est
retirée. Le chokepoint unique est `switch_port_service.watched_ports` : y exclure
un type le sort des règles sans effacer l'information. Ces ports sont aussi hors
du relevé automatique de `max_ports` (on n'élargit le scan que pour des ports
qu'on évalue).

##### ⚠️ Le refus d'un index doit être robuste, il ne l'était pas

`fetch_if_descrs` fait **2 tentatives** par index, pas une. `_snmp_get` envoie un
seul datagramme (`retries=0`) et ces switches **perdent des requêtes SNMP** :
constaté sur TJN1, où `ifDescr.1` est resté sans réponse pendant que ses voisins
répondaient, et où une métrique de port manquait au passage suivant. Comme une
réponse muette **REFUSE** une attribution, un seul paquet UDP perdu suffisait à
écarter un port légitime (`A2-TJN1-EST`, `A2-KS1-EST` au 1er essai). Un index qui
n'existe vraiment pas répond « rien » instantanément deux fois : le coût du retry
ne tombe que sur les trous réels.

##### Avant de compter dessus : le contrôle à blanc

```bash
dc exec backend python scripts/detect_switch_ports.py                  # DRY-RUN, 2 sources
dc exec backend python scripts/detect_switch_ports.py --source uisp    # data-links seuls
dc exec backend python scripts/detect_switch_ports.py --source fdb     # SNMP seul (muet ici)
dc exec backend python scripts/detect_switch_ports.py --apply          # écrit vraiment
```

Le script **n'écrit rien sans `--apply`** et affiche, pour chaque port trouvé,
l'état et la vitesse **lus en SNMP à l'instant** — c'est-à-dire exactement ce qui
alerterait une fois le job actif. À lancer **sur un vrai switch avant de
déployer** : le support BRIDGE-MIB dépend du firmware, et un port légitimement à
100 Mb/s apparaît ici en clair plutôt qu'en critique WhatsApp à 3 h du matin. La
logique vit dans le **service** (le job et le script appliquent la même règle —
un job ne peut pas importer depuis `scripts/`).

#### Topologie inter-sites — le maillage des backhauls (2026-08-04)

`site_topology_service` + `/network-topology` + page `/topology`. Complète la
topologie **intra-site** (composant `SiteTopology`, sur `/sites`) : ici c'est le
niveau au-dessus, quel site est raccordé à quel autre.

**La donnée n'existe pas chez nous.** On connaît le `site` de chaque AF60 et de
chaque PTP LiteBeam, jamais **quel AF60 parle à quel AF60**. Le contrôleur le
sait (ses agents le rapportent) et le publie sur `GET /nms/api/v2.1/data-links` —
la même source que le câblage des ports de switch, qui n'en retient que les liens
`ethernet`. Les liens **radio** de la même réponse portent nos backhauls.

##### ⚠️ Deux fraîcheurs, deux chemins — la règle centrale

| | Cadence | Source |
|---|---|---|
| **Câblage** (qui est relié à qui) | **1×/jour** (`site_topology_sync_job`) | UISP → table **`site_links`** |
| **Santé** (statut, capacité, potentiel) | **à chaque affichage** | notre base (`devices`/`device_metrics`) |

`sync_site_links` est le **seul** code du module qui parle au contrôleur.
`get_site_topology` sert la page **sans aucun appel à UISP**.

Ce n'est pas une optimisation tardive, c'est la nature de la donnée : un backhaul
est posé quelques fois par an, alors que la santé d'une liaison change en
permanence. **Ne jamais figer la santé dans `site_links`** — ce serait afficher
l'état d'hier comme celui de maintenant. Et ne jamais remettre la page en lecture
live : avant ce découpage, chaque ouverture d'onglet téléchargeait ~1300
équipements + ~1400 sites + ~1300 liens, et le `refreshInterval` de la page le
**rejouait toutes les 2 minutes** sur un onglet oublié. La page n'a donc
**délibérément pas** de `refreshInterval`, contrairement aux autres du projet —
elles affichent des métriques vivantes, celle-ci du câblage.

Pourquoi une table plutôt qu'un cache : les data-links désignent leurs extrémités
par l'**id UISP de l'équipement**, id qu'on ne stocke pas — résoudre un lien exige
donc `/devices`. On stocke le résultat résolu (les **MAC** des deux bouts, notre
identité partout ailleurs), pas la réponse brute.

⚠️ **Remplacement intégral de la table à chaque sync**, et c'est correct **ici** :
contrairement à la FDB d'un switch (où l'absence d'une MAC et la chute du port
sont la même observation), UISP **ne retire pas** un lien qui tombe — il le
rapporte `state="disconnected"` (vérifié sur CT1↔SK1 en panne). Une absence
signifie donc « dé-provisionné », pas « down ». **Garde-fou** : un fetch qui ne
résout **aucune** liaison ne vide **pas** la table (un payload vide serait sinon
lu comme « plus aucun backhaul » et effacerait toute la topologie — même leçon
que la passe de suppression du sync des stations).

##### Ce qui fait qu'une arête existe

Un data-link dont les deux bouts se résolvent sur deux **sites d'infra
différents**. Trois précisions portent tout le résultat :

- **Un site d'infra est un site qui PORTE de l'infra** — au moins un équipement
  que `uisp_sync_service.classify_device` reconnaît (le classificateur unique du
  projet, réutilisé et jamais recopié). ⚠️ **Le filtre `ucrm.client` seul ne
  suffit pas** : mesuré le 2026-08-04, **deux sites d'abonnés ont été créés à la
  main dans UISP sans rattachement CRM** (« Haydara, Ousmane », « El id, Mohamed
  fall »). Ils passaient donc pour de l'infra — le premier s'affichait comme un
  **site enfant de SK1** alors que le lien est un banal AP↔abonné, le second comme
  un site d'infra orphelin. (Ces deux lignes sont par ailleurs une anomalie de
  gestion : un abonné sans lien CRM est un abonné **potentiellement non facturé**,
  même famille que la section « absents de UISP » de `/access-diagnostics`.)
- **Le type de lien n'est PAS un filtre.** **3 des 5 liaisons du HQ sont en
  `ethernet`** (fibre vers ARF1, AT1, CT1) : filtrer sur `wireless` amputerait le
  graphe de sa racine.
- **L'identité est la MAC** des deux côtés, jamais le nom.

##### ⚠️ Le graphe N'EST PAS un arbre — layout en couches obligatoire

Mesuré sur le parc : **17 sites, 19 liaisons, dont 2 hors arbre** (`SK1↔CT2` et
`KS1↔SM1`). Ce sont de vraies boucles de redondance — CT2 est joignable par PK1
**et** par SK1. Un rendu arborescent devrait en jeter une **sans le dire**. Le
service produit donc des **couches** (parcours en largeur : `sites[].depth`) et
rend les arêtes surnuméraires à part (`layout.extra_edges`), tracées en
pointillé. Ne jamais « simplifier » en arbre.

##### La racine ne se déduit pas

Le lien Internet→HQ **n'est pas un data-link** : le contrôleur ignore quel site
fait face à l'amont. La racine est donc un réglage (`TOPOLOGY_ROOT_SITE`, défaut
`A2 HQ`), avec repli sur le site de plus haut degré — et la réponse dit toujours
laquelle a servi (`root_source`), parce qu'un repli silencieux se lirait comme
une déduction.

##### Couleur d'une liaison = état des SITES, pas des radios (2026-08-04)

La page est volontairement **dépouillée** : le graphe et rien d'autre. Pas de
tuiles de comptage, pas de légende, pas de liste des liaisons — le détail d'un
lien est au **survol** du trait.

**Couleur** — cinq valeurs, dans cet **ordre de priorité strict** :

| # | Condition | Couleur |
|---|---|---|
| 1 | Site **ENTIÈREMENT** tombé (`is_down` = tous ses équipements d'infra `down`) | **rouge** |
| 2 | **Boucle de redondance** (`!is_tree_edge`) — chemin de secours, pas la dorsale | **gris** |
| 3 | **Fibre / cuivre** (`medium === "wired"`) | **bleu** |
| 4 | Radio debout mais **INERTE** (débit relevé sous `TOPOLOGY_TRAFFIC_MIN_MBPS`) | **jaune** |
| 5 | Radio qui écoule | **vert** |

⚠️ **Le rouge est en tête et doit y rester** : une panne ne doit jamais être
masquée par une couleur de support ou de rôle — une dorsale fibre coupée doit
crier, pas rester bleue. (Vérifié sur le parc : `HQ↔CT1` est fibre *et* rouge.)

##### Pastilles de PORT aux extrémités

Chaque extrémité de liaison porte une **pastille** à son point d'accroche : la
**vitesse négociée du port de switch** sur lequel l'équipement est câblé.
**Vert ≥ 1 Gb/s**, **jaune à 100 Mb/s**, **rouge sous 100**.

Ce n'est pas décoratif : le parc porte de vrais backhauls **bloqués à 100 Mb/s
alors qu'ils devraient être en gigabit** (`A2-AT2-SUD1`, `A2-NR1-NORD`,
`A2-SM1-OUEST`, `F60 CT1-NR1`, relevés le 2026-07-30). Une carte qui ne montre
que haut/bas ne peut pas le révéler.

Aucune requête nouvelle sur le terrain : le port vient de
`devices.uplink_switch_id/_port` (posé par `switch_port_mapping_job` depuis les
data-links) et la vitesse de `port_N_speed_mbps`, déjà relevée à chaque cycle
SNMP sur le switch. `_attach_uplink_port_speed` ne fait que croiser les deux.

⚠️ **Pas de port connu, ou `ifSpeed = 0` (cage SFP) ⇒ AUCUNE pastille.** Les
liaisons **fibre** sont exactement dans ce cas : leurs deux bouts sont des
switches, et l'uplink inter-switch n'est volontairement pas attribué (chaque bout
est une affirmation valable, mais un équipement n'a qu'une colonne d'uplink). Une
pastille grise « indéterminée » se lirait comme un diagnostic ; l'absence, non.
Sur une liaison **redondante**, c'est le port le **plus lent** qui est retenu —
c'est lui qui bride.

**Style** = le **support** : liaison **radio en tirets**, **fibre/cuivre en trait
plein** (`edges[].medium`, `wireless` seulement si TOUS les liens physiques le
sont — une liaison mixte compte comme filaire, le chemin cuivre étant le plus
capable). Les tirets ne distinguent plus les boucles (toutes radio) : c'est leur
**couleur grise** qui s'en charge.

⚠️ **`traffic="unknown"` est rendu VERT, jamais jaune.** **Jaune = « mesuré à
zéro », jamais « pas mesuré »** — la même règle que partout ici.

##### Débit d'une liaison FIBRE — dérivé des compteurs du port SFP (2026-08-11)

Un switch **n'expose aucun débit instantané** en SNMP, seulement des compteurs
cumulés (`ifHCInOctets`/`ifHCOutOctets`) — que `collect_switch_port_metrics`
relevait déjà en `port_N_rx/tx_bytes`. Les liaisons fibre, dont les **deux bouts
sont des switches**, restaient donc « non mesuré » alors qu'elles portent la
dorsale.

`snmp_poll_job` dérive maintenant le débit du port `fiber_port_index` par delta
de compteurs, via le **même** `_derive_throughput_from_counters` que le LiteBeam
M5 (qui n'a pas davantage de débit instantané). Vérifié sur `ARF1-UISP-S-Pro 409`
(10.135.2.209, port 25) le 2026-08-11 : deux cycles à 17 s donnent **445 Mb/s
descendant / 43 Mb/s montant**.

- ⚠️ **Clés `fiber_dl/ul_throughput_mbps` dédiées**, jamais `dl/ul_throughput_mbps` :
  sur un switch « le débit de l'équipement » ne veut rien dire (28 ports), alors
  que « le débit de son port fibre » est précis. Ça évite aussi de l'inscrire
  dans les courbes de `GRAPH_METRICS`.
- ⚠️ **Le SENS tombe juste sans effort** : sur un switch, `rx` est ce qu'il
  REÇOIT — exactement la convention des radios (`dl` = ce que l'équipement
  reçoit). `edge_traffic` ne fait qu'un **repli** `key` → `fiber_{key}`, et toute
  la lecture inter-sites reste inchangée. Une mesure directe l'emporte toujours
  sur le repli.
- ⚠️ C'est une **moyenne sur l'intervalle de poll** (60 s), pas un instantané —
  comme pour le M5. Suffisant pour distinguer « ça passe » de « ça ne passe pas ».
- Les deux clés sont dans **`GRAPH_METRICS`** : la fiche d'un switch fibre expose
  donc la **courbe d'historique** du backhaul (24 h / 7 j / 30 j ou plage de
  dates), au même titre qu'un LR ou un AF60. Le bouton « Plus d'infos » de
  `DeviceDetailModal` est ouvert au type `uisp_switch` ; sur un switch **sans**
  fibre il reste inoffensif, les onglets suivant `available_metrics`.
- ⚠️ **Seuls les sites dont `fiber_port_index` est renseigné** (AT1 p9, CT1 p25,
  ARF1 p25) produisent ces clés. Ailleurs la liaison reste honnêtement « non
  mesuré » — et rendue **verte**, jamais jaune.
- ⚠️ **`ifSpeed = 0` sur une cage SFP reste vrai** : ces extrémités n'ont
  toujours **aucune pastille** de port. C'est le **débit** qu'on gagne, pas la
  vitesse négociée.

Le débit retenu est le **maximum** des deux extrémités (`dl+ul`) : les deux bouts
décrivent le même lien, mais l'un peut n'avoir aucun relevé frais ; prendre le
maximum évite de déclarer inerte une liaison que l'autre extrémité voit passer.
Pour une liaison **redondante**, une branche qui écoule l'emporte — si une
branche passe, la liaison passe.

⚠️ **Un équipement HS ne met pas un site à terre.** Peindre en rouge les
liaisons d'un site qui fonctionne enverrait chercher une panne de backhaul là où
il n'y en a pas. La panne isolée reste visible : le compteur **« 14/1 »** sous le
nœud (part en panne en rouge), comme le fait le contrôleur, et le nom du site
passe en rouge quand il est entièrement tombé.

⚠️ **Conséquence assumée** : un backhaul dont la radio est tombée reste **vert**
tant que le reste de son site répond. Le trait ne porte plus cette information —
le compteur du nœud si.

⚠️ **Seuls les types d'INFRA sont comptés** (`INFRA_SITE_DEVICE_TYPES`). Les LR
portent le `site` de leur AP (le sync fait suivre le site au CPE) : les inclure
afficherait « 9 » comme « 87 », et un seul abonné éteint fausserait le compteur.
Un statut `unknown` n'est **pas** compté comme tombé — on n'affirme une panne que
sur constat.

Les notions de capacité et de plancher ci-dessous **restent calculées** (elles
alimentent `health` et l'infobulle) mais ne pilotent plus la couleur ; leur vue
dédiée est la section « Liaisons entre sites » de `/lr-health`.

##### Colorer une liaison : ce que la carte UISP ne sait pas faire

Les mesures viennent de **notre** poll (`total_capacity_mbps`,
`link_potential_pct`), et le verdict `degraded` est calculé **côté service**
contre les planchers réels — `af60_capacity_display_min_mbps` (1,95 Gb/s) et
`airmax_backhaul_capacity_min_mbps` (150 Mb/s), **les mêmes que**
`lr_health_service.get_site_link_health`. Une liaison est donc rendue dégradée
sur la carte **exactement quand** la section « Liaisons entre sites » la
listerait. Recopier un barème dans le frontend les ferait diverger au premier
ajustement de seuil.

⚠️ **Une liaison a DEUX bouts et ils ne répondent pas toujours tous les deux**
(mesuré : 6 liaisons radio sur 15 mesurées des deux côtés, 6 d'un seul, 3
d'aucun) :
- deux bouts mesurés → on retient le **pire** (un lien vaut son extrémité la plus
  dégradée) ;
- un seul → celui-là ;
- **aucun → `state="unmeasured"`, rendu NEUTRE (gris), jamais vert.** Un lien
  qu'on ne mesure pas n'est pas un lien sain — ce serait le mensonge le plus
  coûteux de la carte.
- Un bout **`down`** l'emporte sur tout : sa dernière capacité en base est stale
  et ne doit pas maquiller la panne (cas réel `CT1↔SK1`, où le F60 côté CT1 est
  down mais porte encore 3902 Mb/s).

Pour une liaison **redondante** (plusieurs radios entre les deux mêmes sites), on
remonte au contraire la **meilleure** branche : le trafic passe par celle qui
marche, une branche morte ne coupe pas la liaison. Règle inverse **à l'intérieur**
d'un lien, où les deux extrémités décrivent le même lien physique.

##### Contrôle en ligne de commande

```bash
dc exec backend python scripts/dump_site_topology.py            # lit la base, 0 appel UISP
dc exec backend python scripts/dump_site_topology.py --sync     # rapatrie d'abord le câblage
dc exec backend python scripts/dump_site_topology.py --root "A2 HQ"
dc exec backend python scripts/dump_site_topology.py --json > topo.json
```

Le script imprime le **même graphe** que la page et nomme ce qui cloche. La
logique vit dans le **service** (l'API et le script appliquent la même règle — un
service ne peut pas importer depuis `scripts/`). **Par défaut il ne contacte pas
le contrôleur** ; `--sync` force le rapatriement, comme `POST /network-topology/sync`.

##### Vue CARTE de la topologie (Google Maps) — 2026-08-10

Bascule **Graphe / Carte** dans l'en-tête de `/topology` (`TopologyView`). Le
**graphe reste la vue par défaut** : il est complet (tous les sites y figurent,
même sans position connue) et ne dépend d'aucun service externe.

Ce que la carte apporte et que le graphe ne peut pas : la **distance** et la
**direction**. Un backhaul de 400 m et un de 12 km sont deux traits identiques
sur un graphe en couches.

⚠️ **Aucune donnée nouvelle n'est demandée au terrain** : la carte est peinte
depuis la **même réponse** `/network-topology` que le graphe — coordonnées
comprises, jointes côté service depuis **`site_locations`** (les 17 pylônes,
semés depuis UISP en médiane par site, cf. `models/site_location.py`). Les deux
vues ne peuvent donc pas se contredire, et basculer ne coûte pas une requête.

⚠️ **Le barème de couleurs est PARTAGÉ, pas recopié** : `lib/topologyColors.ts`
(`edgeColor`, `downSiteSet`, `siteColor`) est importé par `TopologyGraph` **et**
par `TopologyMap`. Une liaison a la même couleur dans les deux vues ; deux
écrans du même réseau qui se contrediraient sur l'état d'une dorsale seraient
pires que pas de carte. Même raison pour `lib/googleMaps.ts` : le verrou
anti-double-injection du script doit être au niveau **module et commun aux deux
pages** qui affichent une carte (`/map` et `/topology`), sinon chacune injecte
son `<script>` et Google refuse le second (« included multiple times »).

⚠️ **Un site sans position n'est PAS escamoté** : il est nommé sous la carte
(« N sites sans position connue »), et ses liaisons ne sont pas tracées — une
carte qui omet un site sans le dire se lit comme un réseau qui n'a pas ce site.
Même principe que les `outliers` de la carte des clients. La jointure se fait
sur la chaîne **exacte** (`site_locations.site` = `devices.site`) : ne jamais
« nettoyer » les noms, le double espace de « A2  ARF1 » est voulu et le
normaliser ferait disparaître ce site.

Détails de rendu : liaisons **sous** les marqueurs, radio en **tirets** (via
`icons` répétés — une polyline Google n'a pas de `dash`), filaire en trait
plein, redondance en trait plus épais ; marqueur de site **vert / ambre / rouge**
(`siteColor` : ambre = une partie en panne, rouge = site entièrement tombé — les
confondre enverrait chercher une panne de site pour un seul secteur HS) ;
`labelOrigin` décale le nom **sous** la pastille (sans lui, Google centre
l'étiquette sur le point et le nom recouvre la couleur d'état) ; cadrage
automatique **une seule fois** (le rejouer annulerait le zoom de l'opérateur).
La sélection de site est **partagée** avec le graphe : basculer garde le filtre.

#### Barre de recherche globale (bandeau de l'application) — 2026-08-10

`AppShell` porte un **bandeau collant** (`sticky top-0`) présent sur toutes les
pages du dashboard, avec la recherche d'équipement/client (`DeviceSearchBar`,
raccourci **Ctrl/⌘+K**, navigation ↑ ↓ Entrée). Source : `/devices/search`
(nom ou IP — le nom d'un LR porte le nom **et le téléphone** du client).

Le composant est **le même** que celui de `/sites`, généralisé par props
(`placeholder`, `className`, `shortcut`) plutôt que dupliqué ; le champ local de
`/sites` a été retiré (doublon à 60 px du bandeau). Le raccourci est sous prop
parce que **deux champs le réclamant se voleraient le focus** — il est réservé à
l'instance globale.

Choisir un résultat navigue vers **`/sites?device=<id>`**, le deep-link qui
existait déjà (« Voir l'équipement → » de `/lr-health`) : la barre n'invente
aucun chemin d'ouverture qui lui soit propre.

⚠️ **Le paramètre `?device=` est CONSOMMÉ** (retiré de l'URL) une fois la fiche
ouverte, sinon rechercher **deux fois** le même équipement ne rouvrirait pas sa
fiche (URL inchangée ⇒ aucun changement de dépendance ⇒ aucun effet).

⚠️ **Aucun garde « déjà traité » sur ce deep-link.** Il y en avait un
(`lastHandledDevice`) : combiné au drapeau `cancelled`, il verrouillait la fiche
sur un **chargement perpétuel** en **Strict Mode** (activé par défaut en dev sur
l'App Router, donc invisible en build de prod) — la 1re passe posait le garde et
lançait le fetch, son nettoyage l'annulait, la 2e ressortait sur le garde, et
plus personne ne posait la fiche alors que la requête répondait 200.

Le bandeau porte aussi le **bouton de retour du menu** quand celui-ci est replié
(`/topology`) : il flottait auparavant en `absolute` par-dessus le contenu, avec
un `pl-16` pour dégager la place — deux bricolages supprimés.

#### Enrôlement UISP d'un CPE (pose de la clé par SSH) — 2026-07-28

Un CPE ne remonte dans l'inventaire UISP que s'il porte la **clé du contrôleur**
dans ses clés `unms.*` (config airOS). Un abonné actif qui ne l'a pas est
invisible — donc potentiellement **non facturé** : c'est la liste « découverts
par radio mais absents de UISP » de `/access-diagnostics`. `ssh_service.set_uisp_key`
la pose **sans reboot ni coupure** (écriture config → `cfgmtd -w -p /etc/` →
`killall -SIGHUP udapi-bridge`, le signal que le firmware s'envoie lui-même).
Validé sur **airOS 8.7.11** (NanoStation 5AC) et **airOS 6.3.24** (LiteBeam M5).

Trois règles qu'aucune doc Ubiquiti ne donne, toutes découvertes en échouant :

1. **`/tmp/system.cfg` et `/tmp/running.cfg` doivent être IDENTIQUES** au moment
   de la pose. Modifier `system.cfg` seul : le CPE se connecte au contrôleur puis
   refuse de persister, **en boucle 1×/s**, avec `unms_key_store_airos: … is
   different from … not saving new UNMS key`. On écrit donc UN fichier canonique
   aux deux emplacements. ⚠️ Le symptôme ressemble à « mauvaise clé » alors que
   le journal dit `connection established`.
2. **La clé qu'on écrit n'est qu'un jeton d'enrôlement.** Après adoption, le
   contrôleur émet une clé **propre à l'équipement**, le démon réécrit `unms.uri`
   et la persiste en flash tout seul (le suffixe change au passage :
   `+allowSelfSignedCertificate` posé → `+allowUntrustedCertificate` en place).
3. **`/var/run/unms-conn-status` est un drapeau de SESSION, pas un état
   d'enrôlement** : le démon se déconnecte après 30 s d'inactivité et met ~1 min
   à rétablir après un redémarrage, donc il vaut **0 par intermittence sur un
   équipement parfaitement enrôlé**. Le SIGHUP ne fait d'ailleurs pas toujours
   relire la config (une clé erronée est restée en flash pendant que la session
   établie rapportait encore `1`).

**Conséquence de conception — l'idempotence porte sur l'HÔTE de `unms.uri`**
(`ssh_service.uisp_uri_host`), jamais sur le jeton (que le contrôleur réécrit)
ni sur le drapeau de session (qui clignote). Le contrôleur réécrit le jeton mais
ne déplace jamais l'équipement vers un autre hôte. Se fier au drapeau écrasait la
clé propre d'un équipement sain dès qu'un clic tombait sur un 0 transitoire —
reproduit sur un M5 le 2026-07-28, clé à restaurer à la main.
`force=True` passe outre : **uniquement** pour une **clé orpheline** (équipement
supprimé de UISP — il se connecte sans jamais être adopté ; signature :
`connection established` toutes les ~32 s, jamais de `got unmsSetup`,
`unms-conn-time` VIDE). Sur un équipement sain, forcer le **dé-enrôle**.

**⚠️ La clé d'enrôlement cesse d'être honorée — vérifier sa fraîcheur AVANT une
campagne.** Chronologie du 2026-07-28 : la même clé a adopté trois CPE à 09:25,
09:39 et 09:58, puis a été **refusée à 10:23 et 10:35**. Le second échec est
décisif : il portait sur un CPE **absent de UISP** dont la config venait d'être
rendue correcte (deux fichiers identiques, 4 clés, flashé) — le CPE se connecte,
ne reçoit jamais `unmsSetup`. Ni l'état de l'équipement ni le fait qu'il soit
déjà connu du contrôleur n'expliquent donc l'échec : c'est la **clé** qui n'est
plus valable. Indice corroborant dans le journal : `Using deprecated option
allowSelfSignedCertificate, please replace it with allowUntrustedCertificate` —
les équipements réellement adoptés portent tous la forme récente.

**En pratique : régénérer la clé dans UISP → Paramètres → Équipements juste avant
une campagne d'enrôlement**, et ne pas compter sur une valeur posée il y a des
semaines. Une clé morte fait échouer *toute* la campagne, proprement (rien n'est
cassé) mais intégralement.

⚠️ **Corollaire sur `force`** : forcer avec une clé morte sur un équipement
adopté lui fait PERDRE sa clé propre et le sort de UISP (constaté sur un 5AC
adopté à 09:25, forcé à 10:23). Il a fallu réécrire sa clé propre, **récupérée
dans la sauvegarde**, pour qu'il revienne (adoption en 6 s). C'est LA raison de
toujours sauvegarder la config avant d'écrire.

**Jeu de clés par famille** : airOS 8 → 4 (`status`, `ui_url`,
`unms_redirector`, `uri`) ; airOS 6 → 2 (`status`, `uri`), et 2 suffisent. On
écrit à chaque famille exactement la séquence validée sur elle. ⚠️ Un CPE non
enrôlé n'a que les 2 clés **vides** : il faut en **ajouter**, pas substituer.

Exposition : `POST /devices/{id}/enroll-uisp` (unitaire) +
`POST /access-diagnostics/enroll-uisp` (lot, avec `force`), bouton par ligne et
« Tout enrôler » sur la page **Diagnostics d'accès**. `lrs.uisp_enrolled_at`
enregistre le dernier enrôlement réussi — il sépare « jamais tenté » de « adopté,
en attente du sync quotidien », mais n'atteste PAS la présence dans UISP (seul
`uisp_synced_at`, écrit par le sync, le fait). Aucun job d'enforcement : un
enrôlement est ponctuel, le réappliquer périodiquement dé-enrôlerait.

#### Association client CRM (2026-07-28)

`POST /uisp/assign` transpose le geste manuel de l'opérateur : chercher la MAC
dans UISP, la voir en « unknown », cliquer dessus et choisir le client. Le
contrat est **volontairement minimal — une MAC et un id CRM, rien d'autre**.

⚠️ **Le « site » ne fait PAS partie du contrat.** UISP ne rattache pas un
équipement à un client mais à un **site**, et c'est le site qui porte
`ucrm.client.{id,name}`. Traduire l'id CRM en site est de la plomberie interne :
le mot n'apparaît ni en entrée ni en sortie de l'API. La table de correspondance
vient de `GET /nms/api/v2.1/sites` — **aucune clé CRM n'est nécessaire** (l'API
CRM `/crm/api/v1.0/` refuse d'ailleurs le token NMS en 401, elle a ses propres
App Keys).

**Pourquoi l'id CRM et pas le nom** (mesuré sur 1410 sites clients) : 1402 ids
distincts contre 1395 noms — **7 noms désignent deux clients différents** (« Ba,
Amadou » = clients 1361 et 1369). Le nom seul assignerait au mauvais abonné.

**Clients à plusieurs services** : **6 sur 1402** en ont (donc plusieurs sites).
L'id du client ne suffit alors pas → l'appelant précise `crm_service_id`. Sans
lui : **409 avec la liste des services**, jamais d'arbitrage — choisir au hasard
rattacherait l'abonné au mauvais service, en silence et durablement.

⚠️ **Le service se désigne par son ID, jamais par son nom** : les services d'un
même client portent régulièrement des noms **identiques** (client 11 → trois
« 20Mb TEST » ; client 1005 → trois « AirFiber 15Mb Familial »). L'id est unique
sur tout le contrôleur (1410 ids pour 1410 sites, **zéro doublon**), donc il
détermine le site à lui seul. Il est quand même vérifié comme appartenant au
client annoncé : un appelant qui intervertit deux ids rattacherait sinon
l'équipement à un tout autre abonné sans que rien ne le signale.

⚠️ **Un équipement déjà rattaché à un AUTRE client n'est jamais déplacé en
silence** : refus 409 avec l'id du détenteur actuel, `reassign=true` pour passer
outre. Déplacer un CPE est légitime (matériel récupéré et réinstallé ailleurs)
mais **retire son rattachement à l'abonné actuel** — une MAC saisie de travers
ferait ce dégât sans que rien ne le signale. Même principe que le contrôle
d'identité avant blocage : aucune action ne doit pouvoir toucher le mauvais
abonné par accident. Un équipement déjà chez le **bon** client est un no-op
(aucune écriture), donc l'appel est rejouable sans effet.

⚠️ **Le nom d'hôte de l'équipement n'est utilisé nulle part** — ni comme critère,
ni en sortie (messages et journaux ne citent que MAC et ids CRM). Un CPE s'annonce
sous un nom qu'il s'est donné (« <contrat>-<nom du client> ») ; s'en servir
revient à identifier par un nom, et les noms se ressemblent (« Keida, Mariem
Oumar » vs « Sall, Mariem oumar » = deux clients CRM distincts).

**Ordre imposé** : vérifier le client CRM **avant** de toucher à l'équipement
(inutile de poser une clé pour un client qui n'existe pas, et un échec ne laisse
alors aucune trace) ; puis, si l'équipement est absent du contrôleur, lui poser
la clé (sans elle il ne se déclare jamais → rien à associer). L'enregistrement
n'étant pas instantané, la réponse porte `pending_registration`.

#### Agent d'une action FAI — le champ `user` (2026-08-06)

Le système de paiement transmet désormais **qui** déclenche chaque coupure :
`user` dans le corps de `POST /fai/block` et `/fai/unblock` — e-mail de l'agent
pour un geste manuel, ou libellé automatique (`auto system` pour la campagne
d'impayés, `auto retry` pour le rejeu). Écrit dans la piste d'audit
(`fai_audit`), rendu **en seconde ligne de la colonne « Origine »** de
`/fai-journal` (pas de colonne à lui : le tableau en porte déjà 7) et **inclus
dans la recherche** de la page (« toutes les coupures ordonnées par untel » est la
question qu'on pose à un journal d'audit).

⚠️ **`user` n'est pas `source`.** `source` est déduit du **motif** et dit quel
SCRIPT a appelé (`Block_all.php`, `enforce`, `script`…) ; `user` dit qui est
derrière — deux agents passent par le même script. Les deux colonnes coexistent.

⚠️ **Facultatif, et ça n'est pas une facilité** : un ordre de coupure ne doit
jamais échouer sur un champ d'audit manquant (un abonné impayé resterait en ligne
pour ça). Aucun contrôle sur la valeur non plus — ce n'est pas un compte chez
nous ; elle est seulement normalisée (espaces réduits, 120 caractères max, `|`
neutralisé) puis journalisée telle quelle.

⚠️ **Le format du journal a changé sur un fichier DÉJÀ écrit** — c'est le vrai
risque de ce changement, et il porte sur la RELECTURE : `fai_audit._parse`
reconnaît le champ à sa **forme** (`_USER_FIELD_RE`), pas à sa position, parce
que le nombre de champs ne sépare pas les deux formats (un ancien message
contenant un ` | ` produit lui aussi 9 morceaux). Repasser à un split à arité
fixe rejetterait d'un coup **tout l'historique antérieur** — c.-à-d. effacerait
la piste d'audit en la laissant sur disque. Verrouillé par
`tests/test_fai_audit_user.py`.

⚠️ **Portée : la ligne de la DEMANDE, pas ses suites.** L'agent est journalisé
sur l'action que l'API a reçue. Les lignes émises **plus tard** par le job
d'enforcement pour le même ordre (`RETRY_OK`, `ABANDON`, `IDENT_KO`,
`ROUTER_BLOCK`) restent sans agent : rien ne le porte en base. L'attribuer aussi
aux rejeux demanderait une colonne sur `lrs` (donc une migration) — non fait,
délibérément.

#### DÉBIT vs CAPACITÉ — deux mesures distinctes (2026-07-20)

Piège corrigé le 2026-07-20 : le « débit » de la fiche équipement était lu dans
`capacity.*`, c.-à-d. la **capacité**. « Débit DL » et « Capacité DL »
sortaient de la même source et affichaient la même valeur, alors que le
dashboard Ubiquiti montre bien deux séries (**140 Mb/s** de capacité pour
**94 kb/s** de trafic réel sur le même lien).

| Notion | Clés | Sens |
|---|---|---|
| **Capacité** | `dl_capacity_mbps` / `ul_capacity_mbps` | Ce que le lien **pourrait** écouler (UI « RX CAPACITY ») |
| **Débit** | `dl_throughput_mbps` / `ul_throughput_mbps` | Ce qui **circule réellement** (UI « RX THROUGHPUT ») |
| **Modulation** | `dl_phy_rate_mbps` / `ul_phy_rate_mbps` | Taux PHY négocié — **ni** l'un **ni** l'autre. airMAX-M (wstalist) et SNMP seulement |

Source du débit par famille — **jamais la même clé, et jamais dérivable de la capacité** :
- **LTU** : `peer.common.counters.txRate/rxRate` — ⚠️ l'unité est le **bit/s**, pas le kb/s (vérifié sur un lien réel afltu v2.4.1 : `rxRate 191961 ÷ 8 ÷ rxPPS 16 = 1500 o/paquet`, soit la MTU exacte ; lu en kb/s ça donnerait 192 Mb/s sur un lien à 51,8 Mb/s de capacité). Les compteurs reflètent l'interface **locale** (égaux au bit près à `interfaces[].statistics`) : on poll le **Rocket**, donc `tx` = AP→CPE = **DL du client**. ⚠️ Les clés `txkbps/rxkbps` **n'existent pas** — les chercher laissait le débit vide sur tout le parc LTU
- **airMAX AC** : **`wireless.throughput.rx/tx`**, bloc **hors de `sta[0]`** — c'est pour ça qu'il avait échappé au parser. ⚠️ **Direction inversée** : on interroge le CPE, donc son `rx` est le **descendant** client et son `tx` le **montant**
- **AF60** : **`interfaces[wlan0].statistics.rxRate/txRate`** (bit/s), **surtout PAS `peers[0].common.counters`** — VÉRIFIÉ sur un AF60-LR réel le 2026-08-03 (10.135.80.1, fw v2.6.8, 6 relevés à 10 s). Le bloc `peers` est **relayé par la radio et retarde** : il a annoncé **0,06 Mb/s pendant que le lien écoulait 76,69 Mb/s** — une courbe bâtie dessus montre des effondrements qui n'ont pas eu lieu. `wlan0` colle au bit près à `eth0` à chaque relevé (ce qui entre par la radio ressort par le cuivre). Unité prouvée par la taille de paquet : `66_100_104 ÷ 8 ÷ rxPPS 6520 = 1267 o` < MTU. ⚠️ **SENS** : `rx`/`tx` sont relatifs à l'équipement interrogé → rattachés au `dl`/`ul` que le firmware emploie lui-même dans `linkQuality.capacity` (déjà la source de `dl/ul_capacity_mbps`), donc **`dl` = ce que l'AF60 REÇOIT**. Preuve interne à la capture : `capacity.dl=600000 < ul=975000` avec `mcs.rxIdx=6 < txIdx=9` (et `linkScore` dl 26 < ul 43) — la direction la moins bien modulée est la moins capable. `wlan0` absent ⇒ clés `None` (trou), **jamais** de repli sur le bloc `peers`. Fixture entière + test de non-régression : `tests/test_af60_api_service.py`. ⚠️ Aucune règle d'alerte AF60 ne lit le débit : casser source ou sens ne ferait échouer **rien d'autre** que ces tests
- **LiteBeam M5** : **aucun débit instantané nulle part** — ni `wstalist`, ni `status.cgi`, ni `ifstats.cgi` (vérifié sur un M5 réel, fw v6.3.24 XW). Le débit est **DÉRIVÉ du delta des compteurs d'octets** par `jobs._derive_throughput_from_counters`, exactement comme le fait l'écran « Monitor > Throughput » du M5 lui-même. ⚠️ C'est une **moyenne sur l'intervalle de poll** (3-8 min sur la sonde SSH), pas l'instantané des autres familles. Le sens est **fourni par l'appelant** (`throughput_from_counters=("radio_rx_bytes", "radio_tx_bytes")`), jamais deviné : sur une **station** ce que la radio reçoit est le DESCENDANT client, sur un AP ce serait l'inverse. Pas de relevé précédent ou compteur remis à zéro (reboot) → clé **absente** (trou), jamais un 0. Le taux PHY reste dans `dl/ul_phy_rate_mbps`
- **Capacité du M5** : le « **TX/RX Rate** » de son écran Status (= le taux PHY négocié, `wstalist` `rx`/`tx`) est mappé sur `dl/ul_capacity_mbps`. C'est le **seul** chiffre de capacité que le M5 donne : `airmax.capacity` y vaut 0 et `polling.capacity` est un **pourcentage**. ⚠️ Optimiste — le débit utile airMAX tourne autour de la **moitié** du taux PHY. ⚠️ **SENS** : on interroge la STATION, donc son `tx` = MONTANT et son `rx` = DESCENDANT (l'inverse était câblé, hérité de la convention LTU où l'on poll l'AP ; vérifié en comparant `sta.cgi` et `status.cgi` au même instant)
- **SNMP (airMAX AP)** : aucun débit — seulement le taux PHY dans `dl/ul_phy_rate_mbps`. La dérivation par compteurs n'y est **pas** branchée : le sens y est inversé (AP) et personne ne l'a vérifié

**Capacité idéale supprimée** (`tx_ideal_mbps`/`rx_ideal_mbps`) : on affiche la
capacité réelle. Les règles `capacity_low`/`capacity_ul_low`, qui reposaient sur
le ratio réel/idéal, sont supprimées avec elle (migration `z9k0l1m2n3o4`, qui
renomme aussi l'historique des courbes sans perte).

**`throughput_anomaly` a été SUPPRIMÉE** (2026-07-20). Elle ne surveillait pas
ce que son nom indiquait : sur un Rocket elle lisait `tx_rate_mbps`, qui ne
venait que du SNMP airMAX et valait le **taux de modulation PHY** — ni un débit
ni une capacité — et sur les Rockets LTU la clé était absente, donc la règle ne
s'exécutait jamais. Supprimés avec elle : l'alert_type, la politique, les
libellés, les 3 réglages (`throughput_anomaly_drop_pct` / `_min_mbps` /
`_failure_threshold`), l'injection EMA `_inject_throughput_baseline` et les
baselines `_throughput_ema` en base (migration `z9k0l1m2n3o4`).

Le **débit agrégé de l'AP** (somme du trafic de ses CPE, calculée dans
`ltu_api_poll_job`) est conservé : il ne sert plus à aucune règle, seulement à
**afficher** le débit sur la fiche d'un Rocket comme sur celle d'un LR.

⚠️ **La fixture de test airOS était trimmée** « aux seuls champs que le parser
lit », ce qui avait fait conclure à tort que le firmware n'exposait pas le
débit. Une fixture doit rester la preuve de ce que l'équipement envoie, pas le
miroir de ce que le code sait déjà lire.

#### Le M5 sert bien `status.cgi` — mais en schéma airOS 6

Vérifié le 2026-07-21 sur un LiteBeam M5 (fw v6.3.24 XW) : il **répond** à `status.cgi`, contrairement à ce qu'on croyait. Mais sa structure est celle d'airOS 6 — champs **à plat** sous `wireless` (`signal`, `ccq`, `txrate`, `rxrate`, `polling.capacity`) et **aucun bloc `sta[]`**. Le parser airOS AC, qui lit `wireless.sta[0]`, n'en tire donc **que l'uptime**. La conclusion pratique (passer par le SSH `wstalist`) reste la bonne ; c'est la raison qui était fausse. ⚠️ `wireless.polling.capacity` y est un **pourcentage** (« airMAX Capacity 33 % »), pas des Mb/s — ne pas le mapper sur `dl_capacity_mbps`.

#### Politique device_metrics (history vs latest) — `persist_device_metrics` dans `jobs.py`
Tous les jobs de polling persistent leurs métriques via `persist_device_metrics(session, device_id, metrics, unit_map)`. Règle unique : si le `metric_name` est dans `HISTORY_METRICS`, on **empile** une ligne par cycle (série temporelle conservée) ; sinon on **écrase en place** (1 ligne par `(device_id, metric_name)` via DELETE+INSERT). `HISTORY_METRICS` = les **seules** métriques relues comme série par un consommateur, c.-à-d. **uniquement les compteurs bytes** :
- `peer_tx_bytes`, `peer_rx_bytes`, `radio_rx_bytes`, `radio_tx_bytes` → deltas `LAG()` de `consumption_service` (24h/7j/30j).

Tout le reste est collapsé (latest-only) **dans `device_metrics`** — y compris `lr_latency_ms`, `total_capacity_mbps`, `dl_capacity_mbps`, `ul_capacity_mbps`, `dl_throughput_mbps`, `ul_throughput_mbps` et les autres métriques radio (`signal_dbm`, `cinr_db`, `ccq_pct`, `link_potential_pct`, `local/remote_rx_rate_idx`).

⚠️ **Les courbes de la fiche équipement ne contredisent PAS cette politique** : leur série vit dans la table dédiée **`lr_metric_samples`** (buckets 5 min, cf. `lr_metric_history_service`), écrite par le même `persist_device_metrics` pour les seules clés de `GRAPH_METRICS`. C'est exactement pour ne pas rouvrir le robinet du bloat dans `device_metrics`. Donc : **vouloir une nouvelle courbe ne justifie JAMAIS d'ajouter une métrique à `HISTORY_METRICS`** — il faut l'ajouter à `GRAPH_METRICS`. La page « Liaisons clients » tourne en LIVE (`get_live_link_health`, fetch direct LTU/airOS), pas sur `device_metrics`. La matview `lr_health_metric_stats_30d` + son job de refresh ont été supprimés (migration `x5d6e7f8a9b0`). L'alert engine lit ses baselines (EMA throughput, deltas d'erreurs) depuis `AlertState`, **jamais** depuis `device_metrics` → collapser ne casse aucune alerte. Sans cette politique, un seul UISP Power empilait ~25 métriques toutes les 30 s (~70k lignes/jour) que rien ne relit.

### Device types reconnus
| `device_type` | Polling |
|---|---|
| `ltu_rocket` | Ping + SNMP (ath0/eth0) + API HTTP (signal, CCQ, CINR, CPE peers, distance) |
| `ltu_lr` | Ping + SNMP + Sonde transit SSH (ping internet depuis le device) |
| `uisp_switch` | Ping + SNMP standard (ports, vitesse, erreurs) |
| `uisp_power` | Ping + API REST (voltage, current, power, batterie) |

### Page /incidents = INFRASTRUCTURE uniquement (suppression côté client, 2026-06-09)
La page `/incidents` ne montre que les incidents **d'infrastructure**. Les incidents **côté client** ne sont **ni créés ni stockés** (purge DB via migration `z7f8a9b0c1d2`). Le découpage est **par device** (`rule_category`), **pas par alert_type** : les types radio (`signal_low`, `ccq_low`, `cinr_low`, `radio_link_degraded`, `high_rx_tx_errors`) se déclenchent à la fois sur les **Rockets de base station** (infra → gardés) et sur les **LR abonnés** (client → supprimés), donc filtrer sur la string `alert_type` masquerait de vraies alertes infra. Le garde-fou unique est `incident_service.is_suppressed_incident(device, alert_type)`, appelé en tête de `open_incident` (retourne `(None, False)` sans rien créer) — tous les appelants ne déréférencent l'incident que sous `if is_new`, donc un `None` est sûr. **`airmax_down` est infra** (Rocket airMAX = AP de base, pas le LiteBeam abonné). Exceptions explicites (cf. `alert_constants`) :
- `CLIENT_KEPT_ALERT_TYPES = {}` — **vide** (plus aucune exception « gardé même sur un LR »).
- `INFRA_DEVICE_SUPPRESSED_ALERT_TYPES = {cpe_disconnected, rocket_client_overload, lr_bridge_mode_misconfig}` — supprimés **toujours**, même sur un device infra : `cpe_disconnected` (un CPE qui disparaît = churn côté abonné, pas notre panne) ; **`rocket_client_overload` (saturation Rocket) est géré par la page `/capacity`** et **`lr_bridge_mode_misconfig` (LR en bridge) par la page `/access`** (politique 2026-06-25 — purge DB via migration `l9a0b1c2d3e4`) : ces deux-là sont surfacés sur leur page dédiée, jamais comme incident.

Conséquence : plus aucune notification ni ligne `alerts` pour les alertes client (signal/ccq/cinr/capacity sur LR, `lr_link_substandard`, `lr_no_transit`, `lr_latency_high`, `lr_discovered`/`lr_ip_changed`/`lr_reassigned`, `cpe_disconnected`). Les jobs continuent de sonder les LR (latence/transit/SSH) et d'incrémenter leurs `AlertState` ; seul l'incident final est court-circuité.

### 23 Alert types
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
| Performance | `high_rx_tx_errors` | Taux d'erreurs delta > seuil |
| Charge AP | `rocket_client_overload` | Rocket de base station saturé : clients connectés ≥ seuil. Seuil = **formule** par famille : base à 10 MHz + `rocket_overload_clients_per_10mhz` (défaut 5) clients par tranche de +10 MHz. Bases : LTU 15, airMAX 10 (configurables, page Seuils). Donc LTU 10→15 / 20→20 / 30→25… ; airMAX 10→10 / 20→15 / 40→25… Largeur auto-détectée en direct (arrondie au multiple de 10 MHz) : LTU via API `wireless.radios[0].channelWidth.tx`, airMAX via airOS `status.cgi` `wireless.chanbw` (lu dans `snmp_poll_job`, requiert les creds airOS sur la fiche). Clients = `len(all_peers)` (LTU) / stations SNMP `airmax_peers` (airMAX). Largeur < 10 MHz → pas de seuil → pas d'incident. **Override manuel par Rocket** : `rockets.max_clients_override` (Integer nullable, migration `f3a4b5c6d7e8`) — quand posé, il **remplace entièrement** la formule (s'applique même sans largeur connue) ; éditable depuis la page **Capacité** (drill-down par Rocket, colonne « Capacité max » → bouton « modifier », vide = retour auto). Injecté dans les métriques de la règle par `alert_engine` (comme `is_backhaul`) ; `_rocket_overload_threshold(settings, airmax, width, override)`. Préservé par le sync UISP. Critique, anti-flap 3 cycles |
| Disponibilité | `device_flapping` | Équipement d'infra qui flappe : > `FLAP_THRESHOLD_24H` (3) incidents de disponibilité sur `FLAP_WINDOW_HOURS` (24 h). **UISP Power exclus** (leurs cycles up/down sur coupure secteur sont normaux → couverts par `mains_power_lost`). Critique. **Pas** un type de disponibilité (se résout/purge normalement). `flap_detection_job` |
| Power | `battery_internal_low` | **Batterie INTERNE (Li-Ion UPS) < `BATTERY_INTERNAL_CRITICAL_PCT` (50%)** → critique + notif immédiate. Pas de message de rétabli (fermeture silencieuse). `power_poll_job` |
| Power | `battery_external_low` | **Batterie EXTERNE (banc plomb) < `BATTERY_EXTERNAL_CRITICAL_PCT` (30%)** → critique + notif immédiate. Pas de message de rétabli. `power_poll_job` |
| Power | `uisp_power_unreachable` | ⚠️ **Plus émis depuis 2026-06-11** : un UISP Power down est couvert par `device_unreachable` (ping). Type conservé pour le journal/legacy ; le job ferme silencieusement les incidents legacy. |
| Power | `battery_low_warning` / `battery_low_critical` | ⚠️ **Plus émis depuis 2026-06-11** (remplacés par `battery_internal_low` / `battery_external_low`). Fermés silencieusement par le job. |
| Power | `voltage_anomaly` | ⚠️ **Plus émis depuis 2026-06-11** (politique UISP Power : seules les 2 alertes batterie + down). Fermé silencieusement. |
| Power | `mains_power_lost` | Coupure secteur (SOMELEC) : UISP Power passé sur batterie (≥ `MAINS_LOSS_THRESHOLD` cycles). **Affiché dans /incidents mais NON notifié** (hors `WHATSAPP_ALERT_TYPES`). `power_poll_job` / `_evaluate_mains_power` |
| Switch | `switch_port_down` | Port **surveillé** DOWN. Un incident par switch listant tous les ports fautifs et l'équipement câblé sur chacun. Critique → WhatsApp immédiat |
| Switch | `switch_port_speed_low` | Port **surveillé** UP mais vitesse négociée < `port_min_speed_mbps` (1000). Même forme (1 incident/switch, ports + équipements + vitesses dans le message). `ifSpeed` = 0 (cage SFP) ⇒ ignoré : un débit inconnu n'est pas un débit dégradé. Critique → WhatsApp immédiat |
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
| GET | `/api/v1/devices/{id}/metric-history` | Oui | **Historique d'une courbe d'un équipement** (graphes « Plus d'infos » de la fiche). `?metric=` (clé de `GRAPH_METRICS`, **allowlist** — sinon 422 ; jamais passé au SQL) + soit `?period=24h\|7d\|30d`, soit `?start=&end=` (`YYYY-MM-DD` UTC, **fin incluse**, les deux ensemble sinon 422 — même convention que `/clients/consumption`) ; la plage l'emporte sur `period`. Renvoie `points[]` (avg/min/max/sample_count par bucket) + `bin_seconds` + `label`/`unit` + `threshold` et `threshold_direction` (`max` = alerte au-dessus (latence), `min` = alerte en dessous (capacité) ; seuil **effectif**, donc le graphe trace la même ligne que l'alerting) + `available_metrics` (les courbes que ce device possède). Série **vide** (pas une erreur) si pas d'historique. `lr_metric_history_service` |
| POST | `/api/v1/devices/{id}/check-ssh` | Oui | Test SSH vers le device |
| POST | `/api/v1/devices/{id}/check-ping` | Oui | Ping internet via SSH depuis le device |
| POST | `/api/v1/devices/{id}/block-client` | Oui | Bloque l'accès internet du client via SSH — body `mode`: `full` (shutdown port LAN) ou `whatsapp_only` (filtre iptables WhatsApp+DNS) |
| POST | `/api/v1/devices/{id}/unblock-client` | Oui | Rétablit l'accès internet complet du client (port LAN remonté + filtre WhatsApp retiré) |
| GET | `/api/v1/incidents` | Oui | Liste incidents (filtres: status, severity, device_id, alert_type) — lecture seule |
| GET | `/api/v1/incidents/{id}` | Oui | Détail incident — lecture seule |
| GET | `/api/v1/system` | Oui | Infos système (version, uptime scheduler) |
| POST | `/api/v1/system/test-whatsapp` | Oui | Diagnostic WhatsApp (Ultramsg) — envoie un message de test au groupe `WHATSAPP_GROUP_ID` |
| POST | `/api/v1/uisp/assign` | Oui | **Associe un équipement à un client CRM** — body `mac` + `crm_client_id`, plus `crm_service_id` **uniquement** si le client a plusieurs services. Équivalent du formulaire UISP (chercher la MAC en « unknown », choisir le client). Si l'équipement est absent du contrôleur, sa clé lui est posée d'abord et la réponse porte `pending_registration` (rejouer dans la minute — ce n'est pas une erreur). Rapport étape par étape. 400 MAC invalide · 404 client CRM introuvable **ou service n'appartenant pas à ce client** · **409 client à plusieurs services sans `crm_service_id`** (services renvoyés) · **409 équipement déjà rattaché à un AUTRE client** (id du détenteur renvoyé ; `reassign=true` pour passer outre) · 502 clé non posée (échec SSH — surtout pas un 404) · 403 token UISP sans droits d'écriture. Voir **Association client CRM** |
| POST | `/api/v1/uisp/sync` | Oui | Import des équipements d'infra depuis le contrôleur UISP (`?dry_run=true` = prévisualisation sans écriture). Renvoie un résumé (créés/màj/ignorés + échantillon) |
| GET | `/api/v1/network-capacity` | Oui | Capacité clients : par famille (LTU/airMAX) et par site, clients connectés (`peer_count`) vs max (seuil `rocket_client_overload`). Rockets sans largeur connue exclus des totaux (`unknown`). `network_capacity_service`. Inclut aussi la clé **`infra`** (`site_infra_service.get_site_infra_capacity`) : budget d'équipements infra par site (Rockets+AF60+PTP) vs `SITE_INFRA_MAX`, avec marge `remaining` signée |
| GET | `/api/v1/network-topology` | Oui | **Graphe INTER-SITES** (le maillage des backhauls). **Servi depuis NOTRE base — zéro appel au contrôleur.** Le câblage vient de la table `site_links` (rapatrié 1×/jour par `site_topology_sync_job`) ; la **santé** de chaque liaison est relue **en direct** depuis `devices`/`device_metrics`. Renvoie `synced_at` (date du **câblage** — l'état, lui, est de maintenant), `sites[]` (`depth` = couche, `parent`, `degree`, `reachable`, **`device_count`/`device_down_count`** = le compteur « 14/1 », **`is_down`** = site ENTIÈREMENT tombé, le seul cas qui rougit ses liaisons), **`latitude`/`longitude`/`position_source`** = position du pylône, jointe depuis `site_locations` sur la chaîne **exacte** du nom de site — `null` quand elle est inconnue, jamais devinée), `edges[]` (une **liaison logique** par paire de sites, portant 1..n `links[]` physiques, `redundant`, `is_tree_edge`, `health`), `layout` (`components`, `orphan_sites`, `unreached_sites`, `extra_edges`) et `stats`. `?root=` sinon `TOPOLOGY_ROOT_SITE`. `available:false` tant que le câblage n'a jamais été synchronisé — carte **absente** plutôt que vide (une carte vide se lit comme un réseau sans liaisons). Voir **Topologie inter-sites** |
| POST | `/api/v1/network-topology/sync` | Oui | **Rapatrie le câblage maintenant** (le seul chemin de ce module qui parle à UISP), sans attendre le job quotidien — après une intervention terrain. **502** si le contrôleur est injoignable ; la table reste alors intacte |
| GET | `/api/v1/traffic/top-destinations` | Oui | **Volume** Internet par opérateur/CDN (ASN) sur `?period=24h\|7d\|30d` : SUM(down/up) GROUP BY asn depuis `traffic_dest_stats`, trié par total + part %. `traffic_service.get_top_destinations` |
| GET | `/api/v1/traffic/throughput` | Oui | **Débit** (Gb/s) par opérateur sur le dernier bucket : descendant/montant Mbps + part du download. Montre le partage de la bande passante WAN en direct. `traffic_service.get_throughput` |
| GET | `/api/v1/traffic/throughput-history` | Oui | **Historique de débit** descendant par opérateur sur `?period=1h\|6h\|24h` : re-bin des buckets 1 min (top-N opérateurs + « Autres »), séries alignées pour un graphe d'aires empilées. `traffic_service.get_throughput_history` (SQL `date_bin`) |
| POST | `/api/v1/devices/{id}/enroll-uisp` | Oui | **Enrôle un LR dans UISP** en posant la clé du contrôleur par SSH (sans reboot ni coupure). `ok` = contrôleur ayant **adopté** l'équipement, constaté sur l'équipement. Sans effet sur un LR déjà provisionné pour ce contrôleur ; body `force` passe outre (clé orpheline seulement — sur un équipement sain, forcer le dé-enrôle). 409 si `UISP_DEVICE_KEY` absente. Cf. **Enrôlement UISP** |
| POST | `/api/v1/access-diagnostics/enroll-uisp` | Oui | Même chose **en lot** sur les LR vus par radio mais absents de UISP. Body `lr_ids` (vide = toute la population) + `force`. Séquentiel, concurrence SSH bornée : compter jusqu'à 45 s par équipement |
| GET | `/api/v1/access-diagnostics` | Oui | **Deux anomalies d'accès abonné** : `ssh_refused` (LR encore `up` dont `lrs.ssh_status` ∈ {`auth_failed`,`ssh_disabled`,`host_key_mismatch`}) + `radio_not_in_uisp` (LR `last_discovered_at`≠NULL **et** `uisp_synced_at`=NULL = vu par radio mais non provisionné dans UISP) + `counts`. `access_diagnostics_service` |
| POST | `/api/v1/fai/block` | FAI | Bloque un client par **MAC** de son LR (système de paiement). Body `mac` + `reason` + `mode` (`full`/`whatsapp_only`) + **`user`** (l'**agent** à l'origine de l'ordre — cf. **Agent d'une action FAI**). Même mécanisme que `/devices/{id}/block-client`, indexé par MAC. 409 si LR en bridge |
| POST | `/api/v1/fai/unblock` | FAI | Débloque un client par **MAC** de son LR. Body `mac` + **`user`** (idem) |
| GET | `/api/v1/fai/status` | FAI | État de blocage actuel d'un client par **MAC** (lecture seule, ne touche pas au LR) |
| GET | `/api/v1/router-rules` | Oui | **Ce que le ROUTEUR DE CŒUR porte vraiment** — les règles `chain=forward action=drop` ciblant une MAC, lues **en direct** par l'API RouterOS à chaque appel (aucun cache, aucun job). Auth normale (**pas** la clé FAI : le système de paiement demande des coupures, il ne lit pas l'état du réseau). Chaque règle est **croisée avec l'inventaire** (nom/site/IP) et avec l'intention en base → `state` : `expected` (coupure voulue), **`unexpected`** (la base ne veut plus couper ce client : il a payé et reste hors ligne), `unknown` (MAC hors inventaire — système historique, ou LR déprovisionné). Plus l'écart **inverse** dans `missing[]` : la base croit le client coupé par le routeur, le routeur n'a rien → il navigue. Ces deux écarts sont invisibles autrement, le renforcement ne parlant au routeur que **sur transition**. `origin` (`supervisor`/`legacy`) vient de la marque du commentaire (`mikrotik_service.is_supervisor_comment`) — un indice, pas une preuve. **502** si le routeur est configuré mais injoignable : une liste vide se lirait « aucun client bloqué ». `available:false` = repli désactivé. **Lecture seule** (retirer une règle ici serait annulé par le renforcement au cycle suivant). `router_rules_service` |
| GET | `/api/v1/fai/verify` | **Verify** | **Contrôle pré-vol LIVE d'un LR par MAC** pour un système tiers. **Teste l'équipement en direct par SSH au moment de l'appel** (PAS les colonnes de sonde) : `ssh_service.verify_lr_live` ouvre une session avec le mot de passe attendu **uniquement** (`fai_expected_lr_ssh_password`, défaut `A2HQ@87654321`, pas de fallback) et lit `netmode` dans `system.cfg` sur la même session. La seule lecture DB est la résolution MAC → équipement (IP/nom/creds). **Clé API DÉDIÉE `LR_VERIFY_API_KEY`** (router `fai_verify.py` séparé, `require_verify_client`) : scellée à cette seule route — n'ouvre pas block/unblock/status ; repli accepté sur l'auth /fai (FAI_API_KEY / master / session). 4 contrôles : **existe** (sinon `KO`, `name=null`, **200 pas 404**), **ssh_active** (poignée de main SSH aboutit maintenant), **password_valid** (auth live réussie avec `A2HQ@87654321` — distinct de ssh_active : daemon qui répond mais mauvais mdp ⇒ ssh_active=true/password_valid=false), **router_mode** (`netmode=="router"` en direct — **même clé sur LTU et airOS**, valider sur `netmode` PAS sur les `bridge.*` internes VLAN). ⚠️ **LR éteint / SSH injoignable ⇒ `KO` ssh_active=false** (un live ne se prononce pas sur ce qu'il ne joint pas) ; l'appel dure une poignée de main SSH (quelques s). Renvoie `ok`/`status` (`OK`/`KO`) + `name` + `reason` + `checks{}` + brut `ssh_status`/`topology_mode`/`ssh_checked_at` (= instant du test). 400 si MAC mal formée |

### Frontend Next.js
| Page | Chemin | Contenu |
|---|---|---|
| Devices | `/devices` | Liste avec statut, dernière vue, métriques, modal détail. Sur un **LR**, un **AF60** et un **switch** (courbe de son port fibre), la fiche expose un bouton **« Plus d'infos — graphes d'historique »** (`MetricHistoryModal`) : courbes SVG sur 24h/7j/30j ou une plage de dates, avec **onglets** pilotés par `available_metrics` (latence Internet, capacité du lien, potentiel du lien, capacités DL/UL, débits DL/UL). Bande min/max (garde visible un pic court noyé par la moyenne du bucket), ligne de seuil (au-dessus ou en dessous selon `threshold_direction`), survol détaillé, chiffres clés, et la **cadence réelle** du relevé affichée (elle est dictée par la durée d'un tour de poll, pas par le graphe). **Les trous = périodes sans mesure**, pas des 0. Source : `/devices/{id}/metric-history` |
| Accès clients | `/access` | Table des LR abonnés (source UISP). Filtres dont **« Hors supervision »** : LR sans IP **et** non vu par UISP depuis `OUT_OF_SUPERVISION_DAYS` — badge ambre, **exclu du compteur « Accès actif »** (la tuile indique combien sont exclus). Distinct de « Hors ligne > 1 mois » (`long_offline`, absence prolongée vue par UISP) : ici c'est une absence de **mesure**, pas une absence constatée |
| Anomalies détectées | `/incidents` | Anomalies actuellement détectées (lecture seule, résolution automatique) |
| Capacité du réseau | `/capacity` | 2 cercles (LTU/airMAX) consommé vs disponible sur tout le réseau + barres par site (LTU/airMAX séparés) ; clic site → table Rockets (connectés/max + largeur). Donut SVG custom (pas de lib de charts). Inclut la section **« Capacité infra par site »** (table Site/Équip. infra/Max/Marge, marge +N vert / -N rouge) alimentée par la clé `infra` de `/network-capacity` |
| Topologie du réseau | `/topology` | **Graphe inter-sites** — sites rendus par l'icône de pylône (`public/devices/antenne.png` ; ⚠️ **dans `devices/`** car le middleware d'auth intercepte tout sauf ce dossier — ailleurs l'image serait redirigée vers `/login`). **Pas de `refreshInterval`**, contrairement aux autres pages : elle affiche du **câblage**, pas des métriques vivantes. Affiche la date du dernier rapatriement du câblage, distincte de l'état des équipements qui est de maintenant. **Écran dépouillé** : le graphe seul (ni tuiles, ni légende, ni liste des liaisons — le détail d'un lien est au survol). **Pleine largeur + menu replié à l'arrivée** (`FULL_WIDTH_ROUTES` dans `AppShell` : la colonne perd son `max-w-6xl` et la barre latérale se masque). Le repli se commande par un bouton dans l'en-tête du menu, et un bouton flottant le ramène quand il est masqué — **jamais un clic n'importe où** : le graphe est lui-même cliquable (sélection d'un site), un basculement au moindre clic ferait disparaître le menu par accident. L'effet est clé sur `pathname` seul, donc un repli/dépli manuel n'est pas écrasé tant qu'on reste sur la page. Sous chaque site, le compteur **« 14/1 »** (équipements d'infra / en panne, la part rouge). ⚠️ **Aucun bloc d'anomalies sous la carte** : sites sans liaison, composantes séparées et extrémités non supervisées restent **exacts dans la réponse d'API** (`layout.orphan_sites`, `layout.components`, `stats.unsupervised_ends`) et **visibles sur le dessin** (un site orphelin y est dessiné, simplement flottant) ; `scripts/dump_site_topology.py` continue de les nommer en clair. Rendu SVG **en couches** (jamais en arbre — le graphe porte de vraies boucles), nœuds cliquables pour filtrer les liaisons d'un site, liaisons colorées par **notre** mesure (vert au-dessus du plancher / ambre dégradée / rouge hors service / **gris non mesurée**), boucles de redondance en pointillé. Sous le graphe : la liste des liaisons avec leurs liens physiques et l'état de chaque bout, puis la section **« Ce que la carte ne montre pas »** (sites sans liaison, composantes séparées, liaisons sans mesure, extrémités non supervisées). Complète `SiteTopology` (intra-site, sur `/sites`). **Bascule Graphe / Carte** dans l'en-tête → voir **Vue carte de la topologie**. Source : `/network-topology` |
| Destinations Internet | `/traffic` | 3 sections : **Débit en direct** (descendant/montant Gb/s + partage par opérateur, `/traffic/throughput`, refresh 30 s), **Débit descendant par opérateur** (graphe d'aires empilées SVG sur 1h/6h/24h, `/traffic/throughput-history`) et **Volume** (par opérateur sur 24h/7j/30j, down/up/total + part, `/traffic/top-destinations`). Repère les candidats à un serveur de cache. **Vide tant que `NETFLOW_COLLECTOR_ENABLED=false` ou que le routeur n'exporte pas vers le collecteur** |
| Règles du routeur | `/router-rules` | Sous **FAI** dans la barre latérale (à côté du Journal des blocages). Les coupures d'abonnés **réellement posées sur le routeur de cœur**, lues en direct à l'ouverture. Complète les deux autres vues du blocage : le **journal** dit ce qui s'est passé, la **base** ce qu'on croit avoir posé, celle-ci ce que le routeur porte **maintenant** — la seule qui réponde à « ce client a payé, pourquoi est-il coupé ? ». Tuiles (règles, coupés à tort, MAC inconnues, coupures manquantes, posées par nous), bloc rouge des **coupures absentes du routeur**, table filtrable (client/MAC/site/état/origine/trafic jeté/commentaire). ⚠️ **Pas de `refreshInterval`** (comme `/topology`, et pour une raison plus forte) : chaque chargement ouvre une session API RouterOS — le clic dans le menu **est** la demande, et le bouton « Actualiser » rejoue la lecture. Une règle **désactivée** est listée mais marquée « ne coupe pas » (elle explique un client bloqué toujours en ligne). Source : `/router-rules` |
| Diagnostics d'accès | `/access-diagnostics` | 2 sections d'anomalies de gestion du parc abonné (sidebar **Anomalies**) : **LR qui refusent le SSH** (mot de passe invalide / SSH désactivé / clé d'hôte incompatible — les **offline sont exclus**, ce n'est pas un refus) et **découverts par radio mais absents de UISP** (non provisionnés, potentiellement non facturés). Source : `/access-diagnostics`. La 1re remplace côté UI l'ancien diag SSH par grep de logs. La 2e porte l'**action d'enrôlement** : bouton par ligne + « Tout enrôler dans UISP », avec un interrupteur **Forcer** décoché par défaut (il écrase une clé existante — cf. **Enrôlement UISP**). Une ligne déjà enrôlée affiche la date au lieu du bouton : elle attend le sync quotidien |

### À implémenter (prochaines phases)
- [ ] Tests unitaires et d'intégration
- [ ] Config nginx pour la production (reverse proxy)

## Déploiement production (serveur physique)

Le système est prévu pour être déployé sur un serveur physique après validation maquette.

### Points d'attention pour la production
- Mettre `APP_ENV=production` dans le `.env` du serveur → uvicorn sans `--reload`, avec workers
- **Scheduler isolé en prod** : `docker-compose.prod.yml` ajoute des containers dédiés (`RUN_MODE=scheduler`, `SCHEDULER_ENABLED=true`) qui exécutent APScheduler en process séparés, un par `SCHEDULER_GROUP` : **`scheduler`** (`fast` — disponibilité de l'INFRA + maintenance), **`scheduler-heavy`** (`heavy` — SSH : sonde LR, blocage + snmp/power/uisp_sync/switch_port), **`scheduler-ping-lr`** (`ping-lr` — le ping des LR clients, SEUL), et **un container par poll fan-out** : **`scheduler-poll-switch`** (`poll-switch` — le SNMP des switches, isolé depuis le 2026-08-11 : servis en dernier derrière ~100 radios, ils ont passé 14 h sans écriture, rendant `switch_port_down`/`fiber_link_down` aveugles), **`scheduler-poll-af60`** (`poll-af60`), **`scheduler-poll-ltu`** (`poll-ltu`), **`scheduler-poll-airos`** (`poll-airos`). Le `backend` tourne avec `SCHEDULER_ENABLED=false` et peut scaler à `UVICORN_WORKERS>1` sans dupliquer les jobs (sinon chaque worker démarrerait son propre scheduler → SSH/alertes en double). Les migrations Alembic restent gérées par le container `backend` ; les schedulers attendent `backend: service_healthy` avant de démarrer. **Le découpage des groupes est dans `jobs.py` (`_JOBS_BY_GROUP`)** : un job non classé est conservé en `fast` (jamais perdu silencieusement). **`ping-lr` est séparé de `fast`** parce que le sweep LR (~600 hôtes) lance des centaines de sous-process `ping` à la re-confirmation quand beaucoup de LR sont down côté abonné (normal) : dans le même process, cette rafale disputait le CPU au sweep infra, seule source d'incidents. **Les 3 polls HTTP (ltu/airos/af60) ont été SORTIS de `heavy` en process séparés (2026-08-03)** : leurs fetches HTTP async y étaient affamés par les threads SSH (`lr_internet_probe`, paramiko tient le GIL pendant sa crypto), et la phase 2 de ltu/airos (persistance de CENTAINES de LR en série, tours de 7-22 min mesurés) affamait AF60 dont le travail est minuscule → sa courbe débit/capacité n'avait qu'un point toutes les ~3 min. Un process par job = aucune famine croisée : **AF60 seul tombe à quelques secondes/tour (1 pt/min)** ; ltu/airos restent lents par leur propre phase 2 (à optimiser séparément) mais ne ralentissent plus personne.
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

# Déploiement standard (11 conteneurs : postgres + backend + frontend +
# scheduler[fast] + scheduler-heavy + scheduler-ping-lr + scheduler-poll-af60 +
# scheduler-poll-ltu + scheduler-poll-airos + scheduler-poll-switch + netflow-collector). Le backend
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
