# Sauvegarde de la base

Runbook de la sauvegarde quotidienne de PostgreSQL et de son envoi vers le
serveur Windows de sauvegarde (`10.135.0.210`).

| Pièce | Où | Rôle |
|---|---|---|
| `scripts/backup-db.sh` | prod | Produit une archive chiffrée par nuit dans `/opt/a2project/backups/` |
| `scripts/push-backup.sh` | prod | Envoie au serveur Windows les archives pas encore confirmées |
| `scripts/receive-backup.ps1` | serveur Windows | Vérifie l'empreinte et range l'archive dans `C:\Backups\supervisor` |
| `docs/backup-database.md` | — | ce fichier |

---

## 1. Le montage

```
  prod Ubuntu 10.135.3.25                    serveur Windows 10.135.0.210
  ───────────────────────                    ───────────────────────────
  backup-db.sh       (cron 05:00 UTC)
    pg_dump ──► archive chiffrée
                /opt/a2project/backups/
                          │
  push-backup.sh     (cron toutes les heures)
                          │  scp
                          └──────────────►  C:\Backups\_transit      (transit)
                             ssh                   │
                                                   │ receive-backup.ps1
                                                   │   vérifie le SHA-256
                                                   ▼
                                            C:\Backups\supervisor
```

### ⚠️ Mode en place : UNE seule copie, datée, qui remplace la précédente (2026-09-22)

`BACKUP_SINGLE_COPY=true` : chaque nuit crée `supervisor-<date>.tar` puis
**supprime la précédente**, sur la prod (`backup-db.sh`) comme sur le serveur
Windows (`receive-backup.ps1 -KeepOnlyLatest`).

- La suppression n'a lieu qu'**après** que la nouvelle est complète (prod) ou
  vérifiée et publiée (Windows) : un échec ne laisse jamais sans sauvegarde.
- Côté Windows, seules les archives **plus anciennes** sont supprimées (ordre du
  nom = ordre chronologique) : un envoi en retard ne peut pas effacer une plus
  récente.
- La nuit précédente reste dans la **corbeille de Sync.com** (« Deleted files »),
  pour la durée prévue par l'abonnement : c'est le **seul** retour en arrière.
  Un problème découvert après la sauvegarde suivante (inventaire vidé dans la
  nuit, comme le 2026-05-17) n'a plus de copie saine ailleurs.

Pour revenir à une archive par nuit gardée N jours : `BACKUP_SINGLE_COPY=false`.

### C'est le DOSSIER qui est envoyé, pas seulement la dernière archive

`push-backup.sh` envoie **toute archive locale que le serveur Windows n'a pas
encore confirmée**, de la plus ancienne à la plus récente. La confirmation est
un marqueur `<archive>.pushed`, posé **seulement** quand le script Windows a
vérifié l'empreinte et rangé le fichier — un `scp` réussi ne suffit pas.

Conséquences :

- une nuit où le serveur Windows était éteint **n'est pas perdue** : l'archive
  part au passage suivant ;
- un passage sans rien en attente **n'ouvre aucune connexion** — d'où la
  planification toutes les heures, qui rattrape tout seul un serveur Windows
  revenu en ligne ;
- ⚠️ le rattrapage est borné par la rétention locale (14 j) : au-delà, les
  archives les plus anciennes sont supprimées sur la prod avant d'avoir pu
  partir.

Pour renvoyer une archive déjà confirmée (supprimée par erreur côté Windows) :
`./scripts/push-backup.sh /opt/a2project/backups/supervisor-<date>.tar.enc`.

### Deux règles à ne pas casser

**1. L'archive atterrit dans un dossier de TRANSIT**, jamais directement dans
`C:\Backups\supervisor`. Le fichier n'y apparaît qu'une fois **complet et
vérifié**, par un simple déplacement. C'est indispensable si ce dossier est un
jour synchronisé vers un cloud (client Sync.com par exemple) : son client
téléverserait sinon un fichier à moitié écrit.

> ⚠️ Transit et sauvegarde doivent être **sur le même volume**. Sur un même
> volume un déplacement est un renommage, instantané et indivisible ; d'un
> volume à l'autre c'est une copie, pendant laquelle le fichier est visible
> incomplet.

**2. Le serveur Windows ne déchiffre jamais rien.** Il range un bloc opaque. La
phrase secrète ne doit **pas** s'y trouver.

### Et Sync.com ?

Sync.com n'a **aucun client Linux, aucune API, pas de WebDAV, pas de backend
rclone** : la prod ne peut pas lui parler directement. Si le compte Sync.com de
l'entreprise doit recevoir une copie, il suffit d'installer le client Sync.com
sur le serveur Windows et de faire pointer `BACKUP_REMOTE_DIR` vers un dossier
qu'il synchronise. Rien d'autre ne change.

**En place depuis le 2026-09-22** :
`BACKUP_REMOTE_DIR=C:/Users/Administrator/Sync/Systeme_Developpement/Backup_DB`,
avec `icacls <dossier> /grant "backup:(OI)(CI)M"` (le compte `backup` n'a sinon
aucun droit dans le profil d'Administrator). Le transit reste `C:\Backups\_transit`,
**hors** du dossier synchronisé et sur le même volume.

> ⚠️ **Le client Sync.com vit dans la SESSION d'Administrator.** Une session
> fermée (« Se déconnecter ») l'arrête : les archives continuent d'arriver dans
> `Backup_DB` mais **ne montent plus dans le cloud**, sans aucune alerte. Fermer
> le bureau à distance par la croix, jamais par « Se déconnecter ».
>
> ⚠️ **Archives en clair + dossier cloud** : tout accès au compte Sync.com ou au
> dossier partagé `Systeme_Developpement` donne les mots de passe SSH du parc.
> Le chiffrement de Sync.com protège contre Sync.com, pas contre ceux qui ont
> accès au compte. Réactiver `BACKUP_PASSPHRASE` est le seul correctif (§3).
>
> ⚠️ La rétention (`BACKUP_REMOTE_KEEP_DAYS`) supprime aussi **dans le cloud**.

---

## 2. Ce qui est sauvegardé, et ce qui ne l'est pas

L'archive `supervisor-<date>.tar` contient :

| Fichier | Contenu |
|---|---|
| `network_supervisor.dump` | `pg_dump` format custom, restaurable par `pg_restore` |
| `fai_actions.log` | **le journal FAI** — une ligne par coupure / déblocage d'abonné |
| `fai_evidence/` | les **preuves** : la transcription SSH de chaque action |
| `MANIFEST.txt` | date, hôte, commit git, exclusions, taille du journal |

> ⚠️ **Le journal FAI n'est pas dans la base.** C'est un fichier texte
> (`backend/logs/`), hors de portée de `pg_dump` — il manquait donc
> **entièrement** à la sauvegarde jusqu'au 2026-09-25. C'est pourtant la seule
> trace de qui a coupé quel abonné, quand et sur ordre de qui, et elle ne se
> reconstitue depuis rien.
>
> Il est copié **depuis l'hôte** et non via `docker compose exec backend` : la
> sauvegarde doit continuer de fonctionner quand le backend est en panne, ce
> qui est précisément le moment où l'on veut une archive.

> Il y en avait trois jusqu'au 2026-09-25 : un `client_consumption_30d.csv`
> compensait le fait que la consommation n'existait que sous forme de deltas à
> recalculer sur `device_metrics`, dont les données sont exclues. Elle vit
> désormais dans la table `client_consumption_daily`, donc dans le dump.

### Conservé intégralement

`devices` et ses sous-classes (`rockets`, `lrs`, `uisp_switches`, `uisp_powers`,
`airfibers`, `ptp_litebeams`, `client_modems`), `incidents`, `manual_alerts`,
`alert_states`, `site_links`, `site_locations`, `system_settings` (les seuils),
`users`, `audit_log`.

C'est-à-dire **tout ce qui ne se reconstruit pas tout seul** : l'inventaire, les
credentials des équipements, les seuils réglés à la main, les overrides de
capacité, le câblage inter-sites, les positions des pylônes, les comptes.

### Exclu — schéma gardé, lignes non (`--exclude-table-data`)

| Table | Pourquoi |
|---|---|
| `device_metrics` | ~20 M lignes / ~6,8 Go — re-remplie par les polls |
| `power_status_logs` | relevés UISP Power — écrits, mais lus par **aucun** service |
| `auth_sessions` | cookies de session — les restaurer serait une faute |

À la restauration ces tables reviennent **vides mais existantes**, et se
re-remplissent au premier tour de poll.

### Historiques INCLUS depuis le 2026-09-22

| Table | Contenu | Rétention en base |
|---|---|---|
| `lr_metric_samples` | courbes des fiches équipement (« Plus d'infos ») | 30 j |
| `traffic_dest_stats` | trafic Internet par opérateur (page `/traffic`) | 90 j |

Ce sont les deux historiques qui **ne se reconstituent pas** : sans eux, une
restauration rendait graphes et page `/traffic` vides. Contrepartie : l'archive
passe de ~1 Mo à plusieurs centaines de Mo, voire quelques Go, et le `pg_dump`
de 05:00 dure plus longtemps. Le dossier de travail est donc dans
`$BACKUP_DIR/.work.*` (700) et non dans `/tmp`.

### ⚠️ La limite à connaître : le détail infra-journalier

La consommation client **est sauvegardée** : depuis le 2026-09-25 elle vit dans
`client_consumption_daily` (un total par client, par compteur et par journée),
une vraie table, donc dumpée avec ses lignes comme le reste.

Ce qui **reste hors sauvegarde**, c'est le détail **à la minute** — « combien
entre 14 h et 15 h le 3 mars » — puisque `device_metrics` est dumpée sans ses
données. Jamais le total d'une journée ni d'une période. Ce détail est de toute
façon purgé sur la prod au-delà de `DEVICE_METRICS_RETENTION_DAYS` (7 jours).

> ⚠️ **L'ordre des jobs de nuit compte.** Le résumé de la veille est écrit à
> 02:00 UTC (`CLIENT_CONSUMPTION_DAILY_ROLLUP_HOUR`) — **d'où la sauvegarde à
> 05:00 UTC**, jamais avant : une archive prise à 01:00 n'aurait pas la
> journée de la veille.

---

## 3. Le chiffrement

> ⚠️ **Décision d'exploitation du 2026-09-22 : les sauvegardes partent EN CLAIR.**
> `.env` de la prod : `BACKUP_PASSPHRASE` vide + `BACKUP_ALLOW_PLAINTEXT=true`.
> Les archives sont alors des `supervisor-<date>.tar` (sans `.enc`), envoyées
> par `push-backup.sh` seulement grâce à ce réglage explicite.
>
> **Conséquence acceptée** : chaque archive porte les mots de passe SSH de tout
> le parc radio en clair. Sur le serveur Windows, **seule l'ACL de `C:\Backups`
> les protège** — le dossier doit être fermé à tout compte autre que `backup`,
> `Administrators` et `SYSTEM` (§5b). Un fichier copié ailleurs (clé USB, cloud,
> pièce jointe) emporte tous les accès du réseau.
>
> Pour revenir au chiffrement : renseigner `BACKUP_PASSPHRASE` (et la ranger
> dans le coffre de l'entreprise) — rien d'autre à changer, les deux formats
> coexistent dans le même dossier.

Ce qui suit décrit le mode chiffré. Le dump contient les colonnes `ssh_password`, `api_password` et
`management_password` de `devices` et de ses sous-classes : **les mots de passe
SSH de tout le parc radio, en clair**.

`backup-db.sh` **refuse de produire une archive** sans `BACKUP_PASSPHRASE`
(AES-256-CBC, PBKDF2 600 000 itérations, sel aléatoire). L'archive est chiffrée
**sur la prod, avant tout transfert**. `push-backup.sh` refuse en plus d'envoyer
une archive non chiffrée.

> ⚠️ **Où ranger la phrase secrète.** Ni sur la prod seule (une panne disque
> emporterait la donnée *et* sa clé), ni sur le serveur Windows (la donnée et sa
> clé seraient au même endroit). Sa place est le gestionnaire de mots de passe
> de l'entreprise, ou un coffre. **Sans elle, aucune restauration n'est
> possible.**

---

## 4. Installation — côté prod

```bash
ssh a2@10.135.3.25
cd /opt/a2project
git pull
```

**a. Phrase secrète**

```bash
openssl rand -base64 48          # copier le résultat dans le coffre d'entreprise
nano .env                        # BACKUP_PASSPHRASE=<la valeur>
                                 # + le bloc BACKUP_REMOTE_* (cf. .env.example)
chmod 600 .env
```

**b. Clé SSH vers le serveur Windows** (sans passphrase : cron ne peut répondre
à aucune invite)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_backup_windows -N ''
cat ~/.ssh/id_backup_windows.pub          # à poser côté Windows (§5c)
```

**c. Planifier**

⚠️ Le cron tourne sous `a2`, qui ne peut pas écrire dans `/var/log` : sans ce
fichier préparé, la redirection échoue et **la commande ne s'exécute même pas**,
sans laisser de trace.

```bash
sudo touch /var/log/supervisor-backup.log
sudo chown a2:a2 /var/log/supervisor-backup.log
crontab -e
```

```cron
# cron démarre avec un PATH minimal où `docker` est absent.
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# 05:00 UTC — après le rafraîchissement des vues de consommation (03:00, 04:00).
0 5 * * * /bin/bash /opt/a2project/scripts/backup-db.sh    >> /var/log/supervisor-backup.log 2>&1

# Toutes les heures à :20 — envoie ce qui est en attente, et rien sinon (aucune
# connexion ouverte). Séparé de la sauvegarde : un serveur Windows injoignable
# ne doit pas faire échouer ce qui est déjà fait.
20 * * * * /bin/bash /opt/a2project/scripts/push-backup.sh >> /var/log/supervisor-backup.log 2>&1
```

---

## 5. Installation — côté serveur Windows (`10.135.0.210`)

Aucune tâche planifiée ici : le serveur Windows **subit** l'envoi.

**a. Serveur OpenSSH** (PowerShell administrateur)

Windows Server **2019 et plus** — composant intégré :

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
```

Windows Server **2016** (cas de `10.135.0.210`) — le composant n'existe pas, on
installe le paquet officiel Microsoft `OpenSSH-Win64-v<version>.msi`, téléchargé
depuis https://github.com/PowerShell/Win32-OpenSSH/releases (sur un poste
connecté si le serveur n'a pas Internet, puis copié) :

```powershell
msiexec /i C:\Temp\OpenSSH-Win64-v<version>.msi ADDLOCAL=Server /qn
```

Puis, dans les deux cas :

```powershell
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd

# N'accepter le SSH que depuis la prod (la règle peut ne pas exister selon
# la méthode d'installation : on la crée alors)
if (Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -RemoteAddress 10.135.3.25
} else {
    New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName "OpenSSH (prod uniquement)" `
      -Direction Inbound -Protocol TCP -LocalPort 22 -RemoteAddress 10.135.3.25 -Action Allow
}
```

**b. Compte dédié et dossiers**

Un compte **non administrateur**, qui n'a de droits d'écriture que sur
`C:\Backups` : la clé posée sur la prod ne doit pas ouvrir toute la machine.

```powershell
$pw = Read-Host -AsSecureString "Mot de passe du compte backup"
New-LocalUser -Name backup -Password $pw -PasswordNeverExpires

New-Item -ItemType Directory -Force C:\Backups\_transit, C:\Backups\supervisor

# Droits EXPLICITES uniquement : sous C:\, un dossier hérite par défaut de
# droits pour « Utilisateurs » et « Utilisateurs authentifiés », c.-à-d. que
# tout compte de la machine lirait les dumps (en clair, cf. §3).
icacls C:\Backups /inheritance:r `
  /grant "Administrators:(OI)(CI)F" /grant "SYSTEM:(OI)(CI)F" /grant "backup:(OI)(CI)M"
icacls C:\Backups

# y copier scripts/receive-backup.ps1 depuis le dépôt
Copy-Item .\receive-backup.ps1 C:\Backups\receive-backup.ps1
```

> ⚠️ `C:\Backups\supervisor` contient des archives chiffrées, mais retirer
> l'accès en lecture aux autres utilisateurs de la machine reste une bonne
> habitude.

**c. Clé publique de la prod**

Pour le compte `backup` (non administrateur) :

```powershell
$dir = "C:\Users\backup\.ssh"
New-Item -ItemType Directory -Force $dir
Add-Content -Path "$dir\authorized_keys" -Value "<coller la clé publique>" -Encoding ascii
icacls "$dir\authorized_keys" /inheritance:r /grant "backup:F" /grant "SYSTEM:F"
```

> ⚠️ **Si c'est un compte ADMINISTRATEUR**, OpenSSH ignore
> `~/.ssh/authorized_keys` et ne lit que
> `C:\ProgramData\ssh\administrators_authorized_keys` (ACL : `Administrators`
> et `SYSTEM` seulement). L'authentification échoue sans message utile si on se
> trompe d'emplacement. (Le dossier `C:\Users\backup` n'existe qu'après une
> première ouverture de session du compte.)

**d. Essai depuis la prod**

```bash
ssh -i ~/.ssh/id_backup_windows backup@10.135.0.210 "echo connexion OK"
./scripts/backup-db.sh
./scripts/push-backup.sh --dry-run
./scripts/push-backup.sh
ls -l /opt/a2project/backups/          # un .pushed à côté de l'archive = confirmée
```

Si `scp` refuse le chemin `C:/Backups/_transit`, essayer la forme SFTP
`/C:/Backups/_transit` dans `BACKUP_REMOTE_STAGING`.

---

## 6. Restaurer

### Archive EN CLAIR (`.tar`, le mode actuel)

```bash
mkdir -p /tmp/restore && cd /tmp/restore
scp -i ~/.ssh/id_backup_windows \
    "backup@10.135.0.210:C:/Backups/supervisor/supervisor-2026-09-22_050000.tar*" .
sha256sum -c supervisor-2026-09-22_050000.tar.sha256
tar -xf supervisor-2026-09-22_050000.tar -C /tmp/restore
cat /tmp/restore/MANIFEST.txt
```

Puis passer directement à « Restaurer dans la base ».

### Récupérer et déchiffrer l'archive (`.tar.enc`)

```bash
mkdir -p /tmp/restore && cd /tmp/restore
scp -i ~/.ssh/id_backup_windows \
    "backup@10.135.0.210:C:/Backups/supervisor/supervisor-2026-09-16_050000.tar.enc*" .

sha256sum -c supervisor-2026-09-16_050000.tar.enc.sha256    # intégrité d'abord

export BACKUP_PASSPHRASE='<la phrase du coffre>'
openssl enc -d -aes-256-cbc -md sha512 -pbkdf2 -iter 600000 \
    -pass env:BACKUP_PASSPHRASE \
    -in supervisor-2026-09-16_050000.tar.enc | tar -xf - -C /tmp/restore

cat /tmp/restore/MANIFEST.txt    # vérifier la date et le commit
```

### Restaurer dans la base

```bash
cd /opt/a2project
export LAN_BIND_IP=10.135.3.25
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lan.yml'

# 1. ARRÊTER TOUT CE QUI ÉCRIT. Un scheduler qui poursuit ses polls pendant un
#    pg_restore --clean se bat avec lui pour les mêmes lignes.
dc stop backend scheduler scheduler-heavy scheduler-ping-lr \
        scheduler-poll-switch scheduler-poll-af60 scheduler-poll-ltu \
        scheduler-poll-airos netflow-collector

# 2. Copier le dump dans le conteneur puis restaurer
dc cp /tmp/restore/network_supervisor.dump postgres:/tmp/restore.dump
dc exec -T postgres pg_restore -U supervisor -d network_supervisor \
    --clean --if-exists --no-owner --single-transaction /tmp/restore.dump

# 3. Relancer
dc up -d
dc logs -f backend
```

`--single-transaction` : la restauration **passe ou ne passe pas**, pas de base
à moitié restaurée.

Les tables exclues reviennent vides et se re-remplissent en quelques minutes.
`alembic_version` fait partie du dump : si le code déployé est plus récent, le
backend applique les migrations manquantes à son démarrage.

La consommation se relit dans `client_consumption_daily` (`device_id`,
`metric_name`, `day`, `bytes`, `samples`) ; `device_id` se recoupe avec
`devices`.

### Restaurer le journal FAI

Il ne passe pas par `pg_restore` — c'est un fichier à remettre en place :

```bash
tar -xf supervisor-<date>.tar fai_actions.log fai_evidence
cp fai_actions.log  /opt/a2project/backend/logs/
cp -r fai_evidence  /opt/a2project/backend/logs/
```

⚠️ Le journal est **append-only** : si le serveur a continué de tourner depuis
la sauvegarde, écraser le fichier **perd les lignes écrites entre-temps**.
Restaurer à côté puis fusionner (les lignes sont horodatées UTC et triables)
plutôt qu'écraser à l'aveugle.

---

## 7. Tester la restauration — et le faire vraiment

> Une sauvegarde jamais restaurée n'est pas une sauvegarde : c'est une
> hypothèse.

Une fois maintenant, puis une fois par trimestre, sur une base jetable —
jamais sur la production :

```bash
dc exec -T postgres createdb -U supervisor restore_test
dc exec -T postgres pg_restore -U supervisor -d restore_test --no-owner /tmp/restore.dump
dc exec -T postgres psql -U supervisor -d restore_test -c \
  "SELECT count(*) AS devices FROM devices;
   SELECT count(*) AS lrs FROM lrs;
   SELECT count(*) AS seuils FROM system_settings;"
dc exec -T postgres dropdb -U supervisor restore_test
```

---

## 8. Ajouter un dump intégral hebdomadaire (optionnel)

Seulement si les compteurs bruts de consommation doivent être restaurables à
l'octet près. Compter **plusieurs Go** par archive.

> ⚠️ Les exclusions sont écrites en dur dans `backup-db.sh`, précisément pour
> qu'on ne produise pas une archive de 7 Go par inadvertance. Ajouter une
> variable pour les lever est une petite modification, à faire **le jour où la
> décision est prise**.

---

## 9. Ce que ce montage ne couvre pas

| Non couvert | Conséquence |
|---|---|
| Le fichier `.env` de la prod | Contient `API_KEY`, `POSTGRES_PASSWORD`, les clés cloisonnées **et `BACKUP_PASSPHRASE`** — le sauvegarder ici ferait voyager la clé avec la donnée. À ranger dans le coffre d'entreprise, à la main. |
| Les volumes Docker, les images | Reconstruits par `git pull` + `dc up -d --build`. |
| Les certificats nginx | Auto-signés, régénérables. |
| Le journal FAI (`fai_audit`) | Fichier sur disque, **hors base** (`FAI_LOG_PATH`, défaut `/app/logs/fai_actions.log`) — à sauvegarder séparément si la piste d'audit des coupures doit survivre au serveur. |
| Un serveur Windows injoignable | L'archive est **quand même produite et gardée en local**, et partira au prochain passage horaire. Mais **rien ne le signale tout seul** : surveiller `/var/log/supervisor-backup.log` (une ligne `ERREUR` par heure d'indisponibilité). |
| Une copie hors site | `10.135.0.210` est sur le **même LAN** que la prod : un sinistre sur le site emporte les deux. Pour une copie hors site, faire synchroniser `C:\Backups\supervisor` vers un cloud depuis le serveur Windows (§1, « Et Sync.com ? »). |
