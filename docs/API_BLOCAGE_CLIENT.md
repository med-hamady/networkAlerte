# API de blocage / déblocage de l'accès internet d'un client

Guide d'intégration à destination de tout système tiers (facturation, paiement, CRM,
portail self-care, script d'exploitation) qui doit **couper** ou **rétablir** l'accès
internet d'un abonné.

---

## 1. Principe

Vous nous transmettez l'**adresse MAC** de l'équipement radio du client (le « LR »,
installé chez lui) et l'action souhaitée. Nous appliquons l'ordre **directement sur
cet équipement**, et nous le **maintenons** : si le client redémarre ou débranche son
matériel, le blocage est ré-appliqué automatiquement à son retour.

Trois opérations, et rien d'autre à connaître :

| Objectif | Appel |
|---|---|
| Impayé → couper l'accès | `POST /block` |
| Paiement reçu → rétablir | `POST /unblock` |
| Connaître l'état d'un client | `GET /status` |

---

## 2. Accès

| | |
|---|---|
| **URL de base** | `https://102.215.95.233/api/v1/fai` |
| **Authentification** | En-tête HTTP `X-API-Key: <clé fournie par l'équipe réseau>` |
| **Portée de la clé** | La clé n'ouvre **que** ces trois routes. Elle ne donne accès à aucune autre partie du système. |
| **Format** | JSON (`Content-Type: application/json`) |
| **Débit autorisé** | 120 requêtes/minute par adresse IP source |

### Trois contraintes techniques à respecter

**Certificat TLS auto-signé.** Votre client HTTP doit désactiver la vérification du
certificat **pour cet hôte uniquement**, sinon tous les appels échoueront :
`verify=False` (Python `requests`), `-k` (curl), `rejectUnauthorized: false` (Node).
La connexion reste chiffrée.

**Timeout d'au moins 60 secondes.** Un appel attend la réponse réelle de l'équipement
du client avant de répondre — c'est ce qui rend le résultat fiable. Avec le timeout par
défaut de nombreuses bibliothèques (10 s), vous verriez une erreur alors que l'ordre a
été exécuté.

**Confidentialité de la clé.** L'URL est joignable depuis Internet ; la clé est le seul
secret qui protège l'accès des clients. Ne la commitez pas dans un dépôt, ne la mettez
pas dans une URL, ne la diffusez pas hors de votre équipe. En cas de doute sur une fuite,
prévenez l'équipe réseau : elle est révoquée et remplacée en une minute.

### Format de la MAC

Tous les formats usuels sont acceptés et normalisés côté serveur :
`d0:21:f9:f6:07:c2`, `D0-21-F9-F6-07-C2`, `d021.f9f6.07c2`, `d021f9f607c2`.

---

## 3. Bloquer un client

```http
POST /api/v1/fai/block
X-API-Key: <clé>
Content-Type: application/json

{
  "mac": "d0:21:f9:f6:07:c2",
  "mode": "full",
  "reason": "Impayé facture 2026-07"
}
```

| Champ | Obligatoire | Description |
|---|---|---|
| `mac` | oui | MAC de l'équipement du client |
| `mode` | non | `full` = coupure totale. `whatsapp_only` = le client garde WhatsApp (pour vous joindre et payer), tout le reste est coupé. Omis → valeur par défaut du serveur. |
| `reason` | non | Motif, texte libre. Visible par les opérateurs sur le tableau de bord et dans le journal d'audit. |

---

## 4. Débloquer un client

```http
POST /api/v1/fai/unblock
X-API-Key: <clé>
Content-Type: application/json

{ "mac": "d0:21:f9:f6:07:c2" }
```

Rétablit l'accès complet **quel que soit le mode** qui avait été appliqué — vous n'avez
pas à savoir comment le client avait été coupé.

---

## 5. Consulter l'état d'un client

```http
GET /api/v1/fai/status?mac=d0:21:f9:f6:07:c2
X-API-Key: <clé>
```

Lecture seule, sans aucun effet sur l'équipement. **C'est l'appel à utiliser pour tester
votre intégration** : il n'existe pas d'environnement de test, les appels `block` /
`unblock` agissent sur de vrais clients.

---

## 6. La réponse (identique pour les trois routes)

```json
{
  "ok": true,
  "message": "Client 36086261-Toutoumedlimam bloqué (coupure totale).",
  "mac": "d0:21:f9:f6:07:c2",
  "name": "36086261-Toutoumedlimam",
  "client_blocked": true,
  "block_mode": "full",
  "client_block_enforced_at": "2026-07-14T10:32:11Z",
  "retry_scheduled": false,
  "unenforceable_reason": null
}
```

| Champ | Signification |
|---|---|
| `client_blocked` | **L'état officiel du client chez nous.** C'est ce champ qui fait foi. |
| `ok` | L'ordre a-t-il été appliqué **sur l'équipement à cet instant** |
| `message` | Message lisible, à journaliser de votre côté |
| `name` | Nom du client tel que nous le connaissons (contrôle de cohérence) |
| `block_mode` | Mode actif (`full` ou `whatsapp_only`) |
| `client_block_enforced_at` | Dernière application effective sur l'équipement (`null` = jamais) |
| `retry_scheduled` | `true` = non appliqué pour l'instant, mais **sera rejoué automatiquement** |
| `unenforceable_reason` | Non `null` = l'équipement **refuse la connexion** : pas de rattrapage automatique, intervention technique nécessaire |

---

## 7. Le point clé de l'intégration : `ok: false` n'est pas un échec

L'équipement d'un client peut être **éteint** au moment de votre appel. Dans ce cas nous
répondons `HTTP 200` avec `ok: false`, `client_blocked` à jour et `retry_scheduled: true` :
**l'ordre est enregistré** et notre système l'applique automatiquement dès que
l'équipement revient en ligne — y compris des jours plus tard.

C'est vrai **dans les deux sens** : un client qui paie alors que son équipement est
débranché sera rétabli à son retour, sans nouvelle sollicitation de votre part.

> **Ne rejouez pas l'appel en boucle.** Le rattrapage est déjà pris en charge. Un système
> qui réessaie sur ces réponses ne fait que boucler inutilement sur des clients éteints.

La règle tient en trois lignes :

| Réponse | Ce que ça veut dire | Ce que vous faites |
|---|---|---|
| `ok: true` | Appliqué sur l'équipement | Rien |
| `ok: false` + `retry_scheduled: true` | Enregistré, application différée | Journaliser. **Ne pas réessayer.** |
| `ok: false` + `unenforceable_reason` | L'équipement refuse la connexion | **Nous le signaler** — il n'y aura pas de rattrapage |

---

## 8. Codes d'erreur

| Code | Cas | Conduite à tenir |
|---|---|---|
| `200` | Demande prise en compte (voir §7) | — |
| `400` | MAC mal formée | Corriger la MAC envoyée |
| `401` | Clé API absente ou invalide | Vérifier l'en-tête `X-API-Key` |
| `404` | Aucun équipement connu pour cette MAC | Le client n'est pas dans le parc supervisé — **nous le signaler**, ne pas réessayer |
| `409` | Équipement en mode *bridge* : le blocage est techniquement impossible | **Nous le signaler** (reconfiguration nécessaire) |
| `429` | Débit dépassé (120 req/min) | Ralentir, réessayer plus tard |
| `5xx` | Erreur serveur | Réessayer plus tard |

---

## 9. Exemples

### curl

```bash
# Bloquer (coupure totale)
curl -k -X POST https://102.215.95.233/api/v1/fai/block \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"mac":"d0:21:f9:f6:07:c2","mode":"full","reason":"Impayé"}'

# Bloquer en laissant WhatsApp accessible
curl -k -X POST https://102.215.95.233/api/v1/fai/block \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"mac":"d0:21:f9:f6:07:c2","mode":"whatsapp_only","reason":"Impayé"}'

# Débloquer
curl -k -X POST https://102.215.95.233/api/v1/fai/unblock \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"mac":"d0:21:f9:f6:07:c2"}'

# Consulter (sans effet)
curl -k "https://102.215.95.233/api/v1/fai/status?mac=d0:21:f9:f6:07:c2" \
  -H "X-API-Key: $API_KEY"
```

### Python

```python
import requests

BASE = "https://102.215.95.233/api/v1/fai"
HEADERS = {"X-API-Key": API_KEY}


def bloquer(mac: str, motif: str, mode: str = "full") -> bool:
    """Coupe l'accès d'un client. Retourne True si l'ordre est accepté."""
    r = requests.post(
        f"{BASE}/block",
        json={"mac": mac, "mode": mode, "reason": motif},
        headers=HEADERS,
        verify=False,   # certificat auto-signé
        timeout=60,     # l'appel attend l'équipement
    )
    r.raise_for_status()
    data = r.json()

    if data["unenforceable_reason"]:
        alerter_equipe_reseau(mac, data["unenforceable_reason"])  # pas de rattrapage
    # `client_blocked` fait foi — pas `ok`. Aucun retry à faire ici.
    return data["client_blocked"]


def debloquer(mac: str) -> bool:
    r = requests.post(
        f"{BASE}/unblock", json={"mac": mac},
        headers=HEADERS, verify=False, timeout=60,
    )
    r.raise_for_status()
    return not r.json()["client_blocked"]
```

### PHP

```php
$ch = curl_init("https://102.215.95.233/api/v1/fai/block");
curl_setopt_array($ch, [
    CURLOPT_POST           => true,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_SSL_VERIFYPEER => false,   // certificat auto-signé
    CURLOPT_TIMEOUT        => 60,      // l'appel attend l'équipement
    CURLOPT_HTTPHEADER     => ["X-API-Key: $apiKey", "Content-Type: application/json"],
    CURLOPT_POSTFIELDS     => json_encode([
        "mac"    => "d0:21:f9:f6:07:c2",
        "mode"   => "full",
        "reason" => "Impayé",
    ]),
]);
$data = json_decode(curl_exec($ch), true);
$estBloque = $data["client_blocked"];   // fait foi — pas $data["ok"]
```

---

## 10. Notes d'exploitation

- **Idempotence** : bloquer un client déjà bloqué (ou débloquer un client déjà actif) ne
  pose aucun problème et renvoie simplement l'état courant.
- **Appels en rafale** : vous pouvez envoyer plusieurs demandes simultanément (par exemple
  tous les déblocages de la nuit au matin). Elles sont toutes acceptées et enregistrées
  immédiatement, puis appliquées progressivement.
- **Persistance** : un blocage survit au redémarrage de l'équipement du client (il est
  ré-appliqué toutes les 2 minutes).
- **Traçabilité** : chaque action est journalisée de notre côté (date, MAC, client, motif,
  résultat) et consultable par les opérateurs.
- **Limite connue** : le mode `whatsapp_only` laisse aussi passer Facebook et Instagram —
  ces services partagent les mêmes adresses IP que WhatsApp.
