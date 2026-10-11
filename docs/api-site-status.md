# API — État des sites

Renvoie l'état actuel de chaque site du réseau : `up` (en service) ou `down` (en panne).

L'API est en lecture seule : elle ne modifie rien.

---

## Endpoint

| | |
|---|---|
| **Méthode** | `GET` |
| **URL (accès externe)** | `https://102.215.95.229/api/v1/site-status` |
| **URL (accès LAN interne)** | `https://10.135.3.25/api/v1/site-status` |
| **Paramètres** | aucun |

> **Appelez en `https://`.** Le port 80 renvoie une redirection vers HTTPS.
>
> Le certificat est auto-signé : épinglez notre certificat (`--cacert`) ou, à
> défaut, désactivez la vérification (`curl -k`).
>
> Pour l'accès par l'IP publique, l'IP source de l'appelant doit être autorisée
> sur notre pare-feu. Communiquez-nous la vôtre.

## Authentification

Chaque requête doit porter la clé qui vous a été transmise, dans l'en-tête :

```
X-API-Key: <votre clé>
```

Cette clé ne donne accès **qu'à** cette route. Ne la partagez pas et ne
l'écrivez pas dans le code source.

## Exemple

```bash
curl -k -H "X-API-Key: <votre clé>" https://102.215.95.229/api/v1/site-status
```

## Réponse `200 OK`

```json
{
  "sites": [
    { "site": "A2 AT1", "status": "up" },
    { "site": "A2 CT1", "status": "down" },
    { "site": "A2 HQ",  "status": "up" }
  ]
}
```

| Champ | Type | Description |
|---|---|---|
| `sites` | liste | Un élément par site, trié par nom |
| `sites[].site` | texte | Nom du site, exactement tel qu'il est enregistré chez nous |
| `sites[].status` | texte | `up` ou `down`, rien d'autre |

> Comparez le nom du site **tel quel**, sans le modifier : certains noms
> contiennent un double espace (ex. `A2  ARF1`).

## Ce que signifie `down`

Un site est `down` quand son **switch** ne répond plus sur le réseau. Si le site
a plusieurs switches, il suffit que l'un d'eux soit en panne.

- Une panne est déclarée après environ **1 min 30** sans réponse. Une coupure
  plus courte n'apparaît pas.
- L'état est celui du moment de l'appel. L'API ne renvoie aucun historique.
- Un site sans switch supervisé n'apparaît pas dans la liste.

## Fréquence d'appel

La réponse est immédiate. Une interrogation **par minute** suffit, puisque
l'état est mis à jour toutes les 30 secondes environ. N'interrogez pas plus
d'une fois toutes les 10 secondes.

## Erreurs

| Code | Cause | Que faire |
|---|---|---|
| `401` | Clé absente ou invalide | Vérifiez l'en-tête `X-API-Key` |
| `503` | Trop de requêtes, ou incident de notre côté | Espacez les appels et réessayez ; prévenez-nous si ça persiste |
| `500`, `502`, `504` | Incident de notre côté | Réessayez plus tard ; prévenez-nous si ça persiste |
