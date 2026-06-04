# Rapport technique — Intégration et supervision du UISP Power

**Projet :** Network Supervisor (supervision réseau UISP/Ubiquiti)
**Équipement concerné :** UISP Power Pro — Site AT2 (`10.135.2.144`)
**Date :** 2026-06-04
**Objet :** Détail de l'API du UISP Power, des données qu'elle retourne, et de leur exploitation par le système de supervision (métriques + alertes).

---

## 1. Objectif

Donner à l'équipe technique une vue complète de :
1. **Ce que le UISP Power expose** via son API ;
2. **Ce que notre système exploite** (métriques stockées, alertes déclenchées) ;
3. **Le rôle de chaque métrique** (à quoi elle sert).

Le but métier : **détecter de façon proactive** les problèmes d'alimentation d'un site (coupure secteur, batterie faible, tension anormale, équipement injoignable) et **alerter l'équipe** avant que le site ne tombe.

---

## 2. Architecture électrique du UISP Power

Le UISP Power Pro est un **onduleur/PDU intelligent** : il prend le courant secteur, le convertit, alimente les équipements, et gère des batteries de secours.

```
        SOMELEC                    UISP Power Pro                   Équipements
   (secteur 220 V AC) ──► [Entrées AC] ──► bus DC interne (~28 V) ──► [Sorties DC] ──► radios / switch
                                              │   ▲
                                              ▼   │
                                          [Batteries]
                                   (banc plomb externe + Li-Ion interne)
```

| Élément | Rôle | Sens du courant |
|---|---|---|
| **Entrées AC** | Reçoivent le courant **SOMELEC** (2 entrées sur cet appareil) | Entrée |
| **Batteries** | **Stockent** l'énergie ; prennent le relais si SOMELEC coupe. Deux batteries : un **banc plomb externe** (grosse réserve) + une **Li-Ion interne** (petite réserve) | Stockage |
| **Sorties DC** | **Délivrent** le courant continu aux équipements (radios, switch…) | Sortie |

**Points clés à retenir :**
- **AC = entrée secteur SOMELEC** (ce n'est pas une batterie).
- **Sortie DC = sortie vers les équipements** (ce n'est pas une batterie non plus).
- Les **batteries** sont une réserve intermédiaire sur le bus DC.
- Sur secteur : SOMELEC alimente la sortie **et** recharge les batteries (courant batterie **négatif** = en charge).
- Sur coupure : les batteries alimentent la sortie (courant **positif** = en décharge).

---

## 3. Accès à l'API

Le firmware impose **HTTPS** (certificat auto-signé). L'authentification se fait en deux temps :

```
1) POST https://<ip>/api/v1.0/user/login
   body : {"username": "...", "password": "..."}
   → 200, le jeton est renvoyé dans l'en-tête de réponse  X-Auth-Token

2) GET  https://<ip>/api/v1.0/statistics
   en-tête : X-Auth-Token: <jeton>
   → JSON : [ { "timestamp": ..., "device": {...}, "interfaces": [...] } ]
```

- Identifiants stockés **par équipement** en base (`uisp_powers.api_username` / `api_password` / `api_port`), pas dans le `.env`.
- Le superviseur interroge cette API **toutes les 30 secondes** (`power_poll_job`).
- Les anciens firmwares mFi (`/api/v1.0/login/`, `/api/v1.0/sensors/`) ne sont **pas** supportés : on cible uniquement le chemin moderne `/api/v1.0/...`.

---

## 4. Données brutes retournées par l'API

Exemple réel (relevé sur `10.135.2.144`, secteur présent, sortie sans charge) — extrait simplifié du bloc `device` :

```json
{
  "state": "online",
  "uptime": 1760430,
  "cpu": [ { "identifier": "MIPS 24Kc V7.4", "usage": 11 } ],
  "ram": { "usage": 33, "free": 40394752, "total": 60456960 },
  "temperatures": [ { "name": "Li-Ion battery", "value": 37.0 } ],
  "power": [
    { "psuType": "AC", "connected": true,  "battery": null },
    { "psuType": "AC", "connected": false, "battery": null },
    { "psuType": "DC", "power": 0.14, "voltage": 28.7, "current": 0.005, "connected": true,
      "charging": { "active": false },
      "battery": { "type": "lead-acid", "chargeLevel": 100, "runningTime": 4574700,
                   "capacity": { "configured": 120.0, "estimated": null } } },
    { "psuType": "DC", "power": 0.51, "voltage": 28.5, "current": 0.018, "connected": true,
      "battery": { "type": "li-ion", "chargeLevel": 100, "runningTime": 182400,
                   "capacity": { "configured": 4.6, "estimated": 3.18 } } }
  ],
  "outputPower": {
    "voltage": 28.14, "current": 0.02, "power": 0.56,
    "maximalPower": 100, "powerMetter": 639548.0,
    "dcOutput": [ { "id": 0, "voltage": 28.1, "current": 0.02, "power": 0.56,
                    "maximalPower": 100, "state": "disconnected" } ]
  },
  "interfaces": [ { "id": "eth0", "statistics": { "rxBytes": ..., "txBytes": ... } } ]
}
```

### Champs disponibles (vue d'ensemble)

| Bloc | Contenu |
|---|---|
| `state` | État global d'alimentation nommé par le firmware (`online`, `battery_charging_fast`, …) |
| `uptime` | Secondes depuis le dernier redémarrage |
| `cpu`, `ram`, `temperatures`, `storage` | Santé système (**non exploités** — voir §7) |
| `power[]` | **Sources** : entrées AC (secteur) + entrées DC portant les **batteries** |
| `outputPower` | **Sortie** vers les équipements : tension/courant/puissance, max, compteur d'énergie, ports `dcOutput[]` |
| `interfaces[]` | Trafic réseau eth0 (**non exploité** — voir §7) |

---

## 5. Métriques exploitées par le superviseur

À chaque cycle (30 s), les valeurs utiles sont :
- enregistrées dans **`device_metrics`** (séries temporelles, consultées par l'UI et l'historique) ;
- une ligne de synthèse écrite dans **`power_status_logs`** (tension/courant/puissance + statut online).

> **Convention de nommage** : `<slug>` vaut `lead_acid` (banc plomb externe) ou `li_ion` (UPS interne). `<id>` est le numéro du port de sortie DC.

### 5.1 Sortie d'alimentation (ce que l'appareil délivre)

| Métrique | Source API | Unité | À quoi ça sert |
|---|---|---|---|
| `voltage_v` | `outputPower.voltage` | V | Tension délivrée aux équipements. Hors plage 20–56 V → alerte tension anormale. |
| `current_a` | `outputPower.current` | A | Intensité tirée par les équipements en aval. |
| `power_w` | `outputPower.power` | W | Puissance consommée par la charge à l'instant T (= tension × courant). |
| `output_max_power_w` | `outputPower.maximalPower` | W | Puissance maximale supportée (capacité de l'appareil). Affichée en « actuelle / max ». |
| `output_energy_wh` | `outputPower.powerMetter` | Wh | **Compteur cumulatif** d'énergie débitée depuis la mise en service. Suivi de consommation long terme. *(Unité Wh = hypothèse firmware ; affiché en kWh.)* |

### 5.2 Présence du secteur (SOMELEC)

| Métrique | Source API | Unité | À quoi ça sert |
|---|---|---|---|
| `ac_connected` | `power[].psuType=="AC"` & `connected` | 1/0 | **1** = au moins une entrée AC connectée → secteur **présent**. **0** = aucune entrée AC → l'appareil tourne **sur batterie** (coupure SOMELEC). Pilote l'alerte de coupure secteur. |

### 5.3 Batteries (une ligne par batterie)

| Métrique | Source API | Unité | À quoi ça sert |
|---|---|---|---|
| `battery_<slug>_pct` | `battery.chargeLevel` | % | Niveau de charge de cette batterie. |
| `battery_<slug>_voltage_v` | tension de l'entrée DC | V | Tension aux bornes de la batterie. |
| `battery_<slug>_capacity_ah` | `battery.capacity.configured` | Ah | Capacité **configurée** (déclarée) de la batterie. Plomb ≈ 120 Ah, Li-Ion ≈ 4,6 Ah. |
| `battery_<slug>_runtime_s` | `battery.runningTime` | s | **Autonomie estimée** au rythme de consommation actuel. |
| `battery_<slug>_discharging` | signe de la puissance batterie | 1/0 | **1** = cette batterie **débite** (en service). N'est vrai **que si le secteur est absent** (sur secteur, une batterie pleine affiche un léger positif de bruit → ignoré). Sert à dire **quelle** batterie tient le site pendant une coupure. |
| `battery_pct` *(canonique)* | la batterie **connectée la plus basse** | % | Valeur **unique** utilisée pour l'alerting batterie. On suit la batterie **la plus basse** (le banc plomb en pratique) car c'est elle qui détermine la survie réelle du site, pas la Li-Ion interne. |
| `battery_voltage_v` *(canonique)* | tension de la batterie canonique | V | Tension associée à la batterie canonique. |

> **Pourquoi la « batterie canonique » ?** Le banc plomb (120 Ah) est le vrai secours du site ; la Li-Ion interne (4,6 Ah) ne tient que l'électronique de l'appareil quelques minutes. Avant correction, le système rapportait la Li-Ion (souvent à 100 %) et **masquait** un banc plomb potentiellement bas. On suit désormais la batterie **la plus basse**.

### 5.4 Sorties DC (un port = un équipement alimenté)

| Métrique | Source API | Unité | À quoi ça sert |
|---|---|---|---|
| `dc_output_<id>_voltage_v` | `dcOutput[].voltage` | V | Tension sur ce port de sortie. |
| `dc_output_<id>_current_a` | `dcOutput[].current` | A | Courant tiré sur ce port. |
| `dc_output_<id>_power_w` | `dcOutput[].power` | W | Puissance délivrée sur ce port. |
| `dc_output_<id>_connected` | `dcOutput[].state` | 1/0 | **1** = un équipement est branché/actif sur ce port ; **0** = `disconnected` (rien branché ou rien détecté). |

### 5.5 Système

| Métrique | Source API | Unité | À quoi ça sert |
|---|---|---|---|
| `uptime_seconds` | `device.uptime` | s | Durée depuis le dernier redémarrage. Un reset inattendu = uptime qui retombe à zéro → indice de coupure/plantage. |

---

## 6. Comment on détermine la **source d'alimentation actuelle**

C'est l'information centrale : à un instant donné, le site est-il alimenté par le **secteur SOMELEC** ou par **une batterie** — et laquelle ? On la déduit de trois champs renvoyés par l'API, **sans aucune sonde externe**.

### 6.1 Étape 1 — Secteur présent ou non ?

On regarde **toutes les entrées de type AC** dans `power[]` :

```
ac_connected = (au moins une entrée  psuType == "AC"  a  connected == true)
```

- `ac_connected = 1` → **le secteur SOMELEC alimente l'appareil**. Source = **Secteur**. Les batteries sont en charge (ou pleines).
- `ac_connected = 0` → **plus aucune entrée secteur** → l'appareil bascule **sur batterie**. Source = **Batterie** (coupure SOMELEC).

> Exemple réel du `.144` : deux entrées AC, l'une `connected: true`, l'autre `false` → `ac_connected = 1` → **sur secteur**.

### 6.2 Étape 2 — Si sur batterie, laquelle débite ?

On utilise le **signe de la puissance** de chaque batterie (champ `power` de son entrée DC) :

| Puissance batterie | Signification |
|---|---|
| **négative** | la batterie se **charge** (l'énergie entre dans la batterie) → secteur présent |
| **positive** | la batterie **débite** (elle fournit l'énergie à la sortie) → elle est **en service** |

```
batterie « en service » = ( ac_connected == 0 )  ET  ( puissance batterie > 0,1 W )
```

La batterie ainsi identifiée est nommée dans l'alerte et marquée **« ● en service »** dans l'interface — typiquement le **banc plomb externe**, qui tient le site pendant la coupure.

### 6.3 Pourquoi la condition « secteur absent » est indispensable

Une batterie **pleine et sur secteur** affiche une **petite puissance positive de bruit** (quelques dixièmes de watt) sans pour autant débiter. Sur le `.144`, secteur présent, les deux batteries lisaient `+0,14 W` et `+0,51 W` — un seuil sur la puissance seule les aurait marquées « en service » à tort.

C'est pourquoi le flag « en service » n'est calculé **que lorsque `ac_connected == 0`**. Tant que le secteur est là, **aucune** batterie n'est considérée comme source, quelle que soit sa lecture de puissance.

### 6.4 Récapitulatif de la décision

```
                ┌─────────────────────────────┐
                │  ac_connected (entrées AC)  │
                └──────────────┬──────────────┘
              =1 (secteur)     │     =0 (coupure)
        ┌───────────────────────┴───────────────────────┐
        ▼                                                ▼
  Source = SECTEUR (SOMELEC)                    Source = BATTERIE
  batteries en charge                  batterie en service = celle dont
  aucune batterie « en service »       la puissance est positive (>0,1 W)
                                       → ex. « banc plomb (externe) »
```

### 6.5 Où c'est visible

- **Interface** : ligne **« Source d'alimentation »** en tête de la section Alimentation
  — `⚡ Secteur (SOMELEC)` ou `🔋 Batterie — Banc plomb (externe)`.
- **Métriques** : `ac_connected` (1/0) et `battery_<slug>_discharging` (1/0), historisées toutes les 30 s.
- **Alerte** : `mains_power_lost` au passage sur batterie (voir §7).

---

## 7. Alertes générées

Les seuils sont configurables (`.env` / paramètres). Comportement par type :

| Alerte (`alert_type`) | Déclencheur | Sévérité | Notification |
|---|---|---|---|
| `uisp_power_unreachable` | API injoignable (device ne répond pas) | Critique | Immédiate |
| `battery_low_warning` | Batterie canonique < **25 %** | Warning | Digest (regroupé) |
| `battery_low_critical` | Batterie canonique < **10 %** | Critique | Immédiate |
| `voltage_anomaly` | Tension sortie < **20 V** ou > **56 V** | Critique | Immédiate |
| `mains_power_lost` | **Aucune entrée AC connectée** pendant **2 cycles** (≈ 1 min) → l'appareil est passé **sur batterie** | Warning | **Immédiate** + message au **rétablissement** |

**Détail `mains_power_lost` (détection coupure SOMELEC) :**
- Anti-flap de **2 cycles** (~1 min) pour ignorer les micro-coupures.
- Le compteur de cycles est persisté en base (`alert_states`) → survit à un redémarrage du superviseur.
- Le message **nomme la batterie en service** : *« Batterie en service : banc externe (plomb) »* (ou Li-Ion), déterminée par le signe du courant batterie.
- Résolution automatique dès le retour du secteur.

---

## 8. Données disponibles mais **non exploitées** (et pourquoi)

| Donnée API | Raison de non-exploitation |
|---|---|
| `cpu`, `ram`, `temperatures` | Santé système non pertinente pour la supervision réseau/alimentation visée. |
| `interfaces[]` (trafic eth0) | Jugé inutile pour notre besoin (le trafic est supervisé ailleurs, côté radios/switch). |
| `capacity.estimated` (usure batterie) | Non remonté pour l'instant ; pourrait servir d'indicateur de **vieillissement** des batteries (sur le `.144`, la Li-Ion estimée est descendue à ~3,2 Ah vs 4,6 nominal). À ajouter si l'équipe le souhaite. |

---

## 9. Restitution dans l'interface

Fiche du UISP Power (panneau de détail), section **« Alimentation »** :

- **Source d'alimentation** : ⚡ Secteur (SOMELEC) / 🔋 Batterie — *avec le nom de la batterie en service si coupure*.
- **Tension / Courant / Puissance** (actuelle / max) + **Énergie cumulée** (kWh).
- **Une sous-section par batterie** (Banc plomb externe, puis Li-Ion interne) : Charge, Tension, Capacité, Autonomie estimée, badge **« ● en service »** sur celle qui débite.
- **Sorties DC** : état connectée/déconnectée + puissance/tension par port.
- **Uptime** affiché dans les informations générales.

---

## 10. Résumé

Le système interroge l'API du UISP Power toutes les 30 s, en extrait les grandeurs d'alimentation utiles (sortie, batteries détaillées, sorties DC, présence secteur, uptime), les historise et déclenche des alertes proactives. Les apports clés de cette intégration :

1. **Chaque batterie est suivie séparément** (banc plomb externe + Li-Ion interne).
2. **L'alerting suit le vrai banc de secours** (la batterie la plus basse), plus la Li-Ion qui masquait l'état réel.
3. **Détection de coupure secteur SOMELEC** (`mains_power_lost`) avec notification immédiate et identification de la batterie qui prend le relais.
4. **Métriques enrichies** : capacité, autonomie estimée, énergie cumulée, état des sorties DC, uptime.
