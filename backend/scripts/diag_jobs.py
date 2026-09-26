"""Santé des jobs planifiés — chaque job contre SON intervalle. LECTURE SEULE.

Répond à « un job dépasse-t-il, saute-t-il des tours, touche-t-il sa deadline,
est-il bloqué ? ». Lit les logs des conteneurs scheduler sur l'entrée standard,
rien d'autre : aucune requête en base, aucun équipement contacté.

Usage (sur le serveur, depuis la racine du projet, alias `dc` habituel) :

    dc logs --since 6h --no-color scheduler scheduler-heavy scheduler-ping-lr \
        scheduler-poll-switch scheduler-poll-af60 scheduler-poll-ltu scheduler-poll-airos \
      | dc exec -T backend python scripts/diag_jobs.py

Les intervalles ne sont PAS recopiés ici : le script appelle `register_jobs` sur
un planificateur jamais démarré et lit les déclencheurs qu'il a posés — donc les
mêmes réglages (.env) que les conteneurs de prod. Une copie en dur divergerait au
premier réglage modifié, et c'est justement le rapport durée / intervalle qu'on
veut juger.

Ce qu'il signale :
  * p95 au-dessus de l'intervalle  → le job n'a plus de marge, il va sauter ;
  * tours SAUTÉS (max_instances)   → le précédent n'était pas fini ;
  * tours RATÉS (misfire)          → la boucle du scheduler était en retard ;
  * DEADLINE atteinte              → une partie du parc n'a pas été relevée ;
  * job BLOQUÉ                     → démarré, jamais terminé, depuis longtemps ;
  * job MUET                       → plus aucun tour terminé depuis 3 intervalles.
"""
from __future__ import annotations

import datetime
import os
import re
import statistics
import sys
from collections import defaultdict

# Le backend n'a pas de groupe, mais on force "all" : sans ça, lancé depuis un
# conteneur scheduler, l'élagage ne laisserait que les jobs de CE conteneur.
os.environ["SCHEDULER_GROUP"] = "all"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # noqa: E402
from apscheduler.triggers.interval import IntervalTrigger  # noqa: E402

from app.tasks import jobs as jobs_module  # noqa: E402

# « scheduler-heavy-1  | 2026-09-26 10:00:00 | INFO     | app.tasks.jobs | msg »
_LINE = re.compile(
    r"^(?P<ctr>\S+)\s+\|\s+(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| (?P<lvl>\w+)\s*\| [^|]+? \| (?P<msg>.*)$"
)
_DONE = re.compile(r"JOB (\w+) .{1,3} tour termin.{1,2} en ([0-9.]+) s")
_DB_RETRY = re.compile(r"JOB (\w+) .{1,3} conflit DB transitoire")
_TRIGGER_NAME = re.compile(r'job "(.+?) \(trigger:', re.IGNORECASE)
_FRACTION = re.compile(r"(\d+)/(\d+)")

# Préfixe du message de deadline → job. Ces messages sont écrits à la main dans
# jobs.py (pas de forme commune) : un préfixe renommé là-bas se tait ici, d'où
# le rappel dans la sortie quand aucune deadline n'est vue.
_DEADLINE_PREFIX = {
    "Power poll : deadline": "power_poll_job",
    "LTU API poll : délai global": "ltu_api_poll_job",
    "airOS API poll : deadline": "airos_api_poll_job",
    "AF60 API poll : deadline": "af60_api_poll_job",
    "lr_internet_probe : délai global": "lr_internet_probe_job",
}


def _job_table() -> dict[str, dict]:
    sched = AsyncIOScheduler()
    jobs_module.register_jobs(sched)
    table: dict[str, dict] = {}
    for job in sched.get_jobs():
        func = getattr(job.func, "__name__", str(job.func))
        interval = (
            job.trigger.interval.total_seconds()
            if isinstance(job.trigger, IntervalTrigger)
            else None  # cron quotidien : pas de rapport durée / intervalle
        )
        table[func] = {"name": job.name, "interval": interval}
    return table


def _ts(s: str) -> datetime.datetime:
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def main() -> int:
    table = _job_table()
    by_name = {meta["name"]: func for func, meta in table.items()}

    durations: dict[str, list[float]] = defaultdict(list)
    last_done: dict[str, datetime.datetime] = {}
    last_start: dict[str, datetime.datetime] = {}
    container: dict[str, str] = {}
    skipped: dict[str, int] = defaultdict(int)
    missed: dict[str, int] = defaultdict(int)
    errors: dict[str, int] = defaultdict(int)
    db_retries: dict[str, int] = defaultdict(int)
    deadlines: dict[str, list[tuple[int, int]]] = defaultdict(list)
    first_ts = last_ts = None
    lines = 0

    for raw in sys.stdin:
        m = _LINE.match(raw.rstrip("\n"))
        if not m:
            continue
        lines += 1
        ts, msg, ctr = _ts(m["ts"]), m["msg"], m["ctr"]
        first_ts = ts if first_ts is None or ts < first_ts else first_ts
        last_ts = ts if last_ts is None or ts > last_ts else last_ts

        if d := _DONE.search(msg):
            func = d[1]
            durations[func].append(float(d[2]))
            last_done[func] = ts
            container[func] = ctr
            continue
        if d := _DB_RETRY.search(msg):
            db_retries[d[1]] += 1
            continue
        for prefix, func in _DEADLINE_PREFIX.items():
            if prefix in msg:
                f = _FRACTION.search(msg)
                deadlines[func].append((int(f[1]), int(f[2])) if f else (0, 0))
                break
        else:
            t = _TRIGGER_NAME.search(msg)
            if not t or t[1] not in by_name:
                continue
            func = by_name[t[1]]
            if msg.startswith("Running job"):
                last_start[func] = ts
                container.setdefault(func, ctr)
            elif "maximum number of running instances" in msg:
                skipped[func] += 1
            elif "was missed by" in msg:
                missed[func] += 1
            elif "raised an exception" in msg:
                errors[func] += 1

    if not lines:
        print("Aucune ligne de log reconnue sur l'entrée standard (voir l'usage en tête du script).")
        return 2

    now = datetime.datetime.now()
    window = (last_ts - first_ts).total_seconds() if first_ts and last_ts else 0
    print(f"Fenêtre lue : {first_ts} → {last_ts}  ({window / 3600:.1f} h, {lines} lignes)")
    print(f"Heure du conteneur : {now:%Y-%m-%d %H:%M:%S}\n")

    hdr = (
        f"{'job':<30} {'conteneur':<22} {'interv.':>7} {'tours':>6} {'attendus':>8} "
        f"{'moy':>7} {'p95':>7} {'max':>7} {'>interv':>8} {'sautés':>6} {'ratés':>5} "
        f"{'deadline':>8} {'erreurs':>7} {'rejeuDB':>7} {'dernier':>8}"
    )
    print(hdr)
    print("-" * len(hdr))

    problems: list[str] = []
    for func in sorted(table, key=lambda f: (table[f]["interval"] is None, f)):
        interval = table[func]["interval"]
        ds = durations.get(func, [])
        seen = bool(ds) or func in last_start or func in skipped
        if not seen:
            continue  # job d'un conteneur non lu, ou désactivé
        n = len(ds)
        avg = statistics.fmean(ds) if ds else None
        p95 = sorted(ds)[max(0, int(round(0.95 * n)) - 1)] if ds else None
        mx = max(ds) if ds else None
        over = sum(1 for d in ds if interval and d > interval)
        expected = int(window // interval) if interval and window else None
        age = (now - last_done[func]).total_seconds() if func in last_done else None
        dl = deadlines.get(func, [])

        def f(v):
            return "-" if v is None else f"{v:.1f}"

        print(
            f"{func:<30} {container.get(func, '?')[:22]:<22} "
            f"{('cron' if interval is None else f'{interval:.0f}s'):>7} {n:>6} "
            f"{('-' if expected is None else expected):>8} {f(avg):>7} {f(p95):>7} {f(mx):>7} "
            f"{(f'{over} ({100 * over / n:.0f}%)' if interval and n else '-'):>8} "
            f"{skipped.get(func, 0):>6} {missed.get(func, 0):>5} {len(dl):>8} "
            f"{errors.get(func, 0):>7} {db_retries.get(func, 0):>7} {_fmt_age(age):>8}"
        )

        if interval:
            if p95 is not None and p95 > interval:
                problems.append(
                    f"[DÉPASSE]  {func} : p95 {p95:.0f} s > intervalle {interval:.0f} s "
                    f"({over}/{n} tours trop longs) — plus de marge, il saute des tours."
                )
            elif p95 is not None and p95 > 0.8 * interval:
                problems.append(
                    f"[LIMITE]   {func} : p95 {p95:.0f} s = {100 * p95 / interval:.0f} % de "
                    f"l'intervalle ({interval:.0f} s) — la moindre lenteur le fera sauter."
                )
            started = last_start.get(func)
            done = last_done.get(func)
            if started and (done is None or started > done):
                running = (now - started).total_seconds()
                if running > 3 * interval:
                    problems.append(
                        f"[BLOQUÉ]   {func} : tour démarré il y a {_fmt_age(running)} et "
                        f"jamais terminé (intervalle {interval:.0f} s)."
                    )
            elif age is not None and age > 3 * interval:
                problems.append(
                    f"[MUET]     {func} : dernier tour terminé il y a {_fmt_age(age)} "
                    f"(intervalle {interval:.0f} s) — conteneur arrêté ou job retiré ?"
                )
            if expected and n < 0.8 * expected:
                problems.append(
                    f"[TROUS]    {func} : {n} tours pour {expected} attendus "
                    f"({100 * n / expected:.0f} %) — sautés, ratés ou redémarrages."
                )
        if skipped.get(func):
            problems.append(f"[SAUTÉS]   {func} : {skipped[func]} tour(s) sauté(s) (le précédent n'était pas fini).")
        if missed.get(func):
            problems.append(f"[RATÉS]    {func} : {missed[func]} tour(s) raté(s) (boucle du scheduler en retard).")
        if dl:
            worst = min(dl, key=lambda x: x[0] / x[1] if x[1] else 1)
            problems.append(
                f"[DEADLINE] {func} : délai global atteint {len(dl)} fois — pire tour : "
                f"{worst[0]}/{worst[1]} équipements relevés, le reste attend le cycle suivant."
            )
        if errors.get(func):
            problems.append(f"[ERREUR]   {func} : {errors[func]} tour(s) terminé(s) en exception.")
        if db_retries.get(func):
            problems.append(
                f"[DB]       {func} : {db_retries[func]} rejeu(x) sur interblocage "
                f"(rattrapés, mais chaque rejeu rallonge le tour)."
            )

    unseen = [
        f for f, meta in table.items()
        if meta["interval"] and meta["interval"] <= window
        and f in jobs_module.__dict__ and f not in durations
        and f not in last_start and getattr(jobs_module.__dict__[f], "__wrapped__", None)
    ]
    print()
    if problems:
        print("À REGARDER :")
        for p in problems:
            print("  " + p)
    else:
        print("[OK] Aucun job ne dépasse son intervalle, ne saute de tour ni ne touche sa deadline.")
    if unseen:
        print("\nJobs chronométrés ABSENTS des logs lus (conteneur non passé au script, ou job arrêté) :")
        for f in sorted(unseen):
            print(f"  {f}")
    if not deadlines:
        print(
            "\n(aucune deadline vue — si un message de deadline a été reformulé dans jobs.py, "
            "mettre à jour _DEADLINE_PREFIX ici)"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
