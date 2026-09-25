#!/usr/bin/env bash
#
# Sauvegarde de la base du superviseur, chiffree, prete a partir vers le cloud.
#
# Produit UN artefact par nuit dans $BACKUP_DIR :
#
#   supervisor-YYYY-MM-DD_HHMMSS.tar.enc   + son .sha256
#   latest.tar.enc                         (symlink vers le plus recent)
#
# L'archive contient :
#   network_supervisor.dump      pg_dump format custom (-Fc), restaurable par pg_restore
#   fai_actions.log              le JOURNAL FAI (coupures/deblocages d'abonnes)
#   fai_evidence/                les preuves : transcription SSH de chaque action
#   MANIFEST.txt                 quoi, quand, depuis quel commit, avec quelles exclusions
#
# /!\ LE JOURNAL FAI N'EST PAS DANS LA BASE. C'est un fichier texte
#     (`backend/logs/`), hors de portee de pg_dump - il manquait donc
#     entierement a la sauvegarde jusqu'au 2026-09-25. C'est pourtant la SEULE
#     piste qui dise qui a coupe quel abonne, quand et sur ordre de qui : elle
#     ne se reconstitue depuis rien.
#
# /!\ LE CHIFFREMENT N'EST PAS OPTIONNEL. Le dump porte les colonnes
#     ssh_password / api_password / management_password de la table `devices` et
#     de ses sous-classes : les mots de passe SSH de TOUT le parc radio y sont en
#     CLAIR. Une archive non chiffree deposee sur un cloud tiers, c'est la
#     totalite des acces equipements. Le script refuse de tourner sans
#     BACKUP_PASSPHRASE (voir BACKUP_ALLOW_PLAINTEXT si vraiment necessaire).
#
# /!\ CE QUI N'EST PAS DANS LA SAUVEGARDE - et pourquoi.
#     Les tables time-series sont dumpees SANS LEURS DONNEES
#     (--exclude-table-data) : le schema est la, les lignes non. A la
#     restauration elles reviennent vides et se re-remplissent seules au premier
#     tour de poll.
#
#       device_metrics       ~20 M lignes / ~6,8 Go - re-generee par les polls
#       power_status_logs    releves UISP Power - ecrits mais lus par aucun service
#       auth_sessions        cookies de session - les restaurer serait une faute
#
#     SAUVEGARDEES avec leurs donnees depuis le 2026-09-22 (decision
#     d'exploitation) : l'historique des courbes (`lr_metric_samples`, 30 j) et
#     le trafic Internet par operateur (`traffic_dest_stats`, 90 j). Ce sont les
#     deux historiques qui ne se reconstituent PAS : apres une restauration sans
#     eux, graphes et page /traffic repartaient de zero. Contrepartie : l'archive
#     passe de ~1 Mo a plusieurs centaines de Mo, voire quelques Go.
#
#     /!\ LA CONSOMMATION CLIENT, ELLE, EST BIEN SAUVEGARDEE - depuis le
#     2026-09-25 elle vit dans la table `client_consumption_daily` (un total
#     par client, par compteur et par journee), qui est une VRAIE TABLE donc
#     dumpee avec ses lignes comme le reste. Auparavant elle n'existait que
#     sous forme de deltas a recalculer sur `device_metrics`, dont les donnees
#     sont exclues ici : il fallait joindre un CSV de la matview des 30 jours,
#     qui a ete supprimee avec elle.
#
#     Ce qui reste hors sauvegarde, c'est le detail INFRA-JOURNALIER de la
#     consommation (« combien entre 14 h et 15 h le 3 mars ») - jamais le total
#     d'une journee ni d'une periode. Ce detail est de toute facon purge sur la
#     prod au-dela de `DEVICE_METRICS_RETENTION_DAYS` (7 j).
#
# Usage :
#   ./scripts/backup-db.sh                 # sauvegarde
#   ./scripts/backup-db.sh --list          # ce qui est deja en local
#   BACKUP_RETENTION_DAYS=30 ./scripts/backup-db.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(dirname "$SCRIPT_DIR")}"

log() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*"; }
die() { printf '%s  ERREUR: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*" >&2; exit 1; }

# Lecture d'une cle du .env SANS le sourcer : les valeurs contiennent des
# caracteres ($, #, espaces, guillemets) qu'un `source` interpreterait - et le
# .env porte des secrets qu'on ne veut pas voir se transformer en commandes.
env_get() {
    local key="$1" default="${2-}" line
    line="$(grep -m1 -E "^[[:space:]]*${key}=" "$PROJECT_DIR/.env" 2>/dev/null || true)"
    if [ -z "$line" ]; then printf '%s' "$default"; return; fi
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

BACKUP_DIR="${BACKUP_DIR:-$(env_get BACKUP_DIR "$PROJECT_DIR/backups")}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-$(env_get BACKUP_RETENTION_DAYS 14)}"
PGUSER_="$(env_get POSTGRES_USER supervisor)"
PGDB_="$(env_get POSTGRES_DB network_supervisor)"

if [ "${1:-}" = "--list" ]; then
    ls -lh "$BACKUP_DIR"/supervisor-*.tar* 2>/dev/null \
        || log "Aucune sauvegarde dans $BACKUP_DIR"
    exit 0
fi

# --- Chiffrement : exige, sauf derogation explicite --------------------------
BACKUP_PASSPHRASE="${BACKUP_PASSPHRASE:-$(env_get BACKUP_PASSPHRASE)}"
ALLOW_PLAINTEXT="${BACKUP_ALLOW_PLAINTEXT:-$(env_get BACKUP_ALLOW_PLAINTEXT false)}"
ENCRYPT=1
if [ -z "$BACKUP_PASSPHRASE" ]; then
    if [ "$ALLOW_PLAINTEXT" = "true" ] || [ "$ALLOW_PLAINTEXT" = "1" ]; then
        ENCRYPT=0
        log "/!\\ BACKUP_ALLOW_PLAINTEXT actif - archive NON chiffree. Elle contient"
        log "/!\\ les mots de passe SSH du parc : ne la deposez sur AUCUN cloud."
    else
        die "BACKUP_PASSPHRASE absente du .env.
       L'archive contient les mots de passe SSH de tout le parc radio en clair ; la
       produire non chiffree pour l'envoyer sur un cloud tiers exposerait tous les
       acces equipements. Generer une phrase secrete :
           openssl rand -base64 48
       puis la poser dans $PROJECT_DIR/.env sous BACKUP_PASSPHRASE=... et LA
       CONSERVER AILLEURS QUE SUR CE SERVEUR (sans elle, aucune restauration
       n'est possible)."
    fi
fi

# --- docker compose : les 3 -f + LAN_BIND_IP, comme partout sur cette stack ---
# Un `docker compose` avec moins de fichiers ferait sauter le binding LAN de
# nginx (cf. CLAUDE.md, "Commandes de deploiement type").
export LAN_BIND_IP="${LAN_BIND_IP:-$(env_get LAN_BIND_IP 10.135.3.25)}"
dc() {
    docker compose \
        -f "$PROJECT_DIR/docker-compose.yml" \
        -f "$PROJECT_DIR/docker-compose.prod.yml" \
        -f "$PROJECT_DIR/docker-compose.lan.yml" "$@"
}

cd "$PROJECT_DIR"
command -v docker >/dev/null \
    || die "docker introuvable dans le PATH (cron a un PATH minimal - voir docs/backup-database.md)"
# Sonder la base plutot que lister les services : `docker compose ps` a vu ses
# options de filtrage bouger d'une version a l'autre, et un filtre non reconnu
# renverrait une liste vide qu'on lirait a tort comme "postgres est mort". Ici
# on teste exactement ce dont on a besoin - la base repond-elle.
dc exec -T postgres pg_isready -U "$PGUSER_" -d "$PGDB_" >/dev/null 2>&1 \
    || die "postgres ne repond pas (container arrete ?) - rien a sauvegarder"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date -u '+%Y-%m-%d_%H%M%S')"

# BACKUP_SINGLE_COPY=true : UNE seule sauvegarde. Chaque nuit produit une
# archive DATEE puis supprime la precedente, ici comme sur le serveur Windows
# (decision d'exploitation du 2026-09-22). Le nom date rend la date lisible sans
# ouvrir l'archive ; la nuit precedente reste recuperable dans la corbeille de
# Sync.com (« Deleted files »), pour la duree prevue par l'abonnement.
# /!\ Un probleme vu APRES la sauvegarde suivante (base videe dans la nuit, par
#     exemple) n'a plus de copie saine ni ici ni sur Windows : il ne reste que
#     la corbeille de Sync.com.
SINGLE_COPY="${BACKUP_SINGLE_COPY:-$(env_get BACKUP_SINGLE_COPY false)}"
case "$SINGLE_COPY" in
    true|1) SINGLE_COPY=1 ;;
    *)      SINGLE_COPY=0 ;;
esac

if [ "$ENCRYPT" -eq 1 ]; then
    BASENAME="supervisor-${STAMP}.tar.enc"
else
    BASENAME="supervisor-${STAMP}.tar"
fi
OUT="$BACKUP_DIR/$BASENAME"

# Dossier de travail DANS $BACKUP_DIR (700) et pas dans /tmp : le dump y sejourne
# en clair le temps de l'archiver, et il pese desormais plusieurs centaines de Mo
# a quelques Go (courbes + trafic inclus) - /tmp peut etre petit, ou en memoire.
WORK="$(mktemp -d -p "$BACKUP_DIR" .work.XXXXXX)"
# Nettoyage meme en cas d'echec : le dump en clair ne doit jamais trainer, et un
# .part abandonne ne doit pas ressembler a une archive complete.
cleanup() { rm -rf "$WORK"; rm -f "$OUT.part"; }
trap cleanup EXIT

# --- 1. pg_dump --------------------------------------------------------------
# pg_dump du CONTAINER, pas de l'hote : la version majeure colle forcement au
# serveur (postgres:16-alpine), alors que le postgres systeme de la machine est
# d'une version inconnue et refuserait le dump avec un "server version mismatch".
log "pg_dump de $PGDB_ (donnees time-series exclues)..."
dc exec -T postgres pg_dump \
    -U "$PGUSER_" -d "$PGDB_" \
    --format=custom --compress=9 \
    --exclude-table-data=device_metrics \
    --exclude-table-data=power_status_logs \
    --exclude-table-data=auth_sessions \
    > "$WORK/network_supervisor.dump" \
    || die "pg_dump a echoue - archive NON produite"

log "  dump : $(du -h "$WORK/network_supervisor.dump" | cut -f1)"

# --- 2. Journal FAI (fichiers, hors base) ------------------------------------
# Traduction chemin CONTENEUR -> chemin HOTE : tous les fichiers compose montent
# ./backend sur /app.
#
# /!\ Copie faite sur l'HOTE et non via `dc exec backend` : la sauvegarde doit
# continuer de fonctionner quand le backend est en panne - c'est precisement le
# moment ou l'on veut une archive. Postgres, lui, est indispensable au dump, on
# n'a pas le choix.
fai_host_path() {
    case "$1" in
        /app/*) printf '%s/backend/%s' "$PROJECT_DIR" "${1#/app/}" ;;
        *)      printf '%s' "$1" ;;
    esac
}

FAI_LOG="$(fai_host_path "$(env_get FAI_LOG_PATH /app/logs/fai_actions.log)")"
FAI_EVIDENCE="$(fai_host_path "$(env_get FAI_EVIDENCE_DIR /app/logs/fai_evidence)")"

FAI_LINES="absent"
if [ -f "$FAI_LOG" ]; then
    # Sans -p : les fichiers sont ecrits par le CONTENEUR, donc appartiennent a
    # root sur l'hote. `cp -p` tenterait un chown, echouerait en non-root et,
    # avec `set -e`, tuerait toute la sauvegarde pour une date de fichier.
    cp "$FAI_LOG" "$WORK/fai_actions.log"
    FAI_LINES="$(wc -l < "$WORK/fai_actions.log" | tr -d ' ') ligne(s)"
    log "Journal FAI : $FAI_LINES ($(du -h "$WORK/fai_actions.log" | cut -f1))"
else
    # Pas une erreur : un deploiement ou aucun blocage n'a jamais eu lieu n'a
    # pas encore de journal. On le NOTE dans le manifeste plutot que de le taire
    # - une piste d'audit absente doit se voir a la relecture.
    log "/!\ Journal FAI introuvable ($FAI_LOG) - archive produite sans lui"
fi

FAI_EVIDENCE_COUNT=0
if [ -d "$FAI_EVIDENCE" ]; then
    cp -r "$FAI_EVIDENCE" "$WORK/fai_evidence"   # sans -p, meme raison
    FAI_EVIDENCE_COUNT="$(find "$WORK/fai_evidence" -type f | wc -l | tr -d ' ')"
    log "Preuves FAI : $FAI_EVIDENCE_COUNT fichier(s) ($(du -sh "$WORK/fai_evidence" | cut -f1))"
fi

# --- 3. Manifeste ------------------------------------------------------------
{
    echo "Sauvegarde Network Supervisor"
    echo "date_utc      : $(date -u '+%Y-%m-%d %H:%M:%SZ')"
    echo "hote          : $(hostname)"
    echo "base          : $PGDB_ (user $PGUSER_)"
    echo "commit        : $(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo inconnu)"
    if [ "$ENCRYPT" -eq 1 ]; then
        echo "chiffrement   : AES-256-CBC / PBKDF2 600k iterations"
    else
        echo "chiffrement   : AUCUN"
    fi
    echo
    echo "Journal FAI   : $FAI_LINES, $FAI_EVIDENCE_COUNT preuve(s)"
    echo
    echo "Donnees exclues (schema conserve, lignes non - re-generees par les polls) :"
    echo "  device_metrics, power_status_logs, auth_sessions"
    echo "Historiques INCLUS : lr_metric_samples (courbes), traffic_dest_stats (trafic),"
    echo "                     client_consumption_daily (consommation par journee)"
    echo
    echo "Restauration : voir docs/backup-database.md"
} > "$WORK/MANIFEST.txt"

# --- 4. Archive + chiffrement ------------------------------------------------
# Ecriture en .part puis renommage : le relais Windows lit ce repertoire, et
# doit etre incapable de ramasser une archive a moitie ecrite.
if [ "$ENCRYPT" -eq 1 ]; then
    log "Archivage et chiffrement..."
    # Passphrase via l'environnement et non en argv : argv est visible de tous
    # les utilisateurs de la machine dans `ps`, l'environnement d'un process ne
    # l'est que de son proprietaire.
    #
    # /!\ `export` et PAS un prefixe `VAR=val tar ...` : le prefixe ne vaut que
    # pour la commande qu'il precede, or c'est OPENSSL - un autre processus du
    # pipeline - qui lit la variable. Lue depuis le .env elle n'est qu'une
    # variable de shell, non exportee : sans cet export, openssl ne la voit pas
    # et le chiffrement echoue chaque nuit.
    export BACKUP_PASSPHRASE
    tar -C "$WORK" -cf - . \
        | openssl enc -aes-256-cbc -md sha512 -pbkdf2 -iter 600000 -salt \
              -pass env:BACKUP_PASSPHRASE \
        > "$OUT.part" \
        || die "archivage/chiffrement echoue"
else
    log "Archivage..."
    tar -C "$WORK" -cf - . > "$OUT.part" || die "archivage echoue"
fi

mv "$OUT.part" "$OUT"
chmod 600 "$OUT"
( cd "$BACKUP_DIR" && sha256sum "$BASENAME" > "$BASENAME.sha256" )

# Le symlink porte l'extension REELLE de l'archive. Consequence voulue : le
# relais Windows ne cherche que `latest.tar.enc`, donc une archive produite en
# clair (BACKUP_ALLOW_PLAINTEXT) n'est tout simplement pas ramassee et ne peut
# pas partir vers le cloud par megarde.
case "$BASENAME" in
    *.tar.enc) LATEST="latest.tar.enc" ;;
    *)         LATEST="latest.tar" ;;
esac
ln -sfn "$BASENAME" "$BACKUP_DIR/$LATEST"
ln -sfn "$BASENAME.sha256" "$BACKUP_DIR/$LATEST.sha256"

log "OK - $OUT ($(du -h "$OUT" | cut -f1))"

# --- 5a. Copie unique : l'archive precedente disparait -----------------------
# Seulement MAINTENANT, une fois la nouvelle complete, renommee et son empreinte
# ecrite : un echec plus haut (pg_dump, disque plein) laisse la precedente en
# place, et on n'est jamais sans sauvegarde du tout. Emporte aussi l'empreinte
# et le marqueur d'envoi de l'ancienne. Si l'ancienne n'etait pas encore partie
# vers Windows (serveur eteint), la nouvelle la remplace avantageusement.
if [ "$SINGLE_COPY" -eq 1 ]; then
    REPLACED="$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'supervisor-*' \
                ! -name "$BASENAME" ! -name "$BASENAME.*" -print -delete | wc -l)"
    if [ "$REPLACED" -gt 0 ]; then
        log "Copie unique : $REPLACED fichier(s) de la sauvegarde precedente supprime(s)"
    fi
fi

# --- 5b. Retention locale ----------------------------------------------------
# Le cloud garde sa propre profondeur ; ici on ne garde que de quoi restaurer
# vite sans remplir le disque.
PURGED="$(find "$BACKUP_DIR" -maxdepth 1 -name 'supervisor-*.tar*' -type f \
          -mtime "+$RETENTION_DAYS" -print -delete | wc -l)"
if [ "$PURGED" -gt 0 ]; then
    log "Retention : $PURGED fichier(s) de plus de $RETENTION_DAYS j supprime(s)"
fi

log "Termine."
