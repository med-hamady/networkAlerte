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
  blocked_categories: string[]
  content_block_mode: 'denylist' | 'allowlist'
  content_block_enforced_at: string | null
  topology_mode: TopologyMode
  /** Sans IP (hors du sweep de ping) ET non vu par UISP depuis
   *  OUT_OF_SUPERVISION_DAYS : aucune source ne mesure cet abonné. Ni une
   *  panne constatée ni un accès actif — l'UI le distingue d'un « INCONNU ». */
  out_of_supervision: boolean
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
  fiber_port_index: number | null
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
  fiber_port_index: number | null
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

// Une anomalie du bandeau du dashboard : elle y reste jusqu'à ce qu'un
// opérateur clique « Résoudre », même si elle s'est rétablie entre-temps.
// ⚠️ Distinct d'un Incident, et volontairement : l'incident, lui, s'ouvre et se
// résout tout seul (et est purgé au passage) — voir
// backend/app/core/alert_constants.MANUAL_ACK_ALERT_TYPES.
export interface ManualAlert {
  id: number
  device_id: number
  alert_type: string
  severity: string      // info | warning | critical
  title: string
  description: string | null
  detected_at: string
  acknowledged_at: string | null
  acknowledged_by: string | null
  device_name: string | null
  device_type: string | null
  device_ip: string | null
  device_site: string | null
}

export interface ManualAlertList {
  alerts: ManualAlert[]
  count: number
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
  high_rx_tx_errors:       "Taux d'erreurs réseau élevé",
  lr_link_substandard:     'Lien client sous le seuil',
  rocket_client_overload:  'Station de base saturée (trop de clients)',
  // Qualité radio UL (montant — client → base)
  ccq_ul_low:              'Qualité de connexion côté client faible',
  cinr_ul_low:             'Qualité du signal côté client faible (CINR)',
  // Power & infra
  uisp_power_unreachable:  'UISP Power injoignable',
  battery_low_warning:     'Batterie faible',
  battery_low_critical:    'Batterie critique',
  voltage_anomaly:         "Tension d'alimentation anormale",
  mains_power_lost:        'Coupure secteur (sur batterie)',
  // Switch
  switch_port_down:        'Port du switch coupé',
  switch_port_speed_low:   'Vitesse du port switch dégradée',
  // Backhaul P2P + stabilité — affichés dans le bandeau à acquitter
  af60_link_substandard:   'Liaison F60 dégradée',
  device_flapping:         'Équipement instable (coupures répétées)',
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

// ─── Topologie inter-sites (graphe des backhauls, source : data-links UISP) ──
// Le maillage site→site n'est stocké nulle part chez nous : il est lu en direct
// sur le contrôleur. Le graphe N'EST PAS un arbre — d'où `depth` (couche) plutôt
// qu'un parent unique suffisant, et `layout.extra_edges` pour les boucles de
// redondance, qui doivent être TRACÉES et non masquées.

// Un bout de liaison. `supervised=false` ⇒ ni statut ni mesure de ce côté (le
// switch UniFi du HQ, hors de notre inventaire).
export interface TopologyEnd {
  uisp_name: string
  mac: string | null
  site: string | null
  supervised: boolean
  device_id: number | null
  name: string
  device_type: string | null
  status: string | null
  capacity_mbps: number | null
  link_potential_pct: number | null
  // Port de switch sur lequel cette extrémité est câblée (détecté depuis les
  // data-links UISP) et sa vitesse négociée, relevée en SNMP sur le switch.
  uplink_switch: string | null
  uplink_port: number | null
  port_speed_mbps: number | null
}

/** Port de switch d'un CÔTÉ d'une liaison. `null` = câblage inconnu. */
export interface TopologyPort {
  switch: string | null
  port: number | null
  // `null` = port connu mais vitesse non lue (ou ifSpeed=0, cage SFP) : une
  // vitesse inconnue n'est pas une vitesse dégradée.
  speed_mbps: number | null
}

// Santé d'une liaison. `unmeasured` = AUCUN des deux bouts ne rend de mesure :
// à rendre neutre, JAMAIS en vert — un lien qu'on ne mesure pas n'est pas un
// lien sain.
export interface TopologyHealth {
  state: 'down' | 'unmeasured' | 'measured'
  capacity_mbps: number | null
  link_potential_pct: number | null
  measured_ends: number
  // Plancher applicable à la famille de matériel (AF60 vs PTP LiteBeam), et le
  // verdict. Les deux sont calculés côté BACKEND contre les réglages réels :
  // recopier un barème ici le ferait diverger du seuil qui déclenche l'alerte.
  floor_mbps: number | null
  degraded: boolean
  // Trafic écoulé par la liaison. ⚠️ `unknown` n'est PAS `idle` : les liaisons
  // fibre ont des switches aux deux bouts, et un switch n'expose aucun débit en
  // SNMP. Le rendu doit s'abstenir (vert) plutôt que les déclarer inertes.
  traffic: 'active' | 'idle' | 'unknown'
  traffic_mbps: number | null
  // Débit PAR DIRECTION, nommé par les sites (a = site_a, b = site_b).
  // ⚠️ Pas de « descendant/montant » ici : ces mots n'ont de sens que vu d'un
  // bout, et `dl` d'une extrémité est le `ul` de l'autre. C'est le rendu, qui
  // connaît la relation parent/enfant, qui les traduit.
  traffic_a_to_b_mbps: number | null
  traffic_b_to_a_mbps: number | null
  // OCCUPATION du lien — le temps d'antenne consommé (AF60 uniquement, dérivé
  // du débit et de la capacité par sens). À ne pas confondre avec `traffic`
  // ci-dessus : celui-là dit si ça PASSE, celle-ci si c'est PLEIN. Un backhaul
  // de secours qui encaisse toute une branche est `active` (donc vert) tout en
  // étant à bout de souffle.
  // ⚠️ `null` = non mesurée, ce qui n'est PAS « au repos » — s'abstenir, ne
  // jamais rendre une liaison fluide faute de mesure. Le seuil et le verdict
  // viennent du BACKEND, comme `floor_mbps`/`degraded`.
  occupancy_pct: number | null
  // PAR DIRECTION, nommée par les sites (a = site_a, b = site_b) — même
  // convention que `traffic_a_to_b_mbps`. Le total dit que le lien est plein,
  // ces deux-là par quel bout il se remplit : 89/5 et 47/47 appellent des
  // gestes différents.
  occupancy_a_to_b_pct: number | null
  occupancy_b_to_a_pct: number | null
  occupancy_floor_pct: number
  saturated: boolean
}

export interface TopologyPhysicalLink {
  type: string
  state: string | null
  device_a: TopologyEnd
  device_b: TopologyEnd
  health: TopologyHealth
}

// Une arête = une liaison LOGIQUE entre deux sites, portant 1..n liens
// physiques (`redundant` quand plusieurs radios relient les deux mêmes sites).
export interface TopologyEdge {
  site_a: string
  site_b: string
  is_tree_edge: boolean
  redundant: boolean
  // Support physique : pilote le STYLE du trait (radio en tirets, filaire en
  // trait plein). Une liaison mixte compte comme filaire.
  medium: 'wireless' | 'wired'
  links: TopologyPhysicalLink[]
  health: TopologyHealth
  // Port de switch de chaque côté (a = site_a, b = site_b). Sur une liaison
  // redondante, c'est le port le PLUS LENT qui est retenu : c'est lui qui bride.
  port_a: TopologyPort | null
  port_b: TopologyPort | null
}

export interface TopologySite {
  site: string
  depth: number | null
  parent: string | null
  degree: number
  reachable: boolean
  // Compteur affiché sous le site, façon contrôleur (« 14/1 ») : nombre
  // d'équipements d'INFRA du site, et combien ne répondent plus.
  device_count: number
  device_down_count: number
  // Site ENTIÈREMENT tombé (tous ses équipements down). C'est le seul cas qui
  // rougit ses liaisons — un équipement HS ne met pas un site à terre.
  is_down: boolean
  // SATURATION — occupation de la liaison la plus chargée du site, et le
  // verdict (calculé côté backend contre le seuil réel de l'alerte).
  // ⚠️ Orthogonal à `is_down`/`device_down_count` : un site en parfaite santé
  // peut être saturé, et c'est LE cas intéressant. À rendre comme un signal
  // distinct, jamais comme une nuance de la couleur de disponibilité.
  // `null` = aucune liaison mesurée (les liaisons fibre n'ont pas d'occupation)
  // — ce qui n'est PAS « fluide ».
  occupancy_pct: number | null
  saturated: boolean
  // Position du pylône (table `site_locations`), pour la vue CARTE. `null` =
  // position inconnue : le site n'est pas plaçable et la carte le NOMME au lieu
  // de l'escamoter. 'uisp' = semée depuis le contrôleur, 'manual' = corrigée.
  latitude: number | null
  longitude: number | null
  position_source: 'uisp' | 'manual' | null
}

// ─── Routes vers Internet ────────────────────────────────────────────────────
// Puisque le graphe porte des boucles, un site a souvent PLUSIEURS chemins vers
// la racine. Le backend les énumère, les classe et nomme le goulot de chacun.
//
// ⚠️ Ce sont les chemins que le CÂBLAGE PERMET. Ni OSPF ni la table de routage
// ne sont lus : rien ici n'affirme par où le trafic passe réellement. L'UI doit
// le dire — sans cette phrase, l'écran se lit comme un diagnostic de routage.

/** Un saut d'un chemin — une liaison logique, parcourue de `from` vers `to`. */
export interface TopologyRouteHop {
  // ⚠️ CLÉ DE RÉSOLUTION vers `edges[]` : l'orientation est celle de l'arête
  // (site_a <= site_b), PAS le sens de la marche. La réorienter ferait rater le
  // lookup `${site_a}|${site_b}` en silence — plus de surlignage, plus
  // d'infobulle, et aucune erreur nulle part.
  site_a: string
  site_b: string
  // Le sens de parcours vers la racine, que la paire ne porte pas.
  from: string
  to: string
  // ⚠️ Un saut FIBRE ne se juge PAS comme un saut radio : la seule question est
  // « up, et du trafic passe ». Il n'a ni occupation ni marge, ne porte jamais
  // le goulot, et ne compte pas comme un trou de mesure — les trois dorsales du
  // HQ sont en fibre, et les traiter en « non mesurées » faisait afficher
  // « départage impossible » sur ARF1, AT1 et CT1.
  is_fibre: boolean
  // Fibre dont le port SFP ne passe plus la lumière. ⚠️ Distinct d'un simple
  // « hors service » : l'équipement RÉPOND (le site reste joignable par sa
  // radio de secours), c'est le verre qui est coupé — et le geste terrain n'est
  // pas le même. Sans ce contrôle, une dorsale morte passait pour saine.
  fibre_cut: boolean
  // 'idle' sur une dorsale = anormal, à signaler. ⚠️ 'unknown' n'est PAS 'idle' :
  // un switch n'expose pas toujours son débit.
  traffic: 'active' | 'idle' | 'unknown' | null
  // Charge COURANTE du saut (dernière valeur relevée en base, écrasée à chaque
  // poll) et ce qu'il peut encore prendre, en Mb/s. C'est LE chiffre actionnable
  // — un pourcentage ne se traduit en rien. ⚠️ Pas un pic d'historique : sur un
  // lien radio, l'état de maintenant décrit mieux le réseau qu'un pic d'hier.
  // `null` sur la fibre (par construction) et sous 5 % d'occupation, où la
  // projection n'extrapolerait que du bruit.
  peak_traffic_mbps: number | null
  peak_occupancy_pct: number | null
  max_rate_mbps: number | null
  headroom_mbps: number | null
  // `null` = occupation non mesurée (elle n'existe que sur les AF60). Ce n'est
  // PAS 0 : n'afficher ni « 0 % » ni une barre vide, qui se liraient « fluide ».
  occupancy_pct: number | null
  // Verdict du BACKEND contre le seuil réel de l'alerte — ne recopier aucun
  // barème ici (surtout pas les bandes 70/90 de DeviceDetailModal).
  saturated: boolean
  state: 'down' | 'unmeasured' | 'measured' | null
  degraded: boolean
  capacity_mbps: number | null
  // Deux radios entre les deux mêmes sites = UN saut redondant, jamais deux
  // chemins : la couche IP ne choisit pas la radio.
  redundant: boolean
  links_count: number
  medium: 'wireless' | 'wired' | null
  is_bottleneck: boolean
}

export interface TopologyRoute {
  id: string
  // Les sites traversés, du site vers la racine.
  sites: string[]
  hop_count: number
  // Sauts qui portent réellement une contrainte de débit. `0` = chemin tout en
  // FIBRE : rien ne le bride côté radio, donc c'est le meilleur possible.
  radio_hop_count: number
  hops: TopologyRouteHop[]
  // LE verdict, en Mb/s : ce que le chemin peut encore prendre, et son plafond,
  // bornés par son maillon le plus juste. `null` sur un chemin tout fibre (rien
  // ne le borne) comme sans historique — `radio_hop_count` sépare les deux cas.
  headroom_mbps: number | null
  max_rate_mbps: number | null
  max_occupancy_pct: number | null
  // Le maillon qui plafonne le chemin. ⚠️ C'est celui de plus petite MARGE, pas
  // le plus occupé en % : un lien à 90 % de 1950 Mb/s laisse 195 Mb/s, un lien à
  // 50 % de 300 Mb/s n'en laisse que 150 — c'est le second qui bride.
  bottleneck: {
    site_a: string
    site_b: string
    occupancy_pct: number | null
    headroom_mbps: number | null
    max_rate_mbps: number | null
    peak_traffic_mbps: number | null
  } | null
  // Le maillon le plus ÉTROIT — une autre question que le goulot, jamais à
  // confondre avec lui.
  min_capacity_mbps: number | null
  measured_hops: number
  // Jusqu'où va la mesure, comptée sur les sauts RADIO seuls. Un chemin tout en
  // fibre est 'full' : il n'a rien à mesurer, ce n'est pas un trou.
  coverage: 'full' | 'partial' | 'none'
  // Dorsale fibre debout mais SANS trafic — anormal, donc signalé ; le chemin
  // reste éligible pour autant.
  fibre_idle_hops: { site_a: string; site_b: string }[]
  // Ce chemin cède au MÊME endroit qu'un autre (son id) : il n'est donc pas une
  // alternative pour ce point de rupture. ⚠️ On l'ANNOTE, on ne le cache pas —
  // toutes les sorties d'un site doivent être visibles.
  same_bottleneck_as: string | null
  // Un saut `unmeasured` n'est PAS un saut `down` : seul un lien tombé rend la
  // route inutilisable. Elle reste AFFICHÉE — l'opérateur doit voir que sa
  // seconde route existe et qu'elle est morte.
  usable: boolean
  down_hops: { site_a: string; site_b: string; fibre_cut: boolean }[]
  degraded_hops: { site_a: string; site_b: string }[]
  // ⚠️ AUCUN champ de « projection après bascule », et c'est délibéré : ajouter
  // le trafic du lien coupé à la charge du secours serait un DOUBLE COMPTAGE.
  // Dès que la dorsale tombe, elle ne porte plus rien et le trafic est DÉJÀ
  // reparti par les liaisons de secours — leur mesure courante le contient donc
  // déjà. Après une coupure, on LIT ; on ne projette pas.
  is_best: boolean
}

export interface TopologySiteRoutes {
  site: string
  // ⚠️ Qui DÉCIDE de la direction du trafic. Un site à plusieurs sorties
  // arbitre ; un site à une seule ne choisit rien et remet son trafic au site
  // du dessus. Afficher « 3 routes » à un enfant lui prête un choix qu'il n'a
  // pas — ses chemins sont ceux de son décideur.
  role: 'root' | 'decider' | 'child' | 'isolated' | null
  // Ses sorties RÉELLES vers la racine (premiers sauts des chemins). ⚠️ Pas ses
  // liaisons voisines : un cul-de-sac n'est pas une sortie.
  exits: string[]
  // Pour un enfant : le premier site en amont qui a un vrai choix — c'est là
  // qu'on agit. `null` = sortie unique droit sur la racine (NR1, SNDE) :
  // personne ne peut le rerouter.
  decider: string | null
  // 'racine' | 'aucun chemin vers la racine' | null. Jamais une liste vide
  // muette : une absence de route se lirait comme un oubli du calcul.
  reason: string | null
  best_id: string | null
  // Renseigné SEULEMENT quand best_id est null, et à afficher tel quel : ne pas
  // trancher est une réponse, à condition de dire pourquoi.
  best_reason: string | null
  found: number
  kept: number
  // Bornes de l'énumération, rapportées et jamais silencieuses.
  truncated: { by_hops: boolean; by_budget: boolean }
  paths: TopologyRoute[]
}

export interface NetworkTopology {
  available: boolean
  reason?: string
  // Date du dernier rapatriement du CÂBLAGE (job quotidien). La santé des
  // liaisons affichée à côté est, elle, de maintenant — l'UI doit distinguer les
  // deux, sinon tout l'écran semble avoir le même âge.
  synced_at: string | null
  root: string | null
  // 'paramètre' (TOPOLOGY_ROOT_SITE) ou 'degré maximal' (repli). Affiché : un
  // repli silencieux se lirait comme une déduction.
  root_source: string
  sites: TopologySite[]
  edges: TopologyEdge[]
  // Indexé par nom de site. Un site absent du dict n'a pas été calculé (réponse
  // d'une version antérieure) — traiter comme « pas de routes », pas comme zéro.
  routes: Record<string, TopologySiteRoutes>
  layout: {
    components: string[][]
    orphan_sites: string[]
    unreached_sites: string[]
    extra_edges: { site_a: string; site_b: string; type: string }[]
  }
  stats: {
    infra_sites: number
    edges: number
    physical_links: number
    unsupervised_ends: string[]
    // Sites dont l'énumération a buté sur une borne : leur liste de chemins
    // n'est pas complète, et le panneau doit le dire.
    routes_truncated_sites: string[]
  }
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

// /dashboard/network-health — fn_network_health(start, end, gap)
export interface NetworkHealth {
  network_health_pct: number
  sites_measured: number
  window_start: string
  window_end: string
  sites: { site: string; availability_pct: number }[]
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
  /** Sans IP (donc hors du sweep de ping) ET non vu par UISP depuis
   *  OUT_OF_SUPERVISION_DAYS : aucune source ne mesure cet abonné. Exclu des
   *  « accès actifs » — ce n'est ni une panne constatée ni un client actif. */
  out_of_supervision: boolean
  /** Jours depuis la dernière vue UISP (null = jamais vu). */
  days_offline: number | null
  /** Une règle de coupure est en place sur le routeur (repli). */
  router_blocked: boolean
}
export interface AccessStats {
  total: number
  active: number
  blocked_full: number
  blocked_whatsapp: number
  bridge: number
  disconnected: number
  out_of_supervision: number
  /** Sous-ensembles de out_of_supervision par ancienneté (down ≥ 30 / 90 j). */
  out_of_supervision_30d: number
  out_of_supervision_90d: number
  /** Bloqués, décomposés par mécanisme (mutuellement exclusifs). */
  blocked: number
  blocked_ssh: number      // coupé sur le LR (SSH)
  blocked_router: number   // coupé sur le routeur (repli, LR injoignable)
  blocked_pending: number  // ni l'un ni l'autre — le job rattrapera
}
export interface AccessClientsResponse {
  stats: AccessStats
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
export interface OutageExtraDevice {
  device_id: number
  device_name: string
  device_type: string
  // Residual downtime beyond the switch (by_downtime) / residual outage count
  // beyond the switch (by_pannes). Both fields are always present.
  extra_downtime_seconds: number
  extra_episodes: number
}
export interface OutageSite {
  site: string
  pannes: number
  downtime_seconds: number
  devices: OutageSiteDevice[]
  // Only present on by_downtime: non-switch devices down longer than the site's
  // switch, with the residual downtime beyond the switch outage.
  extra_devices?: OutageExtraDevice[]
}
export interface SiteOutageSummary {
  by_pannes: OutageSite[]
  by_downtime: OutageSite[]
}

// --- Journal FAI (blocages / déblocages) -----------------------------------
// Historique lu du fichier d'audit (backend/logs/fai_actions.log), et LR encore
// en souffrance lus de la base. Voir backend/app/api/endpoints/fai_journal.py.
export interface FaiJournalEntry {
  timestamp: string
  /** IDENT_KO = rien n'a été tenté : l'équipement joint à l'adresse de la
   *  fiche n'était pas celui attendu (MAC differente), donc agir aurait
   *  touché un autre abonné. */
  action:
    | 'BLOCK' | 'UNBLOCK' | 'RETRY_OK' | 'ABANDON' | 'IDENT_KO'
    // Repli : coupure posée / retirée sur le routeur de cœur parce que
    // l'équipement du client ne répondait pas.
    | 'ROUTER_BLOCK' | 'ROUTER_UNBLOCK'
  ok: boolean
  mac: string | null
  name: string
  mode: string
  // 'payment' | 'enforce' | 'script', ou le nom du script appelant déduit du motif
  // côté backend (ex. 'Block_all.php') — affiché tel quel s'il n'est pas dans SOURCE_LABEL.
  source: string
  /** L'AGENT à l'origine de l'action, transmis par l'appelant : e-mail d'un
   *  opérateur (geste manuel) ou libellé automatique ('auto system', 'auto
   *  retry'). `null` = non transmis — appelant qui ne l'envoie pas, action
   *  interne (renforcement, script), ou ligne antérieure au champ. Distinct de
   *  `source`, qui dit quel SYSTÈME a appelé, pas qui est derrière. */
  user: string | null
  message: string
  /** Une transcription de la session SSH est-elle archivée pour cette action ?
   *  `message` est une phrase que NOUS avons rédigée ; la preuve, elle, est ce
   *  que l'équipement a reçu et répondu. Faux sur les actions antérieures à la
   *  fonctionnalité, en mode whatsapp_only, et sur les ordres purement routeur. */
  has_evidence: boolean
}
/** Preuve d'exécution d'une action : la transcription brute de la session SSH. */
export interface FaiEvidence {
  timestamp: string
  mac: string | null
  action: string
  transcript: string
}
export interface FaiJournalStats {
  total: number
  ok: number
  failed: number
  abandoned: number
}
export interface FaiAttentionRow {
  id: number
  name: string
  mac: string | null
  ip_address: string | null
  site: string | null
  // unenforceable = le LR refuse la connexion SSH → intervention technique.
  // pending       = l'ordre est rejoué automatiquement (LR éteint).
  kind: 'unenforceable' | 'pending'
  intent: 'block' | 'unblock'
  reason: string | null
  since: string | null
  // Le repli routeur couvre-t-il ce client ? Si oui il EST coupé : seule la
  // coupure sur son propre équipement reste à poser.
  router_blocked: boolean
}
export interface FaiJournalResponse {
  entries: FaiJournalEntry[]
  stats: FaiJournalStats
  attention: FaiAttentionRow[]
}

// ─── Règles du routeur (page /router-rules) ─────────────────────────────────
// Ce que le routeur de cœur porte VRAIMENT, lu en direct à la demande — à
// distinguer du journal (ce qui s'est passé) et de la base (ce qu'on croit
// avoir posé).
export interface RouterRuleRow {
  rule_id: string | null
  mac: string
  comment: string
  // Une règle désactivée existe sans couper : elle explique un client
  // « bloqué » toujours en ligne.
  disabled: boolean
  dynamic: boolean
  packets: number | null
  bytes: number | null
  // supervisor = posée par nous (marque dans le commentaire) ; legacy = le
  // reste, dont le système historique. Indice d'origine, pas une preuve.
  origin: 'supervisor' | 'legacy'
  // unexpected = client coupé alors que la base ne le veut plus (il a payé) ;
  // unknown = MAC hors inventaire ; expected = coupure voulue.
  state: 'expected' | 'unexpected' | 'unknown'
  lr_id: number | null
  name: string | null
  site: string | null
  ip_address: string | null
  client_blocked: boolean | null
  // Pourquoi ce client est coupé. Un impayé et un client « hors supervision »
  // (perdu de vue, pas mauvais payeur) posent la MÊME règle sur le routeur :
  // ce champ est la seule chose qui les distingue.
  blocked_reason: string | null
  router_blocked: boolean | null
  enforced_on_lr: boolean | null
  // Le LR coupe déjà ce client → la règle du routeur double la coupure et aurait
  // dû être retirée. Calculé côté backend par client_block_service : un LR
  // abandonné garde un enforced_on_lr tout en restant couvert par le routeur
  // exprès, donc ce drapeau n'est PAS déductible de enforced_on_lr.
  redundant: boolean
}
export interface RouterMissingRule {
  lr_id: number
  name: string
  mac: string | null
  site: string | null
  ip_address: string | null
  enforced_on_lr: boolean
  blocked_reason: string | null
}
export interface RouterRulesResponse {
  // false = repli routeur non configuré : afficher l'explication, jamais une
  // liste vide (qui se lirait « aucun client bloqué »).
  available: boolean
  error: string | null
  fetched_at: string
  host: string
  rules: RouterRuleRow[]
  missing: RouterMissingRule[]
  stats: {
    total: number
    supervisor: number
    legacy: number
    unexpected: number
    unknown: number
    redundant: number
    disabled: number
    missing: number
  }
}

// ─── Diagnostics d'accès (page /access-diagnostics) ─────────────────────────
// Deux anomalies de gestion du parc abonné : LR qui refusent le SSH, et LR vus
// par le radio mais absents du roster UISP (non provisionnés).
export type SshRefusalStatus = 'auth_failed' | 'ssh_disabled' | 'host_key_mismatch'
export interface SshRefusedRow {
  id: number
  name: string
  mac: string | null
  ip_address: string | null
  site: string | null
  ap_name: string | null
  ssh_status: SshRefusalStatus
  ssh_error: string | null
  ssh_checked_at: string | null
  client_blocked: boolean
}
export interface RadioNotInUispRow {
  id: number
  name: string
  mac: string | null
  ip_address: string | null
  site: string | null
  ap_name: string | null
  status: string
  last_discovered_at: string | null
  // Dernier enrôlement RÉUSSI qu'on a poussé. Renseigné alors que la ligne est
  // encore là = adopté par le contrôleur mais pas encore repris par le roster
  // (sync quotidien). Sans cette date, ce cas se confond avec « jamais tenté ».
  uisp_enrolled_at: string | null
  // Faux quand le LR n'a ni SSH ni IP : rien à joindre, bouton grisé.
  enrollable: boolean
}
export interface AccessDiagnosticsResponse {
  ssh_refused: SshRefusedRow[]
  radio_not_in_uisp: RadioNotInUispRow[]
  counts: { ssh_refused: number; radio_not_in_uisp: number }
  // Faux si UISP_DEVICE_KEY n'est pas configurée côté serveur.
  enrollment_available: boolean
}

// Enrôlement UISP : pose de la clé du contrôleur sur un CPE par SSH. `ok`
// signifie ADOPTÉ par le contrôleur (constaté sur l'équipement), pas seulement
// « clé écrite ».
export interface UispEnrollResult {
  ok: boolean
  message: string
  uisp_enrolled_at: string | null
}
export interface UispEnrollBulkResult {
  attempted: number
  enrolled: number
  // Déjà provisionnés pour ce contrôleur : NON modifiés. Compté à part —
  // sinon un lot de clés orphelines ressemblerait à un succès complet alors que
  // rien n'a été régularisé.
  skipped: number
  failed: number
  results: {
    id: number; name: string; mac: string | null
    ok: boolean; skipped: boolean; message: string
  }[]
  message: string
}

// ─── Historique des courbes de la fiche équipement (lr_metric_samples) ──────
// Une courbe par métrique (latence, capacité du lien, débits). Alimenté par
// persist_device_metrics : chaque relevé est replié dans un bucket de 5 min.
// Les fenêtres larges sont re-binnées côté backend.
export interface MetricHistPoint {
  bucket_start: string
  avg_value: number
  // Extrêmes du bucket : un pic court est noyé par la moyenne, la bande min/max
  // le garde visible.
  min_value: number
  max_value: number
  sample_count: number
}
export interface MetricOption {
  name: string
  label: string
  unit: string
}
export interface MetricHistory {
  device_id: number
  metric_name: string
  label: string
  unit: string
  // L'axe Y doit-il partir de 0 (vrai pour une grandeur : ms, Mb/s).
  zero_based: boolean
  start: string
  end: string
  // Largeur d'un point en secondes (300 sur 24h, plus large au-delà).
  bin_seconds: number
  // Seuil d'alerte effectif, et son sens : 'max' = alerte au-dessus (latence),
  // 'min' = alerte en dessous (capacité). null = métrique sans seuil.
  threshold: number | null
  threshold_direction: 'max' | 'min' | null
  // Les seules métriques que CE device a en historique → les onglets du graphe.
  available_metrics: MetricOption[]
  // Trous volontaires : un bucket sans relevé est ABSENT, jamais ramené à 0.
  points: MetricHistPoint[]
}

// ── Carte des clients (/map) ─────────────────────────────────────────────────
// Position lue SUR l'équipement (airOS system.cfg) par lr_plan_sync, stockée
// verbatim. Le backend sépare ce qui est plaçable de ce qui est faux : il ne
// filtre pas en silence, il rend les deux listes.
export interface ClientMapPoint {
  id: number
  name: string
  ip_address: string | null
  status: string
  site: string | null
  model_variant: string
  ap_name: string | null
  client_blocked: boolean
  plan_download_mbps: number | null
  plan_upload_mbps: number | null
  latitude: number
  longitude: number
  // false = le site de ce client n'a pas de position connue → aucune liaison
  // ne peut être tracée (le marqueur reste visible).
  linked?: boolean
}

// Un point hors Mauritanie : gardé et nommé pour que le terrain le corrige.
export type ClientMapOutlier = ClientMapPoint & { reason: string }

// Un site = un pylône. Tous ses secteurs partagent cette position (dispersion
// mesurée : 4 à 29 m), d'où UN marqueur par site et non un par Rocket.
export interface MapSite {
  site: string
  latitude: number
  longitude: number
  source: string
  client_count: number
}

// Une position portée par PLUSIEURS clients : ce n'est pas une adresse mais une
// valeur recopiée au provisioning (mesuré : 26 clients sur un point, rattachés
// à 10 sites). Affichée comptée et SANS liaison — tracer N traits depuis une
// position fausse propagerait l'erreur.
export interface MapCluster {
  latitude: number
  longitude: number
  count: number
  sites: string[]
  clients: { id: number; name: string; site: string | null; status: string }[]
}

export interface ClientMapResponse {
  sites: MapSite[]
  points: ClientMapPoint[]
  clusters: MapCluster[]
  outliers: ClientMapOutlier[]
  stats: {
    total: number
    with_position: number
    plotted: number
    stacked_points: number
    stacked_clients: number
    outliers: number
    without_position: number
    sites: number
    linked: number
  }
  bbox: { lat_min: number; lat_max: number; lon_min: number; lon_max: number }
}
