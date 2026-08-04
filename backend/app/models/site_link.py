"""Câblage inter-sites — les backhauls, synchronisés depuis UISP une fois par jour.

Pourquoi une table
------------------
Le maillage site→site n'existe que dans le contrôleur (``/nms/api/v2.1/data-links``)
et **les data-links désignent leurs extrémités par l'id UISP de l'équipement**,
pas par sa MAC. Or nous ne stockons pas cet id : résoudre un lien exige donc la
liste ``/devices``. Servir la page en interrogeant UISP à chaque affichage
revenait à télécharger ~1300 équipements, ~1400 sites et ~1300 liens **par
ouverture d'onglet** — pour une donnée qui ne bouge que lorsque le terrain pose
un backhaul, quelques fois par an.

Cette table porte donc le **câblage** (stable, synchronisé 1×/jour). La **santé**
de chaque liaison — statut, capacité, potentiel — n'y est PAS : elle est relue à
chaque affichage depuis ``devices``/``device_metrics``, que nos polls tiennent à
jour. C'est le découpage qui compte : figer la santé dans une table quotidienne
afficherait l'état d'hier comme s'il était celui de maintenant.

Ce qu'une ligne représente
--------------------------
UN LIEN PHYSIQUE, pas une liaison logique : deux radios reliant les deux mêmes
sites font deux lignes. Le regroupement par paire de sites est fait à la lecture
(``site_topology_service``), pour que le décompte des liens redondants reste
exact.

Les MAC sont conservées **des deux côtés** parce que c'est notre identité partout
ailleurs : c'est par elles que la lecture rejoint notre inventaire pour colorer
la liaison. Les noms UISP sont gardés en plus, car une extrémité peut ne pas être
supervisée du tout (le switch UniFi du HQ porte les trois liaisons fibre de la
racine, et il n'est pas dans notre base) — sans son nom, la carte afficherait un
bout anonyme.
"""

from __future__ import annotations

import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SiteLink(Base):
    """Un lien physique provisionné entre deux sites d'infra."""

    __tablename__ = "site_links"

    # Noms de sites pris VERBATIM du contrôleur : le parc porte « A2  ARF1 »
    # avec un double espace et `devices.site` le stocke tel quel — normaliser
    # ici casserait la jointure avec notre inventaire.
    # Ordonnés (site_a <= site_b) à l'écriture pour qu'une liaison ne change pas
    # d'identité selon le sens dans lequel UISP a provisionné le lien.
    site_a: Mapped[str] = mapped_column(Text, nullable=False)
    site_b: Mapped[str] = mapped_column(Text, nullable=False)

    # "wireless" (backhaul radio) ou "ethernet" (fibre/cuivre inter-sites).
    # Le type n'est PAS un critère de sélection : 3 des 5 liaisons du HQ sont en
    # ethernet, filtrer sur la radio amputerait le graphe de sa racine.
    link_type: Mapped[str] = mapped_column(String(20), nullable=False)

    # État rapporté par le contrôleur AU MOMENT DU SYNC ("active",
    # "disconnected"). ⚠️ Vieux de 24 h au pire : il documente le provisioning,
    # il ne dit PAS si la liaison est up maintenant. C'est le statut de nos
    # équipements, relu en direct, qui tranche.
    state: Mapped[str | None] = mapped_column(String(20), nullable=True)

    mac_a: Mapped[str | None] = mapped_column(String(17), nullable=True)
    mac_b: Mapped[str | None] = mapped_column(String(17), nullable=True)

    # Nom UISP de chaque extrémité — seule étiquette disponible quand
    # l'équipement n'est pas dans notre inventaire.
    name_a: Mapped[str] = mapped_column(String(255), nullable=False)
    name_b: Mapped[str] = mapped_column(String(255), nullable=False)

    synced_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )

    __table_args__ = (
        Index("ix_site_links_site_a", "site_a"),
        Index("ix_site_links_site_b", "site_b"),
    )
