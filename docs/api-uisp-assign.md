# API — Adoption d'un équipement dans UISP (association à un client CRM)

Associe un équipement radio, désigné par son **adresse MAC**, au **client CRM**
auquel il appartient. C'est la transposition exacte du geste manuel dans UISP :
chercher la MAC, la voir en « unknown », cliquer dessus et choisir le client.

Destinée au système tiers qui adopte les équipements nouvellement installés.

> **L'appel est LENT et c'est normal.** Si l'équipement n'est pas encore déclaré
> dans le contrôleur, sa clé UISP lui est d'abord posée **par SSH**, puis l'API
> **attend qu'il se déclare** (60 s au plus) et l'associe **dans le même appel**.
> Compter ≈ 12 s quand l'équipement est déjà déclaré ; davantage après une pose
> de clé (connexion SSH + adoption — 9 s d'adoption mesurées le 10/09).
> **Quoi qu'il arrive, l'API répond en moins de 100 s** : c'est une borne tenue
> côté serveur. **Prévoir un timeout client ≥ 120 s.**

---

## Endpoint

| | |
|---|---|
| **Méthode** | `POST` |
| **URL (accès externe)** | `https://102.215.95.229/api/v1/uisp/assign` |
| **URL (accès LAN interne)** | `https://10.135.3.25/api/v1/uisp/assign` |
| **Format** | JSON (`Content-Type: application/json`) |
| **Débit autorisé** | 120 requêtes/minute par IP source (au-delà : `429`) |

### ⚠️ HTTPS obligatoire — ne pas appeler en `http://`

L'URL doit commencer par **`https://`**. Le port 80 répond par une redirection
`301` vers HTTPS, et **une redirection détruit un POST** : la plupart des clients
(dont curl et Postman) rejouent alors la requête **en GET, sans le corps JSON** —
l'API répond `405 Method Not Allowed`, ou le client s'arrête sur le `301` sans
rien envoyer.

Ce n'est pas une hypothèse : c'est ce que montrent nos journaux, à la même
seconde et depuis le même client (2026-08-11) —

```
"POST /api/v1/uisp/assign HTTP/1.1" 301
"GET  /api/v1/uisp/assign HTTP/1.1" 405
```

Le symptôme se lit comme « l'API ne marche pas en HTTP » ; la cause est la
redirection, et le correctif est d'appeler `https://` directement. **Sur les deux
IP** : le `http://` ne fonctionne pas davantage sur l'adresse LAN.

> C'est la différence avec `GET /api/v1/fai/verify` : un **GET** traverse une
> redirection sans dommage. Un **POST**, non.

Le certificat est auto-signé (réseau interne, pas de nom DNS public). Deux
options, par ordre de préférence :

1. **Épingler notre certificat** — demandez `fullchain.pem` à l'équipe réseau et
   pointez-le en CA de confiance (`CURLOPT_CAINFO` en PHP, `--cacert` en curl).
   Vous gardez une vraie vérification TLS.
2. **Désactiver la vérification** (`CURLOPT_SSL_VERIFYPEER = false`, `curl -k`),
   comme vous le faites déjà pour `/fai/verify`.

L'accès sur l'IP publique passe par l'**allowlist du FortiGate** : l'IP source de
l'appelant doit y être autorisée.

## Authentification

Un en-tête HTTP obligatoire :

```
X-API-Key: <CLE_API_TRANSMISE_SEPAREMENT>
```

- La clé (`UISP_ASSIGN_API_KEY`) est communiquée hors de ce document, par canal
  séparé.
- Elle est **scellée à cette seule route** : elle n'ouvre ni la synchronisation
  d'inventaire (`/uisp/sync`), ni le blocage/déblocage d'abonnés, ni aucune autre
  partie du système.
- Sans en-tête ou clé invalide → `401`, `error_code` `unauthorized`.
- ⚠️ La valeur doit être copiée **sur une seule ligne**, sans espace ni saut de
  ligne (un retour-chariot invisible déclenche « invalid header » côté client).

## Corps de la requête

| Champ | Type | Obligatoire | Description |
|---|---|---|---|
| `mac` | string | Oui | MAC de l'équipement. Formats acceptés : `aa:bb:cc:dd:ee:ff`, `aa-bb-cc-dd-ee-ff`, `aabb.ccdd.eeff`, `aabbccddeeff` (casse indifférente). |
| `crm_client_id` | string | Oui | Id du client dans le CRM. **L'id, jamais le nom** — 7 noms désignent deux clients différents. |
| `crm_service_id` | string | Non | Id du service CRM. Requis **uniquement** si le client a plusieurs services (6 clients sur 1402). |
| `force` | bool | Non | `true` pour déplacer un équipement **déjà rattaché à un autre client**. Défaut `false`. |
| `reassign` | bool | Non | Ancien nom de `force`, toujours accepté. |

```json
{
  "mac": "78:45:58:0B:BC:76",
  "crm_client_id": "1361"
}
```

## Ce que l'API fait, dans cet ordre

1. **Vérifie le client CRM** — inutile de toucher à l'équipement pour un client
   qui n'existe pas, et un échec ne laisse alors aucune trace.
2. **Pose la clé UISP** sur l'équipement (par SSH) s'il est absent du contrôleur.
   Sans elle il ne se déclare jamais, donc il n'y a rien à associer.
3. **Attend que l'équipement se déclare** au contrôleur — interrogé toutes les
   5 s, **60 s au plus**. S'il ne s'est pas déclaré à temps, la réponse le dit
   (`pending_registration`) et indique quand rejouer (`retry_after_seconds`).
4. **Associe** l'équipement au client CRM — dans le même appel.

Un équipement déjà rattaché au **bon** client est un **no-op** : aucune écriture.
L'appel est donc rejouable sans effet de bord.

## Réponse — un contrat stable

**Toute réponse, succès comme erreur, porte ces quatre champs :**

| Champ | Type | Sens |
|---|---|---|
| `assigned` | bool | `true` = l'équipement est rattaché au client (par cet appel, ou déjà en place). |
| `pending_registration` | bool | `true` = la clé est posée mais l'équipement ne s'est pas déclaré dans le délai. **Ce n'est pas une erreur** : rejouer après `retry_after_seconds`. |
| `retry_after_seconds` | int \| null | Délai conseillé avant de rejouer. Renseigné **uniquement** quand `pending_registration` vaut `true`. |
| `error_code` | string \| null | `null` en cas de succès ; sinon un code **stable** (table ci-dessous). **C'est lui qu'il faut tester — jamais le texte de `message`**, qui peut évoluer. |

Trois issues possibles :

| Issue | HTTP | `assigned` | `pending_registration` | `error_code` |
|---|---|---|---|---|
| Associé (ou déjà associé) | `200` | `true` | `false` | `null` |
| En attente de déclaration | `200` | `false` | `true` | `null` |
| Erreur | `4xx` / `5xx` | `false` | `false` | un code |

**Associé :**

```json
{
  "mac": "78:45:58:0b:bc:76",
  "crm_client_id": "1361",
  "crm_service_id": "501",
  "client_name": "Ba, Amadou",
  "assigned": true,
  "pending_registration": false,
  "retry_after_seconds": null,
  "error_code": null,
  "key_injected": true,
  "message": "78:45:58:0b:bc:76 associé au client CRM 1361.",
  "steps": [
    { "step": "resolve_target",     "ok": true, "message": "..." },
    { "step": "inject_key",         "ok": true, "message": "..." },
    { "step": "await_registration", "ok": true, "message": "Déclaré au contrôleur après 3 s." },
    { "step": "assign",             "ok": true, "message": "Associé au client CRM 1361." }
  ]
}
```

**En attente** — la clé est posée, rejouer l'appel **tel quel** après
`retry_after_seconds` : il reprend là où il s'est arrêté.

```json
{
  "mac": "78:45:58:0b:bc:76",
  "crm_client_id": "1361",
  "assigned": false,
  "pending_registration": true,
  "retry_after_seconds": 30,
  "error_code": null,
  "key_injected": true,
  "message": "Clé UISP posée sur 78:45:58:0b:bc:76, mais l'équipement ne s'est pas encore déclaré au contrôleur après 60 s. Rejouer l'appel dans 30 s : il terminera le rattachement au client CRM 1361."
}
```

**Erreur** — même enveloppe pour toutes les erreurs :

```json
{
  "error_code": "device_already_assigned",
  "message": "L'équipement 78:45:58:0b:bc:76 est déjà rattaché au client CRM 1369. Le déplacer vers le client 1361 retirerait son équipement à l'abonné actuel — relancer avec force=true si c'est bien l'intention.",
  "assigned": false,
  "pending_registration": false,
  "retry_after_seconds": null,
  "current_crm_client_id": "1369",
  "current_client_name": "Ba, Amadou",
  "detail": { "message": "...", "current_crm_client_id": "1369" }
}
```

> `detail` est conservé pour la compatibilité avec le format précédent. Une
> intégration nouvelle lit `error_code` et les champs de premier niveau.

## Codes d'erreur

| `error_code` | HTTP | Cause | Que faire |
|---|---|---|---|
| `invalid_request` | `422` | Corps JSON invalide (champ manquant, mauvais type) — `detail` nomme le champ | Corriger l'appel |
| `invalid_mac` | `400` | MAC mal formée | Corriger la MAC |
| `uisp_not_configured` | `400` | Contrôleur UISP non configuré de notre côté | Nous signaler |
| `unauthorized` | `401` | Clé absente ou invalide | Vérifier l'en-tête `X-API-Key` |
| `uisp_write_forbidden` | `403` | Notre token UISP n'a pas les droits d'écriture | Nous signaler |
| `crm_client_not_found` | `404` | Aucun client CRM avec cet id | Vérifier `crm_client_id` |
| `crm_service_mismatch` | `404` | `crm_service_id` n'appartient pas à ce client | Vérifier le couple client / service |
| `device_not_found` | `404` | MAC inconnue du contrôleur **et** de notre inventaire : aucun moyen de joindre l'équipement | Vérifier la MAC ; l'équipement n'a peut-être pas encore été vu sur le réseau |
| `multiple_services` | `409` | Le client a plusieurs services et `crm_service_id` manque — `candidates` les liste | Rejouer avec le bon `crm_service_id` |
| `device_already_assigned` | `409` | Équipement déjà rattaché à un **autre** client — `current_crm_client_id` et `current_client_name` le nomment | Vérifier ; `force: true` **seulement** si le déplacement est voulu |
| `device_unreachable` | `502` | La clé UISP n'a pas pu être posée : équipement injoignable en SSH | Réessayer plus tard |
| `uisp_error` | `502` | Erreur du contrôleur UISP | Réessayer plus tard |
| `internal_error` | `500` | Erreur inattendue de notre côté | Nous signaler |

Trois réponses ne viennent **pas** de l'API mais du proxy, et ne portent donc
**pas** de JSON :

| HTTP | Cause | Que faire |
|---|---|---|
| `405` | **Appel en `http://`** — le POST a été converti en GET par la redirection | Passer en `https://` |
| `429` | Plus de 120 requêtes/minute | Ralentir ; ne pas boucler |
| `504` | Le proxy a coupé avant la fin | L'association a pu réussir : **rejouer est sans danger** (no-op si déjà associé) |

⚠️ **Un `409 device_already_assigned` ne se force jamais par réflexe.**
`force: true` retire l'équipement à son détenteur actuel : à n'utiliser que si
le déplacement est bien l'intention (matériel récupéré et réinstallé ailleurs).
Attention aux homonymes : dans l'exemple ci-dessus, le détenteur (1369) porte le
**même nom** que le client visé (1361) — seul l'id dit qu'il s'agit d'un autre
abonné.

## Exemples

**curl**, certificat épinglé (recommandé) :

```bash
curl --cacert /chemin/a2-supervisor.pem \
  -X POST "https://102.215.95.229/api/v1/uisp/assign" \
  -H "X-API-Key: <CLE_API>" \
  -H "Content-Type: application/json" \
  -d '{"mac":"78:45:58:0B:BC:76","crm_client_id":"1361"}' \
  --max-time 120
```

**curl**, sans vérification TLS :

```bash
curl -k -X POST "https://102.215.95.229/api/v1/uisp/assign" \
  -H "X-API-Key: <CLE_API>" \
  -H "Content-Type: application/json" \
  -d '{"mac":"78:45:58:0B:BC:76","crm_client_id":"1361"}' \
  --max-time 120
```

**PHP** :

```php
$ch = curl_init('https://102.215.95.229/api/v1/uisp/assign');
curl_setopt_array($ch, [
    CURLOPT_POST           => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_HTTPHEADER     => [
        'Content-Type: application/json',
        'X-API-Key: ' . $apiKey,
    ],
    CURLOPT_POSTFIELDS     => json_encode([
        'mac'           => $mac,
        'crm_client_id' => $crmClientId,
    ]),
    // Pose de la clé par SSH + attente de la déclaration : l'API répond en
    // moins de 100 s, on laisse la marge du proxy.
    CURLOPT_TIMEOUT        => 120,
    // Épinglage du certificat (préféré) :
    CURLOPT_CAINFO         => '/chemin/a2-supervisor.pem',
    CURLOPT_SSL_VERIFYPEER => true,
    CURLOPT_SSL_VERIFYHOST => 2,
]);
$response = curl_exec($ch);
$status   = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

$body = json_decode($response, true);
if ($body['assigned']) {
    // Associé (ou déjà associé).
} elseif ($body['pending_registration']) {
    // Clé posée, équipement pas encore déclaré : rejouer UNE fois, plus tard.
    planifierRejeu($mac, $crmClientId, $body['retry_after_seconds']);
} else {
    // Brancher sur error_code — jamais sur le texte de message.
    switch ($body['error_code']) {
        case 'device_already_assigned':
            // $body['current_crm_client_id'], $body['current_client_name']
            break;
        case 'multiple_services':
            // $body['candidates'] : rejouer avec le bon crm_service_id
            break;
        // ...
    }
}
```

⚠️ **Ne pas activer `CURLOPT_FOLLOWLOCATION` comme parade** à la redirection : sur
un `301`, curl rejoue en GET et perd le corps JSON. La seule bonne réponse est
d'appeler `https://` dès le départ.

## Recommandation d'intégration

- **Un seul appel par équipement installé**, au moment de l'installation.
- **Brancher sur `error_code`**, jamais sur `message` : les codes sont stables,
  les messages non.
- **En cas de `pending_registration`**, un seul rejeu après `retry_after_seconds`
  suffit — pas de boucle serrée. Le rejeu ne repose pas la clé : il termine
  l'association.
- **Journaliser la réponse complète** en cas de `409` : elle contient soit la
  liste des services, soit le client détenteur — c'est ce qui permet de trancher
  sans nous appeler.

## Historique du contrat

Tout changement de format de réponse est consigné ici **et annoncé avant d'être
déployé**. Un `error_code` n'est jamais renommé ni réaffecté à un autre cas ; un
champ n'est jamais retiré sans préavis.

| Date | Changement | Compatibilité |
|---|---|---|
| 2026-07-28 | Première version : `mac` + `crm_client_id` (+ `crm_service_id`), paramètre `reassign`. | — |
| 2026-08-11 | Clé API dédiée `UISP_ASSIGN_API_KEY`. Aucun changement de format. | Changement de clé |
| 2026-09-11 | Rattachement **dans le même appel** après la pose de clé (attente de 60 s au plus) · `retry_after_seconds` · `error_code` stable sur toute erreur · `assigned`, `pending_registration`, `retry_after_seconds` et `error_code` présents dans **toutes** les réponses · paramètre `force` (`reassign` reste accepté) · le `409 device_already_assigned` nomme aussi le détenteur par son nom CRM. | **Additif** : `detail` et statuts HTTP inchangés |

> Aucune version de cette API n'a renvoyé de champ `status` : l'état s'est
> toujours lu dans les booléens `assigned` et `pending_registration`. Seul
> changement de forme sur ce point : `pending_registration` est désormais présent
> même quand il vaut `false` — il était auparavant omis dans ce cas.
