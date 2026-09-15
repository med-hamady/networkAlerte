#!/usr/bin/env bash
#
# Diagnostic de performance du serveur de supervision - LECTURE SEULE.
#
# Repond a une question precise : "un serveur dedie aux jobs ameliorerait-il
# les performances ?". Pour cela il faut savoir OU est le goulot :
#
#   CPU        la machine manque de coeurs          -> un 2e serveur aide
#   DISQUE     Postgres attend ses E/S              -> SSD / serveur pour la base
#   BASE       verrous, requetes lentes, bloat      -> correctif SQL, pas de materiel
#   TERRAIN    les jobs attendent les radios        -> aucun serveur n'y changera rien
#
# Le script ne modifie RIEN : aucun redemarrage, aucune ecriture en base (que
# des SELECT), aucun VACUUM. Il peut tourner en pleine journee.
#
# Usage (sur le serveur de prod, depuis la racine du projet) :
#   ./scripts/diag-perf.sh              # analyse les logs des 6 dernieres heures
#   HOURS=24 ./scripts/diag-perf.sh     # fenetre de logs plus large
#
# Le rapport est affiche ET enregistre dans /tmp/diag-perf-<date>.txt.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(dirname "$SCRIPT_DIR")}"
HOURS="${HOURS:-6}"
STAMP="$(date -u '+%Y-%m-%d_%H%M%S')"
REPORT="/tmp/diag-perf-${STAMP}.txt"

# Meme lecteur de .env que backup-db.sh : jamais de `source` (valeurs avec $ et #).
env_get() {
    local key="$1" default="${2-}" line
    line="$(grep -m1 -E "^[[:space:]]*${key}=" "$PROJECT_DIR/.env" 2>/dev/null || true)"
    if [ -z "$line" ]; then printf '%s' "$default"; return; fi
    line="${line#*=}"
    line="${line%\"}"; line="${line#\"}"
    line="${line%\'}"; line="${line#\'}"
    printf '%s' "$line"
}

PGUSER_="$(env_get POSTGRES_USER supervisor)"
PGDB_="$(env_get POSTGRES_DB network_supervisor)"

# Les 3 -f + LAN_BIND_IP, comme toute commande compose sur cette stack.
export LAN_BIND_IP="${LAN_BIND_IP:-$(env_get LAN_BIND_IP 10.135.3.25)}"
dc() {
    docker compose \
        -f "$PROJECT_DIR/docker-compose.yml" \
        -f "$PROJECT_DIR/docker-compose.prod.yml" \
        -f "$PROJECT_DIR/docker-compose.lan.yml" "$@"
}
q() {
    dc exec -T postgres psql -U "$PGUSER_" -d "$PGDB_" -X -q -P pager=off -c "$1" 2>&1
}
section() { printf '\n==================== %s ====================\n' "$*"; }

cd "$PROJECT_DIR" || exit 1

main() {
echo "Diagnostic de performance - $(hostname) - $(date -u '+%Y-%m-%d %H:%M:%SZ')"
echo "Fenetre de logs analysee : ${HOURS} h"

# ---------------------------------------------------------------------------
section "1. MACHINE"
NCPU="$(nproc)"
echo "Coeurs : $NCPU"
uptime
echo
free -h
echo
df -h / /var/lib/docker 2>/dev/null | awk '!seen[$0]++'

# vmstat : la 1re ligne est une moyenne depuis le boot -> on la jette.
echo
echo "vmstat sur 30 s (moyenne) :"
VM="$(vmstat 5 7 | tail -n 6)"
echo "$VM" | awk '
    { r+=$1; b+=$2; cs+=$12; us+=$13; sy+=$14; id+=$15; wa+=$16; st+=$17; n++ }
    END { printf "  file d'"'"'attente CPU (r)=%.1f  bloques E/S (b)=%.1f  changements de contexte=%d/s\n", r/n, b/n, cs/n
          printf "  us=%.0f%%  sy=%.0f%%  idle=%.0f%%  attente disque (wa)=%.0f%%  vole (st)=%.0f%%\n", us/n, sy/n, id/n, wa/n, st/n }'
CPU_IDLE="$(echo "$VM" | awk '{s+=$15; n++} END {printf "%.0f", s/n}')"
CPU_WA="$(echo "$VM" | awk '{s+=$16; n++} END {printf "%.0f", s/n}')"
RUNQ="$(echo "$VM" | awk '{s+=$1; n++} END {printf "%.1f", s/n}')"

# PSI (Pressure Stall Information) : % du temps ou des taches ATTENDENT une
# ressource. Plus parlant que la charge : il dit si quelqu'un a souffert.
echo
echo "Pression (PSI, % du temps sur 60 s ou des taches attendent) :"
PSI_CPU=0; PSI_IO=0; PSI_MEM=0
for res in cpu io memory; do
    f="/proc/pressure/$res"
    if [ -r "$f" ]; then
        v="$(awk -F'avg60=' 'NR==1 {split($2,a," "); print a[1]}' "$f")"
        printf '  %-7s %s %%\n' "$res" "$v"
        case "$res" in cpu) PSI_CPU="$v";; io) PSI_IO="$v";; memory) PSI_MEM="$v";; esac
    else
        printf '  %-7s (PSI indisponible sur ce noyau)\n' "$res"
    fi
done

if command -v iostat >/dev/null 2>&1; then
    echo
    echo "Disques (iostat, 2e echantillon) :"
    iostat -dx 5 2 | awk '/^Device/ {blk++} blk==2'
fi

# ---------------------------------------------------------------------------
section "2. QUI CONSOMME (toute la machine, pas seulement la stack)"
# Le 2026-08-12, le coupable etait un NVR installe HORS compose : un `dc ps`
# ne l'aurait jamais montre. D'ou `docker stats` et `ps` sur tout l'hote.
echo "Conteneurs (tous, y compris hors stack) :"
printf '  %-34s %8s  %-22s %s\n' "CONTENEUR" "CPU" "MEMOIRE" "E/S DISQUE"
docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.BlockIO}}' 2>/dev/null \
    | sort -t$'\t' -k2 -rn | head -25 \
    | awk -F'\t' '{ printf "  %-34s %8s  %-22s %s\n", $1, $2, $3, $4 }'
echo
echo "Processus de l'hote les plus gourmands :"
ps -eo pid,user,pcpu,pmem,etime,comm --sort=-pcpu | head -15
echo
echo "Rappel : sur une machine saturee, un fort % dit qui RAME, pas forcement qui consomme."

# ---------------------------------------------------------------------------
section "3. JOBS - duree des tours (logs des ${HOURS} dernieres heures)"
LOGS="$(mktemp)"
trap 'rm -f "$LOGS"' EXIT
dc logs --since "${HOURS}h" --no-color \
    scheduler scheduler-heavy scheduler-ping-lr scheduler-poll-switch \
    scheduler-poll-af60 scheduler-poll-ltu scheduler-poll-airos \
    > "$LOGS" 2>/dev/null || true

# Ligne emise par @_timed_job : "JOB <nom> — tour terminé en <x> s"
printf "  %-42s %6s %9s %9s\n" "job" "tours" "moy (s)" "max (s)"
grep -oE 'JOB [a-z0-9_]+ .{1,3} tour termin.{1,2} en [0-9.]+ s' "$LOGS" \
    | awk '{ name=$2; d=$(NF-1); n[name]++; s[name]+=d; if (d>m[name]) m[name]=d }
           END { for (k in n) printf "  %-42s %6d %9.1f %9.1f\n", k, n[k], s[k]/n[k], m[k] }' \
    | sort -k4 -rn

echo
SKIPPED="$(grep -cE 'maximum number of running instances reached' "$LOGS" || true)"
MISSED="$(grep -cE 'was missed by' "$LOGS" || true)"
echo "Tours SAUTES (le precedent n'etait pas fini)  : $SKIPPED"
echo "Tours RATES  (le scheduler etait en retard)   : $MISSED"
if [ "${SKIPPED:-0}" -gt 0 ]; then
    echo "  detail par job :"
    # Les jobs portent un `name=` lisible ("airOS HTTP API poll (airMAX LR)") :
    # on prend tout jusqu'a " (trigger", pas un identifiant.
    grep -E 'maximum number of running instances reached' "$LOGS" \
        | sed -nE 's/.*Execution of job "(.+) \(trigger.*/\1/p' \
        | sort | uniq -c | sort -rn | head -10 | sed 's/^/    /'
fi

# La sonde LR separe deja le temps passe a attendre les RADIOS (phase 1, SSH)
# du temps passe a ecrire en BASE (phase 2) : c'est la mesure la plus directe
# de "equipements ou serveur ?".
echo
echo "Sonde LR - radios (phase 1) vs base (phase 2), derniers tours :"
grep -oE 'phase 1 \(SSH, [0-9]+ LR\) [0-9.]+ s \| phase 2 \(DB\) [0-9.]+ s' "$LOGS" | tail -n 5 | sed 's/^/  /'
PROBE_SPLIT="$(grep -oE 'phase 1 \(SSH, [0-9]+ LR\) [0-9.]+ s \| phase 2 \(DB\) [0-9.]+ s' "$LOGS" \
    | awk '{p1+=$6; p2+=$12; n++} END { if (n) printf "%.0f %.0f", p1/n, p2/n }')"

echo
echo "Faux 'down' rattrapes par le re-ping isole (rate-limit ICMP des radios) :"
grep -oE '[0-9]+/[0-9]+ suspect\(s\) répondent au ping isolé' "$LOGS" | tail -n 3 | sed 's/^/  /'

DEADLOCK_LOGS="$(grep -ciE 'deadlock detected' "$LOGS" || true)"
echo
echo "Interblocages vus par les jobs sur la fenetre : $DEADLOCK_LOGS"

# ---------------------------------------------------------------------------
section "4. POSTGRES"
echo "Connexions :"
q "SELECT state, count(*) FROM pg_stat_activity
   WHERE datname = current_database() GROUP BY 1 ORDER BY 2 DESC;"
q "SHOW max_connections;"

echo "Ce qu'attendent les requetes actives (10 echantillons, 1/s) :"
for _ in $(seq 10); do
    dc exec -T postgres psql -U "$PGUSER_" -d "$PGDB_" -X -A -t -c \
        "SELECT coalesce(wait_event_type,'CPU') || ':' || coalesce(wait_event,'-')
         FROM pg_stat_activity
         WHERE state = 'active' AND pid <> pg_backend_pid()
           AND datname = current_database();" 2>/dev/null
    sleep 1
done | sort | uniq -c | sort -rn | head -12 | sed 's/^/  /'
echo "  (IO:* = disque ; Lock:* = verrous entre jobs ; CPU:- = calcul pur)"

echo
echo "Requetes en cours depuis plus de 5 s :"
q "SELECT pid, date_trunc('second', now() - query_start) AS duree, state,
          wait_event_type, wait_event, left(regexp_replace(query, '\s+', ' ', 'g'), 90) AS requete
   FROM pg_stat_activity
   WHERE state <> 'idle' AND pid <> pg_backend_pid()
     AND now() - query_start > interval '5 seconds'
   ORDER BY 2 DESC LIMIT 10;"

echo "Statistiques de la base (depuis stats_reset) :"
q "SELECT deadlocks, temp_files, pg_size_pretty(temp_bytes) AS temp_ecrit,
          round(100.0 * blks_hit / nullif(blks_hit + blks_read, 0), 2) AS cache_hit_pct,
          xact_commit, xact_rollback, stats_reset
   FROM pg_stat_database WHERE datname = current_database();"

echo "Checkpoints (req >> timed = ecritures trop fortes pour la config) :"
q "SELECT checkpoints_timed, checkpoints_req, buffers_checkpoint, buffers_backend
   FROM pg_stat_bgwriter;"

echo "Plus grosses tables - bloat et autovacuum :"
q "SELECT relname, pg_size_pretty(pg_total_relation_size(relid)) AS taille,
          n_live_tup AS vivantes, n_dead_tup AS mortes,
          round(100.0 * n_dead_tup / nullif(n_live_tup + n_dead_tup, 0), 1) AS pct_mortes,
          date_trunc('minute', last_autovacuum) AS dernier_autovacuum
   FROM pg_stat_user_tables
   ORDER BY pg_total_relation_size(relid) DESC LIMIT 12;"
q "SHOW autovacuum;"
q "SELECT relname, reloptions FROM pg_class
   WHERE relname IN ('device_metrics','lr_metric_samples','traffic_dest_stats')
   ORDER BY 1;"

HAS_PGSS="$(dc exec -T postgres psql -U "$PGUSER_" -d "$PGDB_" -X -A -t -c \
    "SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements';" 2>/dev/null | tr -d '[:space:]')"
if [ "$HAS_PGSS" = "1" ]; then
    echo "Requetes les plus couteuses (temps cumule) :"
    q "SELECT calls, round(total_exec_time / 1000) AS total_s,
              round(mean_exec_time::numeric, 1) AS moy_ms,
              left(regexp_replace(query, '\s+', ' ', 'g'), 90) AS requete
       FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;"
else
    echo "(pg_stat_statements absent : pas de classement des requetes couteuses)"
fi

# ---------------------------------------------------------------------------
section "5. VERDICT"
gt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 > b + 0) }'; }
lt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 < b + 0) }'; }

VERDICT=0
echo "Mesures : idle CPU ${CPU_IDLE}% | file CPU ${RUNQ} pour ${NCPU} coeurs | attente disque ${CPU_WA}%"
echo "          PSI cpu ${PSI_CPU}% | PSI io ${PSI_IO}% | PSI memoire ${PSI_MEM}%"
echo "          tours sautes ${SKIPPED:-0} | tours rates ${MISSED:-0}"
echo

if lt "$CPU_IDLE" 15 || gt "$PSI_CPU" 25 || gt "$RUNQ" "$((NCPU * 2))"; then
    echo "[CPU] SATURE. Un serveur supplementaire AIDERAIT - mais regarder d'abord la"
    echo "      section 2 : si le gros consommateur est etranger a la supervision,"
    echo "      le retirer suffit (cas du NVR shinobi, 2026-08-12)."
    VERDICT=1
fi
if gt "$CPU_WA" 10 || gt "$PSI_IO" 20; then
    echo "[DISQUE] Postgres attend ses E/S. Priorite : le bloat (section 4, colonne"
    echo "      pct_mortes) puis un disque plus rapide. Un serveur pour la BASE"
    echo "      aiderait plus qu'un serveur pour les jobs."
    VERDICT=1
fi
if gt "$PSI_MEM" 10; then
    echo "[MEMOIRE] Pression memoire : la RAM manque (voir free -h)."
    VERDICT=1
fi
if [ -n "$PROBE_SPLIT" ]; then
    P1="${PROBE_SPLIT% *}"; P2="${PROBE_SPLIT#* }"
    echo "[SONDE LR] en moyenne ${P1} s a attendre les radios, ${P2} s en base."
    if gt "$P1" "$((P2 * 3))"; then
        echo "      -> domine par le TERRAIN (SSH vers les radios) : un serveur plus"
        echo "         puissant ne raccourcira pas ce tour."
    elif gt "$P2" "$P1"; then
        echo "      -> domine par la BASE : piste = ecritures / verrous / bloat."
    fi
fi
if [ "$VERDICT" -eq 0 ]; then
    echo "[OK] La machine a de la marge (CPU, disque, memoire)."
    echo "     Si des jobs sont lents ou sautent des tours, le goulot n'est pas le"
    echo "     materiel : c'est l'attente des equipements ou le code du job."
    echo "     Un serveur dedie n'apporterait PAS de gain visible."
fi
echo
echo "Rapport enregistre : $REPORT"
}

main 2>&1 | tee "$REPORT"
