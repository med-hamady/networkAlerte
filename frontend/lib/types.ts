export interface Device {
  id: number
  name: string
  ip_address: string
  device_type: string
  model: string | null
  status: string        // up | down | unknown
  location: string | null
  snmp_community: string | null
  ssh_username: string | null
  ssh_port: number
  has_ssh_password: boolean
  notes: string | null
  last_seen: string | null
  created_at: string
  updated_at: string
  parent_id: number | null
  policy_overrides: Record<string, PolicyOverride> | null
}

export interface DeviceFormData {
  name: string
  ip_address: string
  device_type: string
  model: string
  location: string
  snmp_community: string
  ssh_username: string
  ssh_password: string   // write-only — empty = keep existing
  ssh_port: number
  notes: string
}

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
  // Alert engine fields (may be null for incidents created before V1)
  alert_type: string | null
  metric_name: string | null
  metric_value: number | null
  threshold_value: number | null
  probable_cause: string | null
  last_triggered_at: string | null
  // Joined device fields
  device_name: string | null
  device_type: string | null
  device_ip: string | null
  // Pre-formatted operator-facing message and policy fields
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
  channel_type: string             // slack | webhook | email
  config: Record<string, unknown>
  enabled: boolean
}

export interface NotificationChannelInput {
  name: string
  channel_type: string
  config: Record<string, unknown>
  enabled: boolean
}

// Human-readable labels for every alert_type the engine can raise
export const ALERT_TYPE_LABELS: Record<string, string> = {
  // Disponibilité
  rocket_down:            'Rocket hors ligne',
  lr_down:                'LR hors ligne',
  switch_down:            'Switch hors ligne',
  device_unreachable:     'Équipement injoignable',
  airmax_down:            'Rocket airMAX hors ligne',
  // Interface & lien local
  radio_interface_down:   'Interface radio DOWN',
  eth0_down:              'Lien Ethernet DOWN',
  cpe_disconnected:       'CPE déconnecté',
  // Qualité radio
  signal_low:             'Signal faible',
  cinr_low:               'CINR faible',
  ccq_low:                'CCQ faible',
  radio_link_degraded:    'Lien radio dégradé',
  // Performance
  capacity_low:           'Capacité faible',
  high_rx_tx_errors:      'Erreurs RX/TX',
  throughput_anomaly:     'Anomalie débit',
  // Alimentation
  uisp_power_unreachable: 'UISP Power inaccessible',
  battery_low_warning:    'Batterie faible',
  battery_low_critical:   'Batterie critique',
  voltage_anomaly:        'Anomalie tension',
  // Infrastructure & transit
  transit_unavailable:    'Transit indisponible',
  switch_port_down:        'Port switch DOWN',
  switch_port_speed_low:   'Port switch vitesse dégradée',
  lr_no_transit:           'LR sans transit internet',
  // Uplink
  ccq_ul_low:              'CCQ UL faible',
  cinr_ul_low:             'CINR UL faible',
  capacity_ul_low:         'Capacité UL faible',
}

export function alertTypeLabel(alertType: string | null): string {
  if (!alertType) return '—'
  return ALERT_TYPE_LABELS[alertType] ?? alertType
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
  // joined from incident
  incident_title: string | null
  incident_severity: string | null
  incident_alert_type: string | null
  // joined from device
  device_id: number | null
  device_name: string | null
  device_ip: string | null
  // True for warning incidents waiting for the next digest batch
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

// Human-readable labels for device_type values
export const DEVICE_TYPE_LABELS: Record<string, string> = {
  ltu_rocket:    'LTU Rocket',
  ltu_lr:        'LTU LR',
  airmax_rocket: 'Rocket airMAX',
  uisp_switch:   'UISP Switch',
  uisp_power:    'UISP Power',
}

export function deviceTypeLabel(type: string): string {
  return DEVICE_TYPE_LABELS[type] ?? type
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
  priority: string                  // critique | élevé | moyen
  category: string                  // disponibilite | radio | alimentation | transit | switch | performance
  title: string
  description: string
  affected_devices: string[]
  alert_type: string | null
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
