# API de blocage / déblocage client — intégration système de paiement

Le superviseur réseau expose une API qui permet au **système de paiement** de couper
ou de rétablir l'accès internet d'un client, en donnant simplement l'**adresse MAC**
de son équipement (LR) et l'action souhaitée.

Le blocage est appliqué **directement sur le LR du client** (en SSH), persisté en base
et **ré-appliqué automatiquement toutes les 120 secondes** — il survit donc à un reboot
du LR. Ce n'est plus le routeur core qui bloque.

---

## 1. Accès

| | |
|---|---|
| **URL de base** | `https://102.215.95.229/api/v1/fai` |
| **Authentification** | En-tête HTTP `X-API-Key: <clé fournie par l'équipe réseau>` |
| **Portée de la clé** | La clé remise au système de paiement n'ouvre **que** ces trois routes `/fai`. Elle ne donne accès à aucune autre partie du superviseur. |
| **Format** | JSON (`Content-Type: application/json`) |

> **Filtrage par IP source** : l'accès est restreint par pare-feu à une liste d'adresses IP
> autorisées. L'IP publique du serveur de paiement y est déjà déclarée. Les appels **doivent
> partir de ce serveur** — depuis toute autre adresse, la connexion n'aboutira pas.
>
> **TLS** : le serveur présente un certificat **auto-signé**. Le client HTTP du système de
> paiement doit désactiver la vérification du certificat **pour cet hôte uniquement**
> (`verify=False` avec `requests` en Python, `-k` avec curl, `rejectUnauthorized: false`
> en Node). La connexion reste chiffrée.

**Format de la MAC** : tous les formats usuels sont acceptés et normalisés côté serveur —
`d0:21:f9:f6:07:c2`, `D0-21-F9-F6-07-C2`, `d021.f9f6.07c2`, `d021f9f607c2`.

---

## 2. Bloquer un client

`POST /api/v1/fai/block`

```http
POST /api/v1/fai/block HTTP/1.1
Host: 102.215.95.229
X-API-Key: <clé>
Content-Type: application/json

{
  "mac": "d0:21:f9:f6:07:c2",
  "mode": "full",
  "reason": "Impayé facture 2026-07"
}
```

| Champ | Obligatoire | Valeurs | Description |
|---|---|---|---|
| `mac` | oui | — | MAC du LR du client |
| `mode` | non | `full` \| `whatsapp_only` | `full` = coupure totale. `whatsapp_only` = le client garde WhatsApp (pour payer / joindre le support), tout le reste est coupé. Omis → défaut serveur (`full`). |
| `reason` | non | texte libre | Motif enregistré (visible par les opérateurs sur le dashboard) |

---

## 3. Débloquer un client

`POST /api/v1/fai/unblock`

```http
POST /api/v1/fai/unblock HTTP/1.1
Host: 102.215.95.229
X-API-Key: <clé>
Content-Type: application/json

{ "mac": "d0:21:f9:f6:07:c2" }
```

Rétablit l'accès internet complet (port LAN remonté **et** filtre WhatsApp retiré,
quel que soit le mode qui avait été appliqué).

---

## 4. Consulter l'état d'un client

`GET /api/v1/fai/status?mac=d0:21:f9:f6:07:c2`

Lecture seule — ne touche pas au LR. Permet au système de paiement de vérifier l'état
réel avant ou après une action.

---

## 5. Réponse (identique pour les 3 routes)

```json
{
  "ok": true,
  "message": "Client 36086261-Toutoumedlimam bloqué (coupure totale). Interface eth0.1 coupée.",
  "mac": "d0:21:f9:f6:07:c2",
  "name": "36086261-Toutoumedlimam",
  "client_blocked": true,
  "block_mode": "full",
  "client_block_enforced_at": "2026-07-14T10:32:11.482Z"
}
```

| Champ | Signification |
|---|---|
| `ok` | L'action a-t-elle été **appliquée sur le LR** à cet instant (voir §6) |
| `message` | Message lisible, à logger côté paiement |
| `name` | Nom du client tel que connu du superviseur (contrôle de cohérence) |
| `client_blocked` | **L'intention en base** : `true` = client marqué bloqué |
| `block_mode` | Mode actif (`full` ou `whatsapp_only`) |
| `client_block_enforced_at` | Dernière fois où le blocage a réellement été appliqué sur le LR (`null` = jamais) |

---

## 6. Point important : `ok: false` ≠ échec

`ok` reflète l'application **immédiate** sur le LR, pas la prise en compte de la demande.

Si le LR est momentanément injoignable (client éteint, radio coupée…), la réponse est
`HTTP 200` avec `ok: false` et `client_blocked: true` : **l'ordre est enregistré** et un
job le ré-applique automatiquement dès que le LR revient. Le système de paiement n'a
**rien à réessayer** — c'est déjà pris en charge.

Règle simple côté paiement :

- `client_blocked: true` dans la réponse → la demande **est acceptée**, considérer le client comme bloqué.
- `ok: false` → simplement logger « application différée » ; ne pas rejouer l'appel en boucle.

---

## 7. Codes d'erreur

| Code | Cas | Que faire |
|---|---|---|
| `200` | Demande prise en compte (voir `ok` / `client_blocked`) | — |
| `400` | MAC mal formée | Corriger la MAC envoyée |
| `401` / `403` | Clé API absente ou invalide | Vérifier l'en-tête `X-API-Key` |
| `404` | Aucun équipement connu pour cette MAC | Le client n'est pas dans le parc supervisé — signaler à l'équipe réseau, ne pas réessayer |
| `409` | Le LR est en mode *bridge* — le blocage est techniquement impossible | Signaler à l'équipe réseau (reconfiguration du LR nécessaire) |
| `5xx` | Erreur serveur | Réessayer plus tard |

---

## 8. Exemples `curl`

```bash
# Bloquer (coupure totale)
curl -k -X POST https://102.215.95.229/api/v1/fai/block \
  -H "X-API-Key: <clé>" -H "Content-Type: application/json" \
  -d '{"mac":"d0:21:f9:f6:07:c2","mode":"full","reason":"Impayé"}'

# Bloquer en laissant WhatsApp accessible
curl -k -X POST https://102.215.95.229/api/v1/fai/block \
  -H "X-API-Key: <clé>" -H "Content-Type: application/json" \
  -d '{"mac":"d0:21:f9:f6:07:c2","mode":"whatsapp_only","reason":"Impayé"}'

# Débloquer (paiement reçu)
curl -k -X POST https://102.215.95.229/api/v1/fai/unblock \
  -H "X-API-Key: <clé>" -H "Content-Type: application/json" \
  -d '{"mac":"d0:21:f9:f6:07:c2"}'

# Vérifier l'état
curl -k "https://102.215.95.229/api/v1/fai/status?mac=d0:21:f9:f6:07:c2" \
  -H "X-API-Key: <clé>"
```

---

## 9. Notes d'exploitation

- Les appels sont **idempotents** : bloquer un client déjà bloqué (ou débloquer un client
  déjà actif) ne pose aucun problème et renvoie l'état courant.
- Un blocage **survit au reboot du LR** (ré-application automatique toutes les 120 s).
- Le mode `whatsapp_only` laisse aussi passer Facebook/Instagram sur certaines plages
  (les adresses IP de Meta sont partagées) — limite connue et acceptée.
