"""Rapport PDF des coupures de site sur une TRANCHE HORAIRE, nuit après nuit.

Répond à « quels sites sont tombés entre 00 h et 08 h, chaque nuit, du 1er au
15 ? » — la tranche est réglable (ex. 22 h → 06 h, qui enjambe minuit).

Mêmes règles que le Journal des coupures (`network_uptime_service`) :

* **Une panne de SITE est une coupure de son SWITCH** (règle de
  `fn_site_outage_summary`) ; un site à deux switches vaut le PIRE des deux
  pour la disponibilité. Les autres équipements d'infra tombés sont listés en
  annexe, **jamais tus**, mais ne comptent pas dans les chiffres des sites.
* Seuls les incidents de disponibilité existent encore après résolution
  (`AVAILABILITY_ALERT_TYPES`) — ce sont les seuls que ce rapport peut lire.
  Les abonnés (LR) n'en ouvrent aucun : ils sont absents par nature.
* Deux coupures séparées de moins de `merge_gap_seconds` forment UN épisode.

⚠️ **Règle de comptage (décision opérateur du 2026-09-24)** : une coupure n'est
comptée que si elle **COMMENCE dans la tranche**, et sa durée comptée s'arrête à
la fin de la tranche. Une coupure commencée à 23 h 30 n'est donc PAS comptée
dans la tranche 00 h → 08 h, même si elle dure jusqu'à 3 h.

Corollaire à ne pas « corriger » : un site coupé AVANT l'ouverture et encore
coupé à l'ouverture n'a rien de compté — mais il est **signalé** dans le détail
de la nuit (« déjà coupé à l'ouverture de la tranche, non compté »). Le taire
ferait lire « aucune coupure » sur une nuit où le site était mort de bout en
bout — un négatif faux.

⚠️ La fusion anti-flapping se fait **à l'intérieur de la tranche**, sur les
seules coupures qui y commencent : fusionner d'abord sur toute la chronologie
avalerait une vraie coupure de 00 h 02 dans un épisode ouvert à 23 h 58, qui
serait ensuite écarté.

Heures en **UTC** : la Mauritanie est à UTC+0, heure locale = UTC.
"""

from __future__ import annotations

import datetime
import os
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.alert_constants import AVAILABILITY_ALERT_TYPES
from app.models.device import Device
from app.models.incident import Incident

DEFAULT_MERGE_GAP_SECONDS = 300
# Garde-fou de taille : au-delà, le PDF devient un annuaire que personne ne lit
# et la requête parcourt des mois d'incidents pour rien.
MAX_REPORT_DAYS = 92

_SWITCH_TYPE = "uisp_switch"
_TYPE_LABELS = {
    "uisp_switch": "Switch",
    "ltu_rocket": "Rocket LTU",
    "airmax_rocket": "Rocket airMAX",
    "uisp_power": "UISP Power",
    "airfiber": "AF60",
    "ptp_litebeam": "PTP LiteBeam",
}


# --------------------------------------------------------------- les tranches
@dataclass(frozen=True)
class Night:
    """Une occurrence de la tranche horaire, rattachée au jour où elle s'OUVRE."""

    day: datetime.date
    start: datetime.datetime
    end: datetime.datetime  # borne de fin, déjà ramenée à « maintenant » si besoin
    complete: bool  # False = tranche en cours, pas encore terminée

    @property
    def seconds(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds())


def build_nights(
    first_day: datetime.date,
    last_day: datetime.date,
    from_hour: int,
    to_hour: int,
    now: datetime.datetime,
) -> list[Night]:
    """Une tranche par jour de [first_day, last_day].

    `to_hour <= from_hour` = la tranche enjambe minuit (22 h → 06 h s'ouvre le
    jour J et se ferme le jour J+1). Une tranche pas encore ouverte est omise ;
    une tranche en cours est ramenée à `now` et marquée incomplète — sinon sa
    disponibilité serait calculée sur des heures qui n'ont pas eu lieu.
    """
    nights: list[Night] = []
    day = first_day
    while day <= last_day:
        start = datetime.datetime.combine(day, datetime.time(from_hour), datetime.UTC)
        end_day = day if to_hour > from_hour else day + datetime.timedelta(days=1)
        end = datetime.datetime.combine(end_day, datetime.time(to_hour), datetime.UTC)
        if start < now:
            nights.append(Night(day=day, start=start, end=min(end, now), complete=end <= now))
        day += datetime.timedelta(days=1)
    return nights


# --------------------------------------------------------------- les coupures
@dataclass
class RawOutage:
    """Un incident de disponibilité brut (avant fusion)."""

    device_id: int
    started_at: datetime.datetime
    ended_at: datetime.datetime | None  # None = toujours ouvert


@dataclass
class WindowOutage:
    """Une coupure COMPTÉE dans une tranche (après fusion)."""

    device_id: int
    started_at: datetime.datetime
    ended_at: datetime.datetime | None  # fin réelle, peut dépasser la tranche
    counted_seconds: float  # part comptée : du début à min(fin réelle, fin de tranche)
    flap_count: int

    @property
    def is_ongoing(self) -> bool:
        return self.ended_at is None


def outages_in_night(
    raws: list[RawOutage],
    night: Night,
    now: datetime.datetime,
    merge_gap_seconds: int = DEFAULT_MERGE_GAP_SECONDS,
) -> list[WindowOutage]:
    """Les coupures d'UN équipement qui commencent dans `night`, fusionnées.

    `raws` : les incidents de cet équipement, dans n'importe quel ordre.
    """
    inside = sorted(
        (r for r in raws if night.start <= r.started_at < night.end),
        key=lambda r: r.started_at,
    )
    merged: list[WindowOutage] = []
    cur_start: datetime.datetime | None = None
    cur_end: datetime.datetime | None = None  # fin réelle (None = ouvert)
    cur_real_end = night.start
    flaps = 0

    def _flush() -> None:
        real_end = cur_end or now
        counted = max(0.0, (min(real_end, night.end) - cur_start).total_seconds())
        merged.append(
            WindowOutage(
                device_id=inside[0].device_id,
                started_at=cur_start,
                ended_at=cur_end,
                counted_seconds=counted,
                flap_count=flaps,
            )
        )

    for r in inside:
        r_real_end = r.ended_at or now
        if (
            cur_start is not None
            and (r.started_at - cur_real_end).total_seconds() < merge_gap_seconds
        ):
            flaps += 1
            if r_real_end >= cur_real_end:
                cur_end, cur_real_end = r.ended_at, r_real_end
            continue
        if cur_start is not None:
            _flush()
        cur_start, cur_end, cur_real_end, flaps = r.started_at, r.ended_at, r_real_end, 1
    if cur_start is not None:
        _flush()
    return merged


def already_down_at_opening(
    raws: list[RawOutage], night: Night, now: datetime.datetime
) -> datetime.datetime | None:
    """Début de la coupure qui tenait DÉJÀ l'équipement à l'ouverture, sinon None.

    Pas comptée (règle opérateur), mais signalée : voir la docstring du module.
    """
    starts = [
        r.started_at
        for r in raws
        if r.started_at < night.start and (r.ended_at or now) > night.start
    ]
    return min(starts) if starts else None


# --------------------------------------------------------------- l'agrégat
@dataclass
class NightEntry:
    """Une ligne du détail d'une nuit."""

    site: str
    device_name: str
    device_type: str
    outage: WindowOutage | None  # None = ligne « déjà coupé à l'ouverture »
    down_since: datetime.datetime | None = None


@dataclass
class SiteSummary:
    site: str
    switch_count: int
    episodes: int = 0
    downtime_seconds: float = 0.0
    nights_hit: int = 0
    availability_pct: float = 100.0


@dataclass
class NightReport:
    first_day: datetime.date
    last_day: datetime.date
    from_hour: int
    to_hour: int
    generated_at: datetime.datetime
    nights: list[Night]
    sites: list[SiteSummary]
    # Par nuit (index de `nights`) : coupures des switches, puis autres équipements.
    switch_entries: list[list[NightEntry]] = field(default_factory=list)
    other_entries: list[list[NightEntry]] = field(default_factory=list)

    @property
    def total_episodes(self) -> int:
        return sum(s.episodes for s in self.sites)

    @property
    def total_downtime_seconds(self) -> float:
        return sum(s.downtime_seconds for s in self.sites)


def _site_name(site: str | None) -> str:
    return (site or "").strip() or "Sans site"


def _entry_order(e: NightEntry) -> tuple:
    # La ligne « déjà coupé à l'ouverture » passe avant les coupures du même site.
    return (e.site, e.device_name, e.outage.started_at if e.outage else e.down_since)


def assemble_report(
    devices: list[Device],
    raws_by_device: dict[int, list[RawOutage]],
    nights: list[Night],
    *,
    first_day: datetime.date,
    last_day: datetime.date,
    from_hour: int,
    to_hour: int,
    now: datetime.datetime,
    merge_gap_seconds: int = DEFAULT_MERGE_GAP_SECONDS,
) -> NightReport:
    """Croise équipements × incidents × tranches. Pur : aucune I/O."""
    switches = [d for d in devices if d.device_type == _SWITCH_TYPE]
    by_id = {d.id: d for d in devices}

    # TOUS les sites qui portent un switch, même jamais coupés : un site absent
    # du tableau se lirait « pas supervisé », pas « aucune coupure ».
    site_switches: dict[str, list[Device]] = defaultdict(list)
    for sw in switches:
        site_switches[_site_name(sw.site)].append(sw)
    summaries = {
        name: SiteSummary(site=name, switch_count=len(sws)) for name, sws in site_switches.items()
    }
    total_window = sum(n.seconds for n in nights) or 1.0
    per_switch_down: dict[int, float] = defaultdict(float)
    site_nights_hit: dict[str, set[int]] = defaultdict(set)

    switch_entries: list[list[NightEntry]] = []
    other_entries: list[list[NightEntry]] = []
    for idx, night in enumerate(nights):
        sw_rows: list[NightEntry] = []
        other_rows: list[NightEntry] = []
        for device_id, raws in raws_by_device.items():
            dev = by_id.get(device_id)
            if dev is None:
                continue
            is_switch = dev.device_type == _SWITCH_TYPE
            site = _site_name(dev.site)
            rows = sw_rows if is_switch else other_rows
            outs = outages_in_night(raws, night, now, merge_gap_seconds)
            for out in outs:
                rows.append(NightEntry(site, dev.name, dev.device_type or "", out))
            if is_switch and outs:
                summary = summaries[site]
                summary.episodes += len(outs)
                secs = sum(o.counted_seconds for o in outs)
                summary.downtime_seconds += secs
                per_switch_down[device_id] += secs
                site_nights_hit[site].add(idx)
            if not outs:
                since = already_down_at_opening(raws, night, now)
                if since is not None:
                    rows.append(
                        NightEntry(site, dev.name, dev.device_type or "", None, down_since=since)
                    )
        switch_entries.append(sorted(sw_rows, key=_entry_order))
        other_entries.append(sorted(other_rows, key=_entry_order))

    for name, summary in summaries.items():
        summary.nights_hit = len(site_nights_hit.get(name, ()))
        worst = max((per_switch_down.get(sw.id, 0.0) for sw in site_switches[name]), default=0.0)
        summary.availability_pct = max(0.0, min(100.0, 100.0 * (1 - worst / total_window)))

    ordered = sorted(summaries.values(), key=lambda s: (-s.downtime_seconds, -s.episodes, s.site))
    return NightReport(
        first_day=first_day,
        last_day=last_day,
        from_hour=from_hour,
        to_hour=to_hour,
        generated_at=now,
        nights=nights,
        sites=ordered,
        switch_entries=switch_entries,
        other_entries=other_entries,
    )


async def build_night_report(
    db: AsyncSession,
    first_day: datetime.date,
    last_day: datetime.date,
    from_hour: int,
    to_hour: int,
    merge_gap_seconds: int = DEFAULT_MERGE_GAP_SECONDS,
    now: datetime.datetime | None = None,
) -> NightReport:
    now = now or datetime.datetime.now(datetime.UTC)
    nights = build_nights(first_day, last_day, from_hour, to_hour, now)

    raws_by_device: dict[int, list[RawOutage]] = defaultdict(list)
    if nights:
        lo, hi = nights[0].start, nights[-1].end
        q = select(Incident.device_id, Incident.detected_at, Incident.resolved_at).where(
            Incident.alert_type.in_(AVAILABILITY_ALERT_TYPES),
            Incident.detected_at < hi,
            or_(Incident.resolved_at.is_(None), Incident.resolved_at > lo),
        )
        for device_id, detected_at, resolved_at in (await db.execute(q)).all():
            raws_by_device[device_id].append(RawOutage(device_id, detected_at, resolved_at))

    # Les switches TOUS (pour lister chaque site), plus les équipements tombés.
    dev_q = select(Device).where(
        or_(Device.device_type == _SWITCH_TYPE, Device.id.in_(list(raws_by_device) or [-1]))
    )
    devices = [d for d in (await db.execute(dev_q)).scalars().all() if d.device_type != "lr"]

    return assemble_report(
        devices,
        raws_by_device,
        nights,
        first_day=first_day,
        last_day=last_day,
        from_hour=from_hour,
        to_hour=to_hour,
        now=now,
        merge_gap_seconds=merge_gap_seconds,
    )


# --------------------------------------------------------------- le PDF
_FONT_PATHS = (
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
)


def fmt_duration(secs: float) -> str:
    """Même barème que la page Journal des coupures (« 2h 08min »)."""
    if secs < 60:
        return f"{round(secs)}s"
    if secs < 3600:
        return f"{int(secs // 60)} min"
    h = int(secs // 3600)
    m = round((secs % 3600) / 60)
    if m == 60:
        h, m = h + 1, 0
    return f"{h}h" if m == 0 else f"{h}h {m:02d}min"


def window_label(from_hour: int, to_hour: int) -> str:
    return f"{from_hour:02d}:00 - {to_hour:02d}:00"


def night_label(night: Night, crosses_midnight: bool) -> str:
    d = night.day
    if crosses_midnight:
        nxt = d + datetime.timedelta(days=1)
        return f"Nuit du {d:%d/%m/%Y} au {nxt:%d/%m/%Y}"
    return f"{d:%d/%m/%Y}"


def render_pdf(report: NightReport) -> bytes:
    from fpdf import FPDF  # import local : seul ce chemin a besoin de la dépendance

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    # Police TrueType si disponible (accents français) ; sinon Helvetica + latin-1.
    family = "Helvetica"
    for regular, bold in _FONT_PATHS:
        if os.path.isfile(regular) and os.path.isfile(bold):
            pdf.add_font("Body", "", regular)
            pdf.add_font("Body", "B", bold)
            family = "Body"
            break
    unicode_ok = family == "Body"

    def txt(s: str) -> str:
        return s if unicode_ok else s.encode("latin-1", "replace").decode("latin-1")

    def font(size: float, bold: bool = False) -> None:
        pdf.set_font(family, "B" if bold else "", size)

    def cell(w: float, h: float, s: str, **kw) -> None:
        pdf.cell(w, h, txt(s), **kw)

    def clip(s: str, limit: int) -> str:
        s = (s or "").strip()
        if len(s) <= limit:
            return s
        return s[: limit - 1] + ("…" if unicode_ok else ".")

    def table_header(cols: list[tuple[str, float]]) -> None:
        font(8.5, True)
        pdf.set_fill_color(230, 234, 240)
        for label, w in cols:
            cell(w, 6.5, label, border=1, fill=True, align="C")
        pdf.ln()
        font(8.5)

    crosses = report.to_hour <= report.from_hour
    win = window_label(report.from_hour, report.to_hour)
    pdf.add_page()

    font(16, True)
    cell(0, 9, "Coupures des sites par tranche horaire", new_x="LMARGIN", new_y="NEXT")
    font(10)
    cell(
        0,
        5.5,
        f"Tranche : {win} (heure UTC = heure de Mauritanie)"
        + (" - enjambe minuit" if crosses else ""),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    cell(
        0,
        5.5,
        f"Période : du {report.first_day:%d/%m/%Y} au {report.last_day:%d/%m/%Y}"
        f" - {len(report.nights)} tranche(s) analysée(s)",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    cell(
        0,
        5.5,
        f"Généré le {report.generated_at:%d/%m/%Y à %H:%M} UTC",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(1.5)
    font(8.5)
    pdf.set_text_color(90, 90, 90)
    pdf.multi_cell(
        0,
        4.3,
        txt(
            "Une panne de site est une coupure de son switch. Seules les coupures qui "
            "COMMENCENT dans la tranche sont comptées, et leur durée s'arrête à la fin de "
            "la tranche. Deux coupures séparées de moins de 5 minutes comptent pour une "
            "seule (instabilité). Un site déjà coupé à l'ouverture de la tranche est "
            "signalé en gris dans le détail (« coupé depuis … »), sans être compté."
        ),
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

    if not report.nights:
        font(11)
        cell(0, 8, "Aucune tranche écoulée sur cette période.", new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output())

    # ---- Résumé
    touched = sum(1 for s in report.sites if s.episodes)
    font(12, True)
    cell(0, 8, "Résumé", new_x="LMARGIN", new_y="NEXT")
    font(10)
    cell(
        0,
        5.5,
        f"Sites touchés : {touched} / {len(report.sites)}    "
        f"Pannes : {report.total_episodes}    "
        f"Temps de coupure cumulé : {fmt_duration(report.total_downtime_seconds)}",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(2)

    cols = [
        ("Site", 58),
        ("Pannes", 22),
        ("Temps coupé", 30),
        ("Tranches touchées", 36),
        ("Disponibilité", 34),
    ]
    table_header(cols)
    for s in report.sites:
        if s.episodes:
            font(8.5, True)
        cell(58, 6, clip(s.site, 34), border=1)
        cell(22, 6, str(s.episodes), border=1, align="C")
        cell(30, 6, fmt_duration(s.downtime_seconds) if s.episodes else "-", border=1, align="C")
        cell(36, 6, f"{s.nights_hit} / {len(report.nights)}", border=1, align="C")
        if s.availability_pct < 99:
            pdf.set_text_color(190, 60, 20)
        cell(34, 6, f"{s.availability_pct:.2f} %", border=1, align="C")
        pdf.set_text_color(0, 0, 0)
        font(8.5)
        pdf.ln()

    # ---- Détail tranche par tranche
    pdf.ln(4)
    font(12, True)
    cell(0, 8, "Détail tranche par tranche", new_x="LMARGIN", new_y="NEXT")
    # Largeurs en mm (somme = 190, la largeur utile d'un A4 à marges de 10 mm).
    det_cols = [
        ("Site", 25),
        ("Switch", 41),
        ("Tombé", 23),
        ("Revenu", 25),
        ("Durée comptée", 24),
        ("Remarque", 52),
    ]
    widths = [w for _, w in det_cols]

    def row(values: list[str], colors: dict[int, tuple[int, int, int]] | None = None) -> None:
        limits = (15, 25, 14, 16, 14, 44)
        for i, (v, w) in enumerate(zip(values, widths, strict=True)):
            if colors and i in colors:
                pdf.set_text_color(*colors[i])
            if i == 5:
                font(7.5)  # la remarque est la plus longue : un cran plus petit
            cell(w, 6, clip(v, limits[i]), border=1, align="L" if i in (0, 1, 5) else "C")
            if i == 5:
                font(8.5)
            if colors and i in colors:
                pdf.set_text_color(0, 0, 0)
        pdf.ln()

    def entry_rows(entries: list[NightEntry], night: Night, device_col: str) -> None:
        cols_here = [(device_col if i == 1 else lbl, w) for i, (lbl, w) in enumerate(det_cols)]
        table_header(cols_here)
        for e in entries:
            if pdf.will_page_break(6):
                pdf.add_page()
                table_header(cols_here)
            name = e.device_name
            if device_col != "Switch":
                name = f"{name} ({_TYPE_LABELS.get(e.device_type, e.device_type)})"
            if e.outage is None:
                grey = (120, 120, 120)
                row(
                    [e.site, name, "-", "-", "-", f"coupé depuis {e.down_since:%d/%m %H:%M}"],
                    dict.fromkeys(range(6), grey),
                )
                continue
            o = e.outage
            fell = f"{o.started_at:%H:%M}"
            # Tranche qui enjambe minuit : l'heure seule serait ambiguë.
            if o.started_at.date() != night.day:
                fell += f" le {o.started_at:%d/%m}"
            if o.ended_at is None:
                back, note = "toujours coupé", ""
            else:
                back = f"{o.ended_at:%H:%M}"
                if o.ended_at.date() != o.started_at.date():
                    back += f" le {o.ended_at:%d/%m}"
                note = "revenu hors tranche" if o.ended_at > night.end else ""
            if o.flap_count > 1:
                note = (note + " · " if note else "") + f"instable, {o.flap_count} cycles"
            row(
                [e.site, name, fell, back, fmt_duration(o.counted_seconds), note],
                {3: (200, 30, 30)} if o.ended_at is None else None,
            )

    for idx, night in enumerate(report.nights):
        entries = report.switch_entries[idx]
        counted = [e for e in entries if e.outage is not None]
        if pdf.will_page_break(20):
            pdf.add_page()
        pdf.ln(1.5)
        font(10, True)
        head = night_label(night, crosses)
        if not night.complete:
            head += f" (tranche en cours, jusqu'à {night.end:%H:%M})"
        if counted:
            head += (
                f" - {len(counted)} panne(s), "
                f"{fmt_duration(sum(e.outage.counted_seconds for e in counted))}"
            )
        cell(0, 6.5, head, new_x="LMARGIN", new_y="NEXT")
        if not entries:
            font(9)
            pdf.set_text_color(40, 130, 70)
            cell(0, 5.5, "Aucune coupure de site.", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
            continue
        entry_rows(entries, night, "Switch")

    # ---- Annexe : autres équipements (hors décompte des sites)
    if any(report.other_entries):
        pdf.add_page()
        font(12, True)
        cell(
            0,
            8,
            "Annexe - autres équipements coupés dans la tranche",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        font(8.5)
        pdf.set_text_color(90, 90, 90)
        pdf.multi_cell(
            0,
            4.3,
            txt(
                "Rockets, UISP Power, AF60... tombés alors que le switch de leur site "
                "répondait. Hors du décompte des sites ci-dessus, mais nommés : les taire "
                "ferait lire « aucune panne » là où un secteur était hors service."
            ),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_text_color(0, 0, 0)
        for idx, night in enumerate(report.nights):
            entries = report.other_entries[idx]
            if not entries:
                continue
            if pdf.will_page_break(20):
                pdf.add_page()
            pdf.ln(1.5)
            font(10, True)
            cell(0, 6.5, night_label(night, crosses), new_x="LMARGIN", new_y="NEXT")
            entry_rows(entries, night, "Équipement")

    return bytes(pdf.output())
