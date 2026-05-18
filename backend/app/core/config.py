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

    # Latence ping — seuils warning/critical (ms) et anti-flap
    ping_latency_warn_ms: float = 100.0
    ping_latency_crit_ms: float = 300.0
    ping_latency_failure_threshold: int = 3

    # Sonde de transit — vérifie que le trafic traverse bien le lien radio
    # IPs séparées par virgule (au moins une doit répondre pour valider le transit)
    transit_probe_ips: str = "1.1.1.1,8.8.8.8"
    transit_probe_interval: int = 60
    transit_probe_threshold: int = 2

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

    # Transit probe — disable entirely if LTU LR is not part of the topology
    transit_probe_enabled: bool = True

    # Switch port monitoring is configured per-UispSwitch in the database
    # (max_ports / rocket_port_index / port_min_speed_mbps). No global defaults.

    # Anomaly thresholds — radio link (LTU Rocket / LTU LR)
    signal_warning_dbm: int = -70   # below → warning incident
    signal_critical_dbm: int = -80  # below → critical incident
    # Tolerance band on signal: an incident opens only when the signal is
    # this many dBm *below* the (distance-banded) threshold, so a 1-2 dBm
    # dip at the boundary is absorbed instead of flapping into an incident.
    # Applied to signal_low only (warning + critical). 0 = strict threshold.
    signal_tolerance_dbm: float = 5.0
    ccq_warning_pct: int = 75       # below → warning incident
    ccq_critical_pct: int = 50      # below → critical incident

    # Anomaly thresholds — CINR (dB)
    cinr_warning_db: float = 20.0   # below → warning
    cinr_critical_db: float = 10.0  # below → critical

    # Anomaly thresholds — link capacity (% of ideal/rated capacity)
    capacity_low_warning_pct: float = 30.0   # below → warning
    capacity_low_critical_pct: float = 15.0  # below → critical

    # Per-LR link floors — single source shared by the lr-health page
    # classification AND the lr_link_substandard alert rule. Below any of
    # these (30-day mean for the page, live mean for the rule) = bad link.
    lr_link_potential_min_pct: float = 60.0    # link_potential_pct floor
    lr_total_capacity_min_mbps: float = 60.0   # total_capacity_mbps floor
    lr_rx_rate_min_idx: float = 6.0            # local/remote_rx_rate_idx floor (×N)

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
