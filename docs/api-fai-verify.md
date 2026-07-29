# API — Vérification d'un LR par MAC

Contrôle pré-vol d'un équipement client (LR) à partir de son adresse MAC.
L'API ne modifie rien sur l'équipement, elle renvoie un verdict.

Destinée à un système tiers qui transmet une MAC et lit le résultat.

> **Contrôle en temps réel** : au moment de l'appel, l'API se connecte
> réellement à l'équipement (SSH) pour vérifier son état — elle ne renvoie pas
> une valeur mise en cache. Deux conséquences :
> - un équipement **éteint ou injoignable** au moment de l'appel ressort en `KO` ;
> - l'appel prend **quelques secondes** (le temps d'établir la connexion), un peu
>   plus sur un lien radio de mauvaise qualité — prévoir un timeout ≥ 30 s.

---

## Endpoint

| | |
|---|---|
| **Méthode** | `GET` |
| **URL (accès externe)** | `http://102.215.95.229/api/v1/fai/verify` |
| **URL (accès LAN interne)** | `https://10.135.3.25/api/v1/fai/verify` |

> L'appel HTTP est redirigé (302) vers HTTPS. Le certificat est auto-signé : un
> client qui vérifie le certificat doit désactiver la vérification SSL. Un
> navigateur / Postman / `curl -k` suit la redirection sans souci.
>
> L'accès sur l'IP publique passe par l'allowlist du FortiGate : l'IP source de
> l'appelant doit être autorisée (même règle que les routes /fai block/unblock).

## Authentification

Un en-tête HTTP obligatoire :

```
X-API-Key: <CLE_API_TRANSMISE_SEPAREMENT>
```

- La clé (`LR_VERIFY_API_KEY`) est communiquée hors de ce document, par canal séparé.
- Elle est scellée à cette seule route de vérification (n'ouvre ni le blocage,
  ni le reste de l'API).
- Sans en-tête ou clé invalide → `401 Unauthorized`.
- ⚠️ La valeur doit être copiée **sur une seule ligne**, sans espace ni saut de
  ligne (un retour-chariot invisible déclenche « invalid header » côté client).

## Paramètre

| Nom | Emplacement | Obligatoire | Description |
|---|---|---|---|
| `mac` | query string | Oui | Adresse MAC du LR. Formats acceptés : `aa:bb:cc:dd:ee:ff`, `aa-bb-cc-dd-ee-ff`, `aabb.ccdd.eeff`, `aabbccddeeff` (casse indifférente). |

Exemple d'URL complète :

```
http://102.215.95.229/api/v1/fai/verify?mac=78:45:58:0B:BC:76
```

## Ce que l'API vérifie

Quatre contrôles sur le LR :

1. **existe** — un LR correspond à cette MAC ;
2. **ssh_active** — l'accès SSH est actif sur le LR ;
3. **password_valid** — le mot de passe SSH est le mot de passe standard attendu ;
4. **router_mode** — le LR est en mode routeur.

Si les quatre passent → `OK`. Sinon → `KO` avec la raison.

## Réponse

Toujours du JSON. Structure :

```json
{
  "ok": true,
  "status": "OK",
  "mac": "78:45:58:0b:bc:76",
  "name": "49467712-Fatmehamadi",
  "reason": null,
  "checks": {
    "exists": true,
    "ssh_active": true,
    "password_valid": true,
    "router_mode": true
  },
  "ssh_status": "ok",
  "topology_mode": "router",
  "ssh_checked_at": "2026-07-28T08:11:12Z"
}
```

| Champ | Type | Signification |
|---|---|---|
| `ok` | booléen | `true` si tous les contrôles passent, `false` sinon. |
| `status` | texte | `"OK"` ou `"KO"` (équivalent lisible de `ok`). |
| `mac` | texte | La MAC normalisée. |
| `name` | texte / null | Nom du LR s'il existe, `null` sinon. |
| `reason` | texte / null | `null` si `OK` ; sinon résumé des contrôles en échec. |
| `checks` | objet | Détail par contrôle (`exists`, `ssh_active`, `password_valid`, `router_mode`), chacun `true`/`false`. |
| `ssh_status` | texte / null | État SSH brut (`ok`, `auth_failed`, `ssh_disabled`, `host_key_mismatch`, `unreachable`, ou `null` si jamais testé). |
| `topology_mode` | texte | Mode détecté : `router`, `bridge` ou `unknown`. |
| `ssh_checked_at` | date ISO 8601 / null | Date de la dernière vérification SSH (fraîcheur de l'info). |

## Codes HTTP

| Code | Cas |
|---|---|
| `200` | Requête traitée. Lire `status` (`OK` ou `KO`) pour le verdict. |
| `400` | MAC mal formée. |
| `401` | Clé API absente ou invalide. |

> Un LR **introuvable** ne renvoie **pas** `404`. C'est un `200` avec
> `status = "KO"` et `reason = "Aucun LR en base pour cette MAC"` — l'existence
> fait partie des contrôles.

## Exemples

**Verdict favorable :**

```bash
curl -k -H "X-API-Key: <CLE_API>" \
  "http://102.215.95.229/api/v1/fai/verify?mac=78:45:58:0B:BC:76"
```
```json
{ "ok": true, "status": "OK", "name": "49467712-Fatmehamadi", "reason": null, ... }
```

**Verdict défavorable :**

```json
{
  "ok": false,
  "status": "KO",
  "name": "12345678-ClientX",
  "reason": "SSH : authentification refusée (mot de passe rejeté) ; Le LR n'est pas en mode routeur (mode actuel : bridge)",
  "checks": { "exists": true, "ssh_active": false, "password_valid": false, "router_mode": false },
  "ssh_status": "auth_failed",
  "topology_mode": "bridge",
  "ssh_checked_at": "2026-07-28T08:05:00Z"
}
```

**MAC introuvable :**

```json
{ "ok": false, "status": "KO", "name": null, "reason": "Aucun LR en base pour cette MAC",
  "checks": { "exists": false, "ssh_active": false, "password_valid": false, "router_mode": false } }
```

## Recommandation d'intégration

Pour décider, il suffit de lire le champ `ok` (ou `status`). Les champs `checks`
et `reason` servent au diagnostic quand `ok = false`. Le champ `ssh_checked_at`
indique la fraîcheur : c'est le dernier état constaté par nos sondes, pas un test
en direct au moment de l'appel.
