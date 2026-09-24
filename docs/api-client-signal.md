# API — Qualité de connexion d'un abonné par MAC

Renvoie la qualité du **signal radio** et de la **latence Internet** d'un client,
à partir de l'adresse MAC de son équipement (LR).

L'API ne modifie rien : elle mesure et renvoie un verdict.

Destinée à un système tiers qui transmet une MAC et lit le résultat.

> **La latence est mesurée en temps réel** : au moment de l'appel, l'API se
> connecte réellement à l'équipement du client (SSH) et lance un ping vers
> Internet. Elle ne renvoie pas une valeur mise en cache. Trois conséquences :
> - l'appel prend **6 à 15 secondes**, davantage sur un lien radio de mauvaise
>   qualité — c'est-à-dire précisément quand la mesure a le plus de valeur.
>   **Prévoir un timeout client ≥ 60 s** ;
> - un équipement **éteint, injoignable ou sans accès Internet** ressort avec
>   `latency_quality: "indetermine"` et la raison dans `latency_message` — jamais
>   avec une latence de `0` ni une valeur inventée ;
> - le **signal**, lui, est lu de la dernière mesure en base (relevée en continu
>   par la supervision) : c'est instantané, mais daté — voir `measured_at`.

---

## Endpoint

| | |
|---|---|
| **Méthode** | `GET` |
| **URL (accès externe)** | `https://102.215.95.229/api/v1/client-signal` |
| **URL (accès LAN interne)** | `https://10.135.3.25/api/v1/client-signal` |

> **Appeler en `https://`.** Le port 80 répond par une redirection vers HTTPS.
>
> Le certificat est auto-signé : épinglez notre `fullchain.pem` (`--cacert`) ou,
> à défaut, désactivez la vérification (`curl -k`, Postman).
>
> L'accès sur l'IP publique passe par l'allowlist du FortiGate : l'IP source de
> l'appelant doit être autorisée. Cette route n'est **pas** servie sur le
> listener public dédié au système de paiement.

## Authentification

Un en-tête HTTP obligatoire :

```
X-API-Key: <CLE_API_TRANSMISE_SEPAREMENT>
```

- La clé (`CLIENT_SIGNAL_API_KEY`) est communiquée hors de ce document, par
  canal séparé.
- Elle est scellée à cette seule route. Elle n'ouvre **ni** le blocage d'un
  abonné, **ni** le filtre de contenu, **ni** l'inventaire des équipements :
  lire la qualité d'un lien et agir sur l'abonné sont deux pouvoirs distincts.
- Sans en-tête ou clé invalide → `401 Unauthorized`.
- ⚠️ La valeur doit être copiée **sur une seule ligne**, sans espace ni saut de
  ligne (un retour-chariot invisible déclenche « invalid header » côté client).

## Paramètre

| Nom | Emplacement | Obligatoire | Description |
|---|---|---|---|
| `mac` | query string | Oui | Adresse MAC du LR du client. Formats acceptés : `aa:bb:cc:dd:ee:ff`, `aa-bb-cc-dd-ee-ff`, `aabb.ccdd.eeff`, `aabbccddeeff` (casse indifférente). |

Exemple d'appel :

```bash
curl -k --max-time 60 \
  -H "X-API-Key: <CLE>" \
  "https://102.215.95.229/api/v1/client-signal?mac=aa:bb:cc:dd:ee:ff"
```

## Réponse

```json
{
  "mac": "aa:bb:cc:dd:ee:ff",
  "lr_id": 1423,
  "lr_name": "12345 - Ba, Amadou - 22334455",
  "status": "up",
  "signal_dbm": -62.0,
  "quality": "excellent",
  "message": "Signal excellent",
  "measured_at": "2026-09-10T08:41:12Z",
  "latency_avg_ms": 43.2,
  "latency_quality": "excellent",
  "latency_message": "Latence excellente (43 ms)",
  "latency_target": "8.8.8.8",
  "latency_packets_sent": 5,
  "latency_packet_size_bytes": 56,
  "rocket": {
    "id": 312,
    "name": "A2-CT1-EST",
    "mac": "aa:bb:cc:00:11:22",
    "ip_address": "10.135.144.1",
    "site": "A2 CT1",
    "radio_tech": "ltu",
    "status": "up",
    "source": "supervision"
  }
}
```

### Catégories de signal (`quality`)

Lues de la dernière valeur relevée par la supervision.

| Valeur | Signification |
|---|---|
| `excellent` | ≥ -65 dBm |
| `bien` | -75 à -65 dBm |
| `moyen` | -80 à -75 dBm |
| `faible` | < -80 dBm |
| `indetermine` | Aucune mesure de signal disponible |

### Catégories de latence (`latency_quality`)

Mesurées **en direct** à chaque appel (5 paquets ICMP de 56 octets).

| Valeur | Signification |
|---|---|
| `excellent` | < 80 ms |
| `tres_bien` | 80 à 100 ms |
| `bien` | 100 à 120 ms |
| `mauvaise` | 120 à 150 ms |
| `catastrophique` | ≥ 150 ms |
| `indetermine` | Mesure impossible — la raison est dans `latency_message` |

> ⚠️ **Ne pas confondre `indetermine` avec une mauvaise qualité.** Un équipement
> éteint, sans identifiants ou sans accès Internet donne `indetermine`, pas
> `catastrophique`. Si votre interface affiche un verdict à l'abonné, ces deux
> cas doivent se lire différemment : l'un dit « le lien est mauvais », l'autre
> « nous n'avons pas pu mesurer ».

### Champs bruts

| Champ | Description |
|---|---|
| `lr_id` / `lr_name` | Identifiant interne et nom de l'équipement |
| `status` | Joignabilité du LR vue par la supervision : `up` / `down` / `unknown` |
| `signal_dbm` | Valeur de signal en dBm (`null` si aucune mesure) |
| `measured_at` | Horodatage de la mesure de signal — sa **fraîcheur**. Un signal `excellent` daté de trois jours sur un équipement `down` ne décrit pas l'instant présent. |
| `latency_avg_ms` | Latence moyenne mesurée (`null` si la mesure n'a pas abouti) |
| `latency_target` | Cible pingée depuis l'équipement (ex. `8.8.8.8`) |
| `latency_packets_sent` / `latency_packet_size_bytes` | Paramètres de la mesure |
| `rocket` | Le **Rocket** (point d'accès) auquel l'équipement est connecté — voir ci-dessous. `null` si aucun rattachement n'est connu |

### Rocket de connexion (`rocket`)

Lu en base (pas de mesure live), donc instantané.

| Champ | Description |
|---|---|
| `id` / `name` | Identifiant interne et nom du Rocket |
| `mac` / `ip_address` | MAC et IP de management du Rocket |
| `site` | Site (pylône) du Rocket |
| `radio_tech` | Famille radio : `ltu` ou `airmax` |
| `status` | Joignabilité du Rocket : `up` / `down` / `unknown` |
| `source` | `supervision` = Rocket supervisé, tous les champs renseignés. `uisp` = seul le **nom** de l'AP est connu (annoncé par le contrôleur UISP) ; les autres champs valent `null` |

> Pour un équipement **hors ligne**, `rocket` désigne le dernier point d'accès
> auquel il a été vu connecté.

## Codes d'erreur

| Code | Cause | Que faire |
|---|---|---|
| `400` | MAC mal formée | Corriger le format |
| `401` | Clé absente ou invalide | Vérifier l'en-tête `X-API-Key` |
| `404` | Aucun équipement ne porte cette MAC | La MAC n'est pas dans notre inventaire — vérifier auprès de nous |
| `429` | Trop d'appels | Ralentir (plafond : 120 appels/minute) |
| `504` | Timeout du proxy | Ne devrait pas survenir (le proxy attend jusqu'à 120 s) — nous signaler |

## Limites à connaître

- **Débit** : 120 appels par minute et par IP source. Au-delà, `429`.
- **Appel lent** : chaque appel ouvre une session SSH. Pour contrôler un lot de
  clients, **séquencer les appels** plutôt que de les lancer en parallèle : au
  delà d'une dizaine de sessions simultanées, la file d'attente SSH allonge tous
  les temps de réponse, y compris ceux de nos propres sondes de supervision.
- **Pas de mise en cache** côté API : deux appels rapprochés sur la même MAC
  produisent deux mesures réelles. Si vous affichez cette information dans une
  interface rafraîchie automatiquement, mettez en cache **de votre côté**.
