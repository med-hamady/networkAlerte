# Network Supervisor — Document de passation

> **Objet.** Ce document recense **toutes les fonctionnalités** du système de supervision
> réseau A2 Connect et, pour chacune, **les fichiers de code qui la portent**. Il est écrit
> pour l'équipe qui reprend l'exploitation et la maintenance après le départ de l'auteur.
>
> **État au 8 septembre 2026.** Couverture vérifiée : **100 % des services, endpoints,
> schémas et pages** du dépôt sont référencés ici, et **tous les chemins cités ont été
> contrôlés comme existants**.

---

## Comment utiliser ce document

### Les trois usages prévus

| Ce que tu cherches | Où aller |
|---|---|
| « Comment marche telle fonctionnalité, et dans quels fichiers ? » | Les **sections 1 à 19**, dans l'ordre du métier |
| « J'ouvre `xxx_service.py`, à quoi ça sert ? » | **Annexe A — Index inversé fichier → fonctionnalité** |
| « Il se passe tel problème, où je regarde ? » | **Annexe B — Runbook : symptôme → où regarder** |
| « C'est quoi un LR, un Rocket, un CPE ? » | **Annexe C — Glossaire** |

### Le gabarit de chaque fonctionnalité

Chaque fonctionnalité est présentée de la même façon, toujours dans cet ordre :

> **Ce que ça fait** — en une ou deux phrases, sans jargon.
> **Fichiers** — les chemins exacts, backend puis frontend.
> **À savoir** — le piège ou la règle non devinable, celui qui coûte cher si on l'ignore.

### Les conventions de lecture

| Marque | Sens |
|---|---|
| ⚠️ | **Règle non devinable.** Presque toutes viennent d'un incident réel : la casser reproduit la panne. C'est la partie la plus importante du document. |
| *(legacy)* | Conservé pour l'historique, **plus émis / plus utilisé**. Ne pas s'appuyer dessus. |
| **gras** | Le mot qui porte la décision. |

### Où se trouve le reste de l'information

Ce document est **la référence de reprise** : il dit ce que fait chaque fonctionnalité, où
elle vit, et quelles règles ne pas casser. Pour aller plus loin sur un point précis, la
source est **le code lui-même** :

| Besoin | Où chercher |
|---|---|
| Le *pourquoi* d'une décision, la mesure ou l'incident qui l'a motivée | La **docstring du module** concerné (voir Annexe A pour le retrouver) |
| Le comportement exact d'une règle | Le fichier de règle + son fichier de test (§17) |
| Mettre en route un environnement de développement | `docs/getting-started.md` |
| Intégrer un système tiers | `docs/API_BLOCAGE_CLIENT.md`, `docs/api-content-filter.md`, `docs/api-fai-verify.md`, `docs/api-uisp-assign.md` |

⚠️ **Convention du projet** : les commentaires et docstrings du code sont en **français** et
**denses volontairement**. Beaucoup portent un verdict de terrain (« vérifié tel jour sur
tel équipement, ne pas re-tenter »). Ce ne sont pas des commentaires décoratifs : ils
évitent de refaire des investigations qui ont pris des jours. **Ne pas les supprimer lors
d'un refactor.**

---

## Par où commencer — parcours de reprise

Un ordre de lecture testé pour prendre le système en main sans se noyer. Chaque étape
suppose la précédente.

### Étape 1 — Comprendre le métier avant le code *(une demi-journée)*

1. **§1 Vue d'ensemble** — ce que le système surveille et pourquoi.
2. **Annexe C — Glossaire** — le vocabulaire (LR, Rocket, CPE, backhaul, AF60…). Sans lui,
   le reste est illisible.
3. Ouvrir le dashboard `https://10.135.3.25/` et cliquer dans **toutes** les pages
   (§15 en donne la liste et le rôle de chacune).

### Étape 2 — La colonne vertébrale *(une journée)*

4. **§2 Socle applicatif** — comment l'app démarre, et surtout **§2.2 les 5 clés API**.
5. **§16 Déploiement** — les 12 conteneurs et **pourquoi** ils sont éclatés. C'est la
   première chose qu'on casse quand on ne la connaît pas.
6. **§3 Inventaire et découverte** — l'identité d'un équipement est sa **MAC**. Tout le
   reste en découle.

### Étape 3 — Le cycle de la donnée *(une journée)*

7. **§4 Collecte de métriques**, en particulier **§4.0** (la politique *history* vs
   *collapse*) — c'est là qu'on fait exploser la base si on se trompe.
8. **§5 Disponibilité** puis **§6 Moteur d'alertes** — comment une mesure devient un
   incident, puis un message.
9. **§7 Notifications** — et surtout la **liste blanche** : la plupart des alertes
   n'envoient rien, volontairement.

### Étape 4 — Avant la première intervention sur la production

10. **§19 Les règles à ne jamais casser** — à lire en entier, puis à relire.
11. **§12 FAI** — c'est la partie qui **coupe l'accès de vrais abonnés**. Chaque garde-fou
    y a été ajouté après un incident.
12. **Annexe B — Runbook** — pour savoir où regarder quand quelque chose arrive.

### Le réflexe à prendre

Avant de modifier un comportement : **chercher le `⚠️` correspondant dans ce document, puis
la docstring du module dans le code**. Si une règle paraît absurde, c'est généralement
qu'elle résout un problème qu'on n'a pas encore rencontré.

---
## Table des matières

### Les fonctionnalités, dans l'ordre du métier

1. [Vue d'ensemble](#1-vue-densemble)
2. [Socle applicatif](#2-socle-applicatif)
3. [Inventaire des équipements et découverte](#3-inventaire-des-équipements-et-découverte)
4. [Collecte de métriques](#4-collecte-de-métriques)
5. [Disponibilité et supervision](#5-disponibilité-et-supervision)
6. [Moteur d'alertes et incidents](#6-moteur-dalertes-et-incidents)
7. [Notifications WhatsApp et rapports](#7-notifications-whatsapp-et-rapports)
8. [Capacité du réseau](#8-capacité-du-réseau)
9. [Topologie du réseau](#9-topologie-du-réseau)
10. [Trafic Internet (NetFlow)](#10-trafic-internet-netflow)
11. [Clients : consommation, forfaits, signal, carte](#11-clients--consommation-forfaits-signal-carte)
12. [FAI : blocage, filtrage, journal, routeur](#12-fai--blocage-filtrage-journal-routeur)
13. [Intégration UISP](#13-intégration-uisp)
14. [Diagnostics et hygiène du parc](#14-diagnostics-et-hygiène-du-parc)
15. [Frontend — pages du dashboard](#15-frontend--pages-du-dashboard)
16. [Déploiement et exploitation](#16-déploiement-et-exploitation)
17. [Tests](#17-tests)
18. [Scripts d'administration](#18-scripts-dadministration)
19. [Les règles à ne jamais casser](#19-les-règles-à-ne-jamais-casser)

### Les annexes de travail quotidien

- [Annexe A — Index inversé : fichier → fonctionnalité](#annexe-a--index-inversé--fichier--fonctionnalité)
- [Annexe B — Runbook : symptôme → où regarder](#annexe-b--runbook--symptôme--où-regarder)
- [Annexe C — Glossaire](#annexe-c--glossaire)
- [Annexe D — Inventaire des documents et fichiers particuliers](#annexe-d--inventaire-des-documents-et-fichiers-particuliers)

---

## 1. Vue d'ensemble

### Ce que fait le système

Superviser un réseau d'opérateur (WISP) bâti sur du matériel Ubiquiti / UISP :
~1000 abonnés (LTU LR, LiteBeam airMAX), ~100 stations de base (Rockets), 17 sites reliés
par des backhauls AF60 et de la fibre, des switches UISP et des onduleurs UISP Power.
Il détecte les pannes, mesure la qualité, alerte sur WhatsApp, et sert d'outil de
coupure/rétablissement au système de facturation.

### Stack

| Couche | Technologie | Emplacement |
|---|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy async, asyncpg | `backend/app/` |
| Frontend | Next.js (App Router), React, Tailwind | `frontend/` |
| Base | PostgreSQL 16 (+ fonctions SQL `fn_*` et matviews) | définies dans les migrations |
| Migrations | Alembic async — **83 migrations** | `backend/alembic/versions/` |
| Ordonnanceur | APScheduler, éclaté en **7 groupes de process** | `backend/app/tasks/` |
| Réseau | pysnmp-lextudio, paramiko (SSH), httpx | `backend/app/services/` |
| Infra | Docker Compose, nginx | racine + `nginx/` |

### Serveur de production

**`10.135.3.25`**, sur le LAN, derrière un FortiGate 40F.
Dashboard : `https://10.135.3.25/`. **12 conteneurs** en production (voir §16).

### Carte mentale du dépôt

```
backend/app/
├── main.py              app factory + lifespan
├── core/                config, logs, exceptions, CONSTANTES D'ALERTE
├── api/
│   ├── router.py        montage + QUELLE CLÉ OUVRE QUELLE ROUTE
│   ├── deps.py          les 5 dépendances d'authentification
│   └── endpoints/       25 modules de routes
├── models/              ORM SQLAlchemy (14 modèles)
├── schemas/             validation Pydantic
├── services/            TOUTE LA LOGIQUE MÉTIER (47 services)
├── tasks/               jobs.py (l'ordonnanceur) + 3 entrées de process
└── db/                  session async

frontend/
├── app/                 18 pages (App Router)
├── components/          29 composants
└── lib/                 client API, types, barèmes de couleurs partagés
```

---

## 2. Socle applicatif

### 2.1 Démarrage de l'application

**Ce que ça fait** — Fabrique l'app FastAPI, monte les routers, branche le cycle de vie
(démarrage/arrêt de l'ordonnanceur selon `RUN_MODE`).

**Fichiers**
- `backend/app/main.py` — app factory + lifespan
- `backend/app/api/router.py` — montage sous `/api/v1` **et attribution des dépendances d'authentification** (c'est ici qu'on lit quelle clé ouvre quelle route)
- `backend/app/core/config.py` — toute la configuration (`Settings`, pydantic-settings)
- `backend/app/core/logging.py` — logs structurés vers stdout
- `backend/app/core/exceptions.py` — `AppException` + handlers globaux
- `backend/app/db/base.py` — `DeclarativeBase` avec `id` / `created_at` / `updated_at`
- `backend/app/db/session.py` — engine async, `get_db()`, `async_session_factory()`

**À savoir** — `RUN_MODE` décide du rôle du conteneur : `api` (uvicorn + migrations
Alembic), `scheduler` (APScheduler seul), `collector` (collecteur NetFlow). Une seule image
Docker, trois entrées :
`backend/app/main.py`, `backend/app/tasks/runner.py`, `backend/app/tasks/collector_runner.py`.

### 2.2 Authentification — trois populations distinctes

**Ce que ça fait** — Le dashboard est protégé par login + session serveur ; les systèmes
tiers entrent par des **clés API cloisonnées, une par pouvoir**.

**Fichiers**
- `backend/app/api/deps.py` — **le fichier central** : `require_user_or_api_key`, `require_fai_client`, `require_verify_client`, `require_uisp_assign_client`, `require_content_block_client`, `_validate_production_secrets`
- `backend/app/services/auth_service.py` — bcrypt + sessions serveur (le cookie porte un jeton opaque ; la base n'en stocke que le SHA-256, donc une fuite de base ne permet pas de forger un cookie)
- `backend/app/api/endpoints/auth.py` — `/auth/login`, `/logout`, `/me`, `/change-password`
- `backend/app/models/user.py`, `backend/app/models/auth_session.py`
- `backend/app/schemas/auth.py`
- `frontend/app/login/page.tsx`
- `frontend/app/api/proxy/[...path]/route.ts` — proxy Next.js qui porte le cookie vers le backend
- `backend/scripts/create_admin.py` — création du premier compte admin
- `backend/tests/test_uisp_assign_scoped_key.py`, `backend/tests/test_content_filter_api.py` — verrous d'isolation des clés

**Les 5 clés API et leur portée** (⚠️ elles doivent **toutes porter des valeurs
distinctes** — `_validate_production_secrets` **refuse de démarrer** sinon, car une
duplication annulerait le cloisonnement en silence) :

| Clé | Ouvre | Tenue par |
|---|---|---|
| `API_KEY` | tout (clé maîtresse) | nous |
| `FAI_API_KEY` | `/fai/block`, `/fai/unblock`, `/fai/status` | système de paiement |
| `LR_VERIFY_API_KEY` | `GET /fai/verify` seul | système de vérification |
| `UISP_ASSIGN_API_KEY` | `POST /uisp/assign` seul | système d'adoption d'équipements |
| `CONTENT_BLOCK_API_KEY` | `/content-filter/*` seul | système d'options de filtrage |

**À savoir** — Une dépendance de router FastAPI est **additive et non surchargeable par
route**. C'est pourquoi `/fai/verify`, `/uisp/assign` et `/content-filter` vivent dans des
**routers séparés** (`fai_verify.py`, `uisp_assign.py`, `content_filter.py`) : c'est la
seule façon de cloisonner sans ouvrir les routes voisines. Ne pas les fusionner.

### 2.3 Seuils configurables à chaud

**Ce que ça fait** — Les seuils d'alerte sont modifiables depuis le dashboard sans
redéploiement : stockés en base, **superposés** aux valeurs d'environnement.

**Fichiers**
- `backend/app/services/threshold_service.py` — `get_effective_settings()` → objet `MergedSettings`, interface identique à `Settings`, donc compatible avec `alert_rules.py` et `alert_engine.py`
- `backend/app/models/system_setting.py`
- `backend/app/api/endpoints/system.py` — `GET/PATCH /system/thresholds`, `DELETE /system/thresholds/{key}`
- `frontend/app/settings/page.tsx` — page « Seuils »
- `frontend/lib/useThresholds.ts`

**À savoir** — ⚠️ Tout job qui évalue une règle doit lire ses seuils via
`threshold_service.get_effective_settings(session, base_settings)`, **jamais** directement
`get_settings()` — sinon la valeur réglée dans l'UI est silencieusement ignorée.

### 2.4 Logique métier centralisée en base (RPC SQL)

**Ce que ça fait** — Les pages d'agrégation (dashboard, sites, accès, coupures) ne
calculent rien en Python ni en React : elles appellent une fonction SQL qui renvoie du
`jsonb` prêt à afficher.

**Fichiers**
- `backend/app/api/rpc.py` — helper `scalar_json` (asyncpg renvoie le `jsonb` en **chaîne** ; sans parsing, FastAPI double-encode)
- `backend/app/api/endpoints/dashboard.py` → `fn_dashboard_summary()`, `fn_network_health()`
- `backend/app/api/endpoints/sites.py` → `fn_site_overview()`
- `backend/app/api/endpoints/access.py` → `fn_access_clients()`
- `backend/app/api/endpoints/network_uptime.py` → fonctions du journal des coupures
- Les fonctions SQL elles-mêmes sont **définies dans les migrations** : `backend/alembic/versions/`

**À savoir** — ⚠️ **Le frontend REND, il ne ré-agrège pas.** Si un compteur est faux, la
correction va dans la fonction SQL (donc dans une **nouvelle migration**), pas dans le
composant React. Modifier une fonction `fn_*` demande une migration qui la remplace en
entier (`CREATE OR REPLACE FUNCTION`).

### 2.5 Journal d'audit et détection d'anomalie de sécurité

**Ce que ça fait** — Chaque appel d'API modifiant l'état est tracé ; un job compte les
mutations par IP et alerte si une IP en produit un flot anormal.

**Fichiers**
- `backend/app/models/audit_log.py`
- `backend/app/tasks/jobs.py` → `security_anomaly_detection_job`
- `backend/app/services/notification_service.py` → `notify_security_event`

**À savoir** — Né de l'**incident du 2026-05-17** : 15 h de scan automatisé passées
inaperçues, inventaire vidé via un proxy ouvert + nginx exposé publiquement. Rapport
complet : `docs/incident-2026-05-17-rapport.md`.
⚠️ **Ne JAMAIS re-binder nginx sur `0.0.0.0`** — c'est la cause racine de cet incident.
⚠️ Les alertes de sécurité **ne sont pas dans la liste blanche WhatsApp** : elles ouvrent un
incident mais ne partent nulle part. À réexaminer.

---

## 3. Inventaire des équipements et découverte

### 3.1 Modèle de données des équipements

**Ce que ça fait** — Une table `devices` avec héritage (Rocket, Lr, UispSwitch, UispPower,
AF60, PTP LiteBeam). **L'identité d'un équipement est sa MAC**, jamais son IP ni son nom.

**Fichiers**
- `backend/app/models/device.py` — la table et toutes ses sous-classes
- `backend/app/schemas/device.py` — validation I/O + champs calculés (`is_out_of_supervision`)
- `backend/app/services/device_service.py` — CRUD
- `backend/app/api/endpoints/devices.py` — toutes les routes `/devices` (22 routes)
- `frontend/components/DeviceFormModal.tsx`, `DeviceDetailModal.tsx`, `DeviceCard.tsx`, `DeviceImage.tsx`, `DeviceSearchBar.tsx`

**Types reconnus** : `ltu_rocket`, `ltu_lr`, `uisp_switch`, `uisp_power`, `airfiber`
(AF60), `ptp_litebeam`, plus les variantes airMAX.

**À savoir** — ⚠️ Ne **jamais** faire un `UPDATE` en masse sur une sous-classe en filtrant
sur une colonne de la table parente `devices` : SQLAlchemy génère un SQL invalide → 500.

### 3.2 Découverte automatique des abonnés (par radio)

**Ce que ça fait** — L'opérateur n'enregistre **que les Rockets**. À chaque cycle, le
Rocket annonce ses CPE ; ce service transforme cette liste éphémère en lignes `lrs`
persistantes et suit les déménagements d'abonnés.

**Fichiers**
- `backend/app/services/discovery_service.py` — point d'entrée unique `reconcile_peers()`, plus `is_management_ip`, `pick_management_ip`, `_release_ip_if_held`, `_mac_held_by_non_lr`, `_peers_in_lock_order`
- `backend/app/tasks/jobs.py` — appelé depuis `ltu_api_poll_job` et `airos_api_poll_job`
- `backend/tests/test_discovery_service.py`, `test_discovery_lock_order.py`, `test_management_ip_guard.py`
- `frontend/components/LrDiscoveryModal.tsx`

**À savoir**
- ⚠️ **L'identité d'un LR est sa MAC.** Identifier par IP avait produit un « thrashing »
  (création/suppression en boucle). Résolu le 2026-06-09.
- ⚠️ **Allowlist du plan d'adressage** (`MANAGEMENT_IP_CIDRS`, défaut `10.135.0.0/16`) :
  une radio annonce **aussi** son LAN d'usine (`192.168.1.20`, `172.16.0.1`…), identique
  sur des dizaines de CPE. Comme `ip_address` est **UNIQUE**, chaque écriture **volait la
  ligne** d'un autre client et l'éteignait — un CPE éteignait un **autre** abonné sain.
- ⚠️ **Ordre de verrouillage** : toute boucle séquentielle qui écrit des lignes `devices`
  doit les parcourir par **`id` croissant**, sinon interblocages Postgres (**103 en 24 h**
  constatés le 2026-08-11). Verrouillé par
  `backend/tests/test_snmp_poll_device_isolation.py` (test `test_phase_two_iterates_devices_in_a_stable_order`).

### 3.3 Découverte LAN côté client (modems)

**Ce que ça fait** — Ouvre une session SSH sur le LR, lit sa table de routage, ping-sweepe
**uniquement le sous-réseau client** (celui qui ne contient pas la passerelle par défaut),
puis lit `/proc/net/arp` et renvoie les voisins pour que l'opérateur désigne le modem.

**Fichiers**
- `backend/app/services/lan_discovery.py`
- `backend/app/api/endpoints/devices.py` → `POST /devices/{id}/discover-modems`

**À savoir** — Le sous-réseau de la passerelle est le backbone de management (partagé par
tous les LR, souvent un /16) : le balayer serait interminable et n'y trouverait jamais un
modem client.

---

## 4. Collecte de métriques

Cinq chemins de collecte, **un par famille de matériel**, chacun avec ses pièges. Ils
convergent tous sur un point d'écriture unique.

### 4.0 Le point d'écriture unique — politique *history* vs *collapse*

**Ce que ça fait** — Décide, pour chaque métrique, si on **empile** une ligne par cycle
(série temporelle) ou si on **écrase en place** (dernière valeur seulement).

**Fichiers**
- `backend/app/tasks/jobs.py` → `persist_device_metrics()`, constante **`HISTORY_METRICS`**
- `backend/app/models/device_metric.py`
- `backend/app/services/lr_metric_history_service.py` → constante **`GRAPH_METRICS`**
- `backend/app/models/lr_metric_sample.py`

**À savoir**
- ⚠️ **`HISTORY_METRICS` ne contient que les 4 compteurs d'octets** (`peer_tx_bytes`,
  `peer_rx_bytes`, `radio_rx_bytes`, `radio_tx_bytes`) — les seuls relus comme série, par
  le calcul de consommation. **Tout le reste est écrasé** (1 ligne par
  `(device_id, metric_name)`).
- ⚠️ Sans cette politique, un seul UISP Power empilait ~25 métriques toutes les 30 s
  (~70 000 lignes/jour) que **rien ne relisait** → la table a atteint **7,3 Go**
  (2026-06-24, `VACUUM FULL` nécessaire, autovacuum dépassé par le churn).
- ⚠️ **Vouloir une nouvelle courbe ne justifie JAMAIS d'ajouter une clé à
  `HISTORY_METRICS`** — il faut l'ajouter à **`GRAPH_METRICS`**, qui écrit dans la table
  dédiée `lr_metric_samples`, en buckets.

### 4.1 SNMP — radios et switches

**Ce que ça fait** — Relève les interfaces (ath0/eth0) des Rockets, et l'état + la vitesse
+ les compteurs de **chaque port** des switches.

**Fichiers**
- `backend/app/services/snmp_service.py` — collecte radio, `collect_switch_port_metrics`, `collect_airmax_metrics`, `discover_airmax_peers`, `resolve_mac_ports` (FDB), `fetch_if_descrs`
- `backend/app/tasks/jobs.py` → `snmp_poll_job` (radios) et `switch_snmp_poll_job` (switches) — corps commun `_run_snmp_poll(device_types, label)`
- `backend/tests/test_snmp_poll_device_isolation.py`

**À savoir**
- ⚠️ **Les switches ont leur propre job et leur propre conteneur** depuis le 2026-08-11.
  Auparavant ils partageaient le tour des ~100 radios et, étant les plus lents (28 ports),
  passaient **en dernier** : **14 h sans une seule métrique de switch** ont été constatées,
  rendant 3 alertes aveugles pendant que le job « tournait normalement ».
- ⚠️ **`SWITCH_MAX_PORTS` est une fenêtre de scan, pas un nombre de ports.** Rien au-delà
  n'est mesuré — un port hors fenêtre est invisible même quand il tombe. Défaut relevé à
  **30** (24 RJ45 + 4 cages SFP+ d'un UISP-S-Pro). Élargir la fenêtre ne crée **aucune**
  alerte : les règles n'évaluent que les ports surveillés.
- Phase 1 (fetch SNMP) **concurrente** et bornée par sémaphore ; phase 2 (persist/alert)
  **en série**.
- ⚠️ Ces switches **perdent des requêtes SNMP** : `fetch_if_descrs` fait **2 tentatives**
  par index. Un seul datagramme UDP perdu suffisait à écarter un port légitime.

### 4.2 API HTTP LTU (Rockets LTU + fan-out des abonnés)

**Ce que ça fait** — Interroge l'API du Rocket LTU : signal, CCQ, CINR, capacité, débit,
distance, **et la liste de ses CPE** — d'où sont dérivées les métriques de chaque abonné,
sans jamais interroger l'abonné lui-même.

**Fichiers**
- `backend/app/services/ltu_api_service.py`
- `backend/app/tasks/jobs.py` → `ltu_api_poll_job`
- `backend/tests/test_ltu_api_service.py`
- `backend/scripts/ltu_dump_linkquality.py` — dump brut d'un lien, pour investigation

**À savoir**
- ⚠️ Le débit LTU est dans `peer.common.counters.txRate/rxRate` et l'unité est le
  **bit/s**, pas le kb/s (lu en kb/s, un lien à 51,8 Mb/s en annonçait 192). Les clés
  `txkbps`/`rxkbps` **n'existent pas** — les chercher laissait le débit vide sur tout le
  parc LTU.
- On poll l'**AP**, donc son `tx` est le **descendant** du client.
- Phase 1 concurrente (sémaphore 10, deadline global 40 s), phase 2 en série.

### 4.3 API airOS (airMAX AC + LiteBeam M5)

**Ce que ça fait** — Deux chemins distincts : les **5AC** sont interrogés directement
(leurs compteurs de consommation ne peuvent venir que d'eux) ; les **M5** sont servis par
un seul appel à leur **AP**, qui rend toutes ses stations d'un coup.

**Fichiers**
- `backend/app/services/airos_api_service.py` — dont `parse_airos_ap_stations`
- `backend/app/tasks/jobs.py` → `airos_api_poll_job`, `_derive_throughput_from_counters`
- `backend/tests/test_airos_api_service.py`, `test_airos_ap_stations.py`, `test_throughput_derivation.py`

**À savoir**
- ⚠️ **Ne jamais basculer les 5AC sur les compteurs de l'AP** : le compteur que l'AP tient
  pour une station est d'une **autre origine** (**55,46 Gio vu par l'AP contre 2,03 Gio vu
  par le CPE**, même client même instant) → on **facturerait l'écart**.
- ⚠️ Côté AP, les étiquettes `rx`/`tx` sont **croisées** (elles sont relatives à qui répond).
  Les étiquettes `dl_*`/`ul_*` sont absolues.
- ⚠️ L'AP n'émet **pas de CINR exploitable** pour une station airOS 6 (il annonce 3 dB
  quand le SNR réel est 25) → tous les M5 tomberaient sous le seuil critique. Leur CINR/CCQ
  vient du SSH `wstalist`.
- ⚠️ La **réconciliation d'identité** s'applique à **toutes** les stations (5AC comprises),
  alors que le fan-out des **métriques** est M5-only. C'est le **seul chemin** qui suive un
  abonné qui **roame** vers un autre AP — sans lui, il restait affiché hors ligne sur son
  ancien site avec son ancienne IP, indéfiniment.
- ⚠️ Le **LiteBeam M5 n'expose aucun débit instantané** (ni `wstalist`, ni `status.cgi`, ni
  `ifstats.cgi`) : il est **dérivé du delta des compteurs d'octets**. Le sens est **fourni
  par l'appelant**, jamais deviné.

### 4.4 API AF60 (backhauls 60 GHz)

**Ce que ça fait** — Relève capacité, signal, MCS, débit et **occupation** des liaisons
inter-sites.

**Fichiers**
- `backend/app/services/af60_api_service.py`
- `backend/app/tasks/jobs.py` → `af60_api_poll_job`
- `backend/tests/test_af60_api_service.py`, `test_af60_link_occupancy.py`
- `docs/` — voir aussi `reference_af60_local_api` dans les notes

**À savoir**
- ⚠️ Le débit se lit dans **`interfaces[wlan0].statistics`**, **surtout pas** dans
  `peers[0].common.counters` : ce dernier est relayé par la radio et **retarde** — il a
  annoncé **0,06 Mb/s pendant que le lien écoulait 76,69 Mb/s**. Une courbe bâtie dessus
  montre des effondrements qui n'ont pas eu lieu.
- ⚠️ **L'occupation n'existe pas dans le firmware** (vérifié : ni airtime, ni duty cycle
  TDD, ni utilisation — **ne pas re-chercher**). Elle est **dérivée** :
  `dl_débit/dl_capacité + ul_débit/ul_capacité` (somme, car le lien est **TDD** : les deux
  sens se partagent le temps d'antenne).
- ⚠️ **Surtout pas** `débit_total / total_capacity_mbps` : cette clé est la **moyenne** des
  deux sens, et la formule rend alors **120 %** — une occupation > 100 % prouve la formule
  fausse. Les deux formules restent proches sur un lien peu chargé : **rien ne signalerait
  la substitution** hors du test dédié.
- ⚠️ L'AF60 porte un **vrai GPS aux deux bouts** (`device.gps` + `peers[0].remote[0].gps`
  dans le **même** appel). Piste non exploitée pour `site_locations`.

### 4.5 UISP Power (onduleurs)

**Ce que ça fait** — Relève tension, courant, puissance, **batterie interne (Li-Ion UPS)**
et **batterie externe (banc plomb)**, et détecte la coupure secteur (SOMELEC).

**Fichiers**
- `backend/app/services/uisp_power_service.py`
- `backend/app/models/power_status_log.py`
- `backend/app/tasks/jobs.py` → `power_poll_job`, `_evaluate_mains_power`
- `backend/app/api/endpoints/devices.py` → `GET/POST /devices/{id}/power-output` (pilotage des sorties)
- `docs/rapport-uisp-power.md`

**À savoir** — Les deux batteries ont des seuils **volontairement différents** (interne
< 50 %, externe < 30 %). L'ancienne alerte batterie unique, l'alerte de tension et
`uisp_power_unreachable` ne sont **plus émises** (types conservés pour le legacy, fermés
silencieusement par le job). La coupure secteur `mains_power_lost` est **affichée mais non
notifiée**.

### 4.6 Sonde SSH LR → Internet (latence et transit)

**Ce que ça fait** — Une session SSH par LR et par cycle : un `ping` vers `8.8.8.8` qui
produit **deux signaux** — le transit (binaire) et la latence (continue) — plus, en effet
de bord, le **diagnostic d'accès SSH** de chaque abonné.

**Fichiers**
- `backend/app/services/ssh_service.py` — `ping_targets_via_ssh`, `classify_probe_ssh_status`, `_open_transport` (fallback de mots de passe), `verify_lr_live`
- `backend/app/tasks/jobs.py` → `lr_internet_probe_job`
- `backend/tests/test_ssh_service.py`

**À savoir**
- ⚠️ **Les LR bloqués sont exclus de la sonde** (on ne mesure pas un accès qu'on a
  soi-même fermé) **et leur dernière mesure de latence est PURGÉE** : cesser de mesurer ne
  doit jamais laisser une valeur derrière soi, sinon `/lr-health` et le contrôle quotidien
  compteraient éternellement cet abonné en « latence élevée ».
- ⚠️ **Saturation radio au-delà de ~150 poignées de main SSH simultanées** — la concurrence
  est bornée par `lr_probe_concurrency`. Ne pas l'augmenter sans mesurer.
- Un tour dure **100 à 480 s** sur ~557 LR : c'est **la ressource rare** du système. Chaque
  session économisée retourne au budget du tour.
- **Fallback de mot de passe** : `LR_FALLBACK_SSH_PASSWORDS` (CSV) est essayé sur échec
  d'authentification ; le mot de passe qui marche est **promu** sur le LR (auto-réparation).

### 4.7 Historique des courbes de la fiche équipement

**Ce que ça fait** — Stocke, dans une table dédiée en **buckets** (60 s), les séries
affichées par les graphes « Plus d'infos » de la fiche.

**Fichiers**
- `backend/app/services/lr_metric_history_service.py` — **`GRAPH_METRICS`**, `record_sample`, `get_history`, `available_metrics`, `threshold_setting_for`
- `backend/app/models/lr_metric_sample.py`
- `backend/app/api/endpoints/devices.py` → `GET /devices/{id}/metric-history`
- `frontend/components/MetricHistoryModal.tsx`

**À savoir**
- **Ajouter une clé à `GRAPH_METRICS` suffit à rendre une métrique traçable** — pas de
  migration, pas de table à créer.
- ⚠️ Le seuil tracé est **importé de `alert_rules`**, jamais recopié : la ligne du graphe
  doit être celle qui déclenche l'alerte.
- ⚠️ **Les trous ne sont pas comblés** : un bucket sans relevé est absent, **jamais ramené
  à 0** — un 0 se lirait comme « le lien ne passait rien ».
- ⚠️ Le coût en volume est **proportionnel au nombre de métriques** : à 60 s, ~800 LR ×
  1440 buckets/j × N métriques ≈ **110 M lignes** sur 30 j. Surveiller l'autovacuum.
- Rétention batchée : `lr_latency_retention_job` (jamais une grosse transaction).

---

## 5. Disponibilité et supervision

### 5.1 Ping — deux balayages séparés

**Ce que ça fait** — Un balayage pour l'**infra** (le seul qui ouvre des incidents) et un
pour les **LR clients** (dont la panne appartient à l'abonné, pas à nous).

**Fichiers**
- `backend/app/services/poller.py` — `ping_hosts_bulk` (un seul process `fping`)
- `backend/app/tasks/jobs.py` → `infra_ping_job`, `client_ping_job`, `_ping_sweep`, `_reconfirm_unreachable`

**À savoir**
- ⚠️ **Le `fping` groupé n'est qu'un PRÉ-FILTRE** : le CPU de management des radios
  Ubiquiti rate-limite l'ICMP et jette **tout le burst d'un coup**. Sans re-ping isolé,
  **132 LR sains sur 327** étaient marqués « HORS LIGNE » (2026-07-17). Tout hôte déclaré
  injoignable est **re-pingé isolément** avant d'être compté KO. Coût **nul** quand tout
  répond.
- ⚠️ **Un équipement sans IP sort du balayage** (`ip_address IS NOT NULL`), donc plus rien
  n'écrit son statut : il reste figé (un LR s'affichait « EN LIGNE » indéfiniment avec une
  `last_seen` qui vieillissait). **Règle générale : tout chemin qui retire un équipement du
  balayage doit écrire son statut** (`unknown`).
- Le statut `down` n'est posé qu'au **seuil anti-flap** (3 cycles), jamais sur un paquet
  perdu — sinon l'équipement sort des polls qui filtrent sur `status=up`.
- Réglages **séparés par famille**, aucun budget partagé : régler les LR ne peut pas
  dégrader la mesure de l'infra.
- Le balayage LR tourne dans **son propre conteneur** (`scheduler-ping-lr`) : sa rafale de
  re-confirmations disputait le CPU au balayage infra, seule source d'incidents.

### 5.2 Journal des coupures et disponibilité %

**Ce que ça fait** — Reconstruit, sur une fenêtre choisie, tous les épisodes de panne de
chaque équipement d'infra, en fusionnant les micro-coupures et en calculant la
disponibilité en %.

**Fichiers**
- `backend/app/services/network_uptime_service.py`
- `backend/app/api/endpoints/network_uptime.py` — `/site-summary`, `/downtime-log`
- `backend/app/schemas/network_uptime.py`
- `frontend/components/SiteOutageCharts.tsx`, `frontend/components/PanneDetailsModal.tsx`
- `frontend/app/reports/page.tsx`
- `backend/scripts/dump_lr_uptime.py`

**À savoir**
- ⚠️ **C'est LA raison pour laquelle les incidents de disponibilité ne sont pas supprimés à
  leur résolution.** Tous les autres types sont *hard-delete* ; ceux de
  `AVAILABILITY_ALERT_TYPES` sont conservés en `status=resolved` parce que ce journal se
  reconstruit depuis leur `resolved_at`. **Purger ces lignes efface l'historique de
  disponibilité.**
- **Fusion d'épisodes** : des incidents séparés par moins de `merge_gap_seconds` (5 min)
  sont fusionnés — sinon un lien instable qui flappe 50× en une heure apparaîtrait comme
  50 pannes distinctes.
- Les LR clients sont **délibérément exclus** (ils ont `/lr-health`).

### 5.3 Détection d'instabilité (flapping)

**Fichiers**
- `backend/app/tasks/jobs.py` → `flap_detection_job`

**À savoir** — ⚠️ **Les UISP Power sont exclus** : leurs cycles up/down sur coupure secteur
sont normaux et déjà couverts par `mains_power_lost`. Seuil : plus de 3 incidents de
disponibilité sur 24 h.

---

## 6. Moteur d'alertes et incidents

### 6.1 Les constantes — la source unique de vérité

**Fichiers**
- `backend/app/core/alert_constants.py` — **à lire en premier** : les 41 `alert_type` (`KNOWN_ALERT_TYPES`), `Severity`, `AlertChannel`, **`WHATSAPP_ALERT_TYPES`**, `AVAILABILITY_ALERT_TYPES`, `MANUAL_ACK_ALERT_TYPES`, `INFRA_DEVICE_SUPPRESSED_ALERT_TYPES`, `CLIENT_KEPT_ALERT_TYPES`
- `backend/app/core/alert_labels.py` — libellés lisibles par un humain

**À savoir** — ⚠️ **Ne jamais redéfinir un `alert_type` ailleurs.** Une chaîne recopiée qui
diverge d'un caractère crée une alerte qui ne se résout jamais.

**Les 41 types de `KNOWN_ALERT_TYPES`**, par catégorie. ⚠️ Beaucoup sont *(legacy)* ou
*(client)* : ils existent dans le code mais **n'ouvrent aucun incident visible** — voir
§6.4. Le compte réel se lit **toujours dans `KNOWN_ALERT_TYPES`**, jamais dans une liste
recopiée ailleurs :

| Catégorie | Types | Notifié WhatsApp |
|---|---|---|
| **Disponibilité** | `rocket_down`, `switch_down`, `device_unreachable`, `airmax_down` | **oui** |
| | `uisp_power_unreachable` *(legacy — plus émis)* | non |
| | `device_flapping` | **oui** |
| **Interface** | `radio_interface_down`, `eth0_down` | non |
| | `cpe_disconnected` *(supprimé — churn abonné)* | non |
| | `fiber_link_down` | **oui** |
| **Radio** | `signal_low`, `cinr_low`, `ccq_low`, `radio_link_degraded`, `high_rx_tx_errors` | non |
| | `ccq_ul_low`, `cinr_ul_low` *(sens montant)* | non |
| **Charge AP** | `rocket_client_overload` *(supprimé — vu sur `/capacity`)* | non |
| **Alimentation** | `battery_internal_low`, `battery_external_low` | **oui** |
| | `mains_power_lost` *(affiché, non notifié)* | non |
| | `voltage_anomaly`, `battery_low_warning`, `battery_low_critical` *(legacy)* | non |
| **Switch** | `switch_port_down`, `switch_port_speed_low` | **oui** |
| **Transit** | `lr_no_transit`, `lr_latency_high` *(client)* | non |
| | `transit_unavailable` *(réservé, jamais émis)* | non |
| **Lien client** | `lr_link_substandard` *(client)* | non |
| | `lr_bridge_mode_misconfig` *(supprimé — vu sur `/access`)* | non |
| **Backhaul** | `af60_link_down`, `af60_link_substandard`, `p2p_link_substandard` | **oui** |
| | `af60_link_saturated` ⚠️ *(volontairement hors liste, en observation)* | non |
| | `af60_signal_low`, `af60_snr_low` | non |
| **Découverte** | `lr_discovered`, `lr_ip_changed`, `lr_reassigned` *(client)* | non |
| **Sécurité** | `security_anomaly` ⚠️ *(ouvre un incident, n'alerte nulle part)* | non |

⚠️ **Lecture de la 3ᵉ colonne** : seuls les types marqués « oui » déclenchent réellement un
message. Tous les autres ouvrent (ou non) un incident en base **sans que personne ne soit
prévenu**. C'est un choix assumé contre la fatigue d'alerte — mais c'est **le premier
réglage à revoir** si l'équipe estime rater des événements. Voir §7.1.

### 6.2 Les règles

**Ce que ça fait** — Fonctions **pures** (sans DB) qui, à partir d'un dictionnaire de
métriques et de seuils, disent s'il y a anomalie et à quelle sévérité.

**Fichiers**
- `backend/app/services/alert_rules.py` — les règles, `_AIRMAX_LR_VARIANTS`, `_rocket_overload_threshold`
- `backend/tests/test_alert_rules.py`

**À savoir** — Les seuils sont **par famille radio** (LTU vs airMAX) : potentiel de lien
50 % / 40 %, index RX ×6 / ×4. Cette table de variantes est **importée** par le service
d'historique des courbes, jamais recopiée — la ligne tracée doit être celle qui déclenche
l'alerte.
⚠️ La règle `throughput_anomaly` a été **supprimée** (2026-07-20) : elle lisait le **taux
PHY** et non un débit, et sur les Rockets LTU la clé était absente — elle ne s'exécutait
donc jamais. Ne pas la réintroduire sans revoir la mesure.

### 6.3 Le moteur

**Ce que ça fait** — Évalue les règles, tient les compteurs anti-flapping **persistés en
base**, ouvre et résout les incidents.

**Fichiers**
- `backend/app/services/alert_engine.py`
- `backend/app/models/alert_state.py`
- `backend/app/services/alert_policy.py` — registre interne (canal / groupable / recovery / immédiat) par type ; **plus exposé en API**
- `backend/tests/test_alert_engine.py`, `test_alert_engine_integration.py`, `test_alert_policy.py`, `test_alert_policy_overrides.py`

**À savoir** — Les compteurs anti-flap sont **en base** (ils survivent aux redémarrages),
sauf les compteurs de ping qui restent en mémoire (`_failure_counts` dans `jobs.py`).
Le moteur lit ses baselines depuis `AlertState`, **jamais** depuis `device_metrics` — c'est
ce qui permet de collapser les métriques sans casser aucune alerte.

### 6.4 Les incidents

**Ce que ça fait** — Création dédupliquée, résolution, et **suppression** à la résolution
(sauf disponibilité).

**Fichiers**
- `backend/app/services/incident_service.py` — `open_incident`, `resolve_incidents`, **`is_suppressed_incident`**
- `backend/app/models/incident.py`, `backend/app/schemas/incident.py`
- `backend/app/api/endpoints/incidents.py` — **lecture seule**
- `frontend/app/incidents/page.tsx`, `frontend/components/IncidentDetailModal.tsx`, `IncidentStatusBadge.tsx`, `SeverityBadge.tsx`
- `backend/tests/test_incident_service.py`

**À savoir**
- ⚠️ **`/incidents` = INFRASTRUCTURE uniquement.** Le découpage est **par équipement**
  (`rule_category`), **pas par `alert_type`** : les types radio se déclenchent aussi bien
  sur un Rocket de base (infra → gardé) que sur un LR abonné (client → supprimé). Filtrer
  sur la chaîne masquerait de vraies alertes infra. Le garde-fou unique est
  `is_suppressed_incident`, appelé **en tête** de `open_incident`.
- ⚠️ Deux types sont supprimés **même sur un équipement d'infra** parce qu'ils ont leur
  page dédiée : `rocket_client_overload` (→ `/capacity`) et `lr_bridge_mode_misconfig`
  (→ `/access`). Plus `cpe_disconnected` (churn abonné, pas notre panne).
- ⚠️ **Un problème côté client n'est JAMAIS un incident.** Les jobs continuent de sonder
  les LR et d'incrémenter leurs `AlertState` ; seul l'incident final est court-circuité.
- Il n'y a **pas de page d'archive** : les incidents résolus non-disponibilité sont
  *hard-delete*.

### 6.5 Bandeau d'anomalies à acquitter à la main

**Ce que ça fait** — Trois anomalies « silencieuses » (liaison F60 dégradée, port de switch
dégradé, équipement instable) restent affichées en haut du dashboard **jusqu'à un clic
humain**.

**Fichiers**
- `backend/app/models/manual_alert.py`
- `backend/app/services/manual_alert_service.py` — `record_detection`, `list_pending`, `acknowledge`
- `backend/app/api/endpoints/manual_alerts.py` — `GET /manual-alerts`, `POST /manual-alerts/{id}/acknowledge`
- `backend/app/schemas/manual_alert.py`
- `frontend/components/AlertBanner.tsx` (porté par `frontend/components/AppShell.tsx`, donc présent sur toutes les pages)
- `backend/tests/test_manual_alert_banner.py`

**À savoir**
- ⚠️ **Table dédiée, pas une lecture de `incidents`** : un incident est *hard-delete* à sa
  résolution, donc un bandeau bâti dessus verrait sa ligne **s'évaporer dès le retour à la
  normale** — sans que personne ait cliqué, l'exact contraire du besoin.
- ⚠️ **Le point d'accroche EST la règle de récidive** : `record_detection` est appelé depuis
  `open_incident` **après** son `return existing, False`, donc uniquement pour un incident
  **nouveau**. Une anomalie qui **dure** ne produit aucune ligne nouvelle ; une anomalie qui
  **revient** en produit une. Remonter cet appel de deux lignes donnerait ~1 ligne par
  minute de poll — c'est pourquoi un test vérifie l'**ordre dans le source**.
- ⚠️ `record_detection` ne fait **ni flush ni commit** : un rollback qui annule l'incident
  doit annuler la ligne.
- Une ligne du bandeau peut désigner une anomalie **déjà rétablie** : c'est le but — elle
  atteste que c'est **arrivé**. `/incidents` dit ce qui se passe **maintenant**.
- L'acquittement est **partagé** : un clic retire la ligne pour toute l'équipe.

### 6.6 Surveillance des ports de switch

**Ce que ça fait** — Détecte **quel équipement est câblé sur quel port** de switch, puis
alerte quand un port surveillé tombe ou négocie sous le gigabit.

**Fichiers**
- `backend/app/services/switch_port_service.py` — `detect_from_uisp` (**source primaire**), `detect_all` (FDB, **fallback**), **`watched_ports`** (le chokepoint unique), `UNWATCHED_DEVICE_TYPES`, `CABLED_DEVICE_TYPES`
- `backend/app/services/snmp_service.py` → `resolve_mac_ports`, `fetch_if_descrs`
- `backend/app/tasks/jobs.py` → `switch_port_mapping_job`
- `backend/app/models/device.py` → colonnes `uplink_switch_id` / `uplink_switch_port` / `uplink_detected_at`
- `backend/scripts/detect_switch_ports.py` — **contrôle à blanc ; à lancer avant de compter dessus**

**À savoir**
- ⚠️ **Ces deux alertes n'avaient JAMAIS pu se déclencher** avant le 2026-07-30 : elles
  étaient gardées sur `rocket_port_index`, une colonne qu'**aucun code ne renseignait**
  (NULL partout). Le canal WhatsApp était prêt, la mesure aussi — seule la **désignation du
  port** manquait.
- ⚠️ **Les switches UISP n'implémentent pas BRIDGE-MIB et n'émettent pas de LLDP** (vérifié
  sur **les deux familles** du parc : `1.3.6.1.2.1.17` = `NoSuchObject`, 0 trame LLDP en
  45 s de capture). **Ne pas re-tenter.** La source qui marche est **les data-links du
  contrôleur UISP**.
- ⚠️ **Une attribution ne s'efface JAMAIS sur une absence** : le switch fait vieillir une
  MAC hors de sa FDB en quelques minutes après la chute du port — « la MAC a disparu » et
  « le lien vient de mourir » sont **la même observation**, et la seconde est exactement le
  moment où l'alerte doit partir. Seule une **observation positive plus récente** écrase.
- ⚠️ **Les ports d'UISP Power sont mappés mais jamais surveillés** : leur port de management
  est du Fast Ethernet, 100 Mb/s est leur vitesse **nominale**. **11 des 13 ports sous le
  gigabit étaient des UISP Power** — la règle aurait alerté en permanence sur du matériel
  sain et **enterré les 4 vraies trouvailles**.
- ⚠️ Les noms de port `ethN` des switches **UniFi** sont **refusés** : l'indexation n'est pas
  déductible du payload, qui se contredit sur un même switch. Deviner ferait **nommer le
  mauvais port physique dans une alerte critique**.
- ⚠️ `ifSpeed = 0` (cage SFP) est **ignoré** : un débit **inconnu** n'est pas un débit
  **dégradé**.

---

## 7. Notifications WhatsApp et rapports

### 7.1 Le canal WhatsApp — transport unique

**Ce que ça fait** — Envoie les alertes retenues dans un **groupe WhatsApp** via Ultramsg.

**Fichiers**
- `backend/app/services/whatsapp_service.py` — `POST /{instance}/messages/chat` et `/messages/document`
- `backend/app/services/notification_service.py` — **`_dispatch` est le chokepoint unique**
- `backend/app/services/alert_formatter.py` — mise en forme par type, `_DESCRIPTION_ALERT_TYPES`
- `backend/app/api/endpoints/system.py` → `POST /system/test-whatsapp`
- `backend/tests/test_alert_formatter.py`

**À savoir**
- ⚠️ **L'email a été entièrement retiré du projet** (2026-06-16) : `email_service`, la
  config SMTP, la dépendance `aiosmtplib`, l'endpoint `/system/test-email` et le job
  d'instabilité ping ont été **supprimés**. Ne pas le réintroduire sans décision explicite.
- ⚠️ **Liste blanche `WHATSAPP_ALERT_TYPES`** : **13 types seulement** sur les 41, soit
  6 familles — fibre coupée, ports de switch, équipement instable, batteries, liaison P2P
  dégradée, équipement injoignable — plus la latence réseau envoyée en direct (qui n'est
  pas un incident). **Tout le reste ouvre un incident en base mais n'est notifié nulle
  part.** C'est volontaire (fatigue d'alerte) — mais c'est la première
  chose à revoir si l'équipe estime « rater » des choses. Y ajouter un type est **une
  ligne** dans `alert_constants.py`.
- `whatsapp_service` ne lève **jamais** : il renvoie `False` sur échec.
- ⚠️ `af60_link_saturated` est **délibérément hors** de la liste blanche : ses seuils n'ont
  pas d'historique terrain. Décision d'observer d'abord, calibrer, puis décider.

### 7.2 Digest des warnings

**Fichiers**
- `backend/app/services/digest_service.py`
- `backend/app/tasks/jobs.py` → `warning_digest_job` (15 min)
- `backend/tests/test_digest.py`

### 7.3 Rapports PDF quotidiens

**Ce que ça fait** — Deux PDF envoyés chaque matin comme **document WhatsApp** : les
Rockets saturés, et la capacité infra par site.

**Fichiers**
- `backend/app/services/saturation_report_service.py` (lib `fpdf2`)
- `backend/app/services/site_infra_service.py`
- `backend/app/tasks/jobs.py` → `rocket_saturation_report_job`, `site_infra_report_job`, **`_claim_daily_run`**

**À savoir** — ⚠️ Ces jobs tournent aussi **1× au démarrage** (`next_run_time=now`) pour
produire un rapport dès le déploiement. **Piège** : sans garde-fou, chaque `restart` du
conteneur renvoyait le rapport. D'où **`_claim_daily_run` — ne pas le retirer.**
L'envoi est **systématique**, même si la liste est vide (caption ✅) : l'absence de message
se lirait comme une panne du rapport.

### 7.4 Contrôle quotidien de latence réseau

**Fichiers**
- `backend/app/tasks/jobs.py` → `network_latency_aggregate_job`
- `backend/app/services/lr_health_service.py` → `network_latency_summary`

**À savoir** — Envoi **direct** sur WhatsApp, **pas** un incident (un incident exige un
`device_id`, or celui-ci porte sur le réseau entier). Pas de message de rétablissement :
c'est un rapport quotidien qui n'envoie que si la condition est remplie.

---

## 8. Capacité du réseau

### 8.1 Capacité clients par Rocket

**Ce que ça fait** — Compare, pour chaque station de base, le nombre de clients
**installés** à sa capacité maximale, calculée par une formule qui dépend de la largeur de
canal.

**Fichiers**
- `backend/app/services/network_capacity_service.py`
- `backend/app/api/endpoints/network_capacity.py`
- `backend/app/services/alert_rules.py` → `_rocket_overload_threshold`
- `frontend/app/capacity/page.tsx`, `frontend/components/CapacityDonut.tsx`
- `backend/scripts/site_rocket_headroom.py`

**À savoir**
- Formule : base à 10 MHz + 5 clients par tranche de +10 MHz. Bases : **LTU 15, airMAX 10**.
  Largeur auto-détectée en direct (LTU via API, airMAX via `status.cgi` `chanbw`).
- ⚠️ Les clients comptés sont les **installés (roster UISP)**, pas le `peer_count` live —
  un client éteint reste un client installé, donc une place occupée.
- ⚠️ `rockets.max_clients_override` **remplace entièrement** la formule quand il est posé
  (éditable depuis `/capacity`, vide = retour au calcul). **Préservé par le sync UISP.**
- Un Rocket sans largeur connue est exclu des totaux (`unknown`), **jamais compté à 0**.

### 8.2 Capacité infra par site

**Fichiers**
- `backend/app/services/site_infra_service.py` — `INFRA_COUNTED_TYPES`
- `backend/app/api/endpoints/network_capacity.py` (clé `infra`)
- `frontend/app/capacity/page.tsx` (section « Capacité infra par site »)

**À savoir** — Compte **Rockets + AF60 + PTP LiteBeam** ; **exclut** switches, UISP Power et
LR clients (définition de l'opérateur). Plafond `SITE_INFRA_MAX` = **14**. La marge est
signée : **+N** places libres / **−N** dépassement.

---

## 9. Topologie du réseau

### 9.1 Topologie inter-sites — le maillage des backhauls

**Ce que ça fait** — Le graphe « quel site est raccordé à quel autre », avec l'état de
chaque liaison.

**Fichiers**
- `backend/app/services/site_topology_service.py` — **le module central** : `sync_site_links` (le **seul** code qui parle au contrôleur), `get_site_topology`, `internet_routes`, `edge_health`, `edge_traffic`, `edge_occupancy`, `site_occupancy_map`, `fibre_cut_edges`, `silence_dead_link`
- `backend/app/models/site_link.py`
- `backend/app/api/endpoints/network_topology.py` — `GET /network-topology`, `POST /sync`, `GET /export/word`
- `backend/app/tasks/jobs.py` → `site_topology_sync_job`
- `frontend/app/topology/page.tsx`, `frontend/components/TopologyView.tsx`, `TopologyGraph.tsx`, `TopologyMap.tsx`, `TopologyRoutesPanel.tsx`
- `frontend/lib/topologyColors.ts` — **barème partagé entre le graphe et la carte**
- `frontend/lib/googleMaps.ts` — verrou anti-double-injection du script Google
- `backend/scripts/dump_site_topology.py` — même graphe en ligne de commande
- `backend/tests/test_site_topology_service.py`

**À savoir — la règle centrale : deux fraîcheurs, deux chemins**

| | Cadence | Source |
|---|---|---|
| **Câblage** (qui est relié à qui) | **1×/jour** | UISP → table `site_links` |
| **Santé** (statut, capacité, occupation) | **à chaque affichage** | notre base |

- ⚠️ **Ne jamais figer la santé dans `site_links`** — ce serait afficher l'état d'hier.
- ⚠️ **Ne jamais remettre la page en lecture live du contrôleur** : avant ce découpage,
  chaque ouverture d'onglet téléchargeait ~1300 équipements + ~1400 sites + ~1300 liens,
  et le rafraîchissement le **rejouait toutes les 2 minutes** sur un onglet oublié.
- ⚠️ **Le graphe N'EST PAS un arbre** : 17 sites, 19 liaisons, dont **2 hors arbre** (vraies
  boucles de redondance). Le rendu est **en couches** ; les arêtes surnuméraires sont
  tracées en pointillé. **Ne jamais « simplifier » en arbre** — ça jetterait une redondance
  sans le dire.
- ⚠️ **Remplacement intégral de la table à chaque sync**, mais **sauté si aucune liaison
  n'est résolue** (un payload vide se lirait « plus aucun backhaul » et effacerait toute la
  topologie).
- ⚠️ **La racine ne se déduit pas** : le lien Internet→HQ n'est pas un data-link. C'est un
  réglage (`TOPOLOGY_ROOT_SITE`), avec repli **annoncé** (`root_source`).
- ⚠️ **Un site d'infra est un site qui PORTE de l'infra** — le filtre `ucrm.client` seul ne
  suffit pas : deux sites d'abonnés créés à la main dans UISP passaient pour de l'infra.

**Couleur d'une liaison** — ordre de priorité **strict** (le rouge est en tête et doit y
rester : une panne ne doit jamais être masquée par une couleur de support) :
1. site entièrement tombé → **rouge** · 2. boucle de redondance → **gris** ·
3. fibre/cuivre → **bleu** · 4. radio debout mais inerte → **jaune** · 5. radio qui écoule → **vert**

⚠️ **Jaune = « mesuré à zéro », jamais « pas mesuré ».** Une liaison sans relevé reste
**verte** (« pas mesuré » n'est pas « pas de trafic »), et une liaison dont **aucun** bout
ne répond est **grise**, jamais verte.

### 9.2 Routes vers Internet

**Ce que ça fait** — Pour un site, énumère **toutes** ses sorties vers la racine, désigne la
**meilleure** (la plus grande marge restante, en Mb/s) et **le maillon qui bride** chacune.

**Fichiers**
- `backend/app/services/site_topology_service.py` → `internet_routes`, constantes `ROUTE_MAX_HOPS`, `ROUTE_MAX_EXPANSIONS`, `ROUTE_KEEP`, `ROUTE_MIN_OCCUPANCY_FOR_PROJECTION`
- `frontend/components/TopologyRoutesPanel.tsx`
- `backend/scripts/dump_site_topology.py` (option `--site "A2 CT2"`)

**À savoir**
- ⚠️ **CE QU'ON ÉNUMÈRE EST LE CÂBLAGE, PAS LE ROUTAGE.** Ni OSPF ni la table de routage ne
  sont lus. C'est écrit jusque dans le panneau — sans ça l'écran se lit comme un diagnostic
  de routage.
- ⚠️ **Le verdict est un DÉBIT, jamais un pourcentage** :
  `plafond = trafic ÷ (occupation/100)`, `marge = plafond − trafic`.
  **Surtout pas `capacité − trafic`** (la capacité est une moyenne des deux sens).
- ⚠️ **Le goulot est le maillon de plus petite MARGE, pas le plus occupé en %.**
- ⚠️ **AUCUNE projection de bascule** : après une coupure, le trafic est **déjà** reparti
  par le secours, dont la mesure courante contient donc déjà ce qu'on voulait y ajouter.
  L'ajouter serait un **double comptage**. Verrouillé par un test.
- ⚠️ **Rien n'est caché** : le backend rend **toutes** les routes, c'est le rendu qui replie
  au-delà de 3. Deux règles de filtrage ont été écrites puis **retirées** — la décision
  n'appartient pas au backend.
- Un site à **plusieurs** sorties **arbitre** (`decider`) ; un site à **une seule** ne
  choisit rien (`child`) et le panneau renvoie vers son décideur.

### 9.3 Cartographie imprimable des sites (export Word)

**Ce que ça fait** — Trois planches, **une ville par page A4** : les sites à leur vraie
place sur un fond satellite embarqué, les backhauls tracés entre eux.

**Fichiers**
- `backend/app/services/site_map_service.py` — `render_plates`, `_plate_data`, `_place_labels`, constantes `_PLANNED`, `_SOURCES`, `LEGEND`
- `backend/app/api/endpoints/network_topology.py` → `GET /network-topology/export/word`
- `backend/data/maps/` — **fonds de carte commités** : `nouakchott.jpg`, `nouadhibou.jpg`, `rosso.jpg` + `bounds.json` + `README.md`
- `backend/scripts/build_site_map_basemaps.py` — régénération des fonds
- `backend/tests/test_site_map_export.py`
- `frontend/app/topology/page.tsx` — bouton « Carte Word »

**À savoir**
- ⚠️ **Le fond de carte est EMBARQUÉ, jamais téléchargé** : le serveur de prod n'a pas
  d'accès Internet sortant garanti. **Contrepartie : le cadrage est FIGÉ** — un site hors
  fenêtre est **NOMMÉ** en fin de document, jamais escamoté.
- ⚠️ **Pourquoi pas Google Maps** : ses conditions interdisent de **stocker** les tuiles.
  La clé Google reste correcte là où elle sert (pages vivantes, appel depuis le navigateur).
- ⚠️ **VERT = en service** (relu dans la topologie à chaque export) / **ROUGE = extension
  PROGRAMMÉE** (constante `_PLANNED`). ⚠️ **Jamais écrites en base** : elles seraient
  pinguées et alerteraient comme injoignables.
- ⚠️ Les compteurs « en service » et « programmés » restent **séparés**.
- ⚠️ La source Internet est **la seule information non mesurée** de la carte (aucune table
  ne dit qu'un site est la tête de réseau) : écrite à la main, verrouillée par un test.
- ⚠️ La **langue du document est l'anglais** ; les commentaires du code restent en français.

### 9.4 Topologie intra-site

**Fichiers**
- `frontend/components/SiteTopology.tsx` (sur `/sites`)
- `frontend/components/SiteCard.tsx`, `SiteOverviewCard.tsx`

---

## 10. Trafic Internet (NetFlow)

**Ce que ça fait** — Un collecteur écoute le NetFlow exporté par le routeur MikroTik,
attribue chaque flux à son **opérateur Internet** (ASN), et alimente les vues de volume et
de débit par destination — pour repérer les candidats à un serveur de cache.

**Fichiers**
- `backend/app/services/netflow_service.py` — collecteur asyncio UDP, décode v1/v5/v9/IPFIX
- `backend/app/services/asn_service.py` — IP → (ASN, opérateur) ; **primaire** = datasets BGP iptoasn.com, **fallback** = MaxMind GeoLite2-ASN
- `backend/app/services/traffic_service.py` — `get_top_destinations` (volume), `get_throughput` (débit), `get_throughput_history`
- `backend/app/models/traffic_dest_stat.py`
- `backend/app/api/endpoints/traffic.py`
- `backend/app/tasks/collector_runner.py` — **entrée du conteneur dédié** (`RUN_MODE=collector`)
- `backend/app/tasks/jobs.py` → `traffic_stats_retention_job`
- `frontend/app/traffic/page.tsx`
- `backend/data/README.md` — comment déposer les datasets ASN
- `docs/netflow-capture-explained.md`

**À savoir**
- ⚠️ **`NETFLOW_INTERNAL_PREFIXES` DOIT inclure RFC1918/CGNAT ET tout notre bloc public**
  (les clients ont des IP publiques dans `102.215.95.0/24`). Sinon les flux **descendants**
  sont vus opérateur↔opérateur et **ignorés** → download à 0.
- ⚠️ Le port UDP n'est publié **que sur l'IP LAN** (`docker-compose.lan.yml`), **jamais
  `0.0.0.0`** : NetFlow n'est pas authentifié. Restreindre la source au MikroTik au firewall.
- ⚠️ Sans dataset ASN déposé, **tout est agrégé sous « Indéterminé »** — la page semble
  cassée alors qu'elle fonctionne.
- C'est un **process long dédié**, pas un job APScheduler.
- La page reste **vide** tant que `NETFLOW_COLLECTOR_ENABLED=false` ou que le routeur
  n'exporte pas.

---

## 11. Clients : consommation, forfaits, signal, carte

### 11.1 Consommation par client

**Ce que ça fait** — Somme les deltas de compteurs d'octets par abonné sur 24 h / 7 j / 30 j
ou une **plage de dates libre**, agrégé site → Rocket → client.

**Fichiers**
- `backend/app/services/consumption_service.py` — `_sum_positive_deltas`, pattern SQL `LAG()`
- `backend/app/api/endpoints/clients.py` → `GET /clients/consumption`
- `backend/app/schemas/clients.py`
- `backend/app/tasks/jobs.py` → `client_consumption_daily_rollup_job` (le résumé de la veille),
  `device_metrics_retention_job` (la purge qu'il autorise)
- `backend/app/models/client_consumption_daily.py` — le résumé : 1 ligne par (équipement, compteur, jour)
- `frontend/app/clients/page.tsx`

**À savoir**
- ⚠️ **Les deux matviews (`client_consumption_30d` / `_7d`) ont été SUPPRIMÉES le
  2026-09-25** (migration `i5d6e7f8a9b0`). Chaque REFRESH relisait `device_metrics` sur
  toute sa fenêtre (**> 19 min** ; planifié toutes les 15 min il tournait **en permanence**,
  saturait l'E/S, faisait ramper la sonde SSH à ~40 min/tour et mettait `ltu_api_poll` à
  **0/60 Rockets** — incident du 2026-07-20), et surtout il **interdisait toute rétention** :
  purger au-delà de 7 j aurait fait afficher une consommation de 30 j **calculée sur 7**.
- Toutes les fenêtres **sauf 24 h** sont servies par le **résumé quotidien**
  (`client_consumption_daily`), recousu avec ses **deux bords partiels** calculés en live
  (`_aggregate_via_daily`). ⚠️ Les chiffres sont **identiques** à ceux des matviews : les
  fenêtres restent glissantes à la seconde. Le bord de TÊTE n'est pas un raffinement — sans
  lui, « 7 j » consulté le matin rend jusqu'à **14 % de moins**.
- ⚠️ **C'est ce bord de tête qui borne la rétention** : celui de « 30 jours » lit des relevés
  de 30 jours, d'où `DEVICE_METRICS_RETENTION_DAYS=33`. Le plancher est **dérivé**
  (`consumption_service.deepest_raw_window_days()`), donc ajouter un onglet plus profond
  ajuste la purge tout seul.
- Les compteurs 32 bits des airMAX rebouclent à ~4 Go : absorbé par la **somme des deltas
  positifs** (un cycle perdu par rebouclage, borné).
- ⚠️ **Il n'y a plus de rétention sur `device_metrics`** : les compteurs de consommation
  sont conservés **indéfiniment** (plage de dates sans limite). **Surveiller le disque.**

### 11.2 Forfait du client (traffic shaper airOS)

**Ce que ça fait** — Lit le **débit contractuel** de l'abonné directement sur son LR (le
shaper airOS), parce qu'aucune API équipement ni UISP ne l'expose.

**Fichiers**
- `backend/app/services/lr_plan_service.py` — `sync_all_lr_plans`
- `backend/app/services/ssh_service.py` → `parse_system_location`
- `backend/app/tasks/jobs.py` → `lr_plan_sync_job` (24 h, + 1× au démarrage)
- `backend/app/api/endpoints/devices.py` → `POST /devices/plans/sync`, `GET /devices/{id}/plan`

**À savoir** — Sur un CPE, l'interface filaire fait face au client (egress = **download**) et
la radio fait face à l'AP (egress = **upload**). Le LR connaît les **débits**, pas le **nom
commercial** du forfait (qui n'existe que dans le CRM UISP).
Ce sync met **aussi** en cache les **coordonnées GPS** provisionnées, qui sont dans le même
`/tmp/system.cfg` : un `grep` sur la session déjà ouverte au lieu d'un second aller-retour SSH.

### 11.3 Carte des clients

**Fichiers**
- `backend/app/services/device_map_service.py`
- `backend/app/api/endpoints/device_map.py`
- `frontend/app/map/page.tsx`, `frontend/lib/googleMaps.ts`

**À savoir** — ⚠️ **Le service SÉPARE, il ne filtre pas.** Les coordonnées viennent de
l'équipement et sont stockées **telles quelles** ; mais les données provisionnées sont
sales (UISP a poussé ses propres erreurs : des clients épinglés au **Yémen**, à **Gaza**, en
**Arabie saoudite**, et des inversions de signe de longitude qui placent des abonnés au
**Tchad**). Tout ce qui est hors de la boîte englobante mauritanienne devient un
**`outlier`** — conservé, nommé, et rendu à l'UI pour correction terrain. Les jeter
masquerait un bug de provisionnement ; les tracer rendrait la carte inutilisable
(auto-zoom à l'échelle du monde).

### 11.4 Qualité du signal et latence d'un client (API tierce)

**Fichiers**
- `backend/app/services/client_signal_service.py`
- `backend/app/api/endpoints/client_signal.py` → `GET /client-signal?mac=`
- `backend/app/schemas/client_signal.py`
- `backend/tests/test_client_signal_latency.py`

**À savoir** — ⚠️ **L'appel n'est pas instantané** : le signal vient de la base (dernière
valeur), mais la **latence est mesurée EN DIRECT** par SSH + ping (typiquement **6-15 s**,
davantage sur un lien en perte). Le consommateur doit prévoir son timeout. La latence
retombe sur `indetermine` — **jamais** sur 0 ni sur une valeur inventée.

### 11.5 Santé des liaisons clients

**Fichiers**
- `backend/app/services/lr_health_service.py` — `get_live_link_health`, `get_site_link_health`, `network_latency_summary`
- `backend/app/api/endpoints/lr_health.py` — `/bad-installations`, `/site-links`, `/high-latency`
- `backend/app/schemas/lr_health.py`
- `frontend/app/lr-health/page.tsx`

**À savoir** — La page « Liaisons clients » tourne en **LIVE** (fetch direct LTU/airOS), pas
sur `device_metrics`. Le scoring lit les **dernières** valeurs — c'est pourquoi les
métriques collapsées **doivent rester persistées**.

---

## 12. FAI : blocage, filtrage, journal, routeur

C'est la partie la plus sensible du système : elle **coupe l'accès Internet de vrais
abonnés**. Chaque garde-fou y a été ajouté après un incident réel.

### 12.1 Blocage client — deux modes

**Ce que ça fait** — Coupe l'accès d'un abonné, soit totalement (`full` = extinction du port
LAN du LR), soit partiellement (`whatsapp_only` = seul WhatsApp reste joignable).

**Fichiers**
- `backend/app/services/client_block_service.py` — `set_client_block`, `default_lan_interface`, `set_content_block`, `set_platform_block`, `platform_target_categories`
- `backend/app/services/ssh_service.py` — `set_lan_interface`, `set_whatsapp_only`, `set_content_block`, **`identity_refusal`**, `_collect_forbidden_ifaces`
- `backend/app/api/endpoints/devices.py` → `POST /devices/{id}/block-client`, `/unblock-client`
- `backend/app/tasks/jobs.py` → `client_block_enforcement_job` (120 s)
- `frontend/components/ClientAccessActionModal.tsx`
- `backend/tests/test_block_identity_guard.py`
- `docs/API_BLOCAGE_CLIENT.md`

**Le mode `whatsapp_only` = 3 couches sur le LR** (FB/IG partagent les IP Meta, donc filtrer
par IP seul ne suffit pas) :
1. DNAT en `iptables` redirigeant tout DNS du client vers le dnsmasq du LR (anti-bypass `8.8.8.8`)
2. entrées `address=/<domaine>/0.0.0.0` dans `/etc/dnsmasq.conf` pour FB/IG/Messenger/Threads
3. chaîne `CLIENTBLOCK` sur `FORWARD` : DNS + plages Meta autorisés, `DROP` le reste

**À savoir**
- ⚠️ **CONTRÔLE D'IDENTITÉ AVANT TOUTE ACTION.** Une fiche cible une **MAC**, mais la
  session SSH part sur une **IP** que le DHCP a pu redonner à un autre abonné.
  `ssh_service.identity_refusal` lit les MAC de l'équipement joint **sur la session déjà
  ouverte** et **refuse d'agir** si la MAC attendue n'y est pas. Câblé sur les **trois**
  chemins d'écriture. ⚠️ **Invérifiable = autorisé** (on ne refuse que sur preuve
  positive, sinon un firmware sans `/sys/class/net` rendrait tout blocage impossible).
  Le refus est journalisé sous l'action dédiée **`IDENT_KO`**.
- ⚠️ **Garde-fou anti-lock-out du mode `full`** : `_collect_forbidden_ifaces` calcule **en
  direct** les interfaces du chemin SSH et de la route par défaut (+ membres de bridge,
  parents VLAN) et **refuse de les couper**. Défaut par famille : `eth0.1` (LTU) / `eth0`
  (airMAX).
- ⚠️ **Quirk airOS 8** : `kill -HUP dnsmasq` **n'applique pas** les directives `address=`.
  Il faut **`killall dnsmasq`** (airOS le relance). Sans ça le filtre semble posé et ne
  filtre rien.
- Le job d'enforcement ré-applique le mode toutes les 120 s : airOS régénère
  `/etc/dnsmasq.conf` au boot, donc un LR redémarré est re-bloqué dans la minute.

### 12.2 Filtre de contenu par plateforme

**Ce que ça fait** — Bloque des plateformes précises (TikTok, Snapchat, YouTube, contenu
18+…) chez un abonné, par empoisonnement DNS sur son LR. Piloté depuis le dashboard **et**
par une API tierce.

**Fichiers**
- `backend/app/api/endpoints/content_filter.py` — l'**API tierce** (`/block`, `/unblock`, `/status`, `/platforms`)
- `backend/app/services/client_block_service.py` → `set_content_block`, `set_platform_block`, `platform_target_categories`, `effective_blocked_platforms`, `validate_platforms`, `content_block_catalog`, constantes `CONTENT_BLOCK_LABELS`, `VALID_CONTENT_CATEGORIES`, `VALID_CONTENT_PLATFORMS`
- `backend/app/api/endpoints/devices.py` → `PUT /devices/{id}/content-block`, `GET /devices/content-block/categories`
- `frontend/app/content-block/page.tsx`, `frontend/lib/platformIcons.ts`
- `backend/tests/test_content_filter_api.py`
- `docs/api-content-filter.md` — **doc d'intégration pour le tiers**

**À savoir**
- ⚠️ **L'ensemble stocké ne veut PAS dire « bloqué » — son sens dépend de la DIRECTION.**
  `Lr.blocked_categories` liste ce qui est **coupé** en `denylist`, et ce qui est **le seul
  joignable** en `allowlist`. Un `append` naïf sur un client en allowlist ferait de TikTok
  **le seul site accessible** à l'abonné à qui on demandait de le bloquer. D'où
  `platform_target_categories`, qui traduit l'**intention**.
- ⚠️ **Un seul cas est refusé (409)** : bloquer la dernière plateforme encore autorisée d'un
  client en allowlist — l'ensemble deviendrait vide, ce qui **efface le filtre** et
  rouvrirait **tout l'internet**.
- ⚠️ **L'API tierce est CUMULATIVE**, contrairement à `PUT /devices/{id}/content-block` qui
  prend l'ensemble complet (pour la page, où l'opérateur voit toutes les cases).
- ⚠️ **Une plateforme inconnue est REFUSÉE (400)**, jamais ignorée : un `titkok` mal
  orthographié rendrait `200` en n'ayant rien bloqué.
- ⚠️ **`adult` est une PSEUDO-plateforme** : pas de liste de domaines (« tous les sites
  adultes » se compte en millions) — elle bascule le **résolveur amont** du dnsmasq vers un
  résolveur familial, et son état vit dans la colonne booléenne `lrs.block_adult_content`.
  **Ne JAMAIS l'ajouter à `CONTENT_BLOCK_LABELS`** : l'API rendrait « filtre appliqué » en
  n'ayant rien bloqué.
- ⚠️ **Il n'y a plus de catégorie `google`** (retirée le 2026-08-25) : couper `google.com`
  emportait `gstatic`/`googleapis`, donc reCAPTCHA, les cartes et les polices — une part
  énorme du web. `youtube` reste et ne l'a jamais incluse.
- ⚠️ Le filtre doit couvrir l'**IPv6** : `address=/d/0.0.0.0` ne couvre que l'IPv4, il faut
  **aussi** `address=/d/::`.

### 12.3 API FAI pour le système de paiement

**Ce que ça fait** — Le système de facturation coupe et rétablit les abonnés **par MAC**,
sans connaître notre inventaire.

**Fichiers**
- `backend/app/api/endpoints/fai.py` — `POST /fai/block`, `/fai/unblock`, `GET /fai/status`
- `backend/app/api/endpoints/fai_verify.py` — `GET /fai/verify` (contrôle pré-vol **live**)
- `backend/app/services/ssh_service.py` → `verify_lr_live`
- `backend/app/api/deps.py` → `require_fai_client`, `require_verify_client`
- `nginx/nginx.conf` — listener dédié + timeouts
- `docs/API_BLOCAGE_CLIENT.md`, `docs/api-fai-verify.md`

**À savoir**
- ⚠️ **`proxy_read_timeout` doit rester élevé** sur ces locations : poser un blocage ouvre
  une session SSH et attend son tour dans la file. À 30 s, l'appelant reçoit un **504 sur
  un blocage RÉELLEMENT appliqué** — piège déjà corrigé sur `/fai`, puis re-rencontré sur
  `/uisp/assign` et `/content-filter`.
- ⚠️ **`/fai/verify` teste l'équipement EN DIRECT** (poignée de main SSH à chaque appel),
  il ne lit pas les colonnes de sonde. LR éteint ⇒ `KO` avec `ssh_active=false` : un
  contrôle live ne se prononce pas sur ce qu'il ne joint pas.
- ⚠️ Valider le mode routeur sur la clé **`netmode`** (identique LTU et airOS), **pas** sur
  les `bridge.*` internes VLAN.
- Un LR introuvable renvoie **200 avec `KO`**, pas 404.

### 12.4 Journal des actions FAI (piste d'audit)

**Ce que ça fait** — Un fichier texte, une ligne par coupure/rétablissement, lisible sans
outil, qui survit à un `docker compose down`.

**Fichiers**
- `backend/app/services/fai_audit.py` — écriture + **`_parse`** (relecture)
- `backend/app/api/endpoints/fai_journal.py` — `GET /fai-journal`, `/fai-journal/evidence`
- `frontend/app/fai-journal/page.tsx`
- `backend/tests/test_fai_audit_user.py`, `test_fai_audit_full_scan.py`
- `backend/scripts/backfill_journal_source.py`
- Fichier : `FAI_LOG_PATH` (défaut `/app/logs/fai_actions.log`, monté sur `backend/logs/` côté hôte — le dossier est créé au premier démarrage, il n’est pas dans le dépôt)

**À savoir**
- ⚠️ **Le format a changé sur un fichier DÉJÀ écrit** (ajout du champ `user=`). `_parse`
  reconnaît le champ à sa **forme**, pas à sa position — le nombre de champs ne sépare pas
  les deux formats. **Repasser à un split à arité fixe rejetterait d'un coup tout
  l'historique antérieur**, c'est-à-dire effacerait la piste d'audit en la laissant sur
  disque.
- ⚠️ **`user` ≠ `source`** : `source` dit quel **script** a appelé, `user` dit quel **agent**
  est derrière. Deux agents passent par le même script.
- `user` est **facultatif** : un ordre de coupure ne doit jamais échouer sur un champ
  d'audit manquant (un abonné impayé resterait en ligne pour ça).
- Actions journalisées : `BLOCK`, `UNBLOCK`, `RETRY_OK`, `ABANDON`, **`IDENT_KO`**,
  `ROUTER_BLOCK`.

### 12.5 Blocage de secours par le routeur de cœur (MikroTik)

**Ce que ça fait** — Quand le LR d'un abonné est éteint ou refuse le SSH, le routeur central
coupe depuis le cœur du réseau — sans avoir besoin du LR.

**Fichiers**
- `backend/app/services/mikrotik_service.py` — client RouterOS, `is_supervisor_comment`
- `backend/app/services/router_rules_service.py` — lecture **en direct** des règles posées
- `backend/app/api/endpoints/router_rules.py` → `GET /router-rules`
- `frontend/app/router-rules/page.tsx`
- `backend/tests/test_router_block_fallback.py`, `test_router_rules.py`
- `backend/scripts/mark_router_blocked.py`

**À savoir**
- ⚠️ C'est un **filet de sécurité**, jamais le mécanisme principal. Contexte : sur un run du
  2026-07-14, **163 clients sur 222** n'ont pas pu être coupés par leur LR.
- ⚠️ La page `/router-rules` est la **seule** qui réponde à « ce client a payé, pourquoi
  est-il coupé ? ». Elle croise les règles réelles avec l'intention en base et rend trois
  états : `expected`, **`unexpected`** (la base ne veut plus couper → il reste hors ligne à
  tort) et `unknown`. Plus l'écart **inverse** : la base croit couper, le routeur n'a rien.
- ⚠️ **502 si le routeur est injoignable** : une liste vide se lirait « aucun client
  bloqué ».
- ⚠️ **Pas de rafraîchissement automatique** : chaque chargement ouvre une session API
  RouterOS. Le clic dans le menu **est** la demande.
- Lecture seule : retirer une règle depuis cette page serait annulé par l'enforcement au
  cycle suivant.

---

## 13. Intégration UISP

### 13.1 Client du contrôleur

**Fichiers**
- `backend/app/services/uisp_service.py` — login → token, `fetch_devices`, `fetch_sites`, `fetch_data_links` ; **lecture seule**

**À savoir** — ⚠️ `UISP_API_TOKEN` est **en lecture seule**. Le token en **écriture**
(`UISP_WRITE_API_TOKEN`) est réservé à `POST /uisp/assign` : aucun job de fond ne peut
modifier le contrôleur, quoi qu'il arrive, et le token d'écriture est révocable sans
interrompre la supervision.

### 13.2 Import de l'inventaire (infra + stations)

**Fichiers**
- `backend/app/services/uisp_sync_service.py` — `sync_uisp_devices`, `sync_uisp_stations`, **`classify_device`** (le classificateur unique du projet), `_adopt_uisp_attribution`, `_demote_reclassified_stations`, `_convert_ptp_litebeam_to_lr`
- `backend/app/api/endpoints/uisp.py` → `POST /uisp/sync` (`?dry_run=true`)
- `backend/app/tasks/jobs.py` → `uisp_sync_job` (quotidien + 1× au démarrage)
- `backend/tests/test_uisp_attribution_adoption.py`, `test_uisp_demote_ptp_to_station.py`

**À savoir**
- ⚠️ **L'infra n'est JAMAIS supprimée** ; les **stations, SI** — un LR issu de UISP
  (`uisp_synced_at` renseigné) dont la MAC a disparu du roster a été **déprovisionné** →
  `session.delete`. Un client **découvert par radio seul** n'est **jamais** supprimé.
- ⚠️ **Garde-fou anti-catastrophe** : la passe de suppression est **entièrement sautée si le
  roster revient vide** — un payload vide serait sinon lu comme « tout le monde
  déprovisionné » et **purgerait tout le parc**.
- ⚠️ **Arbitrage « la source qui a vu la station le plus récemment gagne »** : le
  rattachement radio ne voit que les clients **allumés**, donc un client qui déménage puis
  s'éteint restait figé sur son ancien AP, son ancien site et son ancienne IP (morte).
- ⚠️ **L'AP se reprend, l'IP presque jamais** : pour une station déconnectée, l'IP annoncée
  par UISP n'est qu'un **dernier état connu** que le DHCP a pu réattribuer (au 1er passage
  réel, UISP a rendu la même IP pour **trois** abonnés). L'IP n'est reprise que sous
  conditions strictes, et **jamais volée** à un autre détenteur.
- ⚠️ **La reclassification va dans LES DEUX SENS** : une LiteBeam P2P déposée d'un mât et
  réinstallée chez un client change de **nature** sans changer d'identité. Sans
  `_demote_reclassified_stations`, la ligne fantôme restait comptée dans la capacité infra,
  pingée, **alertée sur WhatsApp pour un équipement sain**, pendant que l'abonné était
  absent de `/access` et **incoupable**. ⚠️ **JAMAIS un AF60** : `role=station` est son état
  **normal** à un bout de chaque lien P2P.
- Les credentials sont posés **par convention famille/site à la CRÉATION** seulement,
  jamais en écrasement.

### 13.3 Enrôlement UISP d'un CPE (pose de la clé par SSH)

**Ce que ça fait** — Pose la clé du contrôleur sur un CPE **sans reboot ni coupure**, pour
qu'il apparaisse dans l'inventaire — donc qu'il soit facturable.

**Fichiers**
- `backend/app/services/uisp_enrollment_service.py`
- `backend/app/services/ssh_service.py` → `set_uisp_key`, **`uisp_uri_host`**
- `backend/app/api/endpoints/devices.py` → `POST /devices/{id}/enroll-uisp`
- `backend/app/api/endpoints/access_diagnostics.py` → `POST /access-diagnostics/enroll-uisp` (lot)
- `frontend/app/access-diagnostics/page.tsx`

**À savoir — trois règles qu'aucune documentation Ubiquiti ne donne**
1. ⚠️ **`/tmp/system.cfg` et `/tmp/running.cfg` doivent être IDENTIQUES** au moment de la
   pose, sinon le CPE se connecte puis refuse de persister, **en boucle 1×/s**.
2. ⚠️ **La clé écrite n'est qu'un jeton d'enrôlement** : après adoption, le contrôleur émet
   une clé **propre à l'équipement** et réécrit `unms.uri` lui-même.
3. ⚠️ **`/var/run/unms-conn-status` est un drapeau de SESSION**, pas un état d'enrôlement :
   il vaut **0 par intermittence sur un équipement parfaitement enrôlé**.

**Conséquence** : l'idempotence porte sur l'**hôte de `unms.uri`**, jamais sur le jeton ni
sur le drapeau.
- ⚠️ **`force=True` DÉ-ENRÔLE un équipement sain.** Réservé à une clé orpheline.
- ⚠️ **La clé d'enrôlement cesse d'être honorée** : la même clé a adopté trois CPE puis a
  été refusée une heure plus tard. **Régénérer la clé dans UISP juste avant une campagne** —
  une clé morte fait échouer *toute* la campagne.
- ⚠️ **Toujours sauvegarder la config avant d'écrire** : forcer avec une clé morte sur un
  équipement adopté lui fait **perdre sa clé propre**. Il a fallu la récupérer dans une
  sauvegarde pour le faire revenir.
- **Aucun job d'enforcement** : un enrôlement est ponctuel, le rejouer dé-enrôlerait.

### 13.4 Association équipement ↔ client CRM

**Fichiers**
- `backend/app/services/uisp_assignment_service.py`
- `backend/app/api/endpoints/uisp_assign.py` → `POST /uisp/assign` (**router séparé**)
- `backend/tests/test_uisp_assign_scoped_key.py`
- `docs/api-uisp-assign.md` — **doc d'intégration pour le tiers**

**À savoir**
- ⚠️ **Le contrat est volontairement minimal : une MAC et un id CRM, rien d'autre.** Le
  **site** est une plomberie **interne** jamais exposée (UISP rattache à un site, et c'est
  le site qui porte `ucrm.client.id`).
- ⚠️ **L'id CRM, jamais le nom** : sur 1402 clients, **7 noms désignent deux clients
  différents**. Le nom seul assignerait au mauvais abonné.
- ⚠️ **Client à plusieurs services** (6 sur 1402) : **409 avec la liste**, jamais
  d'arbitrage. Et le service se désigne par son **id** — les noms de services d'un même
  client sont régulièrement **identiques**.
- ⚠️ **Un équipement déjà rattaché à un AUTRE client n'est jamais déplacé en silence** :
  409, `reassign=true` pour passer outre.
- ⚠️ **Consommée en HTTPS obligatoirement** : le port 80 renvoie un `301`, et une
  redirection **convertit un POST en GET et détruit le corps JSON** → `405`.

---

## 14. Diagnostics et hygiène du parc

### 14.1 Diagnostics d'accès abonné

**Fichiers**
- `backend/app/services/access_diagnostics_service.py`
- `backend/app/api/endpoints/access_diagnostics.py`
- `frontend/app/access-diagnostics/page.tsx`
- `backend/tests/test_access_diagnostics.py`

**À savoir** — Deux anomalies **invisibles ailleurs** : (1) les LR qui **refusent le SSH**
(les hors-ligne sont **exclus** — ce n'est pas un refus) et (2) les LR **vus par radio mais
absents de UISP** (donc **potentiellement non facturés**). La seconde porte l'action
d'enrôlement en lot.

### 14.2 Hygiène des IP

**Fichiers**
- `backend/app/services/ip_hygiene_service.py` — `run_cleanup`
- `backend/app/tasks/jobs.py` → `unverified_ip_cleanup_job` (12 h)
- `backend/scripts/clear_unverified_ips.py` (dry-run par défaut)
- `backend/tests/test_clear_unverified_ips.py`

**À savoir** — ⚠️ **C'est un FILET, pas la protection.** La vraie protection contre une
action sur le mauvais abonné est le **contrôle d'identité MAC** avant chaque blocage
(§12.1). Une adresse périmée est **pire que pas d'adresse**. Rien n'est supprimé : la
découverte rend son IP à la ligne dès qu'un AP la rapporte.

### 14.3 Hors supervision

**Fichiers**
- `backend/app/schemas/device.py` → `is_out_of_supervision`
- la fonction SQL `fn_access_clients` (migration `cc3d4e5f6a7b`)
- `frontend/app/access/page.tsx`
- `backend/tests/test_out_of_supervision.py`
- `backend/scripts/block_out_of_supervision.py`

**À savoir** — ⚠️ **La règle est écrite DEUX FOIS** (Python et SQL) : **les garder
d'accord**. Un LR sans IP **et** que UISP n'a pas vu depuis 7 j : les deux sources se
taisent, donc ni panne constatée ni accès actif. Badge **ambre** (et non rouge « INCONNU »,
lu à tort comme une panne — **124 lignes sur ~1000** en prod). **Aucune suppression.**

### 14.4 Auto-guérison de la clé d'hôte SSH

**Fichiers**
- `backend/app/services/ssh_service.py`

**À savoir** — Ré-épinglage de la clé d'hôte **uniquement si la MAC concorde** ;
invérifiable = **refus**.

---

## 15. Frontend — pages du dashboard

Toutes les pages passent par `frontend/app/api/proxy/[...path]/route.ts` (qui porte le
cookie de session) et par `frontend/lib/api.ts`.

| Page | Fichier | Rôle |
|---|---|---|
| Dashboard | `frontend/app/page.tsx` | KPI globaux (`fn_dashboard_summary`) |
| Sites | `frontend/app/sites/page.tsx` | Cartes par site + **fiche équipement** (`DeviceDetailModal`) — c'est ici qu'on ouvre un équipement, il n'y a pas de page `/devices` |
| Liaisons clients | `frontend/app/lr-health/page.tsx` | Qualité des liens abonnés, mesurée en live |
| Consommation clients | `frontend/app/clients/page.tsx` | Volumes par abonné, 24h/7j/30j/plage libre |
| Capacité du réseau | `frontend/app/capacity/page.tsx` | Clients vs max par Rocket + capacité infra par site |
| Topologie | `frontend/app/topology/page.tsx` | Graphe/carte inter-sites, routes Internet, export Word |
| Carte des clients | `frontend/app/map/page.tsx` | Positions abonnés + liste des `outliers` |
| Destinations Internet | `frontend/app/traffic/page.tsx` | Débit live, historique, volume par opérateur |
| FAI — Accès clients | `frontend/app/access/page.tsx` | Roster abonnés, blocage, filtres |
| FAI — Journal blocages | `frontend/app/fai-journal/page.tsx` | Piste d'audit |
| FAI — Règles du routeur | `frontend/app/router-rules/page.tsx` | Ce que le MikroTik porte **vraiment** |
| FAI — Filtre de contenu | `frontend/app/content-block/page.tsx` | Cases par plateforme |
| Incidents | `frontend/app/incidents/page.tsx` | Anomalies infra en cours |
| Diagnostics d'accès | `frontend/app/access-diagnostics/page.tsx` | SSH refusé + absents de UISP + enrôlement |
| Rapports | `frontend/app/reports/page.tsx` | Capacité + journal des coupures sur une période |
| Seuils | `frontend/app/settings/page.tsx` | Réglage des seuils d'alerte à chaud |
| Login | `frontend/app/login/page.tsx` | Authentification |

**Composants transverses**
- `frontend/components/AppShell.tsx` — coque : bandeau collant, barre de recherche globale (Ctrl/⌘+K), menu, **`AlertBanner`**, `FULL_WIDTH_ROUTES`
- `frontend/components/Sidebar.tsx` — la navigation
- `frontend/components/DeviceSearchBar.tsx` — recherche par nom ou IP (le nom d'un LR porte le **nom et le téléphone** du client)
- `frontend/lib/topologyColors.ts` — **barème partagé graphe/carte** : ne jamais le dupliquer, deux écrans du même réseau qui se contrediraient seraient pires que pas de carte
- `frontend/lib/types.ts`, `frontend/lib/api.ts`

**À savoir**
- ⚠️ **Le paramètre `?device=` est CONSOMMÉ** (retiré de l'URL) après ouverture de la fiche,
  sinon rechercher **deux fois** le même équipement ne rouvrirait pas sa fiche.
- ⚠️ **Aucun garde « déjà traité » sur ce deep-link** : combiné au drapeau d'annulation, il
  verrouillait la fiche sur un chargement perpétuel en **Strict Mode** (dev uniquement,
  donc invisible en build de prod).
- ⚠️ L'image de pylône est dans `frontend/public/devices/` : le middleware d'auth intercepte
  tout sauf ce dossier — ailleurs elle serait redirigée vers `/login`.

---

## 16. Déploiement et exploitation

### 16.1 Les 12 conteneurs de production

| Conteneur | `RUN_MODE` / groupe | Rôle |
|---|---|---|
| `postgres` | — | Base de données |
| `backend` | `api` | uvicorn + **migrations Alembic au démarrage** |
| `frontend` | — | Next.js |
| `nginx` | — | Reverse proxy TLS |
| `scheduler` | `fast` | Ping infra + maintenance |
| `scheduler-heavy` | `heavy` | SSH (sonde LR, blocage) + power + sync UISP + topologie |
| `scheduler-ping-lr` | `ping-lr` | Ping des LR clients, **seul** |
| `scheduler-poll-switch` | `poll-switch` | SNMP des switches, **seul** |
| `scheduler-poll-af60` | `poll-af60` | Poll HTTP AF60, **seul** |
| `scheduler-poll-ltu` | `poll-ltu` | Poll HTTP LTU + fan-out, **seul** |
| `scheduler-poll-airos` | `poll-airos` | Poll HTTP airOS, **seul** |
| `netflow-collector` | `collector` | Écoute NetFlow UDP |

**Fichiers**
- `docker-compose.yml` (base, dev), `docker-compose.prod.yml`, `docker-compose.lan.yml`, `docker-compose.public.yml`
- `backend/app/tasks/scheduler.py`, `runner.py`, `collector_runner.py`
- `backend/app/tasks/jobs.py` → **`_JOBS_BY_GROUP`** (la répartition)
- `nginx/nginx.conf`, `nginx/certs/`
- `Makefile`
- `scripts/start.sh`, `scripts/generate-self-signed-cert.ps1`

**À savoir**
- ⚠️ **UN GROUPE SANS CONTENEUR FAIT DISPARAÎTRE SES JOBS EN SILENCE.** Ajouter une clé à
  `_JOBS_BY_GROUP` **impose** d'ajouter le service correspondant dans
  `docker-compose.prod.yml`. Et `restart` ne suffit pas : il faut `up -d <service>`.
- ⚠️ **Un job non classé reste en `fast`** (jamais perdu silencieusement) — c'est
  intentionnel.
- ⚠️ **Pourquoi cet éclatement** : en mono-process, la sonde SSH (paramiko tient le GIL
  pendant sa crypto) **affamait le ping** (`last_seen` figé ~20 min) ; et la phase 2 de
  ltu/airos (tours de **7 à 22 min** mesurés) affamait AF60, dont la courbe n'avait qu'un
  point toutes les ~3 min au lieu d'une par minute.
- ⚠️ `UVICORN_WORKERS > 1` **uniquement** parce que `SCHEDULER_ENABLED=false` côté backend —
  sinon chaque worker démarrerait son propre scheduler (SSH et alertes en double).

### 16.2 Commande de déploiement — les 3 fichiers obligatoires

> ⚠️ **TOUTE** commande `docker compose` sur cette stack (`up`, `logs`, `exec`, `restart`,
> `down`…) doit reprendre les **3 `-f` + `LAN_BIND_IP`**, sinon le binding LAN saute et le
> dashboard devient injoignable depuis le réseau interne.
>
> ⚠️ **Le `Makefile` ne compose PAS `docker-compose.lan.yml`** : ne pas l'utiliser pour la
> production sans le corriger.

```bash
# Sur le serveur 10.135.3.25
git pull
export LAN_BIND_IP=10.135.3.25
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lan.yml'

dc up -d --build
dc logs -f backend          # suivre les migrations + le démarrage
dc exec backend python scripts/create_admin.py   # 1re fois seulement
```

**Déploiement sans rebuild** : le backend est **bind-monté** → `dc restart <service>` suffit
pour du code Python. Le frontend est **cuit dans l'image** → `dc up -d --build frontend`.

### 16.3 Exposition réseau

- nginx est bindé sur **`127.0.0.1`** + l'IP LAN via `docker-compose.lan.yml`.
- ⚠️ **NE JAMAIS BINDER `0.0.0.0`** — cause racine de l'incident du 2026-05-17.
- Accès distant : tunnel SSH `ssh -L 8443:127.0.0.1:443 a2@<serveur>`.
- Le FortiGate 40F fait le DNAT :443 vers une VIP, avec une **allowlist de sources** :
  ajouter une IP = l'ajouter au groupe `A2-Allowed-Sources`.
- `docs/deploy-windows-server.md`

### 16.4 Sauvegarde de la base

**Fichiers**
- `scripts/backup-db.sh`, `scripts/push-backup.sh`, `scripts/receive-backup.ps1`
- `docs/backup-database.md`

**À savoir** — Sync.com n'a **ni client Linux, ni API, ni WebDAV, ni support rclone**
(**ne pas re-chercher**). La production **pousse** ses archives chiffrées vers le serveur
Windows du LAN **`10.135.0.210`** (Windows Server 2016, compte local `backup` non
administrateur, SSH par clé uniquement), qui vérifie le SHA et les range dans
`C:\Backups\supervisor`. Le relais AWS puis `10.135.0.33` ont été abandonnés.

### 16.5 ⚠️ Le serveur est partagé avec des charges étrangères

Le serveur de production héberge aussi un **NVR `shinobi`** hors compose, qui a déjà saturé
le CPU. ⚠️ **Sur une machine saturée, `docker stats` dit qui RAME, pas qui consomme** — ne
pas accuser le premier conteneur de la liste. `wazuh-indexer` est **conservé
volontairement** (c'est le SIEM). Voir `docs/performance-audit.md`.

---

## 17. Tests

**Emplacement** — `backend/tests/` (35 fichiers de test), fixtures dans `backend/tests/fixtures/`,
configuration dans `backend/tests/conftest.py`.

```bash
dc exec backend pytest
dc exec backend pytest tests/test_alert_rules.py -v
dc exec backend ruff check app/ && dc exec backend ruff format app/
```

**Les tests qui verrouillent une décision** (les casser doit être un **choix conscient**,
jamais un effet de bord) :

| Test | Ce qu'il protège |
|---|---|
| `test_snmp_poll_device_isolation.py` | L'**ordre de verrouillage** de toutes les boucles d'écriture |
| `test_discovery_lock_order.py` | Idem côté découverte |
| `test_manual_alert_banner.py` | L'**ordre dans le source** du point d'accroche du bandeau |
| `test_af60_link_occupancy.py` | La formule d'occupation (et le fait que le type reste **hors** WhatsApp) |
| `test_uisp_assign_scoped_key.py`, `test_content_filter_api.py` | Le **cloisonnement des clés API** |
| `test_uisp_demote_ptp_to_station.py` | Les 3 garde-fous de rétrogradation (dont « jamais un AF60 ») |
| `test_fai_audit_user.py` | La relecture des **deux formats** du journal |
| `test_block_identity_guard.py` | Le contrôle d'identité avant blocage |
| `test_site_map_export.py` | Position des sites, légende, sources Internet |

⚠️ **Une fixture doit rester la preuve de ce que l'équipement envoie**, pas le miroir de ce
que le code sait lire. Une fixture airOS « trimmée aux champs utiles » avait fait conclure à
tort que le firmware n'exposait pas le débit.

---

## 18. Scripts d'administration

Tous dans `backend/scripts/` (à lancer par `dc exec backend python scripts/<nom>.py`) :

| Script | Usage |
|---|---|
| `create_admin.py` | Créer un compte du dashboard |
| `detect_switch_ports.py` | **Contrôle à blanc** du mapping des ports (n'écrit rien sans `--apply`) |
| `dump_site_topology.py` | Le graphe inter-sites en console (`--sync`, `--site`, `--json`) |
| `clear_unverified_ips.py` | Nettoyage des IP non confirmées (dry-run par défaut) |
| `block_clients.py` / `unblock_clients.py` | Blocage en lot depuis un CSV |
| `block_out_of_supervision.py` | Blocage des LR hors supervision |
| `compare_active_macs.py` / `find_missing_macs.py` | Réconciliation d'inventaire |
| `seed_devices.py` | Amorçage d'un environnement de test |
| `ltu_dump_linkquality.py` | Dump brut d'un lien LTU pour investigation |
| `dump_lr_uptime.py` | Historique de disponibilité d'un LR |
| `site_rocket_headroom.py` | Marge de capacité par Rocket |
| `build_site_map_basemaps.py` | Régénérer les fonds de carte (depuis un poste **connecté**) |
| `render_fiber_ring_plan.py` | Rendu du plan de boucle fibre |
| `mark_router_blocked.py` | Marquer des lignes comme bloquées côté routeur |
| `backfill_journal_source.py` | Rétro-remplissage du champ `source` du journal FAI |

Scripts hôte (`scripts/`) : `backup-db.sh`, `push-backup.sh`, `receive-backup.ps1`,
`generate-self-signed-cert.ps1`, `start.sh`.

---

## 19. Les règles à ne jamais casser

Chacune a coûté un incident réel. La section indiquée en donne le détail, et la
docstring du module concerné en donne la mesure d'origine.

1. **L'identité d'un équipement est sa MAC.** Jamais son IP, jamais son nom.
2. **Contrôle d'identité MAC avant toute action de blocage.** La fiche cible une MAC, la
   session SSH part sur une IP que le DHCP a pu réattribuer.
3. **Ne jamais binder nginx sur `0.0.0.0`.** (Incident 2026-05-17.)
4. **Une attribution de port ne s'efface jamais sur une absence de MAC** — l'absence *est*
   le moment où l'alerte doit partir.
5. **Ne jamais effacer sur un payload vide** (roster UISP, data-links, topologie) : « vide »
   veut dire « échec de lecture », pas « tout a disparu ».
6. **Tout chemin qui retire un équipement du balayage de ping doit écrire son statut**,
   sinon il ment pour toujours.
7. **« Pas mesuré » n'est jamais « zéro ».** Ni sur une courbe, ni sur une liaison, ni sur
   une couleur de carte.
8. **Toute boucle séquentielle qui écrit des lignes `devices` les parcourt par `id`
   croissant.**
9. **Une nouvelle courbe va dans `GRAPH_METRICS`, jamais dans `HISTORY_METRICS`.**
10. **Un problème côté client n'est jamais un incident d'infrastructure.**
11. **Les incidents de disponibilité ne se suppriment pas** — le journal des coupures se
    reconstruit dessus.
12. **Un nouveau groupe de scheduler exige un nouveau conteneur**, sinon ses jobs
    disparaissent en silence.
13. **Ne jamais recopier un barème, un seuil ou un classificateur** — les importer. Deux
    copies divergent au premier ajustement.
14. **Ne pas supprimer les commentaires de verdict terrain** : ils évitent de refaire des
    investigations qui ont pris des jours.


---

## Annexe A — Index inversé : fichier → fonctionnalité

**À quoi ça sert** : tu ouvres un fichier et tu ne sais pas ce qu'il fait. Cette annexe
répond en une ligne et te renvoie à la section qui détaille.

### `backend/app/services/` — la logique métier (47 modules)

| Fichier | Ce qu'il fait | Section |
|---|---|---|
| `access_diagnostics_service.py` | LR qui refusent le SSH + LR absents de UISP | §14.1 |
| `af60_api_service.py` | API des backhauls AF60 (60 GHz) | §4.4 |
| `airos_api_service.py` | API airOS des airMAX AC et LiteBeam M5 | §4.3 |
| `alert_engine.py` | Orchestre : évalue les règles, tient l'anti-flap, ouvre/résout | §6.3 |
| `alert_formatter.py` | Met en forme le message WhatsApp par type d'alerte | §7.1 |
| `alert_policy.py` | Registre interne canal/groupable/recovery par type | §6.3 |
| `alert_rules.py` | Les règles d'alerte, **fonctions pures sans DB** | §6.2 |
| `asn_service.py` | IP → opérateur Internet (ASN) | §10 |
| `auth_service.py` | Mots de passe bcrypt + sessions serveur | §2.2 |
| `client_block_service.py` | Blocage abonné (2 modes) + filtre de contenu | §12.1, §12.2 |
| `client_signal_service.py` | Signal + latence d'un client, pour une API tierce | §11.4 |
| `consumption_service.py` | Consommation par abonné (deltas de compteurs) | §11.1 |
| `device_map_service.py` | Carte des clients + séparation des coordonnées aberrantes | §11.3 |
| `device_service.py` | CRUD des équipements | §3.1 |
| `digest_service.py` | Regroupe les warnings en un message toutes les 15 min | §7.2 |
| `discovery_service.py` | **Découverte des abonnés par radio** — le cœur de l'inventaire | §3.2 |
| `fai_audit.py` | Journal texte des coupures (piste d'audit métier) | §12.4 |
| `incident_service.py` | Ouverture/résolution/suppression des incidents | §6.4 |
| `ip_hygiene_service.py` | Retire les IP que plus aucune source ne confirme | §14.2 |
| `lan_discovery.py` | Trouve le modem du client derrière son LR | §3.3 |
| `lr_health_service.py` | Santé des liaisons abonnés (mesure **live**) | §11.5 |
| `lr_metric_history_service.py` | **`GRAPH_METRICS`** — les courbes de la fiche équipement | §4.7 |
| `lr_plan_service.py` | Forfait de l'abonné, lu sur le shaper airOS + GPS | §11.2 |
| `ltu_api_service.py` | API des Rockets LTU + liste de leurs CPE | §4.2 |
| `manual_alert_service.py` | Bandeau d'anomalies à acquitter à la main | §6.5 |
| `mikrotik_service.py` | Blocage de secours par le routeur de cœur | §12.5 |
| `netflow_service.py` | Collecteur NetFlow (process dédié) | §10 |
| `network_capacity_service.py` | Clients installés vs capacité max par Rocket | §8.1 |
| `network_uptime_service.py` | Journal des coupures + disponibilité % | §5.2 |
| `notification_service.py` | **`_dispatch` = le chokepoint des notifications** | §7.1 |
| `poller.py` | Ping ICMP groupé (`fping`) | §5.1 |
| `router_rules_service.py` | Lit **en direct** ce que le routeur porte vraiment | §12.5 |
| `saturation_report_service.py` | PDF quotidien des Rockets saturés | §7.3 |
| `site_infra_service.py` | Budget d'équipements infra par site | §8.2 |
| `site_map_service.py` | Cartographie imprimable des sites (export Word) | §9.3 |
| `site_topology_service.py` | **Topologie inter-sites + routes Internet** | §9.1, §9.2 |
| `snmp_service.py` | SNMP radios et switches + table FDB | §4.1 |
| `ssh_service.py` | **Toutes les opérations SSH** + contrôle d'identité | §4.6, §12.1 |
| `switch_port_service.py` | Quel équipement sur quel port de switch | §6.6 |
| `threshold_service.py` | Seuils réglables à chaud (DB par-dessus l'env) | §2.3 |
| `traffic_service.py` | Volume et débit par opérateur Internet | §10 |
| `uisp_assignment_service.py` | Associe un équipement à un client CRM | §13.4 |
| `uisp_enrollment_service.py` | Pose la clé du contrôleur sur un CPE | §13.3 |
| `uisp_power_service.py` | Onduleurs UISP Power (tension, batteries) | §4.5 |
| `uisp_service.py` | Client REST du contrôleur UISP (**lecture seule**) | §13.1 |
| `uisp_sync_service.py` | Import inventaire + stations + reclassification | §13.2 |
| `whatsapp_service.py` | Envoi Ultramsg (message et document) | §7.1 |

### `backend/app/tasks/` — l'ordonnanceur

| Fichier | Ce qu'il fait | Section |
|---|---|---|
| `jobs.py` | **Tous les jobs planifiés** + `persist_device_metrics` + `_JOBS_BY_GROUP` | §4.0, §16.1 |
| `scheduler.py` | Initialisation APScheduler, cycle de vie | §16.1 |
| `runner.py` | Entrée des conteneurs `RUN_MODE=scheduler` | §16.1 |
| `collector_runner.py` | Entrée du conteneur `RUN_MODE=collector` (NetFlow) | §10 |

> ⚠️ **`jobs.py` est le plus gros fichier du projet** (~4 050 lignes) et le point d'entrée
> de presque toute la collecte. Y chercher par nom de job (`grep "async def .*_job"`).

### `backend/app/models/` — les tables

| Fichier | Table | Section |
|---|---|---|
| `device.py` | `devices` + sous-classes (Rocket, Lr, Switch, Power, AF60, PTP) | §3.1 |
| `device_metric.py` | `device_metrics` — métriques, **collapse sauf compteurs** | §4.0 |
| `lr_metric_sample.py` | `lr_metric_samples` — les **courbes** en buckets | §4.7 |
| `incident.py` | `incidents` | §6.4 |
| `alert_state.py` | Compteurs anti-flapping persistés | §6.3 |
| `manual_alert.py` | Bandeau à acquitter à la main | §6.5 |
| `site_link.py` | Câblage inter-sites (backhauls) | §9.1 |
| `site_location.py` | Coordonnées des pylônes (jointes à la topologie) | §9.1 |
| `power_status_log.py` | Relevés UISP Power | §4.5 |
| `traffic_dest_stat.py` | Agrégats NetFlow par opérateur | §10 |
| `system_setting.py` | Seuils réglés à chaud | §2.3 |
| `user.py`, `auth_session.py` | Comptes et sessions du dashboard | §2.2 |
| `audit_log.py` | Journal des appels d'API modifiants | §2.5 |

### `backend/app/core/` — à connaître par cœur

| Fichier | Ce qu'il fait |
|---|---|
| `alert_constants.py` | **Source unique de vérité** des 24 `alert_type` et des listes blanches |
| `config.py` | Toute la configuration (`Settings`) + validation au démarrage |
| `alert_labels.py` | Libellés lisibles des types d'alerte |
| `exceptions.py`, `logging.py` | Handlers globaux, logs structurés |

---

## Annexe B — Runbook : symptôme → où regarder

**À quoi ça sert** : la table à ouvrir quand quelque chose se passe. Elle ne remplace pas le
diagnostic, elle évite de chercher au mauvais endroit.

### Alertes et notifications

| Symptôme | Première piste | Section |
|---|---|---|
| « On ne reçoit plus rien sur WhatsApp » | Vérifier `POST /system/test-whatsapp`, puis les identifiants Ultramsg | §7.1 |
| « Cette anomalie n'a jamais alerté » | ⚠️ Le type est-il dans **`WHATSAPP_ALERT_TYPES`** ? La plupart ne notifient **rien**, volontairement | §7.1 |
| « L'alerte part en boucle » | Les compteurs anti-flap (`AlertState`) et le seuil de la règle | §6.3 |
| « Un incident ne se résout jamais » | Une chaîne `alert_type` recopiée quelque part au lieu d'être importée | §6.1 |
| « Une anomalie client n'apparaît pas dans /incidents » | **Normal** : `is_suppressed_incident` — un problème client n'est jamais un incident | §6.4 |
| « Le bandeau du dashboard ne se vide pas » | Il ne part **que** sur clic humain ; c'est le principe | §6.5 |

### Mesures et équipements

| Symptôme | Première piste | Section |
|---|---|---|
| « Des équipements sains sont marqués HORS LIGNE » | La re-confirmation isolée du ping (rate-limit ICMP des radios) | §5.1 |
| « Un équipement reste EN LIGNE alors qu'il est mort » | A-t-il perdu son IP ? Sans IP il **sort du balayage** et son statut se fige | §5.1 |
| « Plus aucune métrique de switch » | Le conteneur `scheduler-poll-switch` tourne-t-il ? | §4.1, §16.1 |
| « Un port de switch n'est pas mesuré » | `SWITCH_MAX_PORTS` : c'est une **fenêtre de scan**, rien au-delà n'existe | §4.1 |
| « Le débit affiché est faux ou vide » | Chaque famille radio a **sa propre clé** et sa propre unité | §4.2 à §4.4 |
| « La courbe d'un graphe a des trous » | **Normal** : un bucket sans relevé est absent, jamais ramené à 0 | §4.7 |
| « La base grossit anormalement » | Une métrique a-t-elle été ajoutée à `HISTORY_METRICS` ? | §4.0 |

### Abonnés et FAI

| Symptôme | Première piste | Section |
|---|---|---|
| « Ce client a payé mais reste coupé » | **`/router-rules`** — l'état `unexpected`. C'est la seule page qui réponde | §12.5 |
| « On n'arrive pas à couper ce client » | LR éteint / SSH refusé → le filet est le routeur de cœur | §12.5 |
| « Le blocage a été refusé » | `IDENT_KO` dans le journal = la MAC ne correspond pas, la fiche est périmée | §12.1 |
| « Le filtre de contenu ne filtre rien » | ⚠️ Quirk airOS 8 : il faut `killall dnsmasq`, pas `kill -HUP` | §12.1 |
| « Bloquer une plateforme a tout ouvert » | Le client était en `allowlist` — le sens de l'ensemble s'inverse | §12.2 |
| « Un abonné a disparu de l'inventaire » | Sync UISP : déprovisionné dans UISP = supprimé chez nous | §13.2 |
| « Un abonné n'est nulle part et incoupable » | Équipement d'infra redevenu abonné (`_demote_reclassified_stations`) | §13.2 |
| « Un client s'affiche sur son ancien site » | La réconciliation d'identité (roaming) — arbitrage « dernier vu gagne » | §4.3, §13.2 |

### Intégrations et déploiement

| Symptôme | Première piste | Section |
|---|---|---|
| « Un tiers reçoit des 504 alors que ça a marché » | `proxy_read_timeout` nginx trop court sur une route qui ouvre du SSH | §12.3 |
| « Un tiers reçoit des 405 » | Il appelle en `http://` — le 301 détruit le corps du POST | §13.4 |
| « L'enrôlement UISP échoue partout d'un coup » | ⚠️ **La clé d'enrôlement est périmée.** La régénérer dans UISP | §13.3 |
| « Un job ne tourne plus » | Son groupe a-t-il un **conteneur** ? Un groupe sans conteneur = job disparu | §16.1 |
| « Le dashboard est injoignable depuis le LAN » | Les **3 `-f`** + `LAN_BIND_IP` ont-ils été utilisés ? | §16.2 |
| « /traffic est vide » | Collecteur activé ? Datasets ASN déposés ? Préfixes internes complets ? | §10 |
| « La topologie est vide » | Le câblage n'a jamais été synchronisé (`POST /network-topology/sync`) | §9.1 |
| « Le serveur rame » | ⚠️ Charges **étrangères** (NVR shinobi). `docker stats` dit qui rame, pas qui consomme | §16.5 |

### Les commandes de diagnostic les plus utiles

```bash
export LAN_BIND_IP=10.135.3.25
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lan.yml'

dc ps                              # les 12 conteneurs sont-ils debout ?
dc logs -f scheduler-poll-switch   # suivre UN groupe de jobs
dc exec backend pytest             # la suite de tests
dc exec backend python scripts/detect_switch_ports.py       # contrôle à blanc des ports
dc exec backend python scripts/dump_site_topology.py        # la topologie en console
curl -sk https://10.135.3.25/api/v1/health                  # santé API + DB (public)
```

---

## Annexe C — Glossaire

Le vocabulaire du domaine, indispensable pour lire le code et les alertes.

### Matériel

| Terme | Sens |
|---|---|
| **Rocket** | Station de base (**AP**) posée sur un pylône, qui dessert des dizaines d'abonnés. Deux familles : **LTU** et **airMAX**. |
| **LR** | *LTU Rocket* côté abonné — dans ce projet, **le mot désigne l'équipement de l'abonné** (le CPE), quelle que soit sa famille. C'est la table `lrs`. |
| **CPE** | *Customer Premises Equipment* — synonyme de LR : la radio chez le client. |
| **LTU / airMAX** | Les deux technologies radio Ubiquiti du parc. **Seuils et clés d'API différents** — d'où les variantes partout dans le code. |
| **LiteBeam M5 / 5AC** | Deux modèles airMAX abonnés. Le **M5** (airOS 6) est le plus limité : ni débit instantané, ni CINR fiable. |
| **AF60 / airFiber 60** | Backhaul 60 GHz entre deux sites. Très capable, très sensible à la pluie. |
| **PTP LiteBeam** | Liaison point à point entre deux sites (moins capable qu'un AF60). |
| **UISP Switch** | Switch Ubiquiti. ⚠️ N'expose **que** IF-MIB en SNMP. |
| **UISP Power** | Onduleur : secteur, **batterie interne** (Li-Ion) et **batterie externe** (plomb). |
| **MikroTik / RouterOS** | Le **routeur de cœur**, utilisé comme filet de sécurité du blocage. |

### Réseau

| Terme | Sens |
|---|---|
| **Backhaul** | Liaison entre deux **sites** (par opposition à un lien AP↔abonné). |
| **Site** | Un point haut (pylône) portant plusieurs équipements d'infra. 17 sites en service. |
| **Infra** | Tout ce qui est à nous sur un site (Rockets, AF60, switches, Power) — **par opposition aux abonnés**. Distinction structurante : seule l'infra ouvre des incidents. |
| **Uplink** | Le port de switch sur lequel un équipement est câblé. |
| **FDB** | *Forwarding Database* — la table « quelle MAC sur quel port » d'un switch. ⚠️ Absente sur nos switches UISP. |
| **LLDP** | Protocole de découverte de voisinage. ⚠️ Non émis par nos switches. |
| **ASN** | Numéro d'opérateur Internet, utilisé pour attribuer le trafic. |
| **NetFlow** | Protocole d'export de statistiques de flux par le routeur. |

### Notions du projet

| Terme | Sens |
|---|---|
| **UISP** | Le contrôleur Ubiquiti — l'inventaire officiel du parc. Nous le lisons ; nous n'y écrivons qu'à un seul endroit. |
| **CRM** | Le module client d'UISP : c'est lui qui porte le **client facturé**. |
| **Roster** | La liste des abonnés provisionnés dans UISP. |
| **Collapse** | Métrique écrasée en place (dernière valeur), par opposition à *history* (empilée). |
| **Bucket** | Fenêtre de temps agrégée d'une courbe (60 s par défaut). |
| **Anti-flap** | Nombre de cycles consécutifs avant d'ouvrir une alerte — évite d'alerter sur une rafale. |
| **Digest** | Regroupement des warnings en un seul message toutes les 15 min. |
| **Enrôlement** | Poser la clé du contrôleur sur un CPE pour qu'il apparaisse dans UISP (donc qu'il soit facturable). |
| **Hors supervision** | LR sans IP **et** que UISP n'a pas vu depuis 7 j : les deux sources se taisent. |
| **Occupation** | Temps d'antenne consommé sur un backhaul — « c'est plein », à ne pas confondre avec « c'est cassé ». |

---

## Annexe D — Inventaire des documents et fichiers particuliers

### Documentation du dépôt (`docs/`)

| Fichier | Contenu |
|---|---|
| `PASSATION.md` | **Ce document.** |
| `getting-started.md` | Mise en route d'un environnement de développement. |
| `API_BLOCAGE_CLIENT.md` | **Doc d'intégration** du blocage — à donner au système de paiement. |
| `api-content-filter.md` | **Doc d'intégration** du filtre de contenu — à donner au tiers. |
| `api-fai-verify.md` | **Doc d'intégration** du contrôle pré-vol. |
| `api-uisp-assign.md` | **Doc d'intégration** de l'association client CRM. |
| `backup-database.md` | Procédure de sauvegarde. |
| `deploy-windows-server.md` | Déploiement du relais Windows. |
| `netflow-capture-explained.md` | Comment lire une capture NetFlow. |
| `performance-audit.md` | Audit de performance du serveur. |
| `rapport-uisp-power.md` | Relevés et comportement des onduleurs. |
| `incident-2026-05-17-rapport.md` | **Le rapport d'incident de sécurité fondateur.** |
| `next-steps.md` | Notes de chantiers envisagés. |

> Les quatre **docs d'intégration** sont celles qu'on transmet à un partenaire externe :
> les tenir à jour quand une route change fait partie du travail.

### Fichiers à connaître mais à ne pas confondre avec du code actif

| Fichier | Statut |
|---|---|
| `frontend/app/topo-preview/page.tsx` | ⚠️ **Page temporaire** d'aperçu visuel avec des données factices. Son propre en-tête indique « À supprimer après validation » (avec l'exception dans `middleware.ts` / `AppShell`). **Ne pas s'en servir comme référence.** |
| `backend/scripts/clients_to_block_2026_07_14.csv` | Jeu de données d'une campagne passée. |
| `*.kmz` / `*.jpg` (racine) | Plans d'architecture réseau (actuelle, cible fibre, boucle 2027). |
| `backend/alembic/versions/` | **83 migrations.** Plusieurs portent des fonctions SQL `fn_*` : c'est là qu'on modifie la logique d'agrégation, pas dans le frontend. |

### Petits composants frontend transverses

| Composant | Rôle |
|---|---|
| `frontend/components/StatsBar.tsx` | Les tuiles de compteurs en tête de page. |
| `frontend/components/NetworkHealthBadge.tsx` | « Santé du réseau » en % — calcul en base (`fn_network_health`). |
| `frontend/components/CpuBadge.tsx` | Charge CPU du serveur (`/system/info`). |
| `frontend/components/IpLink.tsx` | Rend une IP cliquable vers l'interface de l'équipement. |
| `frontend/components/StatusBadge.tsx`, `SeverityBadge.tsx`, `IncidentStatusBadge.tsx` | Pastilles d'état, de sévérité et de statut d'incident. |

---
## Contact et transmission

Ce document décrit l'état du système au **8 septembre 2026**.

**Le réflexe à garder** : pour toute question sur une décision de conception, chercher
d'abord le `⚠️` correspondant ici, puis la **docstring du module concerné** (l'Annexe A dit
lequel). La quasi-totalité des choix non évidents y sont expliqués, avec la mesure ou
l'incident qui les a motivés.

**Si une règle paraît absurde**, c'est généralement qu'elle résout un problème qui n'a pas
encore été rencontré. Chercher son origine avant de la retirer.
