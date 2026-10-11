"""Schémas de GET /site-status — état courant de chaque site, pour un tiers.

Voir :mod:`app.services.site_status_service`.
"""

from typing import Literal

from pydantic import BaseModel


class SiteStatus(BaseModel):
    site: str
    status: Literal["up", "down"]


class SiteStatusResponse(BaseModel):
    sites: list[SiteStatus]
