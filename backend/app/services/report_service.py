"""Génération de rapports de supervision réseau.

Couche read-only qui agrège incidents, métriques et alertes pour produire un rapport
décisionnel : fiabilité par équipement, fréquence des problèmes, points de faiblesse,
et recommandations priorisées.
"""

import datetime
import logging
from collections import defaultdict

from sqlalchemy import and_, desc, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device
from app.models.device_metric import DeviceMetric
from app.models.incident import Incident
from app.schemas.report import (
    AlertTypeFrequency,
    DeviceReliability,
    RadioMetrics,
    Recommendation,
    ReportPeriodSummary,
    SupervisionReport,
    WeakPoint,
)

logger = logging.getLogger(__name__)

AVAILABILITY_TYPES: set[str] = {
    "rocket_down",
    "lr_down",
    "switch_down",
    "device_unreachable",
}

RADIO_METRIC_NAMES: set[str] = {"signal_dbm", "cinr_db", "ccq_pct"}

PRIORITY_RANK: dict[str, int] = {"critique": 0, "élevé": 1, "moyen": 2}

ALERT_TYPE_LABELS_FR: dict[str, str] = {
    "rocket_down": "Panne LTU Rocket",
    "lr_down": "Panne LTU LR",
    "switch_down": "Panne Switch",
    "device_unreachable": "Équipement injoignable",
    "radio_interface_down": "Interface radio DOWN",
    "eth0_down": "Interface eth0 DOWN",
    "cpe_disconnected": "CPE déconnecté",
    "signal_low": "Signal faible",
    "cinr_low": "CINR faible",
    "ccq_low": "CCQ faible",
    "radio_link_degraded": "Lien radio dégradé",
    "capacity_low": "Capacité faible",
    "high_rx_tx_errors": "Taux d'erreurs élevé",
    "throughput_anomaly": "Anomalie de débit",
    "uisp_power_unreachable": "UISP Power injoignable",
    "battery_low_warning": "Batterie faible (warning)",
    "battery_low_critical": "Batterie critique",
    "voltage_anomaly": "Anomalie de tension",
    "switch_port_down": "Port switch DOWN",
    "switch_port_speed_low": "Vitesse port switch faible",
    "lr_no_transit": "Coupure transit",
    "transit_unavailable": "Transit indisponible",
    "ccq_ul_low": "CCQ uplink faible",
    "cinr_ul_low": "CINR uplink faible",
    "capacity_ul_low": "Capacité uplink faible",
}


def _label_for(alert_type: str) -> str:
    return ALERT_TYPE_LABELS_FR.get(alert_type, alert_type)


async def _build_period_summary(
    db: AsyncSession,
    date_from_dt: datetime.datetime,
    date_to_dt: datetime.datetime,
) -> ReportPeriodSummary:
    stmt = select(
        func.count(Incident.id).label("total"),
        func.count(Incident.id).filter(Incident.severity == "critical").label("critical"),
        func.count(Incident.id).filter(Incident.severity == "warning").label("warning"),
        func.count(Incident.id).filter(Incident.severity == "info").label("info"),
        func.count(Incident.id).filter(Incident.status == "open").label("open_cnt"),
        func.count(Incident.id).filter(Incident.status == "resolved").label("resolved"),
        func.count(Incident.id).filter(Incident.status == "acknowledged").label("ack"),
    ).where(
        Incident.detected_at >= date_from_dt,
        Incident.detected_at <= date_to_dt,
    )
    result = await db.execute(stmt)
    row = result.one()

    device_count = await db.scalar(select(func.count(Device.id))) or 0

    return ReportPeriodSummary(
        date_from=date_from_dt.date().isoformat(),
        date_to=date_to_dt.date().isoformat(),
        total_incidents=row.total or 0,
        critical_count=row.critical or 0,
        warning_count=row.warning or 0,
        info_count=row.info or 0,
        open_count=row.open_cnt or 0,
        resolved_count=row.resolved or 0,
        acknowledged_count=row.ack or 0,
        devices_supervised=device_count,
    )


async def _build_device_reliability(
    db: AsyncSession,
    date_from_dt: datetime.datetime,
    date_to_dt: datetime.datetime,
) -> list[DeviceReliability]:
    stmt = (
        select(
            Device.id,
            Device.name,
            Device.device_type,
            Device.location,
            Device.status,
            func.count(Incident.id).label("total"),
            func.count(Incident.id)
            .filter(Incident.alert_type.in_(AVAILABILITY_TYPES))
            .label("downtime"),
            func.avg(
                func.extract("epoch", Incident.resolved_at - Incident.detected_at) / 60.0
            )
            .filter(Incident.resolved_at.isnot(None))
            .label("avg_res"),
        )
        .join(
            Incident,
            and_(
                Incident.device_id == Device.id,
                Incident.detected_at >= date_from_dt,
                Incident.detected_at <= date_to_dt,
            ),
            isouter=True,
        )
        .group_by(
            Device.id,
            Device.name,
            Device.device_type,
            Device.location,
            Device.status,
        )
        .order_by(desc("total"), Device.name)
    )
    result = await db.execute(stmt)
    return [
        DeviceReliability(
            device_id=row.id,
            device_name=row.name,
            device_type=row.device_type,
            location=row.location,
            current_status=row.status or "unknown",
            total_incidents=row.total or 0,
            downtime_incidents=row.downtime or 0,
            avg_resolution_minutes=float(row.avg_res) if row.avg_res is not None else None,
        )
        for row in result.all()
    ]


async def _build_alert_frequencies(
    db: AsyncSession,
    date_from_dt: datetime.datetime,
    date_to_dt: datetime.datetime,
) -> list[AlertTypeFrequency]:
    stmt = (
        select(
            Incident.alert_type,
            func.count(Incident.id).label("cnt"),
            func.count(distinct(Incident.device_id)).label("devices"),
            func.avg(
                func.extract("epoch", Incident.resolved_at - Incident.detected_at) / 60.0
            )
            .filter(Incident.resolved_at.isnot(None))
            .label("avg_res"),
        )
        .where(
            Incident.detected_at >= date_from_dt,
            Incident.detected_at <= date_to_dt,
            Incident.alert_type.isnot(None),
        )
        .group_by(Incident.alert_type)
        .order_by(desc("cnt"))
    )
    result = await db.execute(stmt)
    return [
        AlertTypeFrequency(
            alert_type=row.alert_type,
            alert_type_label=_label_for(row.alert_type),
            occurrence_count=row.cnt or 0,
            affected_device_count=row.devices or 0,
            avg_resolution_minutes=float(row.avg_res) if row.avg_res is not None else None,
        )
        for row in result.all()
    ]


async def _build_radio_metrics(
    db: AsyncSession,
    date_from_dt: datetime.datetime,
    date_to_dt: datetime.datetime,
) -> list[RadioMetrics]:
    avg_sig = func.avg(DeviceMetric.metric_value).filter(
        DeviceMetric.metric_name == "signal_dbm"
    )
    min_sig = func.min(DeviceMetric.metric_value).filter(
        DeviceMetric.metric_name == "signal_dbm"
    )
    avg_cinr = func.avg(DeviceMetric.metric_value).filter(
        DeviceMetric.metric_name == "cinr_db"
    )
    avg_ccq = func.avg(DeviceMetric.metric_value).filter(
        DeviceMetric.metric_name == "ccq_pct"
    )

    stmt = (
        select(
            DeviceMetric.device_id,
            Device.name,
            avg_sig.label("avg_sig"),
            min_sig.label("min_sig"),
            avg_cinr.label("avg_cinr"),
            avg_ccq.label("avg_ccq"),
        )
        .join(Device, Device.id == DeviceMetric.device_id)
        .where(
            DeviceMetric.collected_at >= date_from_dt,
            DeviceMetric.collected_at <= date_to_dt,
            DeviceMetric.metric_name.in_(RADIO_METRIC_NAMES),
        )
        .group_by(DeviceMetric.device_id, Device.name)
        .having(or_(avg_sig.isnot(None), avg_cinr.isnot(None), avg_ccq.isnot(None)))
        .order_by(Device.name)
    )
    result = await db.execute(stmt)
    return [
        RadioMetrics(
            device_id=row.device_id,
            device_name=row.name,
            avg_signal_dbm=float(row.avg_sig) if row.avg_sig is not None else None,
            min_signal_dbm=float(row.min_sig) if row.min_sig is not None else None,
            avg_cinr_db=float(row.avg_cinr) if row.avg_cinr is not None else None,
            avg_ccq_pct=float(row.avg_ccq) if row.avg_ccq is not None else None,
        )
        for row in result.all()
    ]


async def _build_weak_points(
    db: AsyncSession,
    date_from_dt: datetime.datetime,
    date_to_dt: datetime.datetime,
) -> list[WeakPoint]:
    # Pattern 1 : (device, alert_type) avec >= 3 occurrences
    stmt_recurrence = (
        select(
            Incident.device_id,
            Device.name,
            Incident.alert_type,
            func.count(Incident.id).label("cnt"),
        )
        .join(Device, Device.id == Incident.device_id)
        .where(
            Incident.detected_at >= date_from_dt,
            Incident.detected_at <= date_to_dt,
            Incident.alert_type.isnot(None),
        )
        .group_by(Incident.device_id, Device.name, Incident.alert_type)
        .having(func.count(Incident.id) >= 3)
        .order_by(desc("cnt"))
    )
    rec_result = await db.execute(stmt_recurrence)
    weak_points: list[WeakPoint] = []
    for row in rec_result.all():
        label = _label_for(row.alert_type)
        weak_points.append(
            WeakPoint(
                device_id=row.device_id,
                device_name=row.name,
                pattern_description=f"{row.cnt} occurrences de « {label} » sur la période",
                alert_type=row.alert_type,
                occurrence_count=row.cnt,
            )
        )

    # Pattern 2 : devices avec pannes de disponibilité sur >= 2 jours distincts
    stmt_outages = (
        select(
            Incident.device_id,
            Device.name,
            func.count(distinct(func.date(Incident.detected_at))).label("days"),
            func.count(Incident.id).label("cnt"),
        )
        .join(Device, Device.id == Incident.device_id)
        .where(
            Incident.detected_at >= date_from_dt,
            Incident.detected_at <= date_to_dt,
            Incident.alert_type.in_(AVAILABILITY_TYPES),
        )
        .group_by(Incident.device_id, Device.name)
        .having(func.count(distinct(func.date(Incident.detected_at))) >= 2)
    )
    out_result = await db.execute(stmt_outages)
    for row in out_result.all():
        weak_points.append(
            WeakPoint(
                device_id=row.device_id,
                device_name=row.name,
                pattern_description=(
                    f"{row.cnt} pannes réparties sur {row.days} jours différents — "
                    "équipement instable"
                ),
                alert_type=None,
                occurrence_count=row.cnt,
            )
        )
    return weak_points


async def _build_recommendations(
    db: AsyncSession,
    date_from_dt: datetime.datetime,
    date_to_dt: datetime.datetime,
) -> list[Recommendation]:
    stmt = (
        select(
            Incident.device_id,
            Device.name,
            Incident.alert_type,
            func.count(Incident.id).label("cnt"),
        )
        .join(Device, Device.id == Incident.device_id)
        .where(
            Incident.detected_at >= date_from_dt,
            Incident.detected_at <= date_to_dt,
            Incident.alert_type.isnot(None),
        )
        .group_by(Incident.device_id, Device.name, Incident.alert_type)
    )
    result = await db.execute(stmt)

    device_counts: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    device_names: dict[int, str] = {}
    for row in result.all():
        device_counts[row.device_id][row.alert_type] = row.cnt
        device_names[row.device_id] = row.name

    # Accumule (priority, category, title, description, alert_type) -> liste de devices
    grouped: dict[tuple[str, str, str, str, str | None], list[str]] = defaultdict(list)

    def _add(
        device_id: int,
        priority: str,
        category: str,
        title: str,
        description: str,
        alert_type: str | None = None,
    ) -> None:
        key = (priority, category, title, description, alert_type)
        name = device_names.get(device_id, f"device-{device_id}")
        if name not in grouped[key]:
            grouped[key].append(name)

    for device_id, type_counts in device_counts.items():
        downtime_total = sum(type_counts.get(at, 0) for at in AVAILABILITY_TYPES)

        # Disponibilité
        if downtime_total > 5:
            _add(
                device_id,
                "critique",
                "disponibilite",
                "Équipement défaillant",
                (
                    f"Plus de 5 pannes détectées sur la période ({downtime_total} pannes). "
                    "Remplacement de l'équipement fortement recommandé."
                ),
            )
        elif 2 <= downtime_total <= 5:
            _add(
                device_id,
                "élevé",
                "disponibilite",
                "Pannes répétées",
                (
                    f"{downtime_total} pannes détectées. Vérifier l'alimentation, "
                    "le câblage RJ45 et la fixation physique de l'équipement."
                ),
            )

        # Radio — signal
        if type_counts.get("signal_low", 0) > 3:
            _add(
                device_id,
                "élevé",
                "radio",
                "Signal radio faible récurrent",
                (
                    "Le signal RF descend régulièrement sous le seuil. "
                    "Réaligner l'antenne, vérifier la ligne de vue (LOS), ou supprimer "
                    "les obstacles physiques (végétation, structure)."
                ),
                "signal_low",
            )

        # Radio — CCQ
        if type_counts.get("ccq_low", 0) > 3:
            _add(
                device_id,
                "élevé",
                "radio",
                "Qualité de connexion radio dégradée",
                (
                    "Le CCQ chute régulièrement sous le seuil acceptable. "
                    "Interférence radio probable — changer le canal RF, "
                    "vérifier l'environnement RF (sources d'interférence)."
                ),
                "ccq_low",
            )

        # Radio — CINR
        if type_counts.get("cinr_low", 0) > 3:
            _add(
                device_id,
                "élevé",
                "radio",
                "Rapport signal/bruit faible",
                (
                    "Le CINR est régulièrement bas. "
                    "Vérifier le bruit ambiant et les interférences sur le lien."
                ),
                "cinr_low",
            )

        # Alimentation — batterie critique
        if type_counts.get("battery_low_critical", 0) >= 1:
            _add(
                device_id,
                "critique",
                "alimentation",
                "Remplacement batterie urgent",
                (
                    "Niveau de batterie critique atteint. "
                    "Remplacement immédiat de la batterie nécessaire pour éviter "
                    "une coupure totale du site."
                ),
                "battery_low_critical",
            )

        # Alimentation — batterie warning répété
        if type_counts.get("battery_low_warning", 0) > 2:
            _add(
                device_id,
                "élevé",
                "alimentation",
                "Planifier le remplacement de batterie",
                (
                    "La batterie passe régulièrement sous le seuil de warning. "
                    "Planifier son remplacement avant la prochaine maintenance."
                ),
                "battery_low_warning",
            )

        # Alimentation — voltage
        if type_counts.get("voltage_anomaly", 0) >= 1:
            _add(
                device_id,
                "élevé",
                "alimentation",
                "Anomalie de tension détectée",
                (
                    "Vérifier l'alimentation électrique du site, "
                    "le chargeur et l'état du réseau électrique amont."
                ),
                "voltage_anomaly",
            )

        # Transit
        if type_counts.get("lr_no_transit", 0) > 2:
            _add(
                device_id,
                "élevé",
                "transit",
                "Coupures de transit récurrentes",
                (
                    "Plusieurs coupures de transit détectées. "
                    "Vérifier la connectivité avec le FAI et envisager "
                    "une liaison de secours (redondance)."
                ),
                "lr_no_transit",
            )

        # Switch
        if type_counts.get("switch_port_down", 0) >= 1:
            _add(
                device_id,
                "élevé",
                "switch",
                "Port switch DOWN",
                (
                    "Le port physique connecté à un équipement aval est tombé. "
                    "Vérifier le câble RJ45, le port switch et l'équipement connecté."
                ),
                "switch_port_down",
            )

        if type_counts.get("switch_port_speed_low", 0) >= 1:
            _add(
                device_id,
                "moyen",
                "switch",
                "Vitesse port switch dégradée",
                (
                    "Le port switch fonctionne en deçà de sa vitesse nominale (< 1 Gbps). "
                    "Remplacer le câble RJ45 (qualité Cat5e/Cat6 minimum) ou tester un autre port."
                ),
                "switch_port_speed_low",
            )

        # Performance
        if type_counts.get("capacity_low", 0) > 3:
            _add(
                device_id,
                "moyen",
                "performance",
                "Lien radio saturé",
                (
                    "La capacité du lien radio est régulièrement saturée. "
                    "Envisager une mise à niveau (largeur de canal, modulation, "
                    "ou changement d'équipement vers un modèle plus performant)."
                ),
                "capacity_low",
            )

        if type_counts.get("throughput_anomaly", 0) > 3:
            _add(
                device_id,
                "moyen",
                "performance",
                "Anomalie de débit récurrente",
                (
                    "Chute de débit anormale détectée plusieurs fois. "
                    "Vérifier les équipements en amont (switch, routeur, FAI) "
                    "et le taux d'erreurs sur les interfaces."
                ),
                "throughput_anomaly",
            )

        # CPE
        if type_counts.get("cpe_disconnected", 0) > 2:
            _add(
                device_id,
                "moyen",
                "radio",
                "CPE clients déconnectés",
                (
                    "Déconnexions répétées de CPE. Vérifier l'alimentation des CPE "
                    "côté client et la qualité du lien radio descendant."
                ),
                "cpe_disconnected",
            )

    recommendations = [
        Recommendation(
            priority=key[0],
            category=key[1],
            title=key[2],
            description=key[3],
            affected_devices=devices,
            alert_type=key[4],
        )
        for key, devices in grouped.items()
    ]
    recommendations.sort(key=lambda r: (PRIORITY_RANK.get(r.priority, 99), r.title))
    return recommendations


async def generate_report(
    db: AsyncSession,
    date_from: datetime.date,
    date_to: datetime.date,
) -> SupervisionReport:
    """Génère un rapport complet de supervision pour la période donnée."""
    date_from_dt = datetime.datetime(
        date_from.year, date_from.month, date_from.day, tzinfo=datetime.timezone.utc
    )
    date_to_dt = datetime.datetime(
        date_to.year,
        date_to.month,
        date_to.day,
        23,
        59,
        59,
        tzinfo=datetime.timezone.utc,
    )

    # Séquentiel : AsyncSession n'est pas thread-safe / concurrent-safe
    period = await _build_period_summary(db, date_from_dt, date_to_dt)
    reliability = await _build_device_reliability(db, date_from_dt, date_to_dt)
    frequencies = await _build_alert_frequencies(db, date_from_dt, date_to_dt)
    radio = await _build_radio_metrics(db, date_from_dt, date_to_dt)
    weak = await _build_weak_points(db, date_from_dt, date_to_dt)
    recs = await _build_recommendations(db, date_from_dt, date_to_dt)

    return SupervisionReport(
        generated_at=datetime.datetime.now(datetime.timezone.utc),
        period=period,
        device_reliability=reliability,
        alert_frequencies=frequencies,
        radio_metrics=radio,
        weak_points=weak,
        recommendations=recs,
    )
