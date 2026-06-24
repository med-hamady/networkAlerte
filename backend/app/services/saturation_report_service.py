"""Daily saturated-Rockets report — build a PDF of overloaded base stations.

A base-station Rocket is **saturated** when its number of installed (provisioned)
clients has reached or passed its capacity ceiling — i.e. ``current_clients >=
max_clients`` — which is exactly the condition that opens the
``rocket_client_overload`` incident. We reuse :func:`network_capacity_service.
get_network_capacity` as the single source of truth for both numbers (installed
roster from ``lrs``, ceiling from the ``rocket_client_overload`` formula /
override), then render the saturated subset as a one-page-or-more PDF table for
the daily WhatsApp document report.

Rockets whose channel width is unknown have no computable ceiling
(``max_clients`` is ``None``) and are excluded — they can't be judged saturated.
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import network_capacity_service

logger = logging.getLogger(__name__)

_FAMILY_LABEL = {"ltu": "LTU", "airmax": "airMAX"}


def _collect_saturated(capacity: dict) -> list[dict]:
    """Flatten the per-site capacity roll-up into a list of saturated Rockets.

    Saturated = a computable ceiling (``max_clients`` not None) reached or passed
    by the installed-client count. Each entry carries its site so the PDF can
    group/sort by site. Ordered most-overloaded first (highest load ratio)."""
    saturated: list[dict] = []
    for site in capacity.get("sites", []):
        site_name = site.get("site", "-")
        for rocket in site.get("rockets", []):
            max_clients = rocket.get("max_clients")
            current = rocket.get("current_clients", 0)
            if max_clients is None or max_clients <= 0:
                continue
            if current < max_clients:
                continue
            saturated.append(
                {
                    "site": site_name,
                    "name": rocket.get("name", f"Rocket #{rocket.get('id')}"),
                    "family": rocket.get("family", "ltu"),
                    "current": current,
                    "max": max_clients,
                    "width": rocket.get("channel_width_mhz"),
                    "ratio": current / max_clients,
                }
            )
    saturated.sort(key=lambda r: r["ratio"], reverse=True)
    return saturated


def _build_pdf(saturated: list[dict], generated_at: datetime.datetime) -> bytes:
    """Render the saturated-Rockets list as a PDF and return its bytes."""
    from fpdf import FPDF  # local import: only the report path needs the dep

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Rockets satures", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    stamp = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(
        0, 6,
        f"Rapport quotidien - genere le {stamp}",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.cell(
        0, 6,
        f"{len(saturated)} Rocket(s) sature(s) (clients installes >= capacite max)",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(4)

    if not saturated:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(
            0, 8,
            "Aucun Rocket sature. Tout le reseau est sous sa capacite maximale.",
            new_x="LMARGIN", new_y="NEXT",
        )
        return bytes(pdf.output())

    # Table header.
    headers = [
        ("Site", 45),
        ("Rocket", 50),
        ("Famille", 22),
        ("Clients", 20),
        ("Max", 16),
        ("Charge", 20),
        ("Largeur", 22),
    ]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    for label, width in headers:
        pdf.cell(width, 7, label, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for r in saturated:
        family = _FAMILY_LABEL.get(r["family"], r["family"])
        ratio_txt = f"{r['ratio'] * 100:.0f}%"
        width_txt = f"{int(r['width'])} MHz" if r.get("width") else "-"
        row = [
            (_truncate(r["site"], 26), 45, "L"),
            (_truncate(r["name"], 30), 50, "L"),
            (family, 22, "C"),
            (str(r["current"]), 20, "C"),
            (str(r["max"]), 16, "C"),
            (ratio_txt, 20, "C"),
            (width_txt, 22, "C"),
        ]
        for value, width, align in row:
            pdf.cell(width, 6, value, border=1, align=align)
        pdf.ln()

    return bytes(pdf.output())


def _truncate(text: str, limit: int) -> str:
    """Trim a cell string so it doesn't overflow its fixed-width column.

    Also drop characters the core Helvetica font can't encode (latin-1 only) so a
    stray glyph in a site/Rocket name can't make fpdf raise mid-render."""
    text = (text or "").strip().encode("latin-1", "replace").decode("latin-1")
    return text if len(text) <= limit else text[: limit - 1] + "."


async def build_saturation_report(db: AsyncSession) -> tuple[bytes, list[dict]]:
    """Build the daily saturated-Rockets PDF.

    Returns ``(pdf_bytes, saturated)`` where ``saturated`` is the (possibly
    empty) list of saturated Rockets — the caller uses it for the WhatsApp
    caption / logging.
    """
    capacity = await network_capacity_service.get_network_capacity(db)
    saturated = _collect_saturated(capacity)
    generated_at = datetime.datetime.now(datetime.UTC)
    pdf_bytes = _build_pdf(saturated, generated_at)
    return pdf_bytes, saturated
