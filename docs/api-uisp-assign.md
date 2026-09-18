# API — Association d'un équipement à un client CRM

Rattache dans UISP un équipement, désigné par sa **MAC**, au **client CRM**
auquel il appartient.

## Appel

| | |
|---|---|
| **Méthode** | `POST` |
| **URL** | `https://102.215.95.229/api/v1/uisp/assign` — LAN : `https://10.135.3.25/api/v1/uisp/assign` |
| **En-têtes** | `X-API-Key: <clé transmise séparément>` · `Content-Type: application/json` |
| **Timeout client** | **120 s minimum** — l'API répond toujours en moins de 100 s |
| **Débit** | 120 requêtes/minute par IP |

- **HTTPS obligatoire.** En `http://`, la redirection transforme le POST en GET
  et supprime le corps (`405`). Ne pas activer le suivi des redirections.
- Certificat auto-signé : épingler notre `fullchain.pem`, ou désactiver la
  vérification (`curl -k`).
- L'IP source doit être autorisée sur notre pare-feu.

## Corps

| Champ | Obligatoire | Description |
|---|---|---|
| `mac` | Oui | MAC de l'équipement (`aa:bb:cc:dd:ee:ff`, `aa-bb-cc-dd-ee-ff`, `aabb.ccdd.eeff` ou `aabbccddeeff`) |
| `crm_client_id` | Oui | **Id** du client CRM — jamais le nom : des clients différents portent le même |
| `crm_service_id` | Si le client a plusieurs services | Id du service CRM |
| `force` | Non | `true` pour déplacer un équipement déjà rattaché à un **autre** client (`reassign`, l'ancien nom, reste accepté) |

```json
{ "mac": "78:45:58:0B:BC:76", "crm_client_id": "1361" }
```

## Déroulement

1. Vérifie le client CRM — s'il n'existe pas, rien n'est touché.
2. Si l'équipement est absent de UISP : pose sa clé UISP, puis attend qu'il se
   déclare (**60 s au plus**).
3. Le rattache au client, dans le même appel.

Un équipement déjà rattaché au bon client ne subit aucune écriture : **l'appel
peut être rejoué sans risque.**

## Réponse

Toute réponse, succès comme erreur, contient ces quatre champs :

| Champ | Sens |
|---|---|
| `assigned` | `true` = l'équipement est rattaché au client |
| `pending_registration` | `true` = clé posée, équipement pas encore déclaré : rejouer **une fois** après `retry_after_seconds` |
| `retry_after_seconds` | Délai avant de rejouer (seulement quand `pending_registration` vaut `true`), sinon `null` |
| `error_code` | `null` en cas de succès, sinon un code stable (voir ci-dessous) |

| Issue | HTTP | `assigned` | `pending_registration` | `error_code` |
|---|---|---|---|---|
| Rattaché | `200` | `true` | `false` | `null` |
| En attente | `200` | `false` | `true` | `null` |
| Erreur | `4xx` / `5xx` | `false` | `false` | un code |

Exemple d'erreur (extrait) :

```json
{
  "error_code": "device_already_assigned",
  "message": "L'équipement 78:45:58:0b:bc:76 est déjà rattaché au client CRM 1369. ...",
  "assigned": false,
  "pending_registration": false,
  "retry_after_seconds": null,
  "current_crm_client_id": "1369",
  "current_client_name": "Ba, Amadou"
}
```

Les erreurs portent aussi un champ `detail` : c'est l'ancien format, conservé
pour compatibilité.

## Codes d'erreur

Tester `error_code`, jamais le texte de `message` (qui peut changer).

| `error_code` | HTTP | Que faire |
|---|---|---|
| `invalid_request` | `422` | Corps invalide — `detail` indique le champ en cause |
| `invalid_mac` | `400` | Corriger la MAC |
| `unauthorized` | `401` | Vérifier l'en-tête `X-API-Key` |
| `crm_client_not_found` | `404` | Vérifier `crm_client_id` |
| `crm_service_mismatch` | `404` | Ce `crm_service_id` n'appartient pas à ce client |
| `device_not_found` | `404` | MAC inconnue de UISP et de notre inventaire |
| `multiple_services` | `409` | Rejouer avec un `crm_service_id` pris dans `candidates` |
| `device_already_assigned` | `409` | Équipement déjà chez un autre client (`current_crm_client_id`, `current_client_name`). `force: true` **seulement** si le déplacement est voulu |
| `device_unreachable` | `502` | Équipement injoignable — réessayer plus tard |
| `uisp_error` | `502` | Erreur du contrôleur UISP — réessayer plus tard |
| `uisp_not_configured` | `400` | Nous signaler |
| `uisp_write_forbidden` | `403` | Nous signaler |
| `internal_error` | `500` | Nous signaler |

Erreurs du proxy, sans JSON : `405` appel en `http://` · `429` trop de requêtes
· `504` coupure avant la fin — rejouer est sans risque.

## Exemple

```bash
curl -k -X POST "https://102.215.95.229/api/v1/uisp/assign" \
  -H "X-API-Key: <CLE_API>" \
  -H "Content-Type: application/json" \
  -d '{"mac":"78:45:58:0B:BC:76","crm_client_id":"1361"}' \
  --max-time 120
```

## Évolutions du format

Toute modification du format de réponse sera annoncée avant d'être déployée.
Version en vigueur : **2026-09-11**.
