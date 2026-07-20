import logging
from functools import lru_cache

from pydantic import computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Content-block categories → human label, in UI display order. The domain set
# for each key lives in Settings.content_block_domains_<key> (env-overridable).
CONTENT_BLOCK_LABELS: dict[str, str] = {
    "facebook": "Facebook / Instagram",
    "whatsapp": "WhatsApp",
    "tiktok": "TikTok",
    "snapchat": "Snapchat",
    "google": "Google / YouTube",
    "telegram": "Telegram",
}


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

    # Dedicated key for the external payment system. Accepted ONLY on the /fai
    # routes (block / unblock / status), never on the rest of the API — so it can
    # be handed out and rotated without touching `api_key` (dashboard + scripts).
    # Empty = no dedicated key; /fai then only accepts api_key or a session.
    fai_api_key: str = ""

    # Journal d'audit des blocages / déblocages (une ligne par action). Fichier
    # texte, dans un volume bind-monté → survit aux redéploiements.
    fai_log_path: str = "/app/logs/fai_actions.log"

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
    # Groupe de jobs enregistré par ce process scheduler. Permet d'isoler la
    # charge SSH/poll lourde du heartbeat de disponibilité (device_ping) sur des
    # PROCESS séparés (un seul GIL/event-loop par process). À ~1000+ devices, la
    # sonde SSH (ThreadPoolExecutor) saturait le GIL et affamait le device_ping
    # → last_seen figé pendant ~20 min. Voir register_jobs().
    #   - "all"     : tous les jobs (dev, process unique — défaut, comportement legacy)
    #   - "fast"    : disponibilité de l'INFRA + maintenance, tout async léger
    #                 (infra_ping, digest, flap, latency-aggregate, matviews,
    #                 rétention, rapports)
    #   - "heavy"   : SSH + gros fan-outs API (lr_probe, lr_plan, client_block,
    #                 snmp, ltu, airos, af60, power, uisp_sync)
    #   - "ping-lr" : le ping des LR clients, SEUL. Isolé de "fast" pour que sa
    #                 rafale de re-confirmation (des centaines de `ping` quand
    #                 beaucoup de LR sont down côté abonné) ne dispute jamais le
    #                 CPU au sweep infra, qui est la seule source d'incidents.
    scheduler_group: str = "all"

    # Polling intervals (seconds)
    ping_interval_seconds: int = 30
    # Intervalle du ping des LR CLIENTS (sweep séparé de l'infra). Plus lent que
    # l'infra : un LR down ne crée aucun incident (panne côté abonné), inutile de
    # le sonder aussi souvent — ça allège fping et le médium radio.
    client_ping_interval_seconds: int = 60
    snmp_interval_seconds: int = 60
    power_interval_seconds: int = 30

    # Warning digest — interval (minutes) between batched warning notifications
    warning_digest_minutes: int = 15

    # Anti-flapping — nombre de pings ratés consécutifs avant d'ouvrir un incident
    ping_down_threshold: int = 3

    # Concurrence de l'airos_api_poll_job (fetch status.cgi des LiteBeam airMAX).
    # Le fetch HTTP (login + status.cgi) est I/O-bound → on parallélise fort. À
    # ~290 devices, une concurrence de 12 étalait la Phase 1 sur ~4 min et le job
    # débordait son intervalle (3 min) → cascade de "maximum instances reached"
    # sur TOUS les jobs heavy. 50 ramène la Phase 1 à ~20-40 s. Combiné à la
    # deadline globale (_AIROS_POLL_DEADLINE_S), le job ne déborde plus jamais.
    airos_concurrency: int = 50

    # Concurrence du snmp_poll_job. Le job était série (un walk SNMP à la fois)
    # → à 78 rockets/switches, aggravé par les timeouts des airMAX SNMP-off qui
    # s'additionnaient, un tour dépassait 60 s. Les collecteurs pysnmp sont async
    # → gather + sémaphore : les timeouts s'exécutent en parallèle.
    snmp_concurrency: int = 30

    # Concurrence du power_poll_job (REST API des UISP Power). Le job était série
    # → avec beaucoup de UISP Power (ou des injoignables qui timeout), un tour
    # dépassait l'intervalle de 30 s → APScheduler skippait chaque cycle. Fetch
    # HTTP async → gather + sémaphore, sous deadline globale (_POWER_POLL_DEADLINE_S).
    power_concurrency: int = 20

    # Concurrence de la sonde transit/latence LR (lr_internet_probe_job). Le job
    # était SÉRIE (une session SSH à la fois) → ~1 h par tour à 500 LR (chaque LR
    # n'était sondé qu'une fois/heure). On parallélise sur un pool de threads
    # dédié de cette taille (chaque sonde = paramiko sync borné par ses timeouts).
    # ARBITRAGE : trop bas → le tour déborde l'intervalle (« skipped: maximum
    # instances ») ; trop haut → N poignées SSH simultanées saturent le médium
    # radio partagé → pertes pendant le kex → « No existing session » sur des LR
    # pourtant sains (mesuré 2026-06-16 : ces LR se connectent en < 1,5 s en solo,
    # échouent à 150 en parallèle). Le backoff par LR (jobs.py) retire les LR
    # chroniquement KO du lot, ce qui permet de tenir une concurrence MODÉRÉE
    # sans déborder. ~80 est un bon point de départ ; ajuster en surveillant à la
    # fois les « skipped » (§ trop bas) et les « No existing session » (trop haut).
    lr_probe_concurrency: int = 80

    # Concurrence max du device_ping_job. Le job lançait TOUS les pings d'un coup
    # (asyncio.gather sur tout le parc) — à 600+ devices ça spawn 600 sous-process
    # `ping` simultanés qui se starvent mutuellement → des devices joignables
    # échouent en masse (faux "down") et le cycle déborde son intervalle de 30 s.
    # On borne le nombre de pings en vol via un sémaphore. ceil(parc/concurrence)
    # batchs × ~2 s/ping doit tenir sous ping_interval_seconds (ex. 600/100 ≈ 12 s).
    ping_concurrency: int = 100

    # Ping INFRA (Rockets base / switches / UISP Power / AF60) — équipements
    # critiques et PEU nombreux, pingés dans un sweep `fping` SÉPARÉ des ~600 LR
    # clients. Pourquoi séparer : un seul fping sur tout le parc (`-r 1 -t 800
    # -i 1`) envoyait ~680 paquets en ~0,7 s ; ce burst noyait l'ICMP que le CPU
    # de management des Rockets rate-limite → 2 sondes perdues → Rocket compté KO
    # → après 3 cycles il passait `down` et SORTAIT des polls API/SNMP, alors
    # qu'il routait le trafic et répondait à son API. Le sweep infra utilise donc
    # des paramètres FIABLES : plus de retries + timeout plus large. Le lot étant
    # petit, le surcoût est négligeable. Les LR gardent les défauts tolérants/
    # rapides de `ping_hosts_bulk` (leur down ne crée même pas d'incident).
    ping_infra_retries: int = 2        # fping -r : 3 tentatives au total
    ping_infra_timeout_ms: int = 1200  # fping -t : timeout par sonde (ms)

    # Re-confirmation ISOLÉE des devices suspectés down, sur les DEUX sweeps
    # (infra ET LR clients). Le faux "down" d'une radio saine vient du BURST
    # fping (la radio rate-limite TOUS les ICMP du paquet d'un coup). Avant de
    # compter un échec, on re-pingue chaque hôte suspect SEUL, hors burst, avec
    # un `ping` dédié : un équipement sain répond du premier coup. "down" reste
    # 100% basé sur le ping — mais sur un ping fiable (isolé). Coût NUL quand
    # tout répond (aucun suspect). Le fping groupé n'est plus qu'un PRÉ-FILTRE
    # rapide : il ne décide jamais seul qu'un device est down.
    #
    # Les LR en avaient encore plus besoin que l'infra : leur sweep tourne avec
    # les défauts tolérants/rapides de ping_hosts_bulk (-r 1 -t 800), et le
    # paquet est bien plus gros (~600 LR) → un LR sain, joignable en HTTPS,
    # restait "HORS LIGNE" des heures (constaté 2026-07-17).
    #
    # Réglages SÉPARÉS par famille : les deux sweeps tournent dans des process
    # distincts (scheduler_group "fast" vs "ping-lr") et n'ont ni le même profil
    # ni le même enjeu — l'infra seule ouvre des incidents. Aucun budget partagé,
    # donc régler les LR ne peut pas dégrader la mesure de l'infra.
    ping_infra_reconfirm_count: int = 2      # ping -c : sondes du re-check isolé
    ping_infra_reconfirm_timeout_s: int = 2  # ping -W : timeout par sonde (s)
    # Lot infra petit (quelques dizaines) → une seule vague suffit.
    ping_infra_reconfirm_concurrency: int = 50

    # LR clients : mêmes sondes, mais concurrence dimensionnée pour un GROS lot de
    # suspects (les vrais LR down côté abonné sont nombreux et c'est normal).
    # ~3 s par hôte mort × ceil(suspects/concurrence) doit rester <
    # client_ping_interval_seconds (ex. 400 suspects / 150 ≈ 3 lots ≈ 9 s).
    ping_client_reconfirm_count: int = 2
    ping_client_reconfirm_timeout_s: int = 2
    ping_client_reconfirm_concurrency: int = 150

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
    # Cadence pilotée par `lr_latency_interval` (secondes, défaut 300).
    transit_probe_threshold: int = 2

    lr_latency_target: str = "8.8.8.8"
    lr_latency_ping_count: int = 5
    lr_latency_critical_ms: float = 100.0
    lr_latency_failure_threshold: int = 3
    # Intervalle de la sonde SSH transit/latence. Porté de 60 → 300 s : à 800+ LR
    # la fan-out SSH toutes les 60 s était la principale source de pression GIL.
    # Transit/latence n'ont pas besoin d'une granularité 60 s (anti-flap en
    # cycles). 5 min décharge massivement la boucle. Ajustable par .env.
    lr_latency_interval: int = 300

    # Notifications — WhatsApp via Ultramsg (https://ultramsg.com).
    # WhatsApp is the ONLY notification transport (email sending was removed from
    # the project): the pipeline resolves a single WhatsApp target (the group
    # below) and every notified infra incident lands there. The group id is a
    # WhatsApp group chat id of the form "1203630xxxxxxx@g.us".
    whatsapp_enabled: bool = False
    whatsapp_base_url: str = "https://api.ultramsg.com"
    whatsapp_instance_id: str = ""   # ex: instance12345
    whatsapp_token: str = ""         # Ultramsg instance token
    whatsapp_group_id: str = ""      # ex: 1203630xxxxxxx@g.us

    @property
    def whatsapp_configured(self) -> bool:
        """True only when every field needed to send a WhatsApp message is set."""
        return bool(
            self.whatsapp_enabled
            and self.whatsapp_instance_id
            and self.whatsapp_token
            and self.whatsapp_group_id
        )

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

    # ── UISP controller sync ─────────────────────────────────────────────────
    # Periodic import of INFRASTRUCTURE devices (base-station Rockets, switches,
    # UISP Power, AF60 backhauls) from the UISP/UNMS controller so the operator
    # doesn't enter each AP/switch/power by hand. Subscriber stations (LTU-LR,
    # LiteBeam) are NOT imported — they keep coming from CPE auto-discovery.
    # Only name / IP / site come from UISP; credentials are stamped from the
    # per-family/site conventions below at CREATE time only, never overwriting an
    # existing device. A device that disappears from UISP is left untouched
    # (no delete, no deactivate). Auth: either UISP_API_TOKEN (preferred — UISP
    # → Settings → Users → API tokens, revocable) or UISP_USERNAME/PASSWORD.
    uisp_sync_enabled: bool = False
    uisp_base_url: str = ""          # e.g. https://13.62.145.152
    uisp_username: str = ""
    uisp_password: str = ""
    uisp_api_token: str = ""         # alternative to username/password
    uisp_verify_tls: bool = False    # controller usually has a self-signed cert
    # Inventory drifts slowly (new sites/APs are rare). The job runs ONCE at
    # scheduler startup (next_run_time=now) so a deploy imports immediately, then
    # daily at uisp_sync_hour (UTC — Mauritania is GMT/UTC+0, so 7 = 07:00 local).
    uisp_sync_hour: int = 7  # daily run time, 24h clock, UTC
    uisp_request_timeout: int = 30

    # Client-station import (subscriber LRs) into the `lrs` table, on the same
    # uisp_sync_job (runs after the infra import). Brings UISP's last-known
    # bridge/router mode + status for every client so /access stays complete and
    # accurate even when a Rocket/LR is down. Gated separately from the infra
    # sync (it pulls ~1000 rows). Imports the FULL roster UISP still lists
    # (UISP's /devices?role=station already drops de-provisioned stations).
    uisp_station_sync_enabled: bool = False

    # UISP site names to skip entirely. SEMICOLON-separated (not comma — UISP
    # site names themselves contain commas, e.g. "Bureau, A2"), case-insensitive.
    # Any device whose site matches is ignored by the sync — neither created nor
    # updated. Use for office/LAN sites whose gear (a desk switch, etc.) is
    # classified as infra but must not be supervised.
    uisp_ignored_sites: str = ""

    # LR subscription-plan sync (traffic-shaper rate caps read over SSH). The
    # plan changes rarely, and the read is a per-LR SSH round-trip (bounded by
    # lr_probe_concurrency), so a slow cadence is plenty. Runs once at scheduler
    # startup too (next_run_time=now), and is triggerable on demand via POST
    # /devices/plans/sync.
    lr_plan_sync_interval_minutes: int = 2880  # 48 h

    # Credential conventions stamped on a device CREATED by the UISP sync.
    # Rocket password is per-site: {site} is the code extracted from the UISP
    # site name ("A2 SNDE" → "SNDE") → "A2SNDE@4321$A2". Switches need no creds
    # (SNMP-only, community auto-filled). Override any of these via env.
    uisp_rocket_ssh_username: str = "ubnt"
    uisp_rocket_ssh_password_template: str = "A2{site}@4321$A2"
    uisp_power_api_username: str = "ubnt"
    uisp_power_api_password: str = "A2@uispp2025"
    uisp_af60_ssh_username: str = "ubnt"
    uisp_af60_ssh_password: str = "A2F60@4321"

    @property
    def uisp_ignored_site_set(self) -> set[str]:
        """Normalised (lower/trimmed) set of UISP site names to skip.

        Semicolon-separated because UISP site names contain commas ("Bureau, A2").
        """
        return {s.strip().lower() for s in self.uisp_ignored_sites.split(";") if s.strip()}

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

    # ── Content block catalogue (per-category destination filter) ────────────
    # Independent feature from the whatsapp_only/full block: the operator picks
    # services to DNS-poison per client (client stays online for everything
    # else). One CSV of domains per category; a client's `blocked_categories`
    # (JSON list of the keys below) selects which sets to apply on its LR.
    # Tune the domain sets here without a redeploy (env override). Blocking
    # `google` also breaks YouTube/reCAPTCHA and other Google services — the UI
    # warns about it; it is an accepted operator choice.
    content_block_domains_facebook: str = (
        "facebook.com,fbcdn.net,fbsbx.com,fb.com,fb.gg,fb.watch,"
        "messenger.com,instagram.com,cdninstagram.com,threads.net"
    )
    content_block_domains_whatsapp: str = "whatsapp.com,whatsapp.net,wa.me"
    content_block_domains_tiktok: str = (
        "tiktok.com,tiktokcdn.com,tiktokv.com,ibytedtos.com,"
        "byteoversea.com,musical.ly,tiktokcdn-us.com"
    )
    content_block_domains_snapchat: str = "snapchat.com,sc-cdn.net,snap.com,snapkit.com"
    content_block_domains_google: str = (
        "google.com,googlevideo.com,youtube.com,youtu.be,ytimg.com,"
        "gstatic.com,googleapis.com,googleusercontent.com,ggpht.com"
    )
    content_block_domains_telegram: str = "telegram.org,telegram.me,t.me,telesco.pe,tdesktop.com"

    def content_block_catalog(self) -> dict[str, list[str]]:
        """Return {category_key: [domains]} for every known content-block service."""
        return {
            key: [
                d.strip()
                for d in getattr(self, f"content_block_domains_{key}").split(",")
                if d.strip()
            ]
            for key in CONTENT_BLOCK_LABELS
        }

    def content_block_label(self, key: str) -> str:
        """Human label for a content-block category key (key itself if unknown)."""
        return CONTENT_BLOCK_LABELS.get(key, key)

    def content_block_domains_for(self, keys: list[str]) -> list[str]:
        """Deduplicated union of domains for the given category keys (unknown keys ignored)."""
        catalog = self.content_block_catalog()
        seen: dict[str, None] = {}  # dict preserves insertion order, dedups
        for key in keys:
            for domain in catalog.get(key, ()):
                seen.setdefault(domain, None)
        return list(seen)

    # Switch port monitoring is configured per-UispSwitch in the database
    # (max_ports / rocket_port_index / port_min_speed_mbps). No global defaults.

    # Anomaly thresholds — radio link (LTU Rocket / LTU LR)
    # Operator-mandated bands (2026-05-21) : warning quand le signal descend
    # entre -75 et -80 dBm, critical strictement sous -80 dBm. Pas de
    # distance-banding (la grille -55/-62/-68/-73/-78 a été retirée car elle
    # divergeait de ces seuils sur les liens courts).
    signal_warning_dbm: int = -75   # below → warning incident
    signal_critical_dbm: int = -80  # below → critical incident
    # Plafond « bien/excellent » pour la classification qualitative du signal
    # exposée par GET /client-signal (consommée par un système tiers). Réutilise
    # warning/critical pour les bandes basses ; ce seuil sépare « bien » (> -65)
    # d'« excellent » (≥ -65). Voir services/client_signal_service.classify_signal.
    signal_excellent_dbm: int = -65  # ≥ → excellent
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

    # Surcharge clients par Rocket (rocket_client_overload) — l'AP de base
    # station est saturé quand le nombre de clients connectés ATTEINT le seuil.
    # Le seuil est une FORMULE déclinée par famille radio : base à 10 MHz, puis
    # +`per_10mhz` clients par tranche de +10 MHz de largeur de canal. La largeur
    # est lue en direct depuis l'API (LTU channelWidth.tx / airMAX chwidth) et
    # arrondie au multiple de 10 MHz le plus proche ; une largeur < 10 MHz n'a
    # pas de seuil défini → la règle ne déclenche pas. Ex. (base LTU 15 / airMAX
    # 10, step 5) : LTU 10→15, 20→20, 30→25 ; airMAX 10→10, 20→15, 40→25.
    # Incident critique. Surchargables via la page Seuils.
    rocket_overload_clients_ltu_base: int = 15
    rocket_overload_clients_airmax_base: int = 10
    rocket_overload_clients_per_10mhz: int = 5

    # Anomaly thresholds — airFiber 60 (AF60-LR) backhaul, lien 60 GHz.
    # Le 60 GHz a des plages très différentes du sub-6 GHz : le signal idéal
    # tourne ~-43 dBm, un linkScore de ~40 % reste exploitable, et un lien sain
    # fait > 1 Gbps. Défauts volontairement conservateurs (validés terrain le
    # 2026-06-05 contre un lien réel à -67 dBm / SNR 12 / 41 % / 3,3 Gbps : reste
    # vert). Surchargables via la page Seuils.
    af60_signal_warning_dbm: int = -70    # below → warning
    af60_signal_critical_dbm: int = -75   # below → critical
    af60_signal_tolerance_dbm: float = 0.0
    af60_snr_warning_db: float = 10.0     # below → warning
    af60_snr_critical_db: float = 6.0     # below → critical
    af60_snr_tolerance_db: float = 0.0
    # Lien dégradé (consolidé) : potentiel sous ce plancher OU capacité totale
    # (dl+ul) sous ce plancher → critique. Plancher capacité aligné sur le seuil
    # d'affichage /lr-health (1.95 Gb/s = capacité nominale d'un backhaul AF60-LR
    # sain) : un lien P2P qui descend sous 1.95 Gb/s est considéré dégradé.
    af60_link_potential_min_pct: float = 30.0
    af60_total_capacity_min_mbps: float = 1950.0

    # Liens P2P LiteBeam (device_type ptp_litebeam) : plancher
    # de capacité totale (Mbps) sous lequel le lien inter-site est jugé dégradé
    # (p2p_link_substandard, notifié WhatsApp). Équivalent airMAX du plancher AF60
    # 1.95 Gb/s — un backhaul airMAX porte beaucoup moins, d'où un seuil dédié.
    # Sert à la fois à l'alerte ET à l'affichage de la section liens inter-sites.
    airmax_backhaul_capacity_min_mbps: float = 150.0

    # Seuil d'AFFICHAGE de la section « Liaisons entre sites » de /lr-health :
    # un AF60 dont la dernière capacité totale est < ce plancher y est surfacé
    # (critère unique, sur la dernière valeur en base — pas de fetch live).
    # 1.95 Gb/s = capacité nominale d'un backhaul AF60-LR sain.
    af60_capacity_display_min_mbps: float = 1950.0

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
    # airFiber 60 anti-flap.
    af60_signal_failure_threshold: int = 2
    af60_snr_failure_threshold: int = 2
    af60_link_down_failure_threshold: int = 2
    af60_link_substandard_failure_threshold: int = 3
    # Lien P2P airMAX dégradé : capacité volatile → débounce sur 4e cycle (count>3).
    p2p_link_substandard_failure_threshold: int = 3
    # Le nombre de clients fluctue (associations/désassociations transitoires) →
    # ouvre l'incident sur le 4e cycle saturé consécutif (count > 3).
    rocket_overload_failure_threshold: int = 3

    # Throughput anomaly — detect sudden drops vs exponential moving average
    throughput_anomaly_drop_pct: float = 50.0   # alert if rate < EMA * (1 - drop_pct/100)
    throughput_anomaly_min_mbps: float = 1.0    # ignore if EMA < this (nearly idle link)

    # Anomaly thresholds — UISP Power
    # Politique 2026-06-11 : deux alertes batterie DISTINCTES, toutes deux
    # critiques + notif immédiate, évaluées par batterie connectée :
    #   - batterie INTERNE (Li-Ion UPS) < battery_internal_critical_pct (50 %)
    #   - batterie EXTERNE (banc plomb) < battery_external_critical_pct (30 %)
    battery_internal_critical_pct: int = 50  # Li-Ion UPS interne
    battery_external_critical_pct: int = 30  # banc plomb externe
    # Legacy (ancienne alerte unique) — conservés pour compat, plus utilisés.
    battery_warning_pct: int = 30   # below → warning
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

    # Client-consumption materialized view refresh interval (minutes). The
    # view `client_consumption_30d` pre-aggregates 30-day byte deltas so
    # /clients/consumption?period=30d serves <100 ms instead of ~36 s.
    # Same 15-min cadence as lr-health: cumulative byte deltas don't change
    # perceptibly in 15 min for a daily/weekly/monthly usage report.
    client_consumption_matview_refresh_interval_minutes: int = 15

    # Same idea for the 7-day window (`client_consumption_7d`). The 7d
    # period was the second-slowest tab (~13 s of seq scan + external sort
    # on the live SQL path) — separate matview because the 30d aggregate
    # can't be subtracted down to 7d.
    client_consumption_7d_refresh_interval_minutes: int = 15

    # device_metrics retention was REMOVED (no automatic purge). Only
    # HISTORY_METRICS rows (byte counters) keep a time series; everything else
    # is collapsed to one latest row per metric by persist_device_metrics, so
    # it never accumulates. The byte-counter history is kept indefinitely so
    # the /clients custom date range can look arbitrarily far back. Trade-off:
    # device_metrics grows without bound — keep an eye on disk / autovacuum.

    # Equipment flapping — flap_detection_job counts the availability incidents
    # (rocket_down / switch_down / device_unreachable / uisp_power_unreachable /
    # airmax_down) a device has accumulated over the last flap_window_hours. A
    # device with more than flap_threshold_24h of them is unstable (repeated
    # down/up) and opens a `device_flapping` incident → WhatsApp. Availability
    # incidents are KEPT in DB after resolution (the downtime journal needs
    # them), so the count is reconstructed straight from the incidents table.
    flap_threshold_24h: int = 3
    flap_window_hours: int = 24
    flap_check_interval_minutes: int = 10

    # Network-wide high latency — network_latency_aggregate_job computes the
    # share of UP client LRs whose latest lr_latency_ms is at or above
    # lr_latency_critical_ms (100 ms). CONTRÔLE QUOTIDIEN (1440 min) : si la part
    # dépasse network_high_latency_pct (20 %) et que l'échantillon vaut au moins
    # network_latency_min_sample LRs, il envoie un message WhatsApp. Signal
    # réseau-wide (pas un incident par device) → envoi direct. Pas de flag /
    # rétabli : rapport quotidien, n'envoie que si la condition est remplie.
    network_high_latency_pct: int = 20
    network_latency_min_sample: int = 10
    network_latency_check_interval_minutes: int = 1440  # 24 h

    # Daily saturated-Rockets PDF report — rocket_saturation_report_job builds a
    # PDF listing every base-station Rocket whose installed clients reached its
    # capacity ceiling (current >= max, i.e. the rocket_client_overload state)
    # and sends it to the WhatsApp group as a document. Unlike the latency job
    # this is sent EVERY day (even when the list is empty) as a control report.
    # Fires once at scheduler boot (deploy) then daily at this hour, UTC
    # (Mauritania is GMT/UTC+0 → 07:00 local).
    rocket_saturation_report_enabled: bool = True
    rocket_saturation_report_hour: int = 7

    # Per-site infra-equipment budget — each site may hold at most SITE_INFRA_MAX
    # infra devices (Rockets + AF60 + PTP LiteBeam; switches, UISP Power and
    # client LRs are NOT counted). site_infra_report_job sends a daily PDF listing
    # every site with its count and the remaining (+N) / over-budget (-N) margin,
    # and the same roll-up is surfaced on the /capacity page. Like the saturation
    # report it fires once at scheduler boot then daily at the hour, UTC.
    site_infra_max: int = 14
    site_infra_report_enabled: bool = True
    site_infra_report_hour: int = 7

    # ── NetFlow traffic collector ────────────────────────────────────────────
    # Top public destinations our clients consult, grouped by operator/CDN (ASN).
    # The edge router (MikroTik) already exports NetFlow; we point it at this
    # server and a dedicated long-running collector process (RUN_MODE=collector,
    # app/tasks/collector_runner.py) listens on UDP, decodes the flows, resolves
    # each destination IP to its ASN (MaxMind GeoLite2-ASN) and periodically
    # flushes per-(time-bucket, ASN) byte aggregates into ``traffic_dest_stats``.
    # The /traffic page reads them back. This is NOT an APScheduler job (a UDP
    # listener is permanent, not interval-based) — hence its own container.
    netflow_collector_enabled: bool = False
    # Bind host INSIDE the container (0.0.0.0 is fine here — Docker only
    # publishes the port on the LAN IP via docker-compose.lan.yml, never on the
    # public interface; restrict the source to the router at the firewall).
    netflow_listen_host: str = "0.0.0.0"
    netflow_listen_port: int = 2055
    # How often the in-memory aggregate is written to the DB, and the size of
    # each time bucket flows are folded into. Kept at 1 min so the /traffic
    # "débit" view (bytes ÷ bucket seconds → Gb/s) reflects near-current
    # throughput rather than a 5-min average. ASN cardinality is small, so
    # 1-min rows stay cheap.
    netflow_flush_interval_seconds: int = 60
    netflow_bucket_minutes: int = 1
    # Prefixes treated as INTERNAL (our side). A flow's INTERNAL end is the
    # client; its other end is the Internet operator we attribute it to. Must
    # include RFC1918/CGNAT **and our own public WAN prefix**, otherwise post-NAT
    # download flows (operator → our public IP) look operator↔operator and are
    # dropped. Set the WAN prefix per deployment via NETFLOW_INTERNAL_PREFIXES.
    netflow_internal_prefixes: str = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10"
    # ASN resolution sources (IP → operator/CDN). Primary: iptoasn.com BGP-derived
    # TSVs (far more complete than GeoLite2 for the long tail — small/regional
    # networks, freshly announced prefixes). Fallback: MaxMind GeoLite2-ASN mmdb.
    # All mounted via the ./backend:/app bind. Refresh the iptoasn files
    # periodically (rebuilt daily). See backend/data/README.md.
    iptoasn_v4_path: str = "/app/data/ip2asn-v4.tsv.gz"
    iptoasn_v6_path: str = "/app/data/ip2asn-v6.tsv.gz"
    geoip_asn_db_path: str = "/app/data/GeoLite2-ASN.mmdb"
    # Retention of the aggregate rows (batched purge, like device_metrics).
    traffic_stats_retention_days: int = 90
    traffic_stats_retention_interval_minutes: int = 360  # every 6 h

    # History behind the device-modal charts (lr_metric_samples): latency, link
    # capacity, link rates. Rows are 5-min buckets written by
    # persist_device_metrics for the metrics in
    # lr_metric_history_service.GRAPH_METRICS — so the cost scales with the
    # NUMBER OF METRICS: ~800 LRs × 288 buckets/day × N metrics. Purged in
    # batches by lr_latency_retention_job.
    # Largeur d'un bucket de l'historique des courbes, en secondes. 60 = un point
    # par relevé de poll (résolution maximale permise par les données). 300 (5 min)
    # divise le volume par ~3 si la table devient trop lourde. Changer la valeur ne
    # réécrit PAS les lignes déjà stockées — elles gardent leur largeur d'origine.
    lr_metric_history_bucket_seconds: int = 60
    lr_metric_history_retention_days: int = 30
    lr_metric_history_retention_interval_minutes: int = 360  # every 6 h

    @property
    def netflow_internal_prefix_list(self) -> list[str]:
        return [p.strip() for p in self.netflow_internal_prefixes.split(",") if p.strip()]

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
