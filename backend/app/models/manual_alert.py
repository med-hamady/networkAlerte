import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ManualAlert(Base):
    """Anomalie signalée dans le bandeau du dashboard jusqu'à ACQUITTEMENT MANUEL.

    Une ligne est posée par `incident_service.open_incident` quand un incident
    NOUVEAU s'ouvre sur l'un des `MANUAL_ACK_ALERT_TYPES`. Elle ne s'efface que
    lorsqu'un opérateur clique « Résoudre » (`acknowledged_at` renseigné).

    ⚠️ Cette table est DÉLIBÉRÉMENT découplée de `incidents`, et c'est toute sa
    raison d'être : un incident non-disponibilité est hard-delete à sa
    résolution automatique. Si le bandeau lisait `incidents`, la ligne
    disparaîtrait au retour à la normale — sans qu'aucun opérateur ait pris
    connaissance de l'anomalie, alors que c'est précisément ce qu'on veut
    garantir ici. Rien dans le cycle de vie de l'incident n'écrit ici après
    l'ouverture : la seule chose qui ferme une ligne est un clic.

    Pas de FK vers `incidents` pour la même raison : la ligne incident visée
    n'existe généralement plus. Le lien avec l'équipement, lui, est une vraie
    FK (CASCADE) — un équipement supprimé n'a plus d'anomalie à acquitter.

    Les lignes acquittées sont CONSERVÉES (piste de qui a pris acte de quoi, et
    quand). Elles ne sont plus servies au bandeau.
    """

    __tablename__ = "manual_alerts"

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="critical")
    # Copies FIGÉES du libellé de l'incident au moment de la détection. Copiées
    # et pas jointes : l'incident d'origine est purgé à sa résolution, donc son
    # titre n'est plus lisible nulle part ensuite.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
    )
    # NULL = encore dans le bandeau. L'acquittement est PARTAGÉ par l'équipe :
    # un clic retire la ligne pour tout le monde. `acknowledged_by` n'est donc
    # pas un filtre, seulement la trace de qui a cliqué (NULL sur un appel
    # authentifié par clé API, qui ne porte aucune identité).
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), index=True,
    )
    acknowledged_by: Mapped[str | None] = mapped_column(String(150))

    def __repr__(self) -> str:
        return (
            f"<ManualAlert(id={self.id}, device_id={self.device_id}, "
            f"alert_type={self.alert_type!r}, "
            f"acknowledged={self.acknowledged_at is not None})>"
        )
