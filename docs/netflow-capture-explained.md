# Mesure du trafic Internet par opérateur/CDN — note technique

**Objet :** expliquer comment le superviseur mesure le **volume**, le **débit** et le **partage
de la bande passante** par opérateur/CDN, et pourquoi les **plages d'adresses internes** sont
déterminantes.
**Destinataires :** équipe réseau
**Date :** 2026-07-01

---

## 1. Objectif

Savoir **vers quels opérateurs / CDN nos clients échangent le plus de trafic** (Google/YouTube,
Facebook, Netflix, Akamai…), en **volume** (Go) et en **débit** (Gb/s), afin de décider quels
**serveurs de cache** demander (Google GGC, Facebook FNA, Netflix OCA) pour réduire la latence et
la charge sur le transit.

La table ARP du switch ne peut pas répondre : elle est de niveau 2 (IP↔MAC local) et ne voit
jamais les IP publiques de destination. L'information vit au niveau **flux (L3/L4)** → on collecte
du **NetFlow**.

---

## 2. Vue d'ensemble

```
Clients ─┐
         ├─► MikroTik cœur (10.135.0.1) ──[export NetFlow v9, UDP]──► Serveur superviseur (10.135.3.25:2055)
Internet ┘                                                                   │
                                                          ┌──────────────────┼───────────────────┐
                                                          │  Conteneur "netflow-collector"        │
                                                          │  1. décode les flux (v5/v9/IPFIX)     │
                                                          │  2. classe chaque flux (sens + opérateur) │
                                                          │  3. résout l'IP publique → ASN (MaxMind)  │
                                                          │  4. agrège par (opérateur, minute)    │
                                                          └──────────────────┬───────────────────┘
                                                                             ▼
                                                              Base de données (traffic_dest_stats)
                                                                             ▼
                                                                Page web  /traffic  (Débit + Volume)
```

---

## 3. Source des données — NetFlow sur le MikroTik

Le routeur cœur MikroTik (`10.135.0.1`, RouterOS v6) **exporte ses flux** en NetFlow v9 vers le
serveur de supervision :

```
/ip traffic-flow set enabled=yes
/ip traffic-flow target add address=10.135.3.25:2055 version=9
```

Chaque **flux** exporté contient notamment : IP source, IP destination, nombre d'octets, sens.
C'est une observation passive : NetFlow ne modifie **ni le routage ni le trafic**, la charge est
négligeable.

**Sécurité :** le port UDP 2055 n'est ouvert que sur l'IP LAN du serveur et **restreint à la
source `10.135.0.1`** (NetFlow n'est pas authentifié).

---

## 4. Le collecteur et l'attribution par opérateur (ASN)

Un service dédié écoute en UDP, décode chaque flux, puis résout l'**IP publique** du flux en
**numéro d'opérateur (ASN)** et nom, via la base offline **MaxMind GeoLite2-ASN**. Exemples :

| Plage IP publique | ASN | Opérateur |
|---|---|---|
| 142.250.0.0/15, 172.217.0.0/16… | AS15169 | Google / YouTube |
| 157.240.0.0/16, 185.60.216.0/22… | AS32934 | Facebook / Instagram |
| 23.246.0.0/18… | AS2906 / AS40027 | Netflix (OCA) |
| 23.0.0.0/8 (partiel)… | AS20940 | Akamai |
| 1.1.1.0/24… | AS13335 | Cloudflare |

Les flux sont **agrégés en mémoire par (opérateur, fenêtre d'1 minute)** puis écrits en base.

---

## 5. Le point clé — sens du trafic (download vs upload) et plages d'adresses

Un même échange client↔Internet génère **deux flux** :

- **Descendant / download** : `opérateur → client` (ce que le client télécharge — YouTube, FB…).
  C'est le **RX du port WAN**, et **l'essentiel** de la bande passante.
- **Montant / upload** : `client → opérateur` (requêtes, uploads). C'est le **TX du port WAN**,
  bien plus petit.

Pour chaque flux, le collecteur identifie l'**extrémité publique** (l'opérateur) et l'**extrémité
interne** (nous). La règle :

- l'extrémité **interne** est reconnue via une **liste de plages « internes »** (`NETFLOW_INTERNAL_PREFIXES`) ;
- l'extrémité **publique** (l'autre) est l'opérateur auquel on attribue le flux ;
- le **sens** se déduit de quelle extrémité est interne (interne en destination = download ;
  interne en source = upload).

### Pourquoi les plages d'adresses sont critiques

Un flux n'est correctement classé que si **notre extrémité est reconnue comme interne**. Sinon
le collecteur voit « deux IP publiques » et **ignore le flux**.

Lors de la mise en service, le **download ressortait à 0** : tous les flux descendants étaient
ignorés. Un diagnostic (comptage des flux par catégorie + échantillon des flux rejetés) a révélé
la cause :

```
flux rejetés (source → destination) :
  157.240.202.1 → 102.215.95.41     (Facebook → un de NOS clients)
  8.8.8.8       → 102.215.95.10     (Google   → un de NOS clients)
  172.217.22.46 → 102.215.95.0      (Google   → un de NOS clients)
```

**Nos clients ont des IP publiques dans `102.215.95.0/24`** (et non des IP privées). Comme cette
plage n'était pas déclarée « interne », les flux `opérateur → 102.215.95.x` étaient vus
opérateur↔opérateur et jetés.

### La correction

On a déclaré **tout notre bloc public `102.215.95.0/24`** comme interne (en plus des plages
privées RFC1918/CGNAT) :

```
NETFLOW_INTERNAL_PREFIXES = 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 102.215.95.0/24
```

Effet immédiat, mesuré dans les compteurs du collecteur :

| Avant | Après |
|---|---|
| download ≈ **2 flux/min** | download ≈ **65 000 flux/min** |
| flux jetés « deux IP publiques » ≈ **81 500/min** | ≈ **10/min** (uniquement du transit) |

Les ~10 flux restants sont du **transit d'uplink** (plage `193.251.250.x`, Orange) entre nos
équipements et l'opérateur — normal, correctement ignoré.

> ⚠️ **À retenir pour l'exploitation** : si le bloc public de nos clients s'étend au-delà de
> `102.215.95.0/24` (autre /24, /23, /22…), il faut **ajouter la plage** à cette liste, sinon
> ces clients-là ne seront pas comptés en download.

---

## 6. Volume vs Débit — deux lectures

À partir des octets agrégés par (opérateur, minute), la page `/traffic` propose deux vues :

- **Volume** (Go) : somme des octets sur une période (24h / 7j / 30j), par opérateur, en
  download / upload / total. → « qui a le plus consommé ».
- **Débit** (Gb/s) : octets ÷ durée du dernier bucket, par opérateur, descendant / montant. →
  « comment la bande passante WAN se partage **en direct** » (ex. la répartition de nos ~3 Gb/s).

---

## 7. Résultat

Le superviseur affiche désormais, par opérateur/CDN et **par sens** :
- le **débit en direct** (Gb/s) et le partage de la bande passante WAN ;
- le **volume** cumulé (Go) sur 24h/7j/30j.

C'est la base chiffrée pour argumenter une **demande de cache** auprès des gros contributeurs
(Google GGC, Facebook FNA, Netflix OCA, Akamai…).

---

## 8. Récapitulatif des paramètres réseau

| Élément | Valeur |
|---|---|
| Exporteur NetFlow | MikroTik cœur `10.135.0.1`, NetFlow v9 |
| Collecteur | Serveur superviseur `10.135.3.25`, UDP `2055` |
| Filtrage source | UDP 2055 autorisé **uniquement depuis `10.135.0.1`** |
| Plages internes (clients + gateways) | `102.215.95.0/24` + RFC1918/CGNAT |
| Transit uplink (ignoré) | `193.251.250.x` (Orange) |
| Base ASN | MaxMind GeoLite2-ASN (hors ligne) |
| Fenêtre d'agrégation | 1 minute (débit « live ») |
