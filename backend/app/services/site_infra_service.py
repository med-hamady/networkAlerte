"""Per-site infra-equipment budget — count infra devices per site vs the cap.

Business rule: a site may hold at most ``settings.site_infra_max`` (default 14)
**infra** devices. "Infra" here means the radio/backhaul gear that physically
sits on a site — **Rockets (LTU/airMAX), AF60 backhauls and PTP LiteBeams**.
**Switches and UISP Power are NOT counted** (operator's definition), and client
LRs are subscriber stations, not infra.

The count is grouped by the denormalised ``devices.site`` column (maintained by
DB triggers — every infra device uses its own ``location``; fallback
"Sans site"). For each site we expose the count and the signed margin
``remaining = max - count``: positive ⇒ free slots (rendered ``+N``), negative ⇒
over budget (rendered ``-N``).

This module is the single source of truth for both the daily WhatsApp PDF
(:func:`build_site_infra_report`) and the /capacity page section (the rollup is
attached to the network-capacity endpoint response).
"""

from __future__ import annotations

import datetime
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Device

logger = logging.getLogger(__name__)

# Infra device_types that count against a site's budget. Switches, UISP Power,
# client LRs and client modems are deliberately excluded.
INFRA_COUNTED_TYPES = ("rocket", "airfiber", "ptp_litebeam")

_SITE_FALLBACK = "Sans site"


async def get_site_infra_capacity(db: AsyncSession) -> dict:
    """Per-site infra-equipment count vs the cap — the shared roll-up.

    Returns::

        {
          "threshold": 14,
          "total_devices": 57,
          "sites": [
            {"site": "A2 SNDE", "count": 16, "remaining": -2, "over": True},
            {"site": "A2 Tevragh", "count": 11, "remaining": 3, "over": False},
            ...
          ],
        }

    Sites are ordered over-budget first (most negative remaining), then by name,
    so the most urgent rows lead both the PDF and the page.
    """
    settings = get_settings()
    threshold = settings.site_infra_max

    rows = (
        await db.execute(
            select(
                func.coalesce(Device.site, _SITE_FALLBACK).label("site"),
                func.count().label("count"),
            )
            .where(Device.device_type.in_(INFRA_COUNTED_TYPES))
            .group_by(func.coalesce(Device.site, _SITE_FALLBACK))
        )
    ).all()

    sites = []
    total = 0
    for site_name, count in rows:
        count = int(count)
        total += count
        remaining = threshold - count
        sites.append(
            {
                "site": site_name,
                "count": count,
                "remaining": remaining,
                "over": remaining < 0,
            }
        )

    # Over-budget first (smallest/most-negative remaining), then alphabetical.
    sites.sort(key=lambda s: (s["remaining"], s["site"].lower()))

    return {"threshold": threshold, "total_devices": total, "sites": sites}


def _truncate(text: str, limit: int) -> str:
    """Trim/encode a cell so it fits its column and the latin-1 core font."""
    text = (text or "").strip().encode("latin-1", "replace").decode("latin-1")
    return text if len(text) <= limit else text[: limit - 1] + "."


def _build_pdf(rollup: dict, generated_at: datetime.datetime) -> bytes:
    """Render the per-site infra capacity as a PDF and return its bytes."""
    from fpdf import FPDF  # local import: only the report path needs the dep

    threshold = rollup["threshold"]
    sites = rollup["sites"]

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Capacite infra par site", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    stamp = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    pdf.cell(
        0, 6,
        f"Rapport quotidien - genere le {stamp}",
        new_x="LMARGIN", new_y="NEXT",
    )
    over = sum(1 for s in sites if s["over"])
    pdf.cell(
        0, 6,
        f"Max {threshold} equipements infra/site (Rockets + AF60 + PTP ; "
        f"hors switch et UISP Power) - {over} site(s) en depassement",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.ln(4)

    if not sites:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(0, 8, "Aucun site avec equipement infra.", new_x="LMARGIN", new_y="NEXT")
        return bytes(pdf.output())

    headers = [("Site", 80), ("Equip. infra", 40), ("Max", 25), ("Marge", 35)]
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(230, 230, 230)
    for label, width in headers:
        pdf.cell(width, 7, label, border=1, fill=True, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 9)
    for s in sites:
        remaining = s["remaining"]
        margin_txt = f"+{remaining}" if remaining >= 0 else str(remaining)
        # Light red fill for over-budget rows so they stand out at a glance.
        if s["over"]:
            pdf.set_fill_color(250, 220, 220)
            fill = True
        else:
            fill = False
        pdf.cell(80, 6, _truncate(s["site"], 48), border=1, align="L", fill=fill)
        pdf.cell(40, 6, str(s["count"]), border=1, align="C", fill=fill)
        pdf.cell(25, 6, str(threshold), border=1, align="C", fill=fill)
        pdf.cell(35, 6, margin_txt, border=1, align="C", fill=fill)
        pdf.ln()

    return bytes(pdf.output())


async def build_site_infra_report(db: AsyncSession) -> tuple[bytes, dict]:
    """Build the daily per-site infra-capacity PDF.

    Returns ``(pdf_bytes, rollup)`` — the caller uses ``rollup`` for the WhatsApp
    caption / logging.
    """
    rollup = await get_site_infra_capacity(db)
    generated_at = datetime.datetime.now(datetime.UTC)
    pdf_bytes = _build_pdf(rollup, generated_at)
    return pdf_bytes, rollup
