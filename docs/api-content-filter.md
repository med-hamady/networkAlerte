# API — Filtre de contenu par plateforme

Rend une **plateforme** (TikTok, Facebook, …) inaccessible — ou de nouveau
accessible — à un abonné désigné par l'**adresse MAC** de son équipement (LR).

C'est la transposition de la page « Filtre de contenu » du dashboard : le filtre
est appliqué **sur l'équipement du client** au niveau DNS, persisté, et
**ré-appliqué automatiquement toutes les 120 s** (il survit à un reboot du LR).

> **L'appel prend quelques secondes** : il ouvre une session SSH sur la radio du
> client pour poser le filtre. Compter **jusqu'à 45 s** sur un client lent.
> **Prévoir un timeout client ≥ 120 s** (c'est la limite côté serveur).

---

## Endpoints

| Méthode | Chemin | Rôle |
|---|---|---|
| `POST` | `/api/v1/content-filter/block` | Rendre une plateforme inaccessible |
| `POST` | `/api/v1/content-filter/unblock` | La rendre de nouveau accessible |
| `GET` | `/api/v1/content-filter/status?mac=…` | Ce qui est bloqué chez ce client |
| `GET` | `/api/v1/content-filter/platforms` | Catalogue des plateformes valides |

| | |
|---|---|
| **URL (accès externe)** | `https://102.215.95.229/api/v1/content-filter/…` |
| **URL (accès LAN interne)** | `https://10.135.3.25/api/v1/content-filter/…` |
| **Format** | JSON (`Content-Type: application/json`) |
| **Débit autorisé** | 120 requêtes/minute par IP source (au-delà : `429`) |

### ⚠️ HTTPS obligatoire — ne pas appeler en `http://`

Le port 80 répond par une redirection `301`, et **une redirection détruit un
POST** : la plupart des clients (curl, Postman, cURL PHP) rejouent la requête
**en GET, sans le corps JSON**, et reçoivent un `405`. Appeler `https://`
directement — sur l'IP publique **comme** sur l'IP LAN.

Le certificat est auto-signé (réseau interne, pas de nom DNS public) : épingler
notre `fullchain.pem` en CA de confiance, ou à défaut désactiver la vérification
(`curl -k`, `CURLOPT_SSL_VERIFYPEER = false`).

L'accès sur l'IP publique passe par l'**allowlist du FortiGate** : l'IP source de
l'appelant doit y être autorisée.

## Authentification

En-tête `X-API-Key`, avec la clé **dédiée à ces routes** (`CONTENT_BLOCK_API_KEY`).

Elle n'ouvre **que** `/content-filter`. Elle ne permet notamment **pas** de
couper l'accès internet d'un abonné (`/fai/block`) : c'est un autre pouvoir,
tenu par un autre système et une autre clé.

```
X-API-Key: <votre clé>
```

---

## Bloquer une plateforme

```bash
curl -k -X POST https://102.215.95.229/api/v1/content-filter/block \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"mac": "d0:21:f9:f6:07:c2", "platform": "tiktok", "user": "ali@a2ict.com"}'
```

| Champ | Obligatoire | Détail |
|---|---|---|
| `mac` | oui | MAC du LR de l'abonné. Formats acceptés : `aa:bb:cc:dd:ee:ff`, `aa-bb-…`, `aabb.ccdd.eeff`, `aabbccddeeff` |
| `platform` | oui | Une clé (`"tiktok"`) **ou** une liste (`["tiktok","facebook"]`) |
| `user` | non | L'agent à l'origine de l'action (e-mail, ou libellé automatique). Journalisé tel quel |

### ⚠️ L'appel est CUMULATIF

Bloquer TikTok chez un abonné qui a déjà Facebook bloqué **laisse Facebook
bloqué**. Vous n'avez donc jamais à connaître — ni à réémettre — l'état complet
du client, qu'un opérateur a pu modifier entre-temps depuis le dashboard.

Pour retirer une plateforme, appelez `/unblock` avec cette plateforme. Les autres
ne sont pas touchées.

### Rejouable sans risque

Réémettre le même ordre ne fait que ré-affirmer le filtre sur l'équipement.
C'est même la bonne conduite quand `ok` vaut `false` : voir plus bas.

## Réponse

```json
{
  "ok": true,
  "message": "Filtre de contenu appliqué pour 36086261-Toutou (services: TikTok). …",
  "mac": "d0:21:f9:f6:07:c2",
  "name": "36086261-Toutou",
  "mode": "denylist",
  "blocked_platforms": ["facebook", "tiktok"],
  "categories": ["facebook", "tiktok"],
  "content_block_enforced_at": "2026-08-19T10:14:52Z",
  "retry_scheduled": false,
  "unenforceable_reason": null
}
```

| Champ | Ce qu'il dit |
|---|---|
| `ok` | Le filtre a été **appliqué sur l'équipement**. Ce n'est pas « ordre reçu » : voir ci-dessous |
| `blocked_platforms` | Ce qui est **effectivement inaccessible** au client. **C'est le champ à lire** |
| `mode` | Direction du filtre : `denylist` (tout sauf ces services) ou `allowlist` (rien sauf ces services) |
| `categories` | L'ensemble brut stocké. Identique à `blocked_platforms` en `denylist`, son **complément** en `allowlist`. Ne jamais l'interpréter sans `mode` |
| `retry_scheduled` | L'ordre n'a pas pu être appliqué **mais sera rejoué automatiquement** (LR éteint, radio coupée) |
| `unenforceable_reason` | Le LR **refuse** la connexion SSH — aucune reprise automatique, intervention technique requise |

### ⚠️ `ok: false` ne veut pas toujours dire « échec »

L'intention est enregistrée **dans tous les cas**. Deux situations à distinguer :

- **`retry_scheduled: true`** — l'abonné était injoignable (LR éteint). Le filtre
  sera posé tout seul dès qu'il répondra, sans nouvel appel de votre part. Rien
  à faire : ne pas boucler sur des rejeux.
- **`unenforceable_reason` renseigné** — l'équipement refuse la connexion (mot de
  passe, clé d'hôte). Là, personne ne réessaiera : à remonter à l'équipe réseau.

## Codes d'erreur

| Code | Cause |
|---|---|
| `400` | MAC mal formée, **ou plateforme inconnue** (la réponse liste les valeurs acceptées) |
| `401` | Clé API absente ou invalide |
| `404` | Aucun abonné avec cette MAC |
| `409` | LR en **mode bridge** (le filtre DNS ne peut pas fonctionner), ou intention inexprimable sur un client en mode « autoriser uniquement » (voir ci-dessous) |

Une plateforme mal orthographiée est **refusée**, jamais ignorée : un `titkok`
qui répondrait `200` vous laisserait croire l'abonné filtré alors qu'il ne l'est
pas.

### Le cas `allowlist`

Un abonné peut avoir été mis par un opérateur en mode « autoriser uniquement »
(par ex. WhatsApp seul). L'API s'y adapte : bloquer une plateforme la **retire**
de la liste autorisée, la débloquer l'y **ajoute** — le verbe garde donc toujours
le sens que vous lui donnez.

Une seule situation est refusée (`409`) : bloquer la **dernière** plateforme
encore autorisée. Cela effacerait le filtre et rouvrirait **tout l'internet** à
l'abonné — l'inverse exact de l'ordre. Il faut alors repasser le client en mode
« bloquer sauf » depuis le dashboard.

## Consulter l'état

```bash
curl -k -H "X-API-Key: $KEY" \
  "https://102.215.95.229/api/v1/content-filter/status?mac=d0:21:f9:f6:07:c2"
```

Même charge utile que ci-dessus. Lecture seule : ne touche pas à l'équipement.

## Plateformes disponibles

```bash
curl -k -H "X-API-Key: $KEY" https://102.215.95.229/api/v1/content-filter/platforms
```

```json
[
  {
    "key": "tiktok",
    "label": "TikTok",
    "description": "TikTok : appli, web, CDN vidéo et télémétrie ByteDance",
    "domains": ["tiktok.com", "tiktokv.com", "…"],
    "domain_count": 25,
    "mechanism": "domains"
  },
  {
    "key": "adult",
    "label": "Contenu adulte (18+)",
    "description": "Sites pour adultes, catégorisés en continu par un résolveur DNS familial en amont (pas une liste de domaines figée)",
    "domains": [],
    "domain_count": 0,
    "mechanism": "upstream_resolver"
  }
]
```

Clés actuelles : `facebook` (inclut Instagram, Messenger, Threads), `whatsapp`,
`tiktok`, `snapchat`, `youtube`, `telegram`, **`adult`**.
(Il n'y a plus de clé `google` depuis le 2026-08-25 — l'envoyer répond 400.)

**À lire plutôt qu'à coder en dur** : les jeux de domaines sont ajustables sans
redéploiement, et une plateforme ajoutée au catalogue devient utilisable sans
aucun changement de votre côté.

### `adult` — le contenu 18+

S'utilise **exactement comme les autres** :

```bash
curl -k -X POST https://102.215.95.229/api/v1/content-filter/block   -H "Content-Type: application/json" -H "X-API-Key: $KEY"   -d '{"mac": "d0:21:f9:f6:07:c2", "platform": "adult", "user": "ali@a2ict.com"}'
```

Cumulable avec les autres dans le même appel :
`"platform": ["adult", "tiktok"]`.

Deux particularités, qui ne changent rien à votre intégration mais expliquent la
réponse :

- **`domains` est vide et `domain_count` vaut 0** — ce n'est pas une erreur de
  configuration. « Tous les sites adultes » représente des millions de domaines,
  impossibles à lister et à tenir sur la radio d'un abonné. Le blocage bascule
  donc le résolveur DNS de l'abonné vers un **résolveur familial**
  (Cloudflare for Families) qui maintient lui-même la catégorisation et la met à
  jour en continu. C'est ce que dit `"mechanism": "upstream_resolver"`.
- **`categories` ne contient jamais `adult`** — il est stocké à part. C'est
  `blocked_platforms` qui le fait apparaître, comme pour les autres : c'est
  toujours **le champ à lire**.

## Limites connues

Le filtrage est **DNS** : il couvre l'usage normal d'un abonné, mais ne peut pas
arrêter un client qui utilise DNS-over-HTTPS, un VPN, ou qui joint un service par
son adresse IP brute.
