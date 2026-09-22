#!/usr/bin/env bash
#
# Envoie les archives de sauvegarde vers le serveur Windows de sauvegarde.
#
#   prod Ubuntu 10.135.3.25              serveur Windows 10.135.0.210
#   -----------------------              ---------------------------
#   backup-db.sh (05:00 UTC)
#     /opt/a2project/backups/
#       supervisor-<date>.tar.enc
#          |
#          |  scp vers un dossier de transit
#          +------------------------------->  C:\Backups\_transit
#          |  ssh                                   |
#          +------------------------------->  receive-backup.ps1
#                                                   verifie le SHA-256
#                                                   deplace vers C:\Backups\supervisor
#
# CE QUI EST ENVOYE : LE DOSSIER, PAS SEULEMENT LA DERNIERE ARCHIVE. Toute
# archive locale pas encore confirmee par le serveur Windows part, de la plus
# ancienne a la plus recente. Une nuit ou le serveur Windows etait eteint n'est
# donc pas perdue : elle part au passage suivant. La confirmation est un
# marqueur `<archive>.pushed`, pose UNIQUEMENT quand le receveur a verifie
# l'empreinte et publie le fichier - un scp reussi ne suffit pas.
#
# Le passage est donc rejouable et peu couteux : sans archive en attente, il ne
# se connecte meme pas. C'est ce qui permet de le planifier toutes les heures.
#
# POURQUOI UN SCRIPT SEPARE de backup-db.sh : une archive presente sur le disque
# local est deja un acquis. Si le serveur Windows est injoignable, l'echec de
# l'envoi ne doit ni faire echouer ni annuler la sauvegarde elle-meme.
#
# /!\ LE SERVEUR WINDOWS NE DECHIFFRE JAMAIS RIEN. L'archive est chiffree ici
#     (cf. backup-db.sh) et il ne fait que ranger un bloc opaque. La phrase
#     secrete ne doit PAS exister sur le serveur Windows : sinon la donnee et
#     sa cle sont au meme endroit et le chiffrement ne protege plus de rien.
#
# /!\ La retention locale (BACKUP_RETENTION_DAYS, 14 j) borne le rattrapage :
#     si le serveur Windows reste injoignable plus longtemps, les archives les
#     plus anciennes sont supprimees ici avant d'avoir pu partir.
#
# Usage :
#   ./scripts/push-backup.sh                  # envoie tout ce qui est en attente
#   ./scripts/push-backup.sh --dry-run        # montre ce qui partirait
#   ./scripts/push-backup.sh <archive>...     # force l'envoi d'archives precises
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
REMOTE_HOST="${BACKUP_REMOTE_HOST:-$(env_get BACKUP_REMOTE_HOST)}"
REMOTE_USER="${BACKUP_REMOTE_USER:-$(env_get BACKUP_REMOTE_USER backup)}"
REMOTE_PORT="${BACKUP_REMOTE_SSH_PORT:-$(env_get BACKUP_REMOTE_SSH_PORT 22)}"
REMOTE_KEY="${BACKUP_REMOTE_SSH_KEY:-$(env_get BACKUP_REMOTE_SSH_KEY /home/a2/.ssh/id_backup_windows)}"
REMOTE_STAGING="${BACKUP_REMOTE_STAGING:-$(env_get BACKUP_REMOTE_STAGING 'C:/Backups/_transit')}"
REMOTE_DIR="${BACKUP_REMOTE_DIR:-$(env_get BACKUP_REMOTE_DIR 'C:/Backups/supervisor')}"
REMOTE_SCRIPT="${BACKUP_REMOTE_SCRIPT:-$(env_get BACKUP_REMOTE_SCRIPT 'C:/Backups/receive-backup.ps1')}"
REMOTE_KEEP_DAYS="${BACKUP_REMOTE_KEEP_DAYS:-$(env_get BACKUP_REMOTE_KEEP_DAYS 30)}"

DRY_RUN=0
FILES=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -*)        die "option inconnue : $arg" ;;
        *)         FILES+=("$arg") ;;
    esac
done

[ -n "$REMOTE_HOST" ] \
    || die "BACKUP_REMOTE_HOST absent du .env - envoi non configure (voir docs/backup-database.md)"

# --- Quelles archives envoyer ? ----------------------------------------------
# Le motif `supervisor-*.tar.enc` exclut d'office :
#   - `latest.tar.enc` (un symlink vers une archive deja dans la liste) ;
#   - une archive en cours d'ecriture (`.tar.enc.part`) ;
#   - une archive EN CLAIR (`.tar`, BACKUP_ALLOW_PLAINTEXT), qui porte les mots
#     de passe SSH de tout le parc et ne doit quitter ce serveur sous aucun
#     pretexte.
# Le developpement du motif est trie par nom, donc par date : les plus
# anciennes partent en premier.
PENDING=()
if [ "${#FILES[@]}" -gt 0 ]; then
    for f in "${FILES[@]}"; do
        [ -f "$f" ] || die "archive introuvable : $f"
        case "$f" in
            *.tar.enc) PENDING+=("$f") ;;
            *) die "archive NON chiffree ($f) - envoi refuse. Voir BACKUP_PASSPHRASE." ;;
        esac
    done
else
    shopt -s nullglob
    for f in "$BACKUP_DIR"/supervisor-*.tar.enc; do
        [ -e "$f.pushed" ] || PENDING+=("$f")
    done
    shopt -u nullglob
fi

if [ "${#PENDING[@]}" -eq 0 ]; then
    # Silencieux au sens du reseau : aucune connexion n'est ouverte.
    log "Rien a envoyer - toutes les archives sont deja confirmees par $REMOTE_HOST."
    exit 0
fi

log "${#PENDING[@]} archive(s) a envoyer vers $REMOTE_USER@$REMOTE_HOST"

# Options communes a ssh et scp. /!\ Le PORT n'y est pas : ssh le prend en
# `-p`, scp en `-P` - et pour scp, `-p` veut dire "conserver les dates". Passer
# `-p 22` a scp ferait de "22" un nom de fichier a envoyer.
SSH_COMMON=(-o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=15)
[ -f "$REMOTE_KEY" ] && SSH_COMMON+=(-i "$REMOTE_KEY")

# Codes de retour : 0 = publie, 1 = refuse/echoue cote Windows (on passe a la
# suivante), 2 = serveur injoignable (inutile d'essayer les suivantes).
push_one() {
    local archive="$1" name sha_file hash cmd
    name="$(basename "$archive")"
    sha_file="$archive.sha256"

    # backup-db.sh ecrit l'empreinte juste APRES avoir renomme l'archive : un
    # passage qui tombe entre les deux trouve l'une sans l'autre. Ce n'est pas
    # une erreur, l'archive partira au passage suivant.
    if [ ! -f "$sha_file" ]; then
        log "  $name : empreinte pas encore ecrite - reportee au prochain passage"
        return 1
    fi
    hash="$(cut -d' ' -f1 < "$sha_file")"

    log "- $name ($(du -h "$archive" | cut -f1))"
    if [ "$DRY_RUN" -eq 1 ]; then
        log "  [dry-run] scp -> $REMOTE_STAGING/ puis receive-backup.ps1 -> $REMOTE_DIR"
        return 0
    fi

    # Depot dans le dossier de TRANSIT, jamais directement dans la destination :
    # un fichier a moitie ecrit ne doit jamais y apparaitre (si la destination
    # est un dossier synchronise, son client le televerserait tel quel).
    if ! scp -P "$REMOTE_PORT" "${SSH_COMMON[@]}" \
            "$archive" "$sha_file" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_STAGING/"; then
        return 2
    fi

    # `powershell -File` explicitement : par defaut, une commande passee a
    # OpenSSH sous Windows s'execute dans cmd.exe, qui ne sait pas lancer un .ps1.
    cmd="powershell -NoProfile -ExecutionPolicy Bypass -File \"$REMOTE_SCRIPT\""
    cmd="$cmd -FileName \"$name\" -ExpectedHash \"$hash\""
    cmd="$cmd -StagingDir \"$REMOTE_STAGING\" -DestDir \"$REMOTE_DIR\""
    cmd="$cmd -KeepDays $REMOTE_KEEP_DAYS"

    # La sortie du receveur est reprise dans NOTRE log : en cas d'incident
    # nocturne tout se lit au meme endroit, sans ouvrir de session Windows.
    if ssh -p "$REMOTE_PORT" "${SSH_COMMON[@]}" "$REMOTE_USER@$REMOTE_HOST" "$cmd" 2>&1 \
            | sed 's/^/    /'; then
        # Le marqueur n'est pose qu'ICI, apres la verification cote Windows.
        # La retention de backup-db.sh le supprime avec l'archive (meme motif).
        touch "$archive.pushed"
        return 0
    fi
    return 1
}

SENT=0
FAILED=0
INDEX=0
for archive in "${PENDING[@]}"; do
    INDEX=$((INDEX + 1))
    RC=0
    push_one "$archive" || RC=$?
    case "$RC" in
        0) SENT=$((SENT + 1)) ;;
        2)
            FAILED=$((FAILED + 1 + ${#PENDING[@]} - INDEX))
            log "ERREUR: $REMOTE_HOST injoignable (scp a echoue) - arret. Les archives"
            log "        restantes sont intactes et repartiront au prochain passage."
            break
            ;;
        *) FAILED=$((FAILED + 1)) ;;
    esac
done

log "Bilan : $SENT envoyee(s), $FAILED en attente ou en echec."
[ "$FAILED" -eq 0 ] || exit 1
