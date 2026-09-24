import datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ClientConsumptionDaily(Base):
    """Consommation d'un client, déjà totalisée pour UNE journée UTC.

    Une ligne = (équipement, compteur, jour) → octets consommés ce jour-là.

    **Pourquoi cette table existe.** La consommation ne se lit pas dans
    `device_metrics` : elle s'y CALCULE, par différences successives entre des
    compteurs d'octets cumulés relevés toutes les minutes. Répondre à « combien
    a consommé ce client en mars » obligeait donc à relire tous les relevés de
    mars — d'où l'interdiction de poser une rétention sur `device_metrics`, qui
    grossissait sans fin (58,4 M lignes / 5,9 Go au 2026-09-24).

    Une journée écoulée ne change plus jamais. On fait donc la soustraction UNE
    fois, la nuit suivante, et on garde le total : ~1 ligne par client et par
    jour au lieu de 1440 relevés × 4 compteurs. Les relevés bruts peuvent alors
    être purgés au-delà de quelques mois sans perdre l'historique de
    consommation — et celui-ci, devenu minuscule, entre enfin dans la sauvegarde
    quotidienne dont `device_metrics` était exclue faute de place.

    ⚠️ **Ce qui est perdu, et c'est assumé** (décision d'exploitation du
    2026-09-24) : le détail INFRA-JOURNALIER au-delà de la rétention des
    relevés bruts. « Combien entre 14 h et 15 h le 3 mars » n'aura plus de
    réponse ; « combien le 3 mars » et « combien en mars » en auront toujours.
    La page `/clients` ne demande que des totaux par période.

    ⚠️ **Même forme de ligne que les matviews `client_consumption_*`**
    (`device_id`, `metric_name`, `bytes`, `samples`, `first_sample_at`) : c'est
    ce qui permet à `consumption_service` de servir une plage de dates depuis
    cette table sans réécrire ni le calcul, ni le regroupement site → Rocket →
    client. Ne pas « simplifier » en stockant directement descendant/montant :
    la correspondance compteur → sens dépend de la famille radio (LTU vs
    airMAX) et vit dans le service, en un seul endroit.
    """

    __tablename__ = "client_consumption_daily"

    __table_args__ = (
        # Cible du ON CONFLICT de l'upsert : rejouer le calcul d'une journée
        # (rattrapage, correction) doit remplacer la ligne, jamais la doubler.
        UniqueConstraint(
            "device_id", "metric_name", "day",
            name="uq_client_consumption_daily_device_metric_day",
        ),
        # Les lectures portent sur une PLAGE DE JOURS tous équipements
        # confondus ; la contrainte ci-dessus commence par device_id et ne peut
        # donc pas les servir.
        Index("ix_client_consumption_daily_day", "day"),
    )

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False,
    )
    metric_name: Mapped[str] = mapped_column(String(100), nullable=False)
    day: Mapped[datetime.date] = mapped_column(Date, nullable=False)

    # BigInteger obligatoire : un client à 100 Mb/s soutenus dépasse le
    # milliard d'octets en 2 minutes, et on totalise une journée entière.
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Nombre de relevés retenus ce jour-là. Sert à distinguer « 0 octet parce
    # que le client n'a rien consommé » de « 0 octet parce qu'on n'a rien
    # mesuré » — la page affiche l'un et l'autre différemment.
    samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Premier relevé retenu dans la journée : alimente le `data_start` de la
    # réponse (« depuis quand mesure-t-on ce client ? »).
    first_sample_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    def __repr__(self) -> str:
        return (
            f"<ClientConsumptionDaily device={self.device_id} "
            f"{self.metric_name} {self.day} bytes={self.bytes}>"
        )
