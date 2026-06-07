# Audit de performance & scalabilité — Network Supervisor

> Rédigé le 2026-06-07, parc **603 devices** (cible **1000-1500+**). Objectif :
> tenir la montée en charge sans les débordements de scheduler observés à 603.

---

## 1. Constat mesuré (état actuel)

Parc : **603 devices** — 497 LR, 63 rockets, 15 switches, 14 UISP Power, 14 AF60.
Architecture : **1 process APScheduler** (container `scheduler` dédié), **1 event
loop asyncio**, ~12 jobs.

Durées d'un tour mesurées en prod (2026-06-06) :

| Job | Volume | Durée/tour | Intervalle | Verdict |
|---|---|---|---|---|
| `device_ping_job` | 603 | ~31 s | 30 s | déborde (skip occasionnel) |
| `snmp_poll_job` | 78 | ~60 s | 60 s | déborde |
| `lr_internet_probe_job` | **482 SSH** | ~64 s | 60 s | déborde |
| `ltu_api_poll_job` | 47 | ~25 s | 60 s | OK |
| `airos_api_poll_job` | 19 | <30 s | 60 s | OK |
| `af60_api_poll_job` | 14 | <30 s | 60 s | OK |
| `power_poll_job` | 14 | <30 s | 30 s | OK |

Symptômes : jobs qui sautent des cycles (`skipped: maximum instances`),
compétition entre jobs (tous démarrent ~en même temps), et — déjà corrigé — un
**faux « down » de masse** quand le ping lançait 603 sous-process simultanés.

### Correctifs déjà livrés (juin 2026)
- Concurrence **bornée** par job : `ping_concurrency` (100), `snmp_concurrency`
  (30), `lr_probe_concurrency` (60, pool de threads), LTU (10 + deadline 40 s).
- Statut `down` seulement au seuil anti-flap (3), ping `-c 2` (≥1 réponse).
- `device_metrics` : collapse latest-only + rétention 90 j (table ~−60 %).
- Liste API `limit=1000` (le frontend tronquait à 100).

Ces correctifs ont **stoppé les incidents** ; l'audit ci-dessous vise la
**scalabilité structurelle** (1000-1500+ devices).

---

## 2. Où part le temps (analyse des coûts par cycle)

| Coût dominant | Détail | Pourquoi ça scale mal |
|---|---|---|
| **Ping = 1 sous-process `ping` / device** | `asyncio.create_subprocess_exec("ping", ...)` × 603 | fork/exec de centaines de process : overhead OS énorme, contention CPU/scheduler |
| **Transit = 1 SSH paramiko / LR** | 482 connexions SSH/cycle (handshake + auth + `ping` distant) | le plus cher et le plus fragile ; coût ∝ nb de **clients**, pas d'infra |
| **SNMP** | 78 walks + découverte airMAX (2ᵉ walk) ; timeouts des airMAX SNMP-off | les timeouts s'ajoutent ; coût ∝ nb de rockets/switches |
| **Écriture DB** | `persist_device_metrics` = DELETE+INSERT **par métrique** ; **1 session/device** en phase 2 | des milliers de petites transactions/cycle → round-trips DB |
| **1 event loop partagé** | tous les jobs tournent dans le même process | les pics d'un job (603 subprocess ping) ralentissent les autres |

**Insight clé** : les deux coûts qui explosent avec le nombre de **clients** (et
non d'infra) sont le **ping** et le **transit SSH**. Ce sont les deux à
réarchitecturer en priorité, car le parc grossit surtout en LR.

---

## 3. Leviers priorisés

### 🔴 P1 — Gros gains, effort modéré (à faire avant 1000 devices)

**P1.1 — Ping : remplacer 603 sous-process par `fping` (un seul process)**
`fping` pingue des centaines d'hôtes en parallèle nativement, dans **un seul
process**, et sort un statut up/down par hôte. On remplace la boucle
`asyncio.gather` de N `ping -c 2` par **un appel `fping -a -r1 -t <timeout>`**
sur toute la liste d'IP.
- Gain : sweep ping de ~31 s → **~2-5 s** pour 603, overhead subprocess ÷600.
- Effort : ~½ jour. Refacto de `poller.py` + `device_ping_job` (parsing fping).
- Garder le fallback `ping` si `fping` absent. Ajouter `fping` à l'image Docker.

**P1.2 — Transit : passer de per-LR à per-Rocket (ou par échantillon)**
Aujourd'hui on SSH **chaque LR** (482) pour pinger Internet. Or tous les LR d'un
même Rocket partagent **le même chemin de transit** (Rocket → backhaul → cœur).
Tester le transit **une fois par Rocket** (63 SSH au lieu de 482) couvre la
réalité métier « ce site/secteur a-t-il Internet ». Options :
- (a) **per-Rocket** : 1 SSH/Rocket vers `8.8.8.8`. 482 → 63 SSH/cycle.
- (b) **échantillon tournant** : sonder 1/4 des LR par cycle (rotation) si on
  tient à la granularité par client.
- (c) garder per-LR mais **espacer** (intervalle 300 s) + pool plus large.
- Gain : ÷7 à ÷8 sur le job le plus cher et le plus fragile.
- ⚠️ Décision **métier** : veut-on la latence/transit *par client* ou *par
  secteur* ? À trancher avant de coder.

**P1.3 — Batcher les écritures `device_metrics`**
`persist_device_metrics` fait un DELETE puis un INSERT **par métrique
collapse**, et la phase 2 ouvre **une session par device**. À 1000 devices ×
~10-25 métriques × chaque cycle = dizaines de milliers de petites requêtes.
- Collapse : remplacer le DELETE+INSERT par métrique par un **UPSERT** groupé
  (`INSERT … ON CONFLICT (device_id, metric_name) DO UPDATE`) avec un index
  unique sur `(device_id, metric_name)` pour les métriques latest-only. Une
  seule requête multi-lignes par device (ou par batch de devices).
- History (compteurs bytes) : `executemany` / `insert().values([...])` groupé.
- Gain : round-trips DB ÷10+, charge d'écriture Postgres très réduite.

### 🟠 P2 — Moyen (hygiène & observabilité)

**P2.1 — Une seule session DB par job (batch commits)**
Les phases 2 (SNMP, transit, LTU) font `async with async_session_factory()`
**par device**. Réutiliser **une session par job** (ou par batch de N devices) +
commit groupé → bien moins d'acquisitions de connexion et de transactions.

**P2.2 — Observabilité du scheduler**
On diagnostique aujourd'hui en `grep`ant les logs. Ajouter :
- **Durée de chaque job** loggée (start/end + nb devices + nb OK/KO).
- Endpoint `GET /api/v1/system` enrichi : dernière durée par job, backlog,
  dernier run. (Idéalement métriques Prometheus + alerte « job > intervalle ».)
- Objectif : **voir la saturation venir** au lieu de la subir.

**P2.3 — Circuit-breaker pour devices chroniquement injoignables**
Un device down depuis longtemps gaspille un timeout (SSH/SNMP) à chaque cycle.
Le ping exclut déjà `status=down` du transit, mais on peut généraliser : backoff
exponentiel par device (sonder un mort moins souvent) → libère du temps de cycle.

**P2.4 — Tuning intervalles/concurrences par taille de parc**
Déjà partiellement fait (`.env`). Table cible :

| Parc | ping interval / conc | snmp interval / conc | transit interval / conc |
|---|---|---|---|
| ~600 | 45 s / 100 | 120 s / 30 | 120 s / 60 |
| ~1000 | 60 s / 150 | 180 s / 50 | 180 s / 100 |
| ~1500 | + fping/transit-per-rocket obligatoires + envisager P3 |

**P2.5 — Pool DB**
`db_pool_size=5, max_overflow=10` (max 15) côté scheduler. Avec session-par-job
(P2.1) ça suffit ; sinon monter à 10+20. Surveiller `pool_timeout`.

### 🟢 P3 — Scaling horizontal (au-delà de ~1500-2000 devices)

**P3.1 — Partitionner les jobs sur plusieurs schedulers**
Le job store APScheduler est **en mémoire** → lancer un 2ᵉ scheduler tel quel
**duplique tous les jobs** (2× pings, 2× SSH, 2× incidents/emails). Pour scaler :
- (a) **Partition statique** (recommandé en premier) : scheduler A = ping+SNMP,
  scheduler B = LTU+transit+AF60. Chaque job ne tourne qu'à un endroit. Simple,
  sans dépendance externe.
- (b) **Partition par shard de devices** : N schedulers, chacun gère les devices
  `id % N == k`. Scale linéaire, mais demande un découpage propre des requêtes.
- (c) **Jobstore partagé (Redis/Postgres) + élection de leader** : plus robuste,
  plus lourd à opérer.

**P3.2 — Pollers dédiés par protocole**
À très grande échelle, sortir chaque poller (ping, snmp, ssh-transit) en service
indépendant scalable (file de tâches type Celery/RQ + workers). Gros chantier,
seulement si P1/P2/P3.1 ne suffisent plus.

---

## 4. Fiabilité (au-delà de la vitesse)

Déjà en place : timeouts bornés partout (SSH 6 s, SNMP, HTTP, ping `-W`),
isolation d'erreur (`return_exceptions=True`), anti-flap persistant (`AlertState`),
idempotence des incidents, `max_instances=1` + `coalesce`.

À renforcer :
- **Observabilité** (P2.2) — le maillon faible aujourd'hui.
- **Circuit-breaker** devices morts (P2.3).
- **Tests de charge** : simuler 1000-1500 devices (mock devices) pour valider les
  durées de cycle **avant** d'y être en prod.
- **Backpressure DB** : si Postgres sature en écriture, les jobs ralentissent en
  cascade — d'où l'importance du batch (P1.3).

---

## 5. Roadmap recommandée (ordre d'exécution)

1. **fping** (P1.1) — plus gros ratio gain/effort. ~½ j.
2. **Transit per-Rocket** (P1.2) — après décision métier. ~1 j.
3. **Batch/UPSERT device_metrics** (P1.3) + **session par job** (P2.1). ~1 j.
4. **Observabilité durée des jobs** (P2.2). ~½ j.
5. **Circuit-breaker devices morts** (P2.3). ~½ j.
6. **Test de charge** mock 1500 devices → valider. ~1 j.
7. **Partition schedulers** (P3.1) — seulement si la capacité estimée est dépassée.

---

## 6. Estimation de capacité

- **Aujourd'hui** (concurrence bornée + intervalles tunés) : 1 scheduler tient
  ~600-800 devices avec des intervalles confortables (45/120 s).
- **Avec P1 (fping + transit per-rocket + batch writes)** : 1 scheduler tient
  confortablement **1000-1500 devices**.
- **Au-delà de ~1500-2000** : partitionnement (P3.1) nécessaire.

Le facteur limitant n°1 reste le **transit SSH par LR** : tant qu'il est per-LR,
il scale avec le nombre de clients (le segment qui grossit le plus). Le passer
per-Rocket (P1.2) est le déblocage structurel le plus important.
