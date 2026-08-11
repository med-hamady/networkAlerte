# API — Adoption d'un équipement dans UISP (association à un client CRM)

Associe un équipement radio, désigné par son **adresse MAC**, au **client CRM**
auquel il appartient. C'est la transposition exacte du geste manuel dans UISP :
chercher la MAC, la voir en « unknown », cliquer dessus et choisir le client.

Destinée au système tiers qui adopte les équipements nouvellement installés.

> **L'appel est LENT et c'est normal.** Si l'équipement n'est pas encore déclaré
> dans le contrôleur, sa clé UISP lui est d'abord posée **par SSH**, puis
> l'association est faite. Compter **jusqu'à 90 s** dans ce cas (≈ 12 s quand
> l'équipement est déjà déclaré). **Prévoir un timeout client ≥ 120 s.**

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
- ⚠️ **C'est une clé NOUVELLE**, différente de celle utilisée jusqu'ici. L'ancienne
  valeur cesse d'être valable après la bascule.
- Sans en-tête ou clé invalide → `401 Unauthorized`.
- ⚠️ La valeur doit être copiée **sur une seule ligne**, sans espace ni saut de
  ligne (un retour-chariot invisible déclenche « invalid header » côté client).

## Corps de la requête

| Champ | Type | Obligatoire | Description |
|---|---|---|---|
| `mac` | string | Oui | MAC de l'équipement. Formats acceptés : `aa:bb:cc:dd:ee:ff`, `aa-bb-cc-dd-ee-ff`, `aabb.ccdd.eeff`, `aabbccddeeff` (casse indifférente). |
| `crm_client_id` | string | Oui | Id du client dans le CRM. **L'id, jamais le nom** — 7 noms désignent deux clients différents. |
| `crm_service_id` | string | Non | Id du service CRM. Requis **uniquement** si le client a plusieurs services (6 clients sur 1402). |
| `reassign` | bool | Non | `true` pour déplacer un équipement **déjà rattaché à un autre client**. Défaut `false`. |

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
3. **Associe** l'équipement au site portant le client CRM.

Un équipement déjà rattaché au **bon** client est un **no-op** : aucune écriture.
L'appel est donc rejouable sans effet de bord.

## Réponse

`200 OK` avec un rapport étape par étape. Un champ mérite attention :

| Champ | Sens |
|---|---|
| `pending_registration` | `true` = la clé vient d'être posée, l'équipement n'est pas encore enregistré auprès du contrôleur. **Ce n'est pas une erreur** : rejouer l'appel dans la minute. |

## Codes HTTP

| Code | Sens | Que faire |
|---|---|---|
| `200` | Association faite (ou déjà en place) | — |
| `400` | MAC mal formée, ou UISP non configuré côté serveur | Corriger la MAC ; sinon nous signaler |
| `401` | Clé absente ou invalide | Vérifier l'en-tête `X-API-Key` |
| `403` | Token UISP sans droits d'écriture | Nous signaler |
| `404` | Client CRM introuvable, ou service n'appartenant pas à ce client | Vérifier `crm_client_id` |
| `405` | **Vous appelez en `http://`** — le POST a été converti en GET par la redirection | Passer en `https://` |
| `409` | Client à **plusieurs services** (la liste est renvoyée) → fournir `crm_service_id` · ou équipement **déjà rattaché à un autre client** (l'id du détenteur est renvoyé) → `reassign=true` en connaissance de cause | Voir ci-contre |
| `429` | Plus de 120 requêtes/minute | Ralentir ; ne pas boucler |
| `502` | Équipement injoignable (clé non posée), ou erreur du contrôleur | Réessayer plus tard |
| `504` | Le proxy a coupé avant la fin | Ne **pas** rejouer aveuglément : l'adoption a pu réussir. Rejouer l'appel est sans danger (no-op si déjà associé) |

⚠️ **Un `409` n'est jamais à forcer par réflexe.** `reassign=true` retire
l'équipement à son détenteur actuel : à n'utiliser que si le déplacement est bien
l'intention (matériel récupéré et réinstallé ailleurs).

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
    // La pose de la clé UISP passe par SSH sur l'équipement.
    CURLOPT_TIMEOUT        => 120,
    // Épinglage du certificat (préféré) :
    CURLOPT_CAINFO         => '/chemin/a2-supervisor.pem',
    CURLOPT_SSL_VERIFYPEER => true,
    CURLOPT_SSL_VERIFYHOST => 2,
]);
$response = curl_exec($ch);
$status   = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);
```

⚠️ **Ne pas activer `CURLOPT_FOLLOWLOCATION` comme parade** à la redirection : sur
un `301`, curl rejoue en GET et perd le corps JSON. La seule bonne réponse est
d'appeler `https://` dès le départ.

## Recommandation d'intégration

- **Un seul appel par équipement installé**, au moment de l'installation.
- **Pas de boucle de retry serrée** : en cas de `pending_registration`, un seul
  rejeu après ~60 s suffit.
- **Journaliser la réponse complète** en cas de `409` : elle contient soit la
  liste des services, soit l'id du client détenteur — c'est ce qui permet de
  trancher sans nous appeler.
