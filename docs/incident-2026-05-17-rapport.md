# Rapport d'incident de sécurité — Network Supervisor

**Référence :** INC-2026-05-17
**Date de l'incident :** 17 mai 2026
**Date du rapport :** 17 mai 2026
**Statut :** Clos — résolu et corrigé
**Classification :** Interne — diffusion équipe technique & direction
**Système concerné :** Network Supervisor (supervision réseau UISP/Ubiquiti), serveur de production `a2st02` (`102.215.95.233`)

> ⚠️ Ce document décrit une intrusion réelle. Aucun secret (clé API, mots de passe)
> n'y figure : ils sont gérés hors de ce rapport et ont été renouvelés.

---

## 1. Résumé exécutif

Le 17 mai 2026, le serveur de production du système de supervision réseau a été
la cible d'une **attaque automatisée depuis Internet**. Un robot d'attaque
(scanner de vulnérabilités) a exploité **deux faiblesses combinées** :

1. L'interface du superviseur était **exposée publiquement sur Internet** (au
   lieu d'être réservée à un accès interne).
2. Un composant interne (le « proxy » du tableau de bord) **ajoutait
   automatiquement la clé d'authentification secrète à toute requête, sans
   vérifier qui la faisait**.

Conséquence : n'importe quel visiteur d'Internet pouvait créer, modifier et
**supprimer** des équipements supervisés. L'attaquant a **détruit la totalité
de l'inventaire** (antennes Rocket, switch, onduleur, modems clients) et
**pollué la configuration** avec ~130 entrées parasites.

**Impact métier :** perte temporaire totale de la visibilité réseau (supervision
aveugle pendant ~15 h), inventaire détruit.

**Point rassurant :** la base de données n'a **pas** été piratée techniquement.
Les tentatives d'injection SQL ont toutes échoué (protections applicatives
efficaces). Aucune fuite de mot de passe d'équipement, aucune prise de contrôle
du serveur. Le dommage est une **destruction de données via l'API légitime**,
pas une compromission du moteur de base de données.

**Résolution :** attaque coupée, accès Internet supprimé, faille corrigée,
inventaire intégralement restauré (avec auto-reconstruction du parc), clé
secrète renouvelée, et **4 correctifs de code déployés** en production pour que
cela ne puisse plus se reproduire.

---

## 2. Chronologie (heures en UTC)

| Heure (17/05) | Événement |
|---|---|
| ~02:35 | Début des suppressions d'équipements par l'attaquant (premières traces dans les logs applicatifs) |
| 02:35 → 17:26 | Attaque continue : ~120 000 requêtes, création/modification/suppression en boucle, scan d'injections |
| ~04:25 | Premières traces dans les logs du reverse-proxy (journalisation démarrée plus tard que l'applicatif) |
| ~17:05 | Détection : le tableau de bord n'affiche plus que des équipements « LR », tout le reste a disparu |
| ~17:10 | Investigation : confirmation en base (0 Rocket, 0 Switch, 0 Power ; 14 LR orphelins) |
| ~17:20 | Analyse des logs : identification de l'attaque (payloads sqlmap dans les noms d'équipements) |
| ~17:26 | **Confinement** : arrêt du reverse-proxy → l'attaque ne peut plus passer |
| ~17:30 | Préservation des preuves (logs applicatifs + proxy copiés hors conteneurs) |
| ~17:40 | Identification de la source (sous-réseau `85.203.47.0/24`) et du vecteur |
| ~18:00 | Correctif proxy codé + reverse-proxy reconfiguré sur `127.0.0.1` (plus d'Internet) |
| ~18:05 | Clé API renouvelée (ancienne révoquée) |
| ~18:07 | Restauration de l'inventaire (5 Rockets + Switch + Power recréés) |
| ~18:08 → 18:55 | Auto-reconstruction du parc clients (LR) par découverte automatique |
| ~18:50 | Découverte et purge de 129 entrées de configuration injectées |
| ~18:55 | Vérifications finales : parc cohérent, notifications opérationnelles |
| — | **Incident clos.** 4 correctifs déployés en production sur `main` |

---

## 3. Description de l'attaque

### 3.1 Origine

- **Source :** sous-réseau **`85.203.47.0/24`** — environ **25 adresses IP en
  rotation** (.106, .114, .121, .124, .130, .132, .134–.139, .142, .145, .147,
  .152, .154, .159, .161, .174, …). La rotation d'IP est une technique
  d'évasion / de répartition typique des botnets et services de proxy.
- **Volume :** **~120 000+ requêtes** sur la fenêtre d'attaque.
- **User-Agent :** falsifié en navigateur Chrome légitime
  (`Mozilla/5.0 (Windows NT 10.0; …) Chrome/126.0.0.0`), avec dans certains cas
  les charges d'attaque collées jusque dans cet en-tête.

### 3.2 Outillage et nature

Attaque **automatisée** (scanner de vulnérabilités, signature compatible
`sqlmap` et outils de scan web génériques). Familles de charges observées :

- **Injection SQL** en aveugle temporisée : `PG_SLEEP(15)`, `sleep(15)`,
  `WAITFOR DELAY '0:0:15'`, `DBMS_PIPE.RECEIVE_MESSAGE(...)` (couvre
  PostgreSQL, MySQL, Oracle, SQL Server).
- **XSS / XSS aveugle** : balises `<ScRiPt>…</ScRiPt>`, sondes hors-bande vers
  `bxss.me`.
- **Injection ESI / SSRF** : `<esi:include src="http://bxss.me/…"/>`, URL hors
  bande `http://dicrpdbjmemujemfyopp.zzz/…`.
- **Traversée de répertoire** : `../../../../etc/passwd`, `c:/windows/win.ini`.
- **Injection de template (SSTI)** : `${98991*97996}`, `{{98991*97996}}`.
- **Injection de commande / code** : `;assert(base64_decode(...))`,
  `response.write(...)`.

L'attaquant ne ciblait pas spécifiquement l'entreprise : il s'agit d'un
**balayage opportuniste d'Internet** qui a trouvé un service exposé et
exploitable.

---

## 4. Vecteur technique et cause racine

L'intrusion résulte de **la combinaison de deux faiblesses** :

### 4.1 Exposition publique sur Internet

Le reverse-proxy (nginx) du serveur écoutait sur `0.0.0.0:443`, c'est-à-dire
**accessible depuis tout Internet**. La conception d'origine prévoyait un accès
**réservé au réseau interne via tunnel SSH** ; un changement antérieur l'avait
rendu public, ce qui était documenté comme un risque connu mais non corrigé.

### 4.2 Proxy interne « relais ouvert » de la clé secrète

Le tableau de bord n'a pas d'authentification propre : il utilise un
composant serveur (`/api/proxy/...`) qui **ajoute automatiquement la clé API
secrète** à chaque appel pour parler au backend. Or ce composant :

- relayait **toutes** les méthodes, y compris **POST / PUT / DELETE**
  (création / modification / suppression) ;
- **n'effectuait aucune vérification** de l'origine ou de l'identité de
  l'appelant.

**Conséquence :** toute personne sur Internet pouvait appeler
`DELETE /api/proxy/devices/{id}` ; le proxy y ajoutait la clé secrète et le
backend exécutait la suppression comme une opération légitime.

> **Cause racine :** un point d'entrée d'écriture exposé à Internet, sans
> authentification réelle, qui appose lui-même le secret d'autorisation.

---

## 5. Impact

### 5.1 Données détruites

Suppression de **l'intégralité de l'inventaire supervisé** via l'API :

- **5 antennes Rocket** : OUEST, EST, SUD, OUEST1, OMNI
- **1 UISP Switch**, **1 UISP Power (onduleur)**
- **L'ensemble des modems clients et radios abonnés (LR)** — seuls **14 LR**
  ont survécu (orphelins, détachés de leur Rocket parent)

Perte de la supervision réseau en temps réel pendant la durée de l'incident
(~15 h de fonctionnement « aveugle »).

### 5.2 Configuration polluée

**129 faux canaux de notification** injectés dans la table de configuration
(noms = charges d'injection). Effet secondaire : saturation du système d'envoi
d'e-mails (chaque alerte tentait une livraison vers les 130 canaux).

### 5.3 Divulgation d'informations

Les requêtes de **lecture (GET)** n'étant pas authentifiées côté périmètre,
l'attaquant a pu lire la liste des équipements, incidents et métriques
(~4 000 lectures réussies). Donnée non sensible (topologie réseau interne,
pas de secret), mais à considérer comme **exposée**.

### 5.4 Ce qui n'a PAS été compromis

- **Moteur PostgreSQL :** aucune injection SQL n'a abouti. Le code utilise des
  requêtes paramétrées (ORM SQLAlchemy), une validation stricte des entrées
  (Pydantic) et un typage entier des identifiants — l'écrasante majorité des
  charges ont été rejetées (réponses 404 / 422 / 405). Le scanner journalisait
  « deleted » parce que le **CRUD normal** fonctionnait, **pas** parce que
  l'injection réussissait.
- **Serveur :** aucune exécution de code à distance, aucun accès shell, aucune
  élévation de privilèges constatés.
- **Secrets d'équipements :** mots de passe SSH/API des Rockets non exposés
  (stockés en base, jamais renvoyés en clair par l'API).
- **Clé API :** sa valeur n'a pas fuité (le proxy l'ajoutait côté serveur,
  invisible pour l'attaquant) — mais elle a été **exploitable** par anonyme
  pendant l'incident, donc renouvelée par précaution.

---

## 6. Détection

L'incident a été détecté **fonctionnellement** : le tableau de bord n'affichait
plus que des équipements « LR », tous les autres ayant disparu. L'investigation
des journaux applicatifs a révélé une longue série de suppressions, dont
plusieurs portant des noms contenant des charges d'injection SQL — signature
non ambiguë d'une attaque automatisée.

> **Constat d'amélioration :** l'attaque a duré ~15 h sans alerte automatique.
> Il n'existait pas de détection sur les suppressions de masse ni sur les pics
> d'erreurs. Voir recommandations (§9).

---

## 7. Réponse et remédiation

Actions menées dans l'ordre :

1. **Confinement** — arrêt immédiat du reverse-proxy : l'attaque ne peut plus
   atteindre l'application.
2. **Préservation des preuves** — copie des journaux applicatifs et proxy hors
   des conteneurs (`~/incident-2026-05-17/`).
3. **Analyse forensique** — identification de la source (`85.203.47.0/24`), du
   vecteur (`/api/proxy`), du volume et de la fenêtre temporelle.
4. **Suppression de l'exposition Internet** — reverse-proxy reconfiguré pour
   n'écouter que sur `127.0.0.1` ; accès admin **uniquement via tunnel SSH**.
   Changement **versionné** (ne peut plus régresser par simple redéploiement).
5. **Correction de la faille applicative** — le proxy refuse désormais les
   écritures qui ne proviennent pas de la page elle-même (contrôle d'origine
   non falsifiable). Requêtes bloquées journalisées, clé jamais ajoutée.
6. **Renouvellement de la clé API** — ancienne clé révoquée.
7. **Restauration de l'inventaire** — recréation des 5 Rockets + Switch +
   Power. Le mécanisme de découverte automatique a **reconstruit le parc
   clients tout seul** (les radios LR se re-rattachent par adresse MAC) :
   passage de 14 à **39+ équipements**, **0 orphelin**.
8. **Nettoyage de la configuration** — purge des 129 canaux de notification
   injectés (conservation du seul canal légitime).
9. **Correction d'un défaut latent** — une notification lente (serveur e-mail
   saturé) pouvait bloquer la persistance de la topologie. Ajout d'un délai
   maximal d'envoi + mise en pause automatique d'un canal défaillant.
10. **Vérifications finales** — parc cohérent, notifications opérationnelles
    (0 échec après stabilisation).

---

## 8. Correctifs déployés (production, branche `main`)

| Commit | Objet |
|---|---|
| `1bc8f13` | **Proxy** : blocage des écritures non same-origin (en-tête `Sec-Fetch-Site`, non falsifiable par script) |
| `f12b6c9` | **Infra** : reverse-proxy lié à `127.0.0.1` (plus aucune exposition Internet) |
| `cb14533` | **Notifications** : délai d'envoi borné + mise en pause d'un canal qui se bloque |
| `8f2b1ee` | **Notifications** : mise en pause aussi après N échecs rapides consécutifs (cas serveur e-mail saturé) |

Ces protections sont **versionnées** : un futur déploiement les conserve ; la
faille ne peut pas être réintroduite par inadvertance.

---

## 9. Recommandations / actions de suivi

### Priorité haute

- **Maintenir l'accès interne uniquement** (tunnel SSH). Ne **jamais**
  ré-exposer sur Internet sans : (a) authentification réelle (login + session),
  **et** (b) filtrage par liste d'IP autorisées / pare-feu.
- **Authentifier aussi les lectures (GET)** : aujourd'hui lisibles par
  quiconque atteint le service → fuite d'information.
- **Renouveler par précaution** les mots de passe SSH/API des équipements
  (Rockets) au vu de l'ampleur du balayage.

### Priorité moyenne

- **Détection & alerte** : alerter automatiquement sur les suppressions de
  masse, pics d'erreurs 4xx, ou volume anormal de requêtes (un signal aurait
  réduit 15 h d'exposition à quelques minutes).
- **Journal d'audit** des opérations d'écriture (qui/quoi/quand) côté backend.
- **Limitation de débit / WAF / fail2ban** sur le périmètre, bannissement de
  sous-réseaux abusifs.
- **Sauvegardes PostgreSQL** : actuellement non mises en place (décision de
  déploiement antérieure, données jugées régénérables). À **réévaluer** : la
  configuration (équipements, canaux, politiques) n'est, elle, **pas**
  régénérable automatiquement.

### Priorité basse

- **E-mail d'alerte** : Gmail gratuit ne supporte pas les pics d'envoi →
  privilégier un *App Password* ou un fournisseur transactionnel
  (le code protège déjà la découverte en cas de saturation).

---

## 10. Leçons apprises

1. **Un point d'entrée d'écriture exposé à Internet sans authentification
   réelle = compromission garantie**, même si la « clé secrète » n'est jamais
   visible (ici, un relais l'apposait pour l'attaquant).
2. **La pollution touche la configuration, pas seulement les données** : 129
   entrées parasites injectées dans une table de config ont eu un effet de bord
   sérieux (saturation des notifications).
3. **Le couplage persistance ↔ effets de bord est dangereux** : une
   notification lente bloquait l'enregistrement de la topologie. Les effets de
   bord (e-mails) doivent être isolés et bornés.
4. **Sans détection, une intrusion dure** : ~15 h ici. La défense périmétrique
   doit s'accompagner d'une supervision de sécurité.
5. **Les protections doivent être versionnées**, pas appliquées à la main sur
   le serveur (sinon elles régressent au déploiement suivant).

---

## 11. Annexe — Indicateurs de compromission (IoC)

| Type | Valeur |
|---|---|
| Sous-réseau source | `85.203.47.0/24` (IP en rotation) |
| User-Agent (falsifié) | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/126.0.0.0 Safari/537.36` |
| Domaine hors-bande (XSS/SSRF) | `bxss.me` |
| Domaine hors-bande (SSRF) | `dicrpdbjmemujemfyopp.zzz` |
| Chemin exploité | `/api/proxy/devices/...` (méthodes POST/PUT/DELETE) |
| Signatures de charge | `PG_SLEEP`, `WAITFOR DELAY`, `DBMS_PIPE.RECEIVE_MESSAGE`, `<ScRiPt>`, `../../etc/passwd`, `${...}`, `{{...}}` |
| IP légitime (à ne pas confondre) | `41.188.114.124` (administrateur — navigation/SSH) |
| Preuves conservées | `~/incident-2026-05-17/` sur `a2st02` (`backend.log`, `nginx-stdout.log`) |

---

*Rapport établi le 17 mai 2026. Les détails sensibles d'exploitation
(secrets, procédures internes) sont volontairement omis et conservés dans le
runbook de déploiement à accès restreint.*
