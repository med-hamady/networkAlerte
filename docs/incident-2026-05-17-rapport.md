# Rapport d'incident de sécurité — Network Supervisor

**Référence :** INC-2026-05-17
**Date de l'incident :** 17 mai 2026
**Date du rapport :** 17 mai 2026
**Statut :** Clos — résolu, corrigé et durci
**Classification :** Interne — diffusion équipe & direction
**Système concerné :** Network Supervisor (supervision réseau UISP/Ubiquiti), serveur de production `a2st02` (`102.215.95.233`)

> Ce document décrit une intrusion réelle. Les secrets (clé d'authentification,
> mots de passe d'équipements) n'y figurent pas : ils sont gérés à part et
> ont été renouvelés là où c'était nécessaire.

---

## 1. Résumé exécutif

Le 17 mai 2026, le serveur de production du système de supervision réseau a
été la cible d'une **attaque automatisée depuis Internet**. Un robot d'attaque
(scanner de vulnérabilités) a exploité **deux faiblesses combinées** :

1. L'interface du superviseur était **exposée publiquement sur Internet**, au
   lieu d'être réservée à un accès interne.
2. Un composant interne ajoutait **automatiquement la clé d'authentification
   secrète** à toute requête venant du navigateur, sans vérifier qui en était
   réellement à l'origine.

Conséquence : n'importe quel visiteur d'Internet pouvait créer, modifier et
**supprimer** des équipements supervisés. L'attaquant a **détruit la totalité
de l'inventaire** (antennes Rocket, switch, onduleur, modems clients) et
**pollué la configuration** avec environ 130 entrées parasites.

**Impact métier :** perte temporaire totale de la visibilité réseau
(supervision aveugle pendant ~15 h), inventaire détruit.

**Point rassurant :** la base de données n'a **pas** été piratée
techniquement. Les tentatives d'injection ont toutes échoué. Aucune fuite de
mot de passe d'équipement, aucune prise de contrôle du serveur. Le dommage
est une **destruction de données via l'API légitime**, pas une compromission
technique du moteur de base de données.

**Résolution :** attaque coupée, accès Internet supprimé, faille corrigée,
inventaire intégralement restauré (avec auto-reconstruction du parc), clé
secrète renouvelée. Dans la foulée, un **audit de sécurité complet** a été
conduit, suivi de **durcissements supplémentaires** et de la mise en place
d'une **détection automatique d'anomalies**. Le délai de détection d'une
attaque équivalente passerait désormais de **~15 h à ~5 minutes**.

---

## 2. Chronologie (heures en UTC)

| Heure (17/05) | Événement |
|---|---|
| ~02:35 | Début des suppressions d'équipements par l'attaquant |
| 02:35 → 17:26 | Attaque continue : environ 120 000 requêtes, créations / modifications / suppressions en boucle, scan d'injections |
| ~17:05 | Détection : le tableau de bord n'affiche plus que des équipements clients, tout le reste a disparu |
| ~17:10 | Investigation : confirmation que les antennes, le switch et l'onduleur ne sont plus en base |
| ~17:20 | Identification de la nature de l'attaque (charges d'injection automatisée dans les noms d'équipements) |
| ~17:26 | **Confinement** : coupure immédiate de l'accès — l'attaque ne peut plus passer |
| ~17:30 | Préservation des preuves (journaux conservés) |
| ~17:40 | Identification de la source (sous-réseau `85.203.47.0/24`) |
| ~18:00 | Premier correctif appliqué, accès reconfiguré en mode interne uniquement |
| ~18:05 | Clé d'authentification renouvelée (ancienne révoquée) |
| ~18:07 | Restauration de l'inventaire principal |
| ~18:08 → 18:55 | Auto-reconstruction du parc clients (les radios abonnées se re-rattachent toutes seules dès que leur antenne parent est recréée) |
| ~18:50 | Découverte et purge de 129 entrées de configuration injectées |
| ~18:55 | Vérifications : parc cohérent, notifications opérationnelles |
| — | **Phase 1 close** — l'incident est neutralisé |
| ~19:00 → 19:15 | **Audit de sécurité complet** du système — trois faiblesses supplémentaires identifiées et corrigées dans la foulée |
| ~19:20 → 19:40 | Mise en place des **contrôles détectifs** (journal d'audit + détection automatique de volume anormal) |
| ~19:45 | Redéploiement final, vérifications complètes passées |
| — | **Phase 2 close** — système durci, détection automatique active |

---

## 3. Description de l'attaque

### 3.1 Origine

- **Source :** sous-réseau **`85.203.47.0/24`** — environ **25 adresses IP en
  rotation**. Cette rotation est une technique typique des botnets et services
  de proxy d'attaque pour échapper aux blocages simples.
- **Volume :** environ **120 000+ requêtes** sur la fenêtre d'attaque.
- **User-Agent :** falsifié pour ressembler à un navigateur Chrome légitime.

### 3.2 Outillage et nature

Attaque **entièrement automatisée** — outil de scan standard du marché.
Trois familles de tentatives observées :

- **Injection en aveugle dans la base de données** — visant à exécuter des
  commandes en se faisant passer pour une valeur normale (différents dialectes
  testés en parallèle).
- **Injection HTML / scripts hors-bande** — visant à exécuter du code dans
  le navigateur d'un administrateur.
- **Traversée de répertoire et inclusions externes** — visant à lire des
  fichiers sensibles du serveur.

L'attaquant **ne ciblait pas spécifiquement l'entreprise** : il s'agit d'un
balayage opportuniste d'Internet qui a trouvé un service exposé et a tenté
toutes ses charges automatiquement.

---

## 4. Faiblesses exploitées et cause racine

L'intrusion résulte de **la combinaison de deux faiblesses** présentes au
moment de l'incident :

### 4.1 Exposition publique sur Internet

L'interface du superviseur était accessible depuis **tout Internet**. La
conception d'origine prévoyait pourtant un accès **réservé au réseau
interne, via tunnel sécurisé**. Un changement antérieur l'avait rendu public,
ce qui était documenté comme un risque connu mais non corrigé.

### 4.2 Authentification automatique sans contrôle d'origine

Le tableau de bord n'a pas de page de connexion : il s'appuie sur un
composant interne qui ajoute **automatiquement la clé d'authentification
secrète** à chaque appel envoyé vers le cœur du système. Or ce composant :

- relayait **toutes les méthodes**, y compris les opérations de **création,
  modification et suppression** ;
- **ne vérifiait pas** si la demande venait réellement de l'interface du
  superviseur ou d'un outil tiers.

**Conséquence :** toute personne sur Internet pouvait appeler les opérations
d'écriture du superviseur, le composant ajoutait la clé secrète, et le
système exécutait la commande comme si elle venait d'un administrateur
légitime.

> **Cause racine :** un point d'entrée d'écriture exposé à Internet, sans
> authentification réelle, qui appose lui-même le secret d'autorisation.

---

## 5. Impact

### 5.1 Données détruites

Suppression de **l'intégralité de l'inventaire supervisé** :

- **5 antennes Rocket** : OUEST, EST, SUD, OUEST1, OMNI
- **1 UISP Switch**, **1 UISP Power (onduleur)**
- **L'ensemble des modems clients et radios abonnées** — seules **14 radios
  clients** ont survécu (orphelines, détachées de leur antenne parent)

Perte de la supervision réseau en temps réel pendant la durée de l'incident
(~15 h de fonctionnement « aveugle »).

### 5.2 Configuration polluée

**129 entrées parasites** ont été ajoutées dans la liste des canaux de
notification. Effet secondaire sérieux : saturation du système d'envoi
d'e-mails (chaque alerte tentait une livraison vers les 130 canaux, dont 129
inutiles).

### 5.3 Divulgation d'informations

Les requêtes de **lecture** n'étaient pas davantage protégées côté périmètre :
l'attaquant a pu consulter la liste des équipements, des incidents et des
métriques (environ 4 000 lectures réussies). Donnée non sensible (topologie
réseau interne, pas de secret), mais à considérer comme **exposée**.

> Corrigé en phase 2 : le contrôle d'origine s'applique désormais aussi aux
> lectures. Un accès direct sans page web légitime est désormais refusé même
> pour de la simple lecture.

### 5.4 Ce qui n'a PAS été compromis

- **Moteur de base de données :** aucune injection technique n'a abouti. Les
  protections applicatives (validation stricte des entrées, requêtes
  préparées, typage des identifiants) ont fait leur travail. L'outil
  d'attaque journalisait « supprimé » parce que les **opérations normales**
  fonctionnaient, **pas** parce que les injections réussissaient.
- **Serveur :** aucune exécution de code à distance, aucun accès
  administrateur, aucune élévation de privilèges constatés.
- **Mots de passe d'équipements :** non exposés (stockés en base, jamais
  renvoyés en clair par le système).
- **Clé d'authentification :** sa valeur n'a pas fuité (elle n'est jamais
  visible côté navigateur) — mais elle a été **exploitable** par anonyme
  pendant l'incident, donc renouvelée par précaution.

---

## 6. Détection

L'incident a été détecté **fonctionnellement** : le tableau de bord
n'affichait plus que des équipements clients, tous les autres ayant disparu.
L'investigation des journaux a révélé une longue série de suppressions, dont
plusieurs portant des noms contenant des charges d'injection — signature non
ambiguë d'une attaque automatisée.

> **Constat d'amélioration :** l'attaque a duré environ 15 heures sans alerte
> automatique. Il n'existait pas de surveillance sur les suppressions de
> masse ni sur les pics de requêtes anormales. **Corrigé en phase 2** : une
> détection automatique a été mise en place (voir §9). Le délai de détection
> d'une attaque équivalente est désormais de l'ordre de **5 minutes**.

---

## 7. Réponse et remédiation

Actions menées dans l'ordre :

1. **Confinement** — coupure immédiate de l'accès : l'attaque ne peut plus
   atteindre l'application.
2. **Préservation des preuves** — journaux applicatifs et journaux de
   périmètre conservés hors des environnements éphémères.
3. **Analyse forensique** — identification de la source (`85.203.47.0/24`),
   du vecteur, du volume et de la fenêtre temporelle.
4. **Suppression de l'exposition Internet** — l'accès au superviseur est
   désormais réservé au réseau interne via tunnel sécurisé. Le changement
   est **enregistré dans la configuration du projet** (il ne peut plus
   régresser par simple redéploiement).
5. **Correction de la faille applicative** — le composant d'authentification
   automatique refuse désormais les écritures qui ne proviennent pas de
   l'interface elle-même. Les tentatives bloquées sont journalisées et la
   clé n'est plus ajoutée à ces requêtes.
6. **Renouvellement de la clé d'authentification** — ancienne clé révoquée.
7. **Restauration de l'inventaire** — recréation des 5 antennes Rocket + du
   switch + de l'onduleur. Le mécanisme de découverte automatique a
   **reconstruit le parc clients tout seul** : passage de 14 à plus de 39
   équipements, **0 orphelin**.
8. **Nettoyage de la configuration** — purge des 129 canaux de notification
   injectés (conservation du seul canal légitime).
9. **Correction d'un défaut latent** — un envoi de notification trop lent
   pouvait, par effet de bord, retarder l'enregistrement de la topologie.
   Un délai maximal d'envoi a été ajouté, et tout canal défaillant est
   désormais mis en pause automatiquement.
10. **Vérifications finales** — parc cohérent, notifications opérationnelles.

---

## 8. Mesures de durcissement déployées

Deux phases successives de corrections ont été déployées en production.

**Phase 1 — réponse immédiate :**

- Blocage des **écritures** non légitimes au niveau du composant
  d'authentification automatique (contrôle d'origine non falsifiable).
- Accès au superviseur **réservé au réseau interne** (le service n'écoute
  plus sur Internet).
- **Délai maximal** sur les envois de notification + **mise en pause
  automatique** d'un canal qui se bloque.
- **Renforcement** de cette mise en pause pour couvrir aussi le cas d'un
  canal qui échoue rapidement de manière répétée (saturation côté
  fournisseur d'e-mail, par exemple).

**Phase 2 — durcissements issus de l'audit complet :**

- **Mot de passe d'équipement** sorti du code source et déplacé en
  configuration d'environnement (le code source ne contient plus aucun
  secret en clair).
- **Contrôle d'origine étendu aux lectures** (et plus seulement aux
  écritures) — fermeture définitive de la fuite d'information potentielle.
- **Limitation de débit** dédiée au composant d'authentification +
  **blocage explicite** du sous-réseau attaquant connu, au niveau du
  périmètre.
- **Journal d'audit** des opérations d'écriture (qui, quoi, quand).
- **Détection automatique** d'un volume anormal d'écritures, avec **alerte
  par e-mail** au-delà d'un seuil et **délai de réarmement** par source
  pour éviter le spam.

Toutes ces protections sont **enregistrées dans la configuration et le code
source du projet** : un futur déploiement les conserve ; la faille ne peut
pas être réintroduite par inadvertance.

---

## 9. Audit de sécurité post-incident

Dans la foulée de la résolution, un **audit complet** a été conduit sur
l'ensemble des composants sensibles du système (authentification, gestion
des entrées utilisateur, exécution de commandes système, manipulation des
secrets, communications sortantes vers les équipements, validation des
données, garde-fous de production).

**Verdict global :** le système est **globalement sain et défensif**. Les
bonnes pratiques de sécurité étaient déjà en place sur les points les plus
risqués (validation stricte des entrées, requêtes préparées, refus des
authentifications faibles, garde-fous au démarrage en production). La
catastrophe du 17/05 venait de **l'architecture de déploiement** (exposition
Internet + composant relais ouvert), pas d'un système foncièrement
vulnérable.

L'audit a néanmoins identifié et corrigé **trois faiblesses réelles** plus
**un sujet à surveiller** :

| Sévérité | Faiblesse identifiée | Statut |
|---|---|---|
| Haute | Mot de passe d'équipement codé en dur dans le code source | ✅ Sorti du code, désormais en configuration (cf. décision opérateur ci-dessous) |
| Haute | Contrôle d'origine appliqué seulement aux écritures, pas aux lectures | ✅ Étendu à toutes les méthodes |
| Moyenne | Pas de limitation de débit dédiée au composant d'authentification, pas de blocage du sous-réseau attaquant | ✅ Mis en place |
| Moyenne | Vérification du certificat TLS désactivée pour les communications vers les équipements Ubiquiti (certificats auto-signés du fabricant) | ⚠️ Documenté (calculé) — feuille de route : épinglage des certificats à terme |

> **Décision opérationnelle :** la **rotation du mot de passe sur la flotte
> des radios clients n'a pas été retenue** (dépôt projet privé + accès au
> serveur strictement interne — risque jugé acceptable). Le secret est
> désormais hors du code source vivant ; il subsiste dans les anciennes
> versions du dépôt, ce qui reste acceptable dans la politique de visibilité
> actuelle. À reconsidérer si cette politique change.

**Mesures détectives ajoutées dans le même mouvement :**

- **Journal d'audit** : chaque opération d'écriture sur le superviseur est
  désormais enregistrée (action effectuée, IP source, code de retour,
  identité du client). On peut répondre à la question « qui a fait quoi,
  et quand ? » même longtemps après les faits, sans dépendre des journaux
  système.
- **Détection automatique** : un dispositif surveille en continu ce journal
  et déclenche une **alerte par e-mail** dès qu'une même source dépasse un
  seuil d'écritures sur une fenêtre courte (par défaut : plus de 50
  écritures en 5 minutes). Un délai de réarmement par source évite le spam
  d'alertes en cas d'attaque soutenue.

**Effet attendu sur un scénario équivalent :** détection en quelques minutes
au lieu de quinze heures, avec une alerte explicite identifiant la source.

---

## 10. Récapitulatif des mesures appliquées

- Accès au superviseur **réservé au réseau interne**, via tunnel sécurisé.
- Authentification des **écritures et des lectures** côté périmètre.
- **Détection automatique** des volumes anormaux d'écritures + **journal
  d'audit** forensique.
- **Limitation de débit** + **blocage** du sous-réseau attaquant connu.
- **Sortie des secrets** hors du code source.
- **Bornage** des envois de notifications (plus de blocage en cas de
  service e-mail défaillant).

---

## 11. Annexe — Indicateurs de compromission (IoC)

| Type | Valeur |
|---|---|
| Sous-réseau source | `85.203.47.0/24` (IP en rotation) |
| User-Agent (falsifié) | `Mozilla/5.0 (Windows NT 10.0; Win64; x64) … Chrome/126.0.0.0 Safari/537.36` |
| Domaines hors-bande (sondes / exfiltration) | `bxss.me`, `dicrpdbjmemujemfyopp.zzz` |
| Cible exploitée | Composant d'authentification automatique du tableau de bord (méthodes d'écriture) |
| Signatures de charge typiques | Charges d'injection SQL en aveugle, scripts hors-bande, traversées de répertoire |
| IP légitime (à ne pas confondre) | `41.188.114.124` (administrateur — navigation / accès interne) |
| Preuves conservées | Journaux applicatifs et de périmètre, conservés sur le serveur |

---

*Rapport établi le 17 mai 2026, mis à jour le même jour après l'audit de
sécurité post-incident et le déploiement des durcissements complémentaires.
Les détails sensibles d'exploitation (secrets, références techniques
internes, configuration précise) sont volontairement omis et conservés dans
le runbook de déploiement à accès restreint.*
