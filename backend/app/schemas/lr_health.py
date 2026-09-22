import datetime

from pydantic import BaseModel, ConfigDict


class SignalEvidence(BaseModel):
    """One independent diagnostic indicator evaluated against an LR.

    The 5 indicators together yield the verdict. `active` is the binary
    "this indicator fired" outcome; `value` and `detail` give the operator
    the raw numbers so a verdict can be explained, not just trusted.
    """

    key: str
    label: str
    active: bool
    value: str
    detail: str


class BadInstallationRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # Identity
    lr_id: int
    lr_name: str
    lr_ip: str | None          # ip_address est NULLABLE depuis l'identité LR par MAC
    lr_mac: str | None
    model_variant: str
    distance_m: float | None
    first_discovered_at: datetime.datetime | None
    rocket_id: int | None
    rocket_name: str | None

    # Verdict (out of 5 indicators)
    verdict: str               # suspect | critical
    active_signals_count: int
    signals: list[SignalEvidence]

    # Latest values of the metrics behind the 5 indicators
    latest_signal_dbm: float | None
    latest_link_potential_pct: float | None
    latest_total_capacity_mbps: float | None
    latest_local_rx_rate_idx: float | None
    latest_remote_rx_rate_idx: float | None

    # LR → Internet RTT (ms) — dernier relevé de lr_internet_probe_job (sonde
    # SSH 60 s). Affichage seulement, n'entre pas dans le verdict. None si le LR
    # n'a pas de mesure récente (pas de transit, sonde KO) ou côté rapport 30 j.
    latency_ms: float | None = None

    # Per-LR floors actually applied (distance-banding removed 2026-05-21;
    # link_potential and rx_rate floors are family-banded LTU vs airMAX).
    signal_warning_threshold: float       # flat — settings.signal_warning_dbm
    link_potential_floor_pct: float       # family floor
    total_capacity_floor_mbps: float
    rx_rate_floor_idx: float              # family floor


class LiveLinkHealthResponse(BaseModel):
    """Réponse de la page « Liaisons clients » en mode **live** (état actuel).

    Pas de ``period_days`` : les indicateurs sont évalués sur les valeurs
    interrogées en direct à l'ouverture de la page, pas sur une fenêtre.
    ``unreachable_count`` = nombre de LR exclus faute d'avoir pu être joints
    en live (lien down, auth, timeout, creds manquants)."""

    generated_at: datetime.datetime
    unreachable_count: int
    items: list[BadInstallationRow]


class SiteLinkRow(BaseModel):
    """Une liaison backhaul point-à-point entre deux sites (airFiber 60).

    Critère unique : la **dernière capacité totale** lue en base est sous le
    plancher d'affichage (``af60_capacity_display_min_mbps``, 1.95 Gb/s). Pas de
    fetch live (trop coûteux) — on relit la dernière valeur de ``device_metrics``.
    Signal/SNR sont joints uniquement pour l'affichage, jamais pour le filtre."""

    model_config = ConfigDict(from_attributes=True)

    device_id: int
    name: str
    ip: str | None             # ip_address est NULLABLE (identité par MAC)
    distance_m: float | None

    # Technologie du lien P2P : "af60" (airFiber 60) ou "airmax" (LiteBeam
    # ptp_litebeam). Le frontend affiche un badge ; le plancher de capacité
    # diffère par techno (af60_capacity_display_min_mbps vs
    # airmax_backhaul_capacity_min_mbps).
    link_type: str = "af60"

    # Critère unique : capacité totale (Mbps) vs plancher d'affichage.
    latest_total_capacity_mbps: float | None
    capacity_floor_mbps: float

    # Affichage seul (dernières valeurs en base), hors filtre.
    latest_signal_dbm: float | None
    latest_snr_db: float | None


class SiteLinkEnd(BaseModel):
    """Une extrémité d'une liaison P2P, avec la RAISON de son état.

    ``state`` dit pourquoi ce bout compte (ou non) dans le verdict — c'est ce qui
    remplace l'ancien « extrémité non listée », qui confondait cinq situations :

    - ``measured``     : en ligne, capacité relevée → entre dans le verdict ;
    - ``no_data``      : en ligne mais aucune capacité en base ;
    - ``down``         : hors ligne au ping (dernière capacité périmée, ignorée) ;
    - ``unknown``      : statut indéterminé (sans IP → hors du ping) ;
    - ``unsupervised`` : radio connue du câblage UISP mais absente de notre
      inventaire ;
    - ``uncabled``     : le câblage ne connaît pas l'autre bout (radio jamais vue
      dans les data-links UISP) — on ne sait pas QUI est en face.
    """

    site: str | None
    state: str
    device_id: int | None = None
    name: str | None = None           # notre nom, sinon le nom UISP
    ip: str | None = None
    status: str | None = None
    capacity_mbps: float | None = None
    dl_capacity_mbps: float | None = None
    ul_capacity_mbps: float | None = None
    signal_dbm: float | None = None
    snr_db: float | None = None


class SiteLinkPair(BaseModel):
    """Une liaison P2P = ses DEUX bouts, appariés par le câblage (MAC), jamais
    par le nom. ``capacity_mbps`` = le pire des bouts ``measured`` (un lien vaut
    son extrémité la plus dégradée — même règle que ``/topology``)."""

    key: str
    link_type: str                     # "af60" | "airmax"
    capacity_floor_mbps: float
    capacity_mbps: float | None
    degraded: bool
    distance_m: float | None
    end_a: SiteLinkEnd
    end_b: SiteLinkEnd


class SiteLinkHealthResponse(BaseModel):
    """Réponse de la page « Point-à-Point ».

    ``links`` = les liaisons DÉGRADÉES (au moins un bout en ligne, mesuré, sous
    le plancher), pires d'abord, chacune avec ses deux bouts. ``unmeasured`` =
    les liaisons dont AUCUN bout n'est évaluable (nommées, pas seulement
    comptées). ``items`` / ``no_data_count`` = ancien format par radio, conservé
    le temps que le frontend déployé bascule."""

    generated_at: datetime.datetime
    links: list[SiteLinkPair] = []
    unmeasured: list[SiteLinkPair] = []
    no_data_count: int
    items: list[SiteLinkRow]


class HighLatencyRow(BaseModel):
    """Un LR client dont la latence LR → Internet dépasse le seuil critique.

    Critère unique : dernier ``lr_latency_ms`` (RTT vers ``lr_latency_target``,
    relevé par la sonde SSH ``lr_internet_probe_job``) ≥ ``lr_latency_critical_ms``
    (défaut 100 ms, configurable page Seuils). Lecture de la dernière valeur en
    base, pas d'interrogation live."""

    model_config = ConfigDict(from_attributes=True)

    lr_id: int
    lr_name: str
    lr_ip: str | None          # ip_address NULLABLE (identité par MAC)
    lr_mac: str | None
    model_variant: str
    distance_m: float | None
    rocket_id: int | None
    rocket_name: str | None

    latency_ms: float
    latency_threshold_ms: float


class HighLatencyResponse(BaseModel):
    """Réponse de la section « Clients à latence élevée ».

    LR clients dont la dernière latence LR → Internet dépasse le seuil critique
    (``lr_latency_critical_ms``). Pires d'abord. ``latency_threshold_ms`` est le
    seuil effectif appliqué (env + overrides runtime)."""

    generated_at: datetime.datetime
    latency_threshold_ms: float
    items: list[HighLatencyRow]
