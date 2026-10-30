# API — Suppression et remplacement d'antenne (S-Ticketing)

Deux routes pour clôturer une intervention sur l'antenne d'un abonné :

* **Suppression** — l'antenne est retirée de UISP Network puis d'A2 Supervision.
* **Remplacement** — la nouvelle antenne est rattachée au client (dans UISP et
  dans A2 Supervision), puis l'ancienne est retirée des deux.

Une antenne est toujours désignée par son **adresse MAC** (toute notation :
`78:45:58:0B:BC:76`, `78-45-58-0b-bc-76`, `7845580BBC76`). Le client est désigné
par son **id CRM**.

⚠️ Ces opérations sont **irréversibles**. Les refus décrits plus bas existent pour
éviter de supprimer l'antenne d'un autre abonné : ne les contournez pas par
habitude.

---

## Accès

| | |
|---|---|
| **URL (accès externe)** | `https://102.215.95.229/api/v1/antennas/...` |
| **URL (accès LAN interne)** | `https://10.135.3.25/api/v1/antennas/...` |
| **Authentification** | en-tête `X-API-Key: <votre clé>` |
| **Corps** | JSON, `Content-Type: application/json` |

> **Appelez en `https://`.** Le port 80 renvoie une redirection, qui transforme un
> `POST` en `GET` et perd le corps : l'appel échouerait en `405`.
>
> Le certificat est auto-signé : épinglez-le (`--cacert`) ou utilisez `curl -k`.
> L'IP source de l'appelant doit être autorisée sur notre pare-feu.

La clé ne donne accès **qu'à** ces deux routes. Ne la partagez pas.

---

## 1. Suppression d'une antenne

`POST /api/v1/antennas/delete`

```json
{ "mac": "78:45:58:0B:BC:76" }
```

| Champ | Obligatoire | Description |
|---|---|---|
| `mac` | oui | MAC de l'antenne à supprimer |
| `force` | non (`false`) | `true` pour supprimer une antenne encore vue **en ligne** (voir `device_online`) |

Réponse `200` :

```json
{
  "mac": "78:45:58:0b:bc:76",
  "deleted": true,
  "removed_from_uisp": true,
  "removed_from_database": true,
  "steps": [
    { "step": "delete_uisp", "ok": true, "message": "Supprimée de UISP." },
    { "step": "delete_database", "ok": true, "message": "Supprimée de la base A2 Supervision." }
  ],
  "message": "Antenne 78:45:58:0b:bc:76 supprimée.",
  "error_code": null
}
```

`removed_from_uisp: false` signifie que l'antenne n'était déjà plus dans UISP (de
même pour la base) : ce n'est pas une erreur.

---

## 2. Remplacement d'une antenne client

`POST /api/v1/antennas/replace`

```json
{
  "crm_client_id": "1361",
  "old_mac": "78:45:58:0B:BC:76",
  "new_mac": "78:45:58:0B:BC:77"
}
```

| Champ | Obligatoire | Description |
|---|---|---|
| `crm_client_id` | oui | Id du client dans le CRM |
| `old_mac` | oui | MAC de l'antenne retirée |
| `new_mac` | oui | MAC de l'antenne installée |
| `crm_service_id` | non | Seulement si le client a plusieurs services **et** que l'ancienne antenne n'est pas dans UISP. Sinon la nouvelle antenne reprend le service de l'ancienne |
| `force` | non (`false`) | `true` pour remplacer alors que l'**ancienne** antenne est encore en ligne |

**Ordre des opérations.** La nouvelle antenne est rattachée **d'abord**,
l'ancienne supprimée **ensuite** : si le rattachement n'aboutit pas, le client
garde son ancienne antenne.

**Appel lent possible (jusqu'à ~2 min).** Si la nouvelle antenne n'est pas encore
connue de UISP, nous lui posons la clé du contrôleur puis attendons qu'elle se
déclare. Réglez le timeout de votre client à **130 s**.

Réponse `200`, remplacement terminé :

```json
{
  "crm_client_id": "1361",
  "old_mac": "78:45:58:0b:bc:76",
  "new_mac": "78:45:58:0b:bc:77",
  "replaced": true,
  "new_assigned": true,
  "pending_registration": false,
  "retry_after_seconds": null,
  "removed_from_uisp": true,
  "removed_from_database": true,
  "client_name": "Ba, Amadou",
  "crm_service_id": "501",
  "steps": [ "..." ],
  "message": "Antenne 78:45:58:0b:bc:76 remplacée par 78:45:58:0b:bc:77 pour le client CRM 1361.",
  "error_code": null
}
```

Réponse `200`, **pas encore terminé** (`replaced: false`,
`pending_registration: true`) : la nouvelle antenne ne s'est pas encore déclarée.
L'ancienne est **conservée**. Rejouez le **même appel** après
`retry_after_seconds` secondes.

**Rejouer est sans danger.** Un remplacement déjà terminé, rejoué, répond
`200 replaced: true` sans rien refaire.

---

## Erreurs

Toute réponse porte `error_code` (`null` en cas de succès) et `message`. Testez
`error_code`, pas le texte de `message`, qui peut changer.

| HTTP | `error_code` | Signification | Que faire |
|---|---|---|---|
| 400 | `invalid_mac` | MAC mal formée | Corriger la MAC |
| 400 | `same_device` | `old_mac` = `new_mac` | Corriger le ticket |
| 401 | `unauthorized` | Clé absente ou refusée | Vérifier l'en-tête `X-API-Key` |
| 404 | `device_not_found` | Suppression : MAC inconnue de UISP et d'A2 Supervision | Vérifier la MAC |
| 404 | `old_device_not_found` | Remplacement : ancienne MAC inconnue | Vérifier la MAC |
| 404 | `crm_client_not_found` | Id CRM inconnu | Vérifier l'id client |
| 404 | `crm_service_mismatch` | `crm_service_id` n'appartient pas au client | Vérifier les ids |
| 409 | `not_a_client_antenna` | La MAC est un équipement d'infrastructure (Rocket, AF60, switch…) | Ne jamais forcer : la MAC est fausse |
| 409 | `device_online` | L'antenne (l'ancienne, pour un remplacement) répond encore | Vérifier la MAC ; `force: true` seulement si elle doit bien être retirée |
| 409 | `client_blocked` | Le client est bloqué (impayé) | Faire débloquer le client d'abord |
| 409 | `content_filter_active` | Remplacement : l'ancienne antenne porte un filtre de contenu | Faire retirer le filtre d'abord |
| 409 | `old_device_not_owned` | L'ancienne antenne appartient à un autre client (`current_crm_client_id`) | Vérifier le client et la MAC |
| 409 | `old_device_owner_unknown` | Le client de l'ancienne antenne est introuvable | Nous contacter |
| 409 | `device_already_assigned` | La nouvelle antenne est déjà rattachée à un autre client (`current_crm_client_id`) | Vérifier la nouvelle MAC |
| 409 | `multiple_services` | Le client a plusieurs services (`candidates`) | Rejouer avec `crm_service_id` |
| 422 | `invalid_request` | Corps invalide (`detail` nomme le champ) | Corriger le corps |
| 502 | `device_unreachable` | La nouvelle antenne n'a pas pu être jointe pour lui poser la clé | Vérifier qu'elle est alimentée et en ligne, rejouer |
| 502 | `old_device_delete_failed` | Nouvelle antenne rattachée (`new_assigned: true`), ancienne non supprimée | Rejouer le même appel |
| 502 | `uisp_error` | Le contrôleur UISP n'a pas répondu | Rejouer plus tard |
| 403 | `uisp_write_forbidden` | Configuration de notre côté | Nous contacter |
| 429 | — | Trop d'appels (60/min) | Ralentir |

`force` ne lève **que** `device_online`. Aucun autre refus ne se contourne.

---

## Historique

| Date | Changement |
|---|---|
| 2026-10-26 | Première version |
