#!/usr/bin/env bash
#
# Pousse la derniere archive de sauvegarde vers le relais AWS Windows, qui la
# depose dans son dossier Sync.com.
#
#   prod Ubuntu 10.135.3.25          AWS Windows 13.49.185.225        Sync.com
#   -----------------------          -------------------------        --------
#   backup-db.sh (05:00 UTC)
#     archive chiffree
#          |
#          |  scp vers un dossier de transit
#          +------------------------------->  receive-backup.ps1
#                                               verifie le SHA-256
#                                               deplace vers le dossier Sync --> cloud
#
# POURQUOI LA PROD POUSSE (et ne se fait pas tirer) : la prod est derriere le
# FortiGate, avec une allowlist d'IP sources. Se faire tirer par AWS imposerait
# d'y ajouter 13.49.185.225, donc d'exposer le SSH de la prod a un hote Internet
# de plus. En poussant, c'est la prod qui initie : aucune regle entrante a
# ouvrir, rien a changer au FortiGate.
#
# POURQUOI UN SCRIPT SEPARE de backup-db.sh : une sauvegarde qui existe sur le
# disque local est deja un acquis. Si le reseau vers AWS est coupe, on ne veut
# pas que l'echec du transfert fasse echouer - ni surtout annuler - la
# sauvegarde elle-meme. Les deux etapes reussissent ou echouent separement, et
# celle-ci est rejouable seule autant de fois qu'on veut.
#
# /!\ LE RELAIS NE DECHIFFRE JAMAIS RIEN. L'archive est chiffree sur la prod
#     (cf. backup-db.sh) et le relais ne fait que deplacer un bloc opaque. La
#     phrase secrete ne doit exister NI sur AWS, NI dans Sync.com : sinon la
#     donnee et sa cle voyagent ensemble et le chiffrement ne protege plus rien.
#
# Usage :
#   ./scripts/push-to-aws.sh                    # pousse la plus recente
#   ./scripts/push-to-aws.sh --dry-run          # montre ce qui serait fait
#   ./scripts/push-to-aws.sh <fichier>          # pousse une archive precise
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(dirname "$SCRIPT_DIR")}"

log() { printf '%s  %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*"; }
die() { printf '%s  ERREUR: %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*" >&2; exit 1; }

# Meme lecture prudente du .env que backup-db.sh : jamais de `source`, les
# valeurs contiennent des caracteres qu'un shell interpreterait.
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
RELAY_HOST="${AWS_RELAY_HOST:-$(env_get AWS_RELAY_HOST)}"
RELAY_USER="${AWS_RELAY_USER:-$(env_get AWS_RELAY_USER Administrator)}"
RELAY_KEY="${AWS_RELAY_SSH_KEY:-$(env_get AWS_RELAY_SSH_KEY /home/a2/.ssh/id_relay_aws)}"
RELAY_PORT="${AWS_RELAY_SSH_PORT:-$(env_get AWS_RELAY_SSH_PORT 22)}"
RELAY_STAGING="${AWS_RELAY_STAGING:-$(env_get AWS_RELAY_STAGING 'C:/backup-staging')}"
RELAY_SCRIPT="${AWS_RELAY_SCRIPT:-$(env_get AWS_RELAY_SCRIPT 'C:/a2project/scripts/receive-backup.ps1')}"
RELAY_KEEP_DAYS="${AWS_RELAY_KEEP_DAYS:-$(env_get AWS_RELAY_KEEP_DAYS 30)}"

DRY_RUN=0
ARCHIVE=""
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -*)        die "option inconnue : $arg" ;;
        *)         ARCHIVE="$arg" ;;
    esac
done

[ -n "$RELAY_HOST" ] || die "AWS_RELAY_HOST absent du .env — relais non configure (voir docs/backup-database.md)"

# --- Quelle archive pousser ? ------------------------------------------------
# `latest.tar.enc` est un symlink pose par backup-db.sh. On resout son nom REEL,
# parce que c'est ce nom-la qui doit arriver dans le dossier Sync : une suite de
# fichiers tous nommes "latest" serait inexploitable le jour de la restauration.
if [ -z "$ARCHIVE" ]; then
    [ -L "$BACKUP_DIR/latest.tar.enc" ] \
        || die "aucun latest.tar.enc dans $BACKUP_DIR — lancer backup-db.sh d'abord"
    ARCHIVE="$BACKUP_DIR/$(readlink "$BACKUP_DIR/latest.tar.enc")"
fi

[ -f "$ARCHIVE" ] || die "archive introuvable : $ARCHIVE"

case "$ARCHIVE" in
    *.tar.enc) : ;;
    # Refus explicite : une archive en clair (BACKUP_ALLOW_PLAINTEXT) porte les
    # mots de passe SSH de tout le parc. Elle ne doit atteindre aucun cloud, et
    # ce refus est le dernier point ou on peut encore l'empecher.
    *) die "archive NON chiffree ($ARCHIVE) — envoi refuse. Voir BACKUP_PASSPHRASE." ;;
esac

NAME="$(basename "$ARCHIVE")"
SHA_FILE="$ARCHIVE.sha256"
[ -f "$SHA_FILE" ] || die "empreinte manquante : $SHA_FILE"
EXPECTED_HASH="$(cut -d' ' -f1 < "$SHA_FILE")"

SIZE="$(du -h "$ARCHIVE" | cut -f1)"
log "Archive  : $NAME ($SIZE)"
log "Relais   : $RELAY_USER@$RELAY_HOST:$RELAY_PORT"

SSH_OPTS=(-p "$RELAY_PORT" -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15)
[ -f "$RELAY_KEY" ] && SSH_OPTS+=(-i "$RELAY_KEY")

if [ "$DRY_RUN" -eq 1 ]; then
    log "[dry-run] scp $NAME -> $RELAY_STAGING/"
    log "[dry-run] puis receive-backup.ps1 -FileName $NAME -ExpectedHash ${EXPECTED_HASH:0:12}..."
    exit 0
fi

# --- 1. Transfert vers le dossier de transit ---------------------------------
# On depose dans un dossier de TRANSIT, jamais directement dans le dossier
# Sync : le client Sync.com surveille ce dernier en permanence et televerserait
# un fichier a moitie ecrit. C'est le receveur qui fera le deplacement final,
# une fois l'empreinte verifiee.
log "Transfert..."
scp "${SSH_OPTS[@]}" "$ARCHIVE" "$SHA_FILE" "$RELAY_USER@$RELAY_HOST:$RELAY_STAGING/" \
    || die "scp a echoue — l'archive locale reste intacte, relancer ce script suffit"

# --- 2. Le receveur verifie et publie ----------------------------------------
# `powershell -File` explicitement : par defaut, une commande passee a OpenSSH
# sous Windows s'execute dans cmd.exe, qui ne sait pas lancer un .ps1.
log "Verification et publication cote relais..."
REMOTE_CMD="powershell -NoProfile -ExecutionPolicy Bypass -File \"$RELAY_SCRIPT\""
REMOTE_CMD="$REMOTE_CMD -FileName \"$NAME\" -ExpectedHash \"$EXPECTED_HASH\""
REMOTE_CMD="$REMOTE_CMD -KeepDays $RELAY_KEEP_DAYS"

# La sortie du receveur est reprise telle quelle dans notre log : en cas
# d'incident nocturne, tout est dans le meme fichier cote serveur, sans avoir a
# ouvrir une session sur la machine Windows.
if ssh "${SSH_OPTS[@]}" "$RELAY_USER@$RELAY_HOST" "$REMOTE_CMD" 2>&1 | sed 's/^/    /'; then
    log "OK — archive publiee dans le dossier Sync.com du relais."
else
    die "le relais a refuse ou echoue (voir ses lignes ci-dessus) — l'archive locale reste intacte"
fi
