# Network Supervisor — Synthèse

**Ce qu'est le système, et la liste de ce qu'il fait.**
Document de vue générale — pour le détail technique et la correspondance avec les fichiers
de code, voir `docs/PASSATION.md`.

*État au 8 septembre 2026.*

---

## 1. À quoi sert le système

A2 Connect exploite un réseau d'accès Internet sans fil (WISP) en Mauritanie. Avant ce
système, l'entreprise n'avait **aucune visibilité en temps réel** sur son réseau : les
pannes étaient découvertes par les appels des clients.

Le Network Supervisor est l'outil qui surveille ce réseau en continu, détecte les pannes et
les dégradations avant les clients, alerte l'équipe sur WhatsApp, et sert d'outil de
coupure et de rétablissement au système de facturation.

**Il remplit trois rôles :**

1. **Superviser** — savoir en permanence ce qui fonctionne et ce qui se dégrade.
2. **Alerter** — prévenir l'équipe, sur WhatsApp, uniquement de ce qui mérite une action.
3. **Agir** — couper, rétablir et filtrer l'accès des abonnés, à la demande du système de
   facturation ou d'un opérateur.

---

## 2. Ce qu'il surveille

| Élément | Volume |
|---|---|
| Abonnés (radios clientes) | ~1 000 |
| Stations de base (Rockets LTU et airMAX) | ~100 |
| Sites (pylônes) | 17 en service |
| Backhauls entre sites (AF60, liaisons P2P, fibre) | ~19 liaisons |
| Switches et onduleurs | une quinzaine de chacun |

**5 familles d'équipements** sont reconnues : stations de base, radios abonnées, switches,
onduleurs et backhauls.

---

## 3. Ce que le système fait

### Supervision du réseau

Le système :

- **surveille en permanence la disponibilité** de chaque équipement, par ping, et distingue
  une vraie panne d'une simple perte de paquet ;
- **relève les métriques radio** de chaque liaison (signal, qualité, débit, capacité) toutes
  les minutes, sur cinq technologies différentes ;
- **relève l'état des ports** de chaque switch, et détecte lequel est tombé ou négocie en
  dessous de sa vitesse normale ;
- **surveille les onduleurs** : tension, batterie interne, batterie externe, et coupure du
  courant secteur ;
- **mesure la latence de chaque abonné vers Internet** et détecte quand un client n'a plus
  de transit ;
- **calcule la saturation des backhauls** entre sites, c'est-à-dire s'ils sont pleins, en
  plus de savoir s'ils sont cassés ;
- **conserve l'historique** des mesures et l'affiche en courbes sur 24 h, 7 jours, 30 jours
  ou une période choisie.

### Détection et alerte

Le système :

- **détecte 41 types d'anomalies** différentes, du simple équipement injoignable à la
  liaison radio dégradée ;
- **ouvre et ferme les incidents automatiquement**, sans intervention humaine, avec un
  mécanisme qui évite d'alerter sur une anomalie passagère ;
- **envoie les alertes sur WhatsApp**, mais uniquement les **13 anomalies jugées
  actionnables** — le reste est enregistré sans réveiller personne, pour éviter la fatigue
  d'alerte ;
- **regroupe les avertissements** en un message unique toutes les 15 minutes ;
- **maintient un bandeau d'anomalies à acquitter à la main** pour trois dégradations
  silencieuses qui, sans cela, passeraient inaperçues ;
- **détecte les équipements instables** qui tombent et reviennent à répétition ;
- **surveille les appels à sa propre API** et signale une activité anormale.

### Vision du réseau

Le système :

- **cartographie le réseau entre les sites** : quel site est relié à quel autre, par quelle
  technologie, et dans quel état ;
- **calcule toutes les routes possibles d'un site vers Internet**, désigne la meilleure et
  nomme le maillon qui limite chacune ;
- **affiche la même topologie en carte géographique**, pour voir les distances et les
  directions réelles ;
- **produit une carte imprimable du réseau** en document Word, avec les sites en service et
  les extensions programmées ;
- **positionne les abonnés sur une carte** et signale à part ceux dont les coordonnées sont
  manifestement fausses, pour correction terrain ;
- **affiche la topologie interne de chaque site**.

### Capacité et planification

Le système :

- **compare, pour chaque station de base, le nombre de clients installés à sa capacité
  maximale**, calculée automatiquement selon sa configuration radio ;
- **compte les équipements de chaque site** et indique combien de places il reste ;
- **envoie chaque matin deux rapports PDF sur WhatsApp** : les stations saturées, et la
  capacité restante par site ;
- **produit un contrôle quotidien de la latence** de l'ensemble du réseau.

### Suivi des clients

Le système :

- **mesure la consommation de chaque abonné** (données descendantes et montantes) sur
  24 h, 7 jours, 30 jours ou une période choisie ;
- **lit le forfait souscrit directement sur l'équipement du client**, information qui
  n'existe dans aucune API ;
- **évalue la qualité de la liaison de chaque abonné** et identifie les installations
  défectueuses ;
- **répond à un système tiers** qui demande, pour un client donné, la qualité de son signal
  et sa latence mesurée en direct ;
- **découvre automatiquement les nouveaux abonnés** dès qu'ils se connectent à une station
  de base — aucune saisie manuelle n'est nécessaire ;
- **suit un abonné qui déménage** ou qui bascule sur une autre station.

### Gestion de l'accès (FAI)

Le système :

- **coupe l'accès Internet d'un abonné**, à la demande du système de facturation, selon deux
  modes : coupure totale, ou accès limité à WhatsApp seul ;
- **rétablit l'accès** de la même façon ;
- **ré-applique la coupure automatiquement** toutes les deux minutes, pour qu'un abonné ne
  puisse pas la contourner en redémarrant son équipement ;
- **vérifie l'identité de l'équipement avant chaque coupure**, pour ne jamais couper le
  mauvais abonné ;
- **coupe depuis le routeur central** quand l'équipement du client est éteint ou inaccessible ;
- **montre ce que le routeur central coupe réellement**, et signale les écarts avec ce qui
  était prévu — c'est la seule façon de répondre à « ce client a payé, pourquoi est-il
  coupé ? » ;
- **filtre l'accès à certaines plateformes** (TikTok, Snapchat, YouTube, contenu adulte…)
  chez un abonné, à la demande d'un système tiers ou d'un opérateur ;
- **tient un journal d'audit** de toutes les coupures : qui, quand, sur ordre de qui, et
  avec quel résultat.

### Trafic Internet

Le système :

- **analyse le trafic sortant du réseau** et l'attribue à chaque opérateur ou service
  (Google, Facebook, Netflix…) ;
- **affiche le débit en direct** et sa répartition entre opérateurs ;
- **affiche le volume consommé** par destination sur 24 h, 7 jours ou 30 jours — ce qui
  permet d'identifier les candidats à un serveur de cache local.

### Inventaire et intégration

Le système :

- **importe l'inventaire depuis le contrôleur UISP** chaque jour, équipements
  d'infrastructure et abonnés ;
- **enrôle un équipement dans le contrôleur** à distance, sans coupure ni redémarrage — ce
  qui rend facturable un abonné jusque-là invisible ;
- **associe un équipement à un client** dans le système de facturation ;
- **détecte les abonnés actifs absents du contrôleur**, donc potentiellement non facturés ;
- **détecte les équipements qui refusent l'accès distant** et ne sont donc plus pilotables ;
- **nettoie automatiquement les adresses réseau périmées**, pour ne jamais agir sur le
  mauvais équipement.

### Exploitation

Le système :

- **fournit un tableau de bord web** de 18 pages, protégé par identifiant et mot de passe ;
- **permet de régler les seuils d'alerte depuis l'interface**, sans redéploiement ;
- **journalise toutes les pannes** et calcule un taux de disponibilité par équipement et par
  site sur la période choisie ;
- **expose une API de 70 routes**, dont quatre ouvertes à des systèmes tiers avec une clé
  d'accès **distincte et limitée** à leur seul usage.

---

## 4. Comment il fonctionne, en une page

**Il interroge les équipements lui-même.** Il n'y a pas d'agent installé sur le matériel :
le système va chercher l'information par les protocoles que les équipements exposent déjà
(SNMP, API HTTP, SSH).

**26 tâches automatiques** tournent en permanence, à des rythmes différents selon ce
qu'elles mesurent : de 30 secondes pour la disponibilité, à une fois par jour pour
l'inventaire et les rapports.

**Elles sont réparties sur 7 processus séparés**, chacun dans son conteneur. Ce n'est pas
un détail d'implémentation : quand tout tournait ensemble, les tâches lentes empêchaient
les tâches rapides de s'exécuter, et la supervision devenait fausse sans que personne ne
s'en aperçoive.

**L'ensemble tourne sur un serveur unique** (12 conteneurs Docker), sur le réseau interne
de l'entreprise, derrière le pare-feu.

---

## 5. Ce que le système ne fait pas

Utile à savoir pour ne pas lui prêter des capacités qu'il n'a pas :

- il **ne remplace pas le contrôleur UISP** : il le lit, et n'y écrit qu'à un seul endroit
  précis ;
- il **ne facture pas** — il fournit la mesure et exécute les coupures, la facturation est
  un système tiers ;
- il **ne lit pas la table de routage** : quand il montre des chemins vers Internet, il
  montre ce que le **câblage permet**, pas ce que le trafic emprunte réellement ;
- il **n'ouvre pas d'incident pour une panne côté client** : un abonné dont l'équipement est
  éteint n'est pas une panne du réseau ;
- il **ne notifie pas tout ce qu'il détecte** — c'est un choix, pour que les messages
  WhatsApp restent lus.

---

## 6. Où trouver le reste

| Document | Contenu |
|---|---|
| `docs/PASSATION.md` | **Le document de reprise.** Chaque fonctionnalité avec les fichiers de code qui la portent, un index inversé fichier → fonctionnalité, un guide de dépannage et un glossaire. |
| `docs/API_BLOCAGE_CLIENT.md` et les autres `api-*.md` | Documents d'intégration à remettre aux partenaires externes. |
