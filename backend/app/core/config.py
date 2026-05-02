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

    # Sonde de transit — vérifie que le trafic traverse bien le lien radio
    # IPs séparées par virgule (au moins une doit répondre pour valider le transit)
    transit_probe_ips: str = "1.1.1.1,8.8.8.8"
    transit_probe_interval: int = 60
    transit_probe_threshold: int = 2

    # Notifications — webhook
    webhook_url: str | None = None
    slack_webhook_url: str | None = None

    # Notifications — WhatsApp via WhatChimp
    whatsapp_test_mode: bool = False   # log payload without sending when True

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

    # UISP Power REST API
    uisp_power_username: str = "ubnt"
    uisp_power_password: str = "ubnt"
    uisp_power_port: int = 80

    # LTU HTTP API (CCQ, signal, rates)
    ltu_api_username: str = "ubnt"
    ltu_api_password: str = "ubnt"
    ltu_api_port: int = 443

    # SSH — LTU LR (credentials SSH, distincts du password API)
    ltu_lr_ssh_username: str = "ubnt"
    ltu_lr_ssh_password: str = ""
    ltu_lr_ssh_port: int = 22

    # Transit probe — disable entirely if LTU LR is not part of the topology
    transit_probe_enabled: bool = True

    # Switch port monitoring
    switch_rocket_port_index: int = 0   # SNMP interface index of port connected to LTU Rocket (0 = disabled)
    switch_max_ports: int = 16          # max interfaces to walk on the switch
    switch_port_min_speed_mbps: float = 1000.0  # below → critical (port UP but link below Gigabit)

    # Anomaly thresholds — radio link (LTU Rocket / LTU LR)
    signal_warning_dbm: int = -70   # below → warning incident
    signal_critical_dbm: int = -80  # below → critical incident
    ccq_warning_pct: int = 75       # below → warning incident
    ccq_critical_pct: int = 50      # below → critical incident

    # Anomaly thresholds — CINR (dB)
    cinr_warning_db: float = 20.0   # below → warning
    cinr_critical_db: float = 10.0  # below → critical

    # Anomaly thresholds — link capacity (% of ideal/rated capacity)
    capacity_low_warning_pct: float = 30.0   # below → warning
    capacity_low_critical_pct: float = 15.0  # below → critical

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

    # Throughput anomaly — detect sudden drops vs exponential moving average
    throughput_anomaly_drop_pct: float = 50.0   # alert if rate < EMA * (1 - drop_pct/100)
    throughput_anomaly_min_mbps: float = 1.0    # ignore if EMA < this (nearly idle link)

    # Anomaly thresholds — UISP Power
    battery_warning_pct: int = 25   # below → warning
    battery_critical_pct: int = 10  # below → critical

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
        if self.uisp_power_password == "ubnt":
            errors.append("UISP_POWER_PASSWORD is using the Ubiquiti default 'ubnt'")
        if self.ltu_api_password == "ubnt":
            errors.append("LTU_API_PASSWORD is using the Ubiquiti default 'ubnt'")
        if self.transit_probe_enabled and not self.ltu_lr_ssh_password:
            errors.append(
                "LTU_LR_SSH_PASSWORD must be set when TRANSIT_PROBE_ENABLED=true. "
                "Set TRANSIT_PROBE_ENABLED=false to skip the transit probe."
            )

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
