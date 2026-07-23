"""Qualité du signal et de la latence d'un client par MAC — GET /client-signal.

Un système tiers passe le MAC du LR client ; on renvoie deux choses :

  - **Signal** : une catégorie qualitative (`excellent` / `bien` / `moyen` /
    `faible`) calculée à partir de la **dernière valeur connue en base**
    (``device_metrics``, métrique ``signal_dbm``, collapse latest-only alimenté
    en continu par les polls de fond) — lecture DB quasi instantanée.
  - **Latence** : une mesure faite **EN DIRECT à chaque appel** — on ouvre une
    session SSH sur le LR et on ping ``lr_latency_target`` avec
    ``client_signal_ping_count`` paquets de 56 octets, puis on classe la moyenne
    (< 80 excellent / 80-100 très bien / 100-120 bien / 120-150 mauvaise /
    ≥ 150 catastrophique).

⚠️ **L'appel n'est donc plus instantané** : il porte le coût d'une poignée de
main SSH + le ping (typiquement 6-15 s, davantage sur un lien radio en perte).
Le consommateur doit prévoir un timeout en conséquence. La latence retombe sur
``indetermine`` — jamais sur un 0 ni sur une valeur inventée — dès que la mesure
n'aboutit pas (LR sans IP, sans identifiants, injoignable, ou sans transit).
"""

import datetime
import logging

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.device import Lr
from app.schemas.client_signal import ClientSignalResponse
from app.schemas.device import normalize_mac
from app.services import ssh_service

logger = logging.getLogger(__name__)

_M_SIGNAL = "signal_dbm"

# Charge utile ICMP de la mesure, en octets (paquet de 64 o sur le fil) —
# c'est le contrat annoncé au tiers, aligné sur la sonde de fond et le
# diagnostic check-ping.
_PING_PAYLOAD_BYTES = 56

# Libellés renvoyés au consommateur, par catégorie.
_MESSAGES = {
    "excellent": "Signal excellent",
    "bien": "Signal bien",
    "moyen": "Signal moyen",
    "faible": "Signal faible",
    "indetermine": "Signal indéterminé",
}

# Libellés de latence, par catégorie. Le libellé final est suffixé de la valeur
# mesurée (cf. _latency_message).
_LATENCY_MESSAGES = {
    "excellent": "Latence excellente",
    "tres_bien": "Latence très bien",
    "bien": "Latence bien",
    "mauvaise": "Latence mauvaise",
    "catastrophique": "Latence catastrophique",
}


def classify_signal(signal_dbm: float | None, settings) -> str:
    """Catégorie qualitative d'une valeur de signal en dBm.

    Bandes (réutilisent les seuils d'alerte existants + ``signal_excellent_dbm``) :
      • ``excellent``  : signal ≥ signal_excellent_dbm (défaut -65)
      • ``bien``       : signal_warning_dbm < signal < signal_excellent_dbm (-75..-65)
      • ``moyen``      : signal_critical_dbm ≤ signal ≤ signal_warning_dbm (-80..-75)
      • ``faible``     : signal < signal_critical_dbm (< -80)
      • ``indetermine``: aucune valeur (None)
    """
    if signal_dbm is None:
        return "indetermine"
    if signal_dbm >= settings.signal_excellent_dbm:
        return "excellent"
    if signal_dbm > settings.signal_warning_dbm:
        return "bien"
    if signal_dbm >= settings.signal_critical_dbm:
        return "moyen"
    return "faible"


def classify_latency(avg_rtt_ms: float | None, settings) -> str:
    """Catégorie qualitative d'une latence moyenne en ms.

    Grille opérateur (bornes dans ``Settings``, ``client_signal_latency_*``) :
      • ``excellent``      : < 80 ms
      • ``tres_bien``      : 80 ≤ avg < 100
      • ``bien``           : 100 ≤ avg < 120
      • ``mauvaise``       : 120 ≤ avg < 150
      • ``catastrophique`` : ≥ 150
      • ``indetermine``    : aucune mesure (None)
    """
    if avg_rtt_ms is None:
        return "indetermine"
    if avg_rtt_ms < settings.client_signal_latency_excellent_ms:
        return "excellent"
    if avg_rtt_ms < settings.client_signal_latency_very_good_ms:
        return "tres_bien"
    if avg_rtt_ms < settings.client_signal_latency_good_ms:
        return "bien"
    if avg_rtt_ms < settings.client_signal_latency_bad_ms:
        return "mauvaise"
    return "catastrophique"


def _latency_message(quality: str, avg_rtt_ms: float | None, reason: str | None) -> str:
    """Description lisible de la latence, valeur mesurée comprise.

    Sur ``indetermine`` on rend la RAISON (pas de transit, LR injoignable…) :
    un tiers doit pouvoir distinguer « lien mauvais » de « rien mesuré ».
    """
    if quality == "indetermine" or avg_rtt_ms is None:
        return f"Latence indéterminée — {reason}" if reason else "Latence indéterminée"
    return f"{_LATENCY_MESSAGES[quality]} ({avg_rtt_ms:.0f} ms)"


async def get_client_signal(db: AsyncSession, mac: str) -> ClientSignalResponse | None:
    """Qualité du signal (en base) et de la latence (mesurée live) du LR ``mac``.

    Renvoie ``None`` si aucun LR ne porte ce MAC (→ 404 côté endpoint). Si le LR
    existe mais n'a aucune mesure de signal récente, ``quality='indetermine'`` ;
    de même ``latency_quality='indetermine'`` si le ping live n'aboutit pas.
    Lève ``ValueError`` si le MAC fourni est mal formé.
    """
    normalized = normalize_mac(mac)  # ValueError si format invalide

    lr = (
        await db.execute(select(Lr).where(func.lower(Lr.mac_address) == normalized))
    ).scalar_one_or_none()
    if lr is None:
        return None

    signal_dbm, measured_at = await _latest_signal(db, lr.id)
    settings = get_settings()
    quality = classify_signal(signal_dbm, settings)

    avg_rtt_ms, reason = await _measure_latency_live(db, lr, settings)
    latency_quality = classify_latency(avg_rtt_ms, settings)

    return ClientSignalResponse(
        mac=normalized,
        lr_id=lr.id,
        lr_name=lr.name,
        status=lr.status,
        signal_dbm=signal_dbm,
        quality=quality,
        message=_MESSAGES[quality],
        measured_at=measured_at,
        latency_avg_ms=avg_rtt_ms,
        latency_quality=latency_quality,
        latency_message=_latency_message(latency_quality, avg_rtt_ms, reason),
        latency_target=settings.lr_latency_target,
        latency_packets_sent=settings.client_signal_ping_count,
        latency_packet_size_bytes=_PING_PAYLOAD_BYTES,
    )


async def _measure_latency_live(
    db: AsyncSession, lr: Lr, settings
) -> tuple[float | None, str | None]:
    """Ping Internet depuis le LR, ici et maintenant → ``(avg_rtt_ms, raison)``.

    ``avg_rtt_ms`` est ``None`` dès que la mesure n'aboutit pas, et ``raison``
    dit pourquoi (le tiers doit distinguer « lien mauvais » de « rien mesuré »).
    Réutilise ``ssh_service.measure_latency_via_ssh``, donc hérite du fallback de
    mot de passe et du contrôle d'empreinte de la sonde de fond ; on n'y active
    ni ``collect_radio`` ni ``collect_model`` (aller-retours SSH inutiles ici).
    """
    if not lr.ip_address:
        return None, "le LR n'a pas d'adresse IP connue"
    if not (lr.ssh_username and lr.ssh_password):
        return None, "le LR n'a pas d'identifiants SSH"

    try:
        (
            ssh_ok, ping_ok, avg_rtt_ms, message, observed_fp, used_pw, _model, _radio
        ) = await ssh_service.measure_latency_via_ssh(
            host=lr.ip_address,
            port=lr.ssh_port or 22,
            username=lr.ssh_username,
            password=lr.ssh_password,
            target=settings.lr_latency_target,
            count=settings.client_signal_ping_count,
            expected_fingerprint=lr.ssh_host_fingerprint,
            fallback_passwords=settings.lr_fallback_password_list,
            expected_mac=lr.mac_address,
        )
    except Exception as exc:  # la mesure live ne doit jamais faire tomber l'API
        logger.warning(
            "client-signal: mesure de latence sur LR '%s' (%s) a échoué — %s",
            lr.name, lr.ip_address, exc,
        )
        return None, "la mesure a échoué"

    # Auto-réparation, comme les diagnostics de /devices : une empreinte d'hôte
    # ou un mot de passe de fallback qui a marché est promu sur la fiche.
    # La session est commitée par get_db à la fin de la requête.
    if ssh_ok and observed_fp and lr.ssh_host_fingerprint != observed_fp:
        lr.ssh_host_fingerprint = observed_fp
    if used_pw and lr.ssh_password and used_pw != lr.ssh_password:
        logger.info(
            "client-signal: LR '%s' (%s) — mot de passe de fallback accepté, "
            "promu sur la fiche.",
            lr.name, lr.ip_address,
        )
        lr.ssh_password = used_pw
    await db.flush()

    if not ssh_ok:
        return None, "le LR est injoignable en SSH"
    if not ping_ok:
        return None, f"pas de transit vers {settings.lr_latency_target}"
    if avg_rtt_ms is None:
        return None, message or "RTT illisible"
    return avg_rtt_ms, None


async def _latest_signal(
    db: AsyncSession, lr_id: int
) -> tuple[float | None, datetime.datetime | None]:
    """Dernière valeur de ``signal_dbm`` du LR + son horodatage, ou (None, None).

    ``signal_dbm`` est collapsé latest-only dans ``device_metrics`` (1 ligne
    fraîche par device), donc c'est un lookup trivial servi par
    ``ix_device_metrics_lookup``."""
    sql = text(
        """
        SELECT metric_value, collected_at
        FROM device_metrics
        WHERE device_id = :lr_id AND metric_name = :metric_name
        ORDER BY collected_at DESC
        LIMIT 1
        """
    )
    row = (await db.execute(sql, {"lr_id": lr_id, "metric_name": _M_SIGNAL})).first()
    if row is None:
        return None, None
    return float(row.metric_value), row.collected_at
