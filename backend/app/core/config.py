import logging
from functools import lru_cache

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "network-supervisor"
    debug: bool = False
    log_level: str = "INFO"
    app_env: str = "development"  # development | production

    # API Key — set a strong secret in production; leave empty to disable auth
    api_key: str = ""

    # CORS — comma-separated origins. Leave empty to disable cross-origin sharing.
    cors_origins: str = "http://localhost:3000"

    # PostgreSQL
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "supervisor"
    postgres_password: str = "supervisor_dev_password"
    postgres_db: str = "network_supervisor"

    # SQLAlchemy connection pool — tune for expected concurrency
    db_pool_size: int = 5       # persistent connections kept open
    db_max_overflow: int = 10   # extra connections allowed above pool_size

    # Scheduler
    scheduler_enabled: bool = True

    # Polling intervals (seconds)
    ping_interval_seconds: int = 30
    snmp_interval_seconds: int = 60
    power_interval_seconds: int = 30

    # Warning digest — interval (minutes) between batched warning notifications
    warning_digest_minutes: int = 15

    # Anti-flapping — nombre de pings ratés consécutifs avant d'ouvrir un incident
    ping_down_threshold: int = 3

    # Instabilité ping — N échecs suivis d'un succès (sans atteindre ping_down_threshold)
    # déclenche un email INFO. 0 = désactivé.
    ping_instability_threshold: int = 2

    # Sonde LR → Internet — un seul job (`lr_internet_probe_job`) ouvre une
    # session SSH par LR par cycle et exécute `ping -c N` vers la cible
    # `lr_latency_target` (par défaut 8.8.8.8). Deux signaux en sortent :
    #
    #   - Transit (binaire) : si le ping échoue après `transit_probe_threshold`
    #     cycles consécutifs (anti-flap, défaut 2 cycles ≈ 2 min) → incident
    #     critique `lr_no_transit`.
    #   - Latence (continue) : si avg RTT ≥ `lr_latency_critical_ms` (défaut
    #     100 ms) pendant `lr_latency_failure_threshold` cycles consécutifs
    #     (défaut 3 cycles ≈ 3 min) → incident critique `lr_latency_high`.
    #
    # Cadence pilotée par `lr_latency_interval` (secondes, défaut 60).
    transit_probe_threshold: int = 2

    lr_latency_target: str = "8.8.8.8"
    lr_latency_ping_count: int = 5
    lr_latency_critical_ms: float = 100.0
    lr_latency_failure_threshold: int = 3
    lr_latency_interval: int = 60

    # Notifications — SMTP email
    smtp_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "Network Supervisor"
    smtp_use_tls: bool = True       # STARTTLS (port 587)
    smtp_use_ssl: bool = False      # SSL direct (port 465)
    # Comma-separated list of recipient emails
    notification_emails: str = ""   # ex: "admin@company.com,ops@company.com"

    @property
    def notification_email_list(self) -> list[str]:
        """Parse comma-separated notification_emails into a list."""
        return [e.strip() for e in self.notification_emails.split(",") if e.strip()]

    # SNMP (Ubiquiti airMAX / LTU)
    snmp_default_community: str = "public"
    snmp_port: int = 161
    snmp_timeout: int = 5

    # TLS verification for device APIs (LTU Rocket HTTPS, UISP Power HTTPS).
    # False is the historical default because Ubiquiti devices ship with self-
    # signed certs. Flip to True once you have either uploaded a CA-signed
    # cert to each device or pinned fingerprints.
    tls_verify_devices: bool = False

    # Device credentials (UISP Power API, LTU HTTP API, LTU LR SSH) are stored
    # per-device in the `devices` table — not as global env vars. Polling jobs
    # skip a device whose credentials are missing and log a warning instructing
    # the operator to set them via PUT /api/v1/devices/{id}.

    # Default SSH credentials stamped on auto-discovered client LRs so the
    # transit probe can reach them right away. The password MUST come from the
    # environment — never hardcode it in source. Empty password ⇒ LRs are
    # created without SSH creds and the transit probe skips them until an
    # operator sets them via PUT /api/v1/devices/{id}.
    lr_default_ssh_username: str = "ubnt"
    lr_default_ssh_password: str = ""
    lr_default_ssh_port: int = 22

    # Fallback passwords tried (in order) when the LR's stored ssh_password
    # fails with AuthenticationException. Lets older LRs still using a
    # historical password keep working without each LR being re-credentialed
    # by hand. When a fallback authenticates, the LR's ssh_password column is
    # updated to the working value so subsequent cycles auth on the first try.
    # CSV. Default contains the legacy "A2HQ@4321" — extend via env if other
    # historical passwords are still in the field.
    lr_fallback_ssh_passwords: str = "A2HQ@4321"

    @property
    def lr_fallback_password_list(self) -> list[str]:
        """Parse lr_fallback_ssh_passwords into a non-empty list (or empty)."""
        return [p for p in self.lr_fallback_ssh_passwords.split(",") if p]

    # Client internet block — the enforcement job re-asserts the LAN-port
    # shutdown on every LR marked client_blocked, so a block survives an LR
    # reboot (the port comes back UP on boot) and retries blocks that could
    # not be applied at click time. The block *state* itself lives per-LR in
    # the DB (lrs.client_blocked) — only the loop's on/off + cadence is env.
    client_block_enforcement_enabled: bool = True
    client_block_enforce_interval: int = 120

    # Default block flavour applied when the operator doesn't pick one:
    #   "full"          → shut the LR LAN port (total cut).
    #   "whatsapp_only" → iptables allowlist (DNS + Meta ranges) so the client
    #                     keeps WhatsApp while the rest of internet is cut.
    client_block_default_mode: str = "full"

    # IPv4 ranges left reachable in whatsapp_only mode. WhatsApp has no
    # isolable CIDR: its servers (messages, media, call relays) live in Meta's
    # AS32934, shared with Facebook/Instagram — allowing these ranges makes
    # WhatsApp fully work but also lets FB/IG through (documented, accepted:
    # IP-level filtering on airOS/LTU cannot separate them). DNS (UDP/TCP 53)
    # is always allowed in addition to these so names resolve. Comma-separated;
    # tune if Meta publishes new prefixes.
    #
    # 57.144.0.0/15 and 163.70.128.0/17 were added 2026-05-19 after a field
    # test showed WhatsApp messages dropping: the production MikroTik
    # address-list of "Whatsapp" peers contained dozens of live IPs in
    # 57.144.x / 57.145.x and 163.70.128.x / 163.70.151.x that the previous
    # CIDR list missed entirely. These are Meta's more recent IPv4 blocks.
    whatsapp_allow_cidrs: str = (
        "31.13.24.0/21,31.13.64.0/18,31.13.96.0/19,45.64.40.0/22,"
        "57.144.0.0/15,"
        "66.220.144.0/20,69.63.176.0/20,69.171.224.0/19,74.119.76.0/22,"
        "102.132.96.0/20,103.4.96.0/22,129.134.0.0/16,157.240.0.0/16,"
        "163.70.128.0/17,"
        "173.252.64.0/18,179.60.192.0/22,185.60.216.0/22,204.15.20.0/22"
    )

    @property
    def whatsapp_allow_cidr_list(self) -> list[str]:
        """Parse whatsapp_allow_cidrs into a clean list of CIDR strings."""
        return [c.strip() for c in self.whatsapp_allow_cidrs.split(",") if c.strip()]

    # Domains resolved to 0.0.0.0 by the LR's dnsmasq in whatsapp_only mode.
    # Field-verified necessity (2026-05-19): the Meta IP allowlist alone lets
    # Facebook/Instagram through because they share Meta's IP space with
    # WhatsApp. Returning 0.0.0.0 at DNS time makes the client's TCP connect
    # to 0.0.0.0 fail immediately — FB/IG cannot establish a session even
    # though their IPs would have passed the iptables allowlist. Extend if
    # Meta ships new top-level domains for FB/IG/Threads etc.
    blocked_domains_whatsapp_only: str = (
        "facebook.com,fbcdn.net,fbsbx.com,fb.com,fb.gg,"
        "messenger.com,instagram.com,cdninstagram.com,threads.net"
    )

    @property
    def blocked_domains_whatsapp_only_list(self) -> list[str]:
        """Parse blocked_domains_whatsapp_only into a clean list of domains."""
        return [d.strip() for d in self.blocked_domains_whatsapp_only.split(",") if d.strip()]

    # Switch port monitoring is configured per-UispSwitch in the database
    # (max_ports / rocket_port_index / port_min_speed_mbps). No global defaults.

    # Anomaly thresholds — radio link (LTU Rocket / LTU LR)
    # Operator-mandated bands (2026-05-21) : warning quand le signal descend
    # entre -75 et -80 dBm, critical strictement sous -80 dBm. Pas de
    # distance-banding (la grille -55/-62/-68/-73/-78 a été retirée car elle
    # divergeait de ces seuils sur les liens courts).
    signal_warning_dbm: int = -75   # below → warning incident
    signal_critical_dbm: int = -80  # below → critical incident
    # Tolerance band on signal: an incident opens only when the signal is
    # this many dBm *below* the threshold, so a small dip at the boundary
    # is absorbed instead of flapping into an incident. Default 0 = strict
    # thresholds (anti-flap delegated to signal_failure_threshold cycles).
    signal_tolerance_dbm: float = 0.0
    ccq_warning_pct: int = 75       # below → warning incident
    ccq_critical_pct: int = 50      # below → critical incident
    # Hysteresis band for ccq_low / ccq_ul_low: opens at threshold − this,
    # resolves only at the nominal threshold. 0 = strict.
    ccq_tolerance_pct: float = 5.0

    # Anomaly thresholds — CINR (dB)
    cinr_warning_db: float = 20.0   # below → warning
    cinr_critical_db: float = 10.0  # below → critical
    # Hysteresis band for cinr_low / cinr_ul_low: opens at threshold − this,
    # resolves only at the nominal threshold. 0 = strict.
    cinr_tolerance_db: float = 3.0

    # Anomaly thresholds — link capacity (% of ideal/rated capacity)
    capacity_low_warning_pct: float = 30.0   # below → warning
    capacity_low_critical_pct: float = 15.0  # below → critical

    # Per-LR link floors — single source shared by the lr-health page
    # classification AND the lr_link_substandard alert rule. Below any of
    # these (30-day mean for the page, live mean for the rule) = bad link.
    #
    # link_potential et débit RX sont déclinés par famille radio (2026-05-21) :
    # le matériel LTU et l'airMAX (Litebeam) ne supportent pas les mêmes
    # bornes — un Litebeam à 45 % de link_potential reste exploitable alors
    # qu'un LTU à 45 % est franchement dégradé.
    lr_link_potential_min_pct_ltu: float = 50.0     # LTU floor (%)
    lr_link_potential_min_pct_airmax: float = 40.0  # airMAX floor (%)
    lr_total_capacity_min_mbps: float = 60.0        # total_capacity_mbps floor

    # Débit RX (mcs idx) — LTU : critical seul ; airMAX : warning + critical.
    lr_rx_rate_critical_idx_ltu: float = 6.0    # LTU < 6 → critical (no warn)
    lr_rx_rate_warning_idx_airmax: float = 6.0  # airMAX : 4 ≤ rx < 6 → warning
    lr_rx_rate_critical_idx_airmax: float = 4.0 # airMAX < 4 → critical

    # Anomaly thresholds — RX/TX error rate (errors / total bytes, %)
    rx_tx_error_warning_pct: float = 1.0    # above → warning
    rx_tx_error_critical_pct: float = 5.0   # above → critical

    # Anti-flap: consecutive bad cycles required before opening an alert
    # (0 = immediate, 1 = after first bad cycle, 2 = after second, etc.)
    signal_failure_threshold: int = 2
    cinr_failure_threshold: int = 2
    ccq_failure_threshold: int = 2
    capacity_failure_threshold: int = 3
    error_failure_threshold: int = 2
    radio_degraded_failure_threshold: int = 2
    throughput_anomaly_failure_threshold: int = 3
    # link_potential/capacity/RX-rate are very volatile → debounce hard:
    # opens on the 5th consecutive bad cycle (count > 4), ~5 min sustained.
    lr_link_substandard_failure_threshold: int = 4

    # Throughput anomaly — detect sudden drops vs exponential moving average
    throughput_anomaly_drop_pct: float = 50.0   # alert if rate < EMA * (1 - drop_pct/100)
    throughput_anomaly_min_mbps: float = 1.0    # ignore if EMA < this (nearly idle link)

    # Anomaly thresholds — UISP Power
    battery_warning_pct: int = 25   # below → warning
    battery_critical_pct: int = 10  # below → critical

    # Security audit log — the FastAPI middleware records every mutating
    # request (POST/PUT/PATCH/DELETE on /api/v1/...) into the audit_log table.
    # The companion detection job (security_anomaly_detection_job) counts rows
    # per client IP over a sliding window and notifies operators when the
    # threshold is exceeded.
    audit_log_enabled: bool = True
    audit_anomaly_window_minutes: int = 5
    audit_anomaly_max_mutations: int = 50
    audit_anomaly_check_interval_seconds: int = 60
    # Per-IP cooldown — a sustained attack must not fire one alert per check.
    audit_anomaly_alert_cooldown_minutes: int = 30

    # Auto-discovery — stale LR detection
    # An auto-discovered LR is considered "disappeared" if it has not been
    # rapported as a peer of any Rocket for more than `stale_lr_minutes` minutes.
    # The detection job runs every `stale_lr_check_interval_minutes` minutes.
    stale_lr_check_interval_minutes: int = 5
    stale_lr_minutes: int = 10

    # LR topology check — router vs bridge detection. The client-block feature
    # only works on router-mode LRs (in bridge the LR is L2-transparent and
    # iptables/dnsmasq are bypassed). The job opens a warning incident
    # (AT_LR_BRIDGE_MODE_MISCONFIG) on bridge-mode LRs so the operator
    # reconfigures them. Bridge/router is a stable config decision so a
    # 60-min cadence is plenty and keeps SSH load low.
    lr_topology_check_interval_minutes: int = 60

    # LR-health materialized view refresh interval (minutes). The view
    # `lr_health_metric_stats_30d` pre-aggregates 30-day device_metrics so
    # /lr-health/bad-installations serves <100 ms instead of ~4 s. 15 min
    # is the sweet spot: a 30-day average barely moves in 15 min, and the
    # refresh itself only costs ~5 s of background CPU/IO.
    lr_health_matview_refresh_interval_minutes: int = 15

    # Client-consumption materialized view refresh interval (minutes). The
    # view `client_consumption_30d` pre-aggregates 30-day byte deltas so
    # /clients/consumption?period=30d serves <100 ms instead of ~36 s.
    # Same 15-min cadence as lr-health: cumulative byte deltas don't change
    # perceptibly in 15 min for a daily/weekly/monthly usage report.
    client_consumption_matview_refresh_interval_minutes: int = 15

    @computed_field(repr=False)
    @property
    def database_url(self) -> str:
        """Async database URL built from individual postgres_* fields.

        repr=False prevents the password from appearing in __repr__ / log dumps.
        """
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """In production, refuse to boot with empty API key or default credentials.

        Failing fast at startup is the only way to prevent a silent open API or
        polling jobs that loop on auth failures.
        """
        if self.app_env != "production":
            if not self.api_key:
                logger.warning(
                    "API authentication is DISABLED (api_key is empty) — dev mode only.",
                )
            return self

        errors: list[str] = []
        if not self.api_key:
            errors.append("API_KEY must be set (and non-empty) when APP_ENV=production")
        if self.postgres_password in ("", "supervisor_dev_password"):
            errors.append("POSTGRES_PASSWORD must be set to a strong value in production")

        if errors:
            raise ValueError(
                "Refusing to start in production with insecure configuration:\n  - "
                + "\n  - ".join(errors),
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings instance (singleton)."""
    return Settings()
