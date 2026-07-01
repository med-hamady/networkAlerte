// Shared base — every device row carries these columns regardless of subtype.
interface DeviceBase {
  id: number
  name: string
  ip_address: string | null   // NULLABLE depuis l'identité LR par MAC (IP volatile)
  status: string        // up | down | unknown
  location: string | null
  site: string | null   // résolu par trigger DB (LR → site du Rocket parent)
  snmp_community: string | null
  notes: string | null
  last_seen: string | null
  created_at: string
  updated_at: string
  mac_address: string | null
  hostname: string | null
  firmware_version: string | null
  auto_discovered: boolean
  first_discovered_at: string | null
  last_discovered_at: string | null
  policy_overrides: Record<string, PolicyOverride> | null
}

export interface Rocket extends DeviceBase {
  device_type: 'rocket'
  radio_tech: 'ltu' | 'airmax'
  // ceiling manuel de saturation clients (null = formule auto par famille/largeur).
  max_clients_override: number | null
  ssh_username: string | null
  ssh_port: number
  ssh_host_fingerprint: string | null
  has_ssh_password: boolean
}

// LiteBeam airMAX en lien point-à-point inter-sites (ni Rocket ni LR).
export interface PtpLiteBeam extends DeviceBase {
  device_type: 'ptp_litebeam'
  ssh_username: string | null
  ssh_port: number
  ssh_host_fingerprint: string | null
  has_ssh_password: boolean
  distance_m: number | null
}

export type LrModelVariant =
  | 'ltu_lr'
  | 'ltu_instant'
  | 'ltu_lite'
  | 'litebeam_5ac'
  | 'litebeam_m5'

export interface Lr extends DeviceBase {
  device_type: 'lr'
  model_variant: LrModelVariant
  rocket_id: number | null
  ssh_username: string | null
  ssh_port: number
  ssh_host_fingerprint: string | null
  has_ssh_password: boolean
  distance_m: number | null
  client_blocked: boolean
  client_blocked_at: string | null
  client_blocked_reason: string | null
  lan_interface: string
  client_block_enforced_at: string | null
  block_mode: BlockMode
  topology_mode: TopologyMode
}

export type BlockMode = 'full' | 'whatsapp_only'
export type TopologyMode = 'router' | 'bridge' | 'unknown'

export interface UispPower extends DeviceBase {
  device_type: 'uisp_power'
  api_username: string | null
  api_port: number
  has_api_password: boolean
}

export interface UispSwitch extends DeviceBase {
  device_type: 'uisp_switch'
  max_ports: number
  rocket_port_index: number | null
  port_min_speed_mbps: number
}

export type ManagementProtocol = 'ssh' | 'telnet'

export interface ClientModem extends DeviceBase {
  device_type: 'client_modem'
  lr_id: number | null
  management_protocol: ManagementProtocol
  management_port: number
  management_username: string | null
  management_host_fingerprint: string | null
  has_management_password: boolean
}

// airFiber 60 (AF60-LR) — lien backhaul 60 GHz. Mêmes creds API que Rocket.
export interface AirFiber extends DeviceBase {
  device_type: 'airfiber'
  ssh_username: string | null
  ssh_port: number
  ssh_host_fingerprint: string | null
  has_ssh_password: boolean
  distance_m: number | null
}

// Discriminated union — narrow by `device_type`.
export type Device = Rocket | Lr | UispPower | UispSwitch | ClientModem | AirFiber | PtpLiteBeam

// ──────────────────────────────────────────────────────────────────────────
// Form payloads — one DeviceFormData per type. The form switches its
// rendered fields by `device_type`, then submits the matching subset.
// ──────────────────────────────────────────────────────────────────────────

interface DeviceFormBase {
  name: string
  ip_address: string
  location: string
  snmp_community: string
  notes: string
}

export type RocketFormData = DeviceFormBase & {
  device_type: 'rocket'
  radio_tech: 'ltu' | 'airmax'
  ssh_username: string
  ssh_password: string   // write-only — empty = keep existing
  ssh_port: number
}

export type PtpLiteBeamFormData = DeviceFormBase & {
  device_type: 'ptp_litebeam'
  ssh_username: string
  ssh_password: string   // write-only — empty = keep existing
  ssh_port: number
}

export type LrFormData = DeviceFormBase & {
  device_type: 'lr'
  model_variant: LrModelVariant
  rocket_id: number | null
  ssh_username: string
  ssh_password: string
  ssh_port: number
}

export type UispPowerFormData = DeviceFormBase & {
  device_type: 'uisp_power'
  api_username: string
  api_password: string   // write-only — empty = keep existing
  api_port: number
}

export type UispSwitchFormData = DeviceFormBase & {
  device_type: 'uisp_switch'
  max_ports: number
  rocket_port_index: number | null
  port_min_speed_mbps: number
}

export type ClientModemFormData = DeviceFormBase & {
  device_type: 'client_modem'
  lr_id: number | null
  management_protocol: ManagementProtocol
  management_port: number
  management_username: string
  management_password: string   // write-only — empty = keep existing
}

export type AirFiberFormData = DeviceFormBase & {
  device_type: 'airfiber'
  ssh_username: string
  ssh_password: string   // write-only — empty = keep existing
  ssh_port: number
}

export type DeviceFormData =
  | RocketFormData
  | LrFormData
  | UispPowerFormData
  | UispSwitchFormData
  | ClientModemFormData
  | AirFiberFormData
  | PtpLiteBeamFormData

export interface Threshold {
  key: string
  label: string
  category: string
  category_label: string
  unit: string
  type: 'int' | 'float'
  min: number
  max: number
  step: number
  value: number
  default: number
  is_overridden: boolean
}

/** Per-device override on top of the base alert policy. */
export interface PolicyOverride {
  notify_immediately?: boolean
  channels?: string[]
  groupable?: boolean
  recovery_notification?: boolean
}

export interface Incident {
  id: number
  device_id: number
  title: string
  description: string | null
  severity: string      // info | warning | critical
  status: string        // open | acknowledged | resolved
  detected_at: string
  resolved_at: string | null
  created_at: string
  updated_at: string
  alert_type: string | null
  metric_name: string | null
  metric_value: number | null
  threshold_value: number | null
  last_triggered_at: string | null
  device_name: string | null
  device_type: string | null
  device_ip: string | null
  device_mac: string | null
  lr_model_variant: LrModelVariant | null
  message: string | null
  notify_immediately: boolean
  notification_channel_policy: string[]
}

// Human-readable labels for every alert_type the engine can raise.
// Keep aligned with backend/app/core/alert_labels.py — single operator vocabulary.
export const ALERT_TYPE_LABELS: Record<string, string> = {
  // Disponibilité
  rocket_down:             'Station de base (Rocket) hors ligne',
  switch_down:             'Switch hors ligne',
  device_unreachable:      'Équipement injoignable',
  airmax_down:             'Station de base airMAX hors ligne',
  // Interfaces et lien local
  radio_interface_down:    'Interface radio coupée',
  eth0_down:               'Lien Ethernet coupé',
  cpe_disconnected:        'Aucun client connecté à la station',
  // Qualité radio (descendant — base → client)
  signal_low:              'Signal radio faible',
  cinr_low:                'Qualité du signal radio faible (CINR)',
  ccq_low:                 'Qualité de connexion radio faible',
  radio_link_degraded:     'Lien radio dégradé',
  // Performance
  capacity_low:            'Capacité du lien radio faible',
  high_rx_tx_errors:       "Taux d'erreurs réseau élevé",
  throughput_anomaly:      'Anomalie de débit détectée',
  lr_link_substandard:     'Lien client sous le seuil',
  rocket_client_overload:  'Station de base saturée (trop de clients)',
  // Qualité radio UL (montant — client → base)
  ccq_ul_low:              'Qualité de connexion côté client faible',
  cinr_ul_low:             'Qualité du signal côté client faible (CINR)',
  capacity_ul_low:         'Capacité montante (côté client) faible',
  // Power & infra
  uisp_power_unreachable:  'UISP Power injoignable',
  battery_low_warning:     'Batterie faible',
  battery_low_critical:    'Batterie critique',
  voltage_anomaly:         "Tension d'alimentation anormale",
  mains_power_lost:        'Coupure secteur (sur batterie)',
  // Switch
  switch_port_down:        'Port du switch coupé',
  switch_port_speed_low:   'Vitesse du port switch dégradée',
  // Transit
  transit_unavailable:     'Transit Internet indisponible',
  lr_no_transit:           'Client (LR) sans accès Internet',
  lr_latency_high:         'Latence élevée du LR vers Internet',
  // Ping
  ping_instability:        'Ping instable',
  // Configuration
  lr_bridge_mode_misconfig: 'LR en mode bridge (blocage client inopérant)',
  // Sécurité
  security_anomaly:        "Volume anormal d'écritures API détecté",
  // Auto-découverte
  lr_discovered:           'Nouveau client (LR) détecté',
  lr_ip_changed:           "Adresse IP d'un client modifiée",
  lr_reassigned:           'Client (LR) reconnecté à une autre station',
}

export function alertTypeLabel(alertType: string | null): string {
  if (!alertType) return '—'
  return ALERT_TYPE_LABELS[alertType] ?? alertType
}

// Human-readable labels for every metric_name the engine attaches to incidents.
// Keep aligned with backend/app/core/alert_labels.py.
export const METRIC_LABELS: Record<string, string> = {
  signal_dbm:      'Niveau de signal (dBm)',
  cinr_db:         'Qualité signal/bruit CINR (dB)',
  ccq_pct:         'Qualité de connexion CCQ (%)',
  ul_ccq_pct:      'Qualité de connexion côté client (%)',
  ul_cinr_db:      'Qualité signal/bruit côté client (dB)',
  tx_rate_pct:     "Capacité d'émission (%)",
  rx_rate_pct:     'Capacité de réception (%)',
  error_rate_pct:  "Taux d'erreurs (%)",
  tx_drop_pct:     'Taux de paquets perdus (%)',
  lr_link_floors:  'Plancher lien client (potentiel/capacité/débit)',
  link_potential_pct:  'Potentiel du lien (%)',
  total_capacity_mbps: 'Capacité totale du lien (Mbps)',
  local_rx_rate_idx:   'Rate local (×)',
  remote_rx_rate_idx:  'Rate distant (×)',
  radio_if_up:     'État interface radio',
  eth_if_up:       'État interface Ethernet',
  peer_count:      'Nombre de clients connectés',
  lr_latency_ms:   'Latence LR → Internet (ms)',
  battery_li_ion_pct:        'Charge batterie Li-Ion (UPS interne) (%)',
  battery_li_ion_voltage_v:  'Tension batterie Li-Ion (V)',
  battery_lead_acid_pct:       'Charge banc plomb (externe) (%)',
  battery_lead_acid_voltage_v: 'Tension banc plomb (V)',
  output_max_power_w:  'Puissance max de sortie (W)',
  output_energy_wh:    'Énergie cumulée de sortie (Wh)',
  uptime_seconds:      'Uptime (s)',
  ac_connected:        'Secteur (AC) présent',
}

export function metricLabel(metricName: string | null): string {
  if (!metricName) return '—'
  return METRIC_LABELS[metricName] ?? metricName
}


// Résultat allégé de la barre de recherche /sites — GET /devices/search?q=…
export interface DeviceSearchResult {
  id: number
  name: string
  ip_address: string | null
  device_type: string
  site: string | null
  status: string
}

export interface MetricPoint {
  value: number
  unit: string | null
  collected_at: string
}

export type DeviceMetrics = Record<string, MetricPoint>

export interface HealthResponse {
  status: string
  app_name: string
  database: string
}

export interface GpuInfo {
  name: string
  memory_total_mb: number | null
  memory_used_mb: number | null
  temperature_c: number | null
  utilization_pct: number | null
}

export interface SystemInfo {
  hostname: string
  os_name: string
  cpu_count: number
  cpu_percent: number
  ram_total_gb: number
  ram_used_gb: number
  ram_percent: number
  disk_total_gb: number
  disk_used_gb: number
  disk_percent: number
  gpus: GpuInfo[]
}

// Human-readable labels for device_type values + radio_tech / model_variant
// refinements. Use `deviceLabel(device)` to get a single human-friendly string
// that distinguishes LTU Rockets from airMAX Rockets, LTU LRs from Litebeams.
export const DEVICE_TYPE_LABELS: Record<string, string> = {
  rocket:       'Rocket',
  lr:           'LR',
  uisp_switch:  'UISP Switch',
  uisp_power:   'UISP Power',
  client_modem: 'Modem client',
  airfiber:     'airFiber 60',
  ptp_litebeam: 'Liaison P2P (airMAX)',
}

export const LR_MODEL_VARIANT_LABELS: Record<LrModelVariant, string> = {
  ltu_lr:       'LTU LR',
  ltu_instant:  'LTU Instant',
  ltu_lite:     'LTU Lite',
  litebeam_5ac: 'Litebeam 5AC',
  litebeam_m5:  'Litebeam M5',
}

export function deviceTypeLabel(type: string): string {
  return DEVICE_TYPE_LABELS[type] ?? type
}

/** Radio family of an LR from its model_variant: 'airMAX' for Litebeams, 'LTU' otherwise. */
export function lrFamilyLabel(variant: LrModelVariant): 'airMAX' | 'LTU' {
  return variant.startsWith('litebeam') ? 'airMAX' : 'LTU'
}

/** Parent rocket id, or null for non-LR devices. Replaces the old `parent_id` access. */
export function parentRocketId(device: Device): number | null {
  return device.device_type === 'lr' ? device.rocket_id : null
}

/** Specific human label for a device — narrows Rockets by radio_tech and LRs by model_variant. */
export function deviceLabel(device: Device): string {
  if (device.device_type === 'rocket') {
    return device.radio_tech === 'airmax' ? 'Rocket airMAX' : 'LTU Rocket'
  }
  if (device.device_type === 'lr') {
    return LR_MODEL_VARIANT_LABELS[device.model_variant] ?? 'LR'
  }
  return deviceTypeLabel(device.device_type)
}

// Severity label + color helpers (centralised so badges stay consistent)
export const SEVERITY_LABELS: Record<string, string> = {
  info:     'INFO',
  warning:  'WARNING',
  critical: 'CRITICAL',
  dynamic:  'DYNAMIC',
}

export function severityLabel(s: string): string {
  return SEVERITY_LABELS[s] ?? s.toUpperCase()
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

export function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h === 0) return `${m} min`
  return `${h}h ${m.toString().padStart(2, '0')}min`
}

export function timeAgo(iso: string | null): string {
  if (!iso) return 'jamais'
  const diff = Date.now() - new Date(iso).getTime()
  const s = Math.floor(diff / 1000)
  if (s < 60) return `il y a ${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `il y a ${m}min`
  const h = Math.floor(m / 60)
  if (h < 24) return `il y a ${h}h`
  return `il y a ${Math.floor(h / 24)}j`
}

// ─── Bad installations (Liaisons clients) ──────────────────────────────────

export type BadInstallationVerdict = 'suspect' | 'critical'

export interface SignalEvidence {
  key: string         // recurrence | persistence | variety | gravity | outlier | duration
  label: string
  active: boolean
  value: string
  detail: string
}

export interface BadInstallationRow {
  lr_id: number
  lr_name: string
  lr_ip: string | null
  lr_mac: string | null
  model_variant: LrModelVariant
  distance_m: number | null
  first_discovered_at: string | null
  rocket_id: number | null
  rocket_name: string | null

  verdict: BadInstallationVerdict
  active_signals_count: number
  signals: SignalEvidence[]

  latest_signal_dbm: number | null
  latest_link_potential_pct: number | null
  latest_total_capacity_mbps: number | null
  latest_local_rx_rate_idx: number | null
  latest_remote_rx_rate_idx: number | null

  // RTT LR → Internet (ms), dernier relevé de la sonde SSH 60 s. Affichage seul.
  latency_ms: number | null

  signal_warning_threshold: number
  link_potential_floor_pct: number
  total_capacity_floor_mbps: number
  rx_rate_floor_idx: number
}

export const VERDICT_LABELS: Record<BadInstallationVerdict, string> = {
  suspect:  'Suspect — à inspecter',
  critical: 'Critique — à reprendre',
}

export interface BadInstallationsResponse {
  period_days: number
  generated_at: string
  items: BadInstallationRow[]
}

// Page « Liaisons clients » en mode live (état actuel) — pas de fenêtre 30 j.
// unreachable_count = LR exclus faute d'avoir pu être joints en direct.
export interface LiveLinkHealthResponse {
  generated_at: string
  unreachable_count: number
  items: BadInstallationRow[]
}

// Section « Liaisons entre sites (P2P) » — backhauls airFiber 60.
// Critère unique : dernière capacité totale < plancher (1.95 Gb/s), lue en base.
export interface SiteLinkRow {
  device_id: number
  name: string
  ip: string | null
  distance_m: number | null

  // "af60" (airFiber 60) ou "airmax" (Rocket/LiteBeam backhaul).
  link_type: 'af60' | 'airmax'

  latest_total_capacity_mbps: number | null
  capacity_floor_mbps: number

  // Affichage seul (dernières valeurs en base), hors filtre.
  latest_signal_dbm: number | null
  latest_snr_db: number | null
}

export interface SiteLinkHealthResponse {
  generated_at: string
  no_data_count: number
  items: SiteLinkRow[]
}

// ─── Clients à latence élevée (RTT LR → Internet ≥ seuil) ────────────────────
export interface HighLatencyRow {
  lr_id: number
  lr_name: string
  lr_ip: string | null
  lr_mac: string | null
  model_variant: LrModelVariant
  distance_m: number | null
  rocket_id: number | null
  rocket_name: string | null
  latency_ms: number
  latency_threshold_ms: number
}

export interface HighLatencyResponse {
  generated_at: string
  latency_threshold_ms: number
  items: HighLatencyRow[]
}

// ─── Capacité du réseau — clients consommés vs disponibles par famille/site ──
// consumed = clients connectés (peer_count), capacity = somme des max par Rocket
// (seuil rocket_client_overload). available = capacity − consumed (≥ 0).
// unknown = Rockets sans largeur de canal connue → exclus des totaux.
export interface CapacityBucket {
  consumed: number
  capacity: number
  available: number
  rockets: number
  unknown: number
}

export interface RocketCapacity {
  id: number
  name: string
  family: 'ltu' | 'airmax'
  current_clients: number
  max_clients: number | null            // ceiling effectif (override si défini, sinon formule)
  max_clients_auto: number | null       // valeur calculée par la formule (null = largeur inconnue)
  max_clients_override: number | null   // ceiling manuel posé par l'opérateur (null = auto)
  channel_width_mhz: number | null
}

export interface SiteCapacity {
  site: string
  ltu: CapacityBucket
  airmax: CapacityBucket
  unknown: number                 // total Rockets à capacité indéterminée (LTU + airMAX)
  rockets: RocketCapacity[]
}

// Budget d'équipements infra par site : count (Rockets + AF60 + PTP, hors
// switch et UISP Power) vs le maximum SITE_INFRA_MAX. remaining = max - count
// (positif = places libres → +N ; négatif = dépassement → -N).
export interface SiteInfra {
  site: string
  count: number
  remaining: number
  over: boolean
}

export interface NetworkInfraCapacity {
  threshold: number
  total_devices: number
  sites: SiteInfra[]
}

export interface NetworkCapacity {
  families: { ltu: CapacityBucket; airmax: CapacityBucket }
  sites: SiteCapacity[]
  infra: NetworkInfraCapacity
}

// ─── Top destinations Internet par opérateur/CDN (collecteur NetFlow) ───────
// Trafic client↔Internet agrégé par ASN/opérateur. Deux vues : volume (octets
// sur une période) et débit (Gb/s sur le dernier bucket). down = descendant
// (download/RX WAN), up = montant (upload/TX WAN). Sert à décider des caches
// (GGC/FNA/OCA). share_pct = part du total.
export interface TrafficDestination {
  asn: number | null
  operator: string
  down_bytes: number
  up_bytes: number
  total_bytes: number
  share_pct: number
}

export interface TopDestinations {
  period: '24h' | '7d' | '30d'
  total_down_bytes: number
  total_up_bytes: number
  destinations: TrafficDestination[]
}

export interface ThroughputOperator {
  asn: number | null
  operator: string
  down_mbps: number
  up_mbps: number
  share_pct: number
}

export interface Throughput {
  bucket_start: string | null
  window_seconds: number
  total_down_mbps: number
  total_up_mbps: number
  operators: ThroughputOperator[]
}

// Historique du débit (download) par opérateur dans le temps — graphe d'aires
// empilées. `times` = axe X ; chaque `series[i].down_mbps` est aligné sur `times`.
export interface ThroughputSeries {
  asn: number | null
  operator: string
  down_mbps: number[]
}

export interface ThroughputHistory {
  period: '1h' | '6h' | '24h'
  step_seconds: number
  times: string[]
  series: ThroughputSeries[]
  total_up_mbps: number[]
}

// ─── Network uptime — Journal des coupures ─────────────────────────────────

export interface FlapSubEpisode {
  started_at: string
  ended_at: string | null
  duration_seconds: number
}

export interface DowntimeEpisode {
  incident_id: number
  alert_type: string
  severity: string                  // warning | critical
  started_at: string
  ended_at: string | null           // null = still ongoing
  is_ongoing: boolean
  duration_seconds: number
  flap_count: number                // 1 = single outage, >1 = fused flapping
  flaps: FlapSubEpisode[]           // raw sub-incidents (empty when flap_count == 1)
}

export interface DeviceDowntime {
  device_id: number
  device_name: string
  device_ip: string
  device_type: string               // rocket | uisp_switch | uisp_power
  current_status: string            // up | down | unknown
  episodes_count: number            // after merging
  raw_episodes_count: number        // before merging — flapping signal
  total_downtime_seconds: number
  longest_episode_seconds: number
  availability_pct: number
  episodes: DowntimeEpisode[]
}

export interface DowntimeLogResponse {
  start: string
  end: string
  merge_gap_seconds: number
  items: DeviceDowntime[]
}

// ─── RPC-backed page payloads (logique centralisée côté DB) ─────────────────
// Ces formes sont renvoyées prêtes-à-afficher par des fonctions SQL ; le
// frontend ne fait QUE les rendre (aucun calcul / groupement / tri).

// Dashboard — fn_dashboard_summary()
export interface DashboardSummary {
  total: number
  up: number
  down: number
  sites: number
  pannes: number
  clients: number
  open_incidents: number
}

// /sites — fn_site_overview()
export interface SitePowerDevice {
  id: number
  name: string
  status: string
  power_source: 'mains' | 'battery' | null
  batteries: { slug: string; pct: number | null }[]
}
export interface SiteDownDevice {
  id: number
  name: string
  device_type: string
  ip_address: string | null
  status: string
  last_seen: string | null
}
export interface SiteOverviewItem {
  name: string
  infra: number
  clients_online: number
  clients_blocked: number
  pannes: number
  down_since: string | null
  down_devices: SiteDownDevice[]
  power_devices: SitePowerDevice[]
}

// /access — fn_access_clients(search, filter). Sourced ENTIRELY from UISP (no
// live poll): mode and reachable both come from the controller snapshot.
export interface AccessClientRow {
  id: number
  name: string
  ip_address: string | null
  client_blocked: boolean
  block_mode: BlockMode
  client_blocked_reason: string | null
  client_blocked_at: string | null
  client_block_enforced_at: string | null
  // UISP snapshot — last-known status from the controller, survives outages.
  uisp_status: string | null
  uisp_last_seen: string | null
  uisp_ap_name: string | null
  // effective_mode = uisp_mode (else 'unknown'); reachable = uisp_status active.
  effective_mode: TopologyMode
  reachable: boolean
}
export interface AccessClientsResponse {
  stats: {
    total: number
    active: number
    blocked_full: number
    blocked_whatsapp: number
    bridge: number
    disconnected: number
  }
  items: AccessClientRow[]
}

// "Pannes par site" — fn_site_outage_summary(start, end, merge_gap)
export interface OutageSiteDevice {
  device_id: number
  device_name: string
  device_type: string
  current_status: string
  episodes_count: number
  total_downtime_seconds: number
}
export interface OutageSite {
  site: string
  pannes: number
  downtime_seconds: number
  devices: OutageSiteDevice[]
}
export interface SiteOutageSummary {
  by_pannes: OutageSite[]
  by_downtime: OutageSite[]
}
