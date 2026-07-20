"""Per-site geographic position (the pylon), for the client map.

A site is a physical mast carrying several sector Rockets (`A2 ARF1` has 7).
They all sit on the same structure, so the position belongs to the SITE, not to
each Rocket — field-confirmed: across all 17 sites the sectors' UISP coordinates
spread by only 4 to 29 m, i.e. the footprint of the mast itself.

Why a dedicated table rather than a column on `devices`:

* There is no `sites` table in this project — `devices.site` is a denormalised
  string maintained by DB triggers. A site has nowhere else to live.
* Storing it per-Rocket would duplicate one truth across 3-16 rows per site and
  invite them to drift apart.
* The Rockets cannot be read over SSH anyway: their `ssh_port` is 443, which is
  the airOS HTTPS API, not SSH ("Error reading SSH protocol banner" on every
  one). So unlike client LRs, the value cannot come from the device itself.

Seeded from the UISP controller (median across each site's sectors — the median
ignores a single mis-provisioned sector, where a mean would drag the whole site).
Editable afterwards: operational config belongs in the DB, not in a migration.
"""

from sqlalchemy import Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SiteLocation(Base):
    """Coordinates of one physical site (mast)."""

    __tablename__ = "site_locations"

    # The site name as denormalised on `devices.site` — the join key. Matched
    # verbatim, including oddities like the double space in "A2  ARF1".
    site: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    # Where the value came from: 'uisp' (seeded), 'manual' (edited by an
    # operator). Kept so a hand-corrected position is recognisable later.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="uisp")
    # `created_at` / `updated_at` come from Base — no need to redeclare them.
