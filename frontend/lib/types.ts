// Shared base — every device row carries these columns regardless of subtype.
interface DeviceBase {
  id: number
  name: string
  ip_address: string
  status: string        // up | down | unknown
  location: string | null
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
  ssh_username: string | null
  ssh_port: number
  ssh_host_fingerprint: string | null
  has_ssh_password: boolean
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
}

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

// Discriminated union — narrow by `device_type`.
export type Device = Rocket | Lr | UispPower | UispSwitch | ClientModem

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

export type DeviceFormData =
  | RocketFormData
  | LrFormData
  | UispPowerFormData
  | UispSwitchFormData
  | ClientModemFormData

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
  probable_cause: string | null
  last_triggered_at: string | null
  device_name: string | null
  device_type: string | null
  device_ip: string | null
  message: string | null
  recommended_action: string
  notify_immediately: boolean
  notification_channel_policy: string[]
}

/** Operational policy attached to a single alert_type. */
export interface AlertPolicy {
  alert_type: string
  severity: string                 // info | warning | critical | dynamic
  recommended_action: string
  notify_immediately: boolean
  channels: string[]
  groupable: boolean
  recovery_notification: boolean
}

/** Notification channel stored in the DB (overrides env-based fallback). */
export interface NotificationChannel {
  id: number
  name: string
  channel_type: string             // email
  config: Record<string, unknown>
  enabled: boolean
}

export interface NotificationChannelInput {
  name: string
  channel_type: string
  config: Record<string, unknown>
  enabled: boolean
}

// Human-readable labels for every alert_type the engine can raise.
// Keep aligned with backend/app/core/alert_labels.py — single operator vocabulary.
export const ALERT_TYPE_LABELS: Record<string, string> = {
  // Disponibilité
  rocket_down:             'Station de base (Rocket) hors ligne',
  lr_down:                 'Client (LR) hors ligne',
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
  // Qualité radio UL (montant — client → base)
  ccq_ul_low:              'Qualité de connexion côté client faible',
  cinr_ul_low:             'Qualité du signal côté client faible (CINR)',
  capacity_ul_low:         'Capacité montante (côté client) faible',
  // Power & infra
  uisp_power_unreachable:  'UISP Power injoignable',
  battery_low_warning:     'Batterie faible',
  battery_low_critical:    'Batterie critique',
  voltage_anomaly:         "Tension d'alimentation anormale",
  // Switch
  switch_port_down:        'Port du switch coupé',
  switch_port_speed_low:   'Vitesse du port switch dégradée',
  // Transit
  transit_unavailable:     'Transit Internet indisponible',
  lr_no_transit:           'Client (LR) sans accès Internet',
  // Ping
  ping_instability:        'Latence ping instable',
  ping_latency_high:       'Latence ping élevée',
  // Auto-découverte
  lr_discovered:           'Nouveau client (LR) détecté',
  lr_ip_changed:           "Adresse IP d'un client modifiée",
  lr_reassigned:           'Client (LR) reconnecté à une autre station',
  lr_disappeared:          'Client (LR) disparu',
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
  radio_if_up:     'État interface radio',
  eth_if_up:       'État interface Ethernet',
  peer_count:      'Nombre de clients connectés',
  ping_latency_ms: 'Latence ping (ms)',
}

export function metricLabel(metricName: string | null): string {
  if (!metricName) return '—'
  return METRIC_LABELS[metricName] ?? metricName
}

export const PROBABLE_CAUSE_LABELS: Record<string, string> = {
  switch_down:        'Switch HS',
  local_link_issue:   'Câble / port switch',
  radio_link_issue:   'Lien radio (HW)',
  radio_quality_issue:'Qualité RF',
}

export function probableCauseLabel(cause: string | null): string {
  if (!cause) return '—'
  return PROBABLE_CAUSE_LABELS[cause] ?? cause
}

export interface AlertRecord {
  id: number
  incident_id: number
  channel_id: number | null
  message: string
  status: string          // sent | failed | pending
  sent_at: string | null
  created_at: string
  incident_title: string | null
  incident_severity: string | null
  incident_alert_type: string | null
  device_id: number | null
  device_name: string | null
  device_ip: string | null
  is_pending_digest: boolean
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

// ─── Reporting types ────────────────────────────────────────────────────────

export interface ReportPeriodSummary {
  date_from: string
  date_to: string
  total_incidents: number
  critical_count: number
  warning_count: number
  info_count: number
  open_count: number
  resolved_count: number
  acknowledged_count: number
  devices_supervised: number
}

export interface DeviceReliability {
  device_id: number
  device_name: string
  device_type: string
  location: string | null
  current_status: string
  total_incidents: number
  downtime_incidents: number
  avg_resolution_minutes: number | null
}

export interface AlertTypeFrequency {
  alert_type: string
  alert_type_label: string
  occurrence_count: number
  affected_device_count: number
  avg_resolution_minutes: number | null
}

export interface RadioMetrics {
  device_id: number
  device_name: string
  avg_signal_dbm: number | null
  min_signal_dbm: number | null
  avg_cinr_db: number | null
  avg_ccq_pct: number | null
}

export interface WeakPoint {
  device_id: number
  device_name: string
  pattern_description: string
  alert_type: string | null
  occurrence_count: number
}

export interface Recommendation {
  priority: string
  category: string
  title: string
  description: string
  affected_devices: string[]
  alert_type: string | null
}

// ─── Bad installations (Liaisons clients) ──────────────────────────────────

export type BadInstallationVerdict = 'watch' | 'suspect' | 'critical'

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
  lr_ip: string
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

  signal_warning_threshold: number
}

export const VERDICT_LABELS: Record<BadInstallationVerdict, string> = {
  watch:    'À surveiller',
  suspect:  'Suspect — à inspecter',
  critical: 'Critique — à reprendre',
}

export interface BadInstallationsResponse {
  period_days: number
  generated_at: string
  items: BadInstallationRow[]
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

export interface SupervisionReport {
  generated_at: string
  period: ReportPeriodSummary
  device_reliability: DeviceReliability[]
  alert_frequencies: AlertTypeFrequency[]
  radio_metrics: RadioMetrics[]
  weak_points: WeakPoint[]
  recommendations: Recommendation[]
}
