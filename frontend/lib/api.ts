import type {
  BadInstallationRow,
  BadInstallationsResponse,
  LiveLinkHealthResponse,
  BlockMode,
  Device,
  DeviceFormData,
  DowntimeLogResponse,
  HealthResponse,
  Incident,
  SystemInfo,
  Threshold,
} from './types'

// All requests go through the same-origin Next.js route handler at /api/proxy.
// That handler injects the X-API-Key header server-side, so the secret never
// lands in the browser bundle. See app/api/proxy/[...path]/route.ts.
const API_BASE = '/api/proxy'

export const fetcher = (url: string) =>
  fetch(url).then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return r.json()
  })

export const endpoints = {
  authLogin:            `${API_BASE}/auth/login`,
  authLogout:           `${API_BASE}/auth/logout`,
  authMe:               `${API_BASE}/auth/me`,
  authChangePassword:   `${API_BASE}/auth/change-password`,
  health:               `${API_BASE}/health`,
  // limit=1000 : le endpoint liste plafonne à 100 par défaut. Au-delà de 100
  // devices (le parc a dépassé ce seuil), la liste était tronquée → des LR
  // pointaient vers un Rocket parent absent de la réponse → rangés "Sans site"
  // sur la page /sites (siteOf cherche le parent dans la liste). 1000 = max
  // autorisé par l'API ; à dépasser ce seuil il faudra paginer pour de vrai.
  // (POST createDevice réutilise cette URL : le query param est ignoré.)
  devices:              `${API_BASE}/devices?limit=1000`,
  // Une seule page (drill-down /sites) : équipements d'un site, filtrés côté
  // backend par la colonne indexée devices.site → petite réponse rapide.
  devicesBySite:        (site: string) => `${API_BASE}/devices?site=${encodeURIComponent(site)}&limit=1000`,
  // Recherche /sites : IP (infra + LR) ou nom (LR — porte le téléphone client).
  devicesSearch:        (q: string) => `${API_BASE}/devices/search?q=${encodeURIComponent(q)}`,
  device:               (id: number) => `${API_BASE}/devices/${id}`,
  incidents:            `${API_BASE}/incidents`,
  incident:             (id: number) => `${API_BASE}/incidents/${id}`,
  deviceMetrics:        (id: number) => `${API_BASE}/devices/${id}/metrics/latest`,
  checkSsh:             (id: number) => `${API_BASE}/devices/${id}/check-ssh`,
  checkPing:            (id: number) => `${API_BASE}/devices/${id}/check-ping`,
  pingFromLr:           (id: number) => `${API_BASE}/devices/${id}/ping-from-lr`,
  pingTarget:           (lrId: number) => `${API_BASE}/devices/${lrId}/ping-target`,
  discoverModems:       (lrId: number) => `${API_BASE}/devices/${lrId}/discover-modems`,
  blockClient:          (lrId: number) => `${API_BASE}/devices/${lrId}/block-client`,
  unblockClient:        (lrId: number) => `${API_BASE}/devices/${lrId}/unblock-client`,
  systemInfo:           `${API_BASE}/system/info`,
  thresholds:           `${API_BASE}/system/thresholds`,
  badInstallations:     `${API_BASE}/lr-health/bad-installations`,
  siteLinks:            `${API_BASE}/lr-health/site-links`,
  highLatencyClients:   `${API_BASE}/lr-health/high-latency`,
  clientsConsumption:   (period: '24h' | '7d' | '30d' | 'lifetime') => `${API_BASE}/clients/consumption?period=${period}`,
  networkCapacity:      `${API_BASE}/network-capacity`,
  topDestinations:      (period: '24h' | '7d' | '30d') => `${API_BASE}/traffic/top-destinations?period=${period}`,
  trafficThroughput:    `${API_BASE}/traffic/throughput`,
  trafficThroughputHistory: (period: '1h' | '6h' | '24h') => `${API_BASE}/traffic/throughput-history?period=${period}`,
  downtimeLog:          (startIso: string, endIso: string) =>
    `${API_BASE}/network-uptime/downtime-log?start=${encodeURIComponent(startIso)}&end=${encodeURIComponent(endIso)}`,
  // Logique centralisée côté DB (fonctions RPC) — payloads prêts-à-afficher.
  dashboardSummary:     `${API_BASE}/dashboard/summary`,
  sitesOverview:        `${API_BASE}/sites/overview`,
  accessClients:        (search: string, filter: string) =>
    `${API_BASE}/access/clients?search=${encodeURIComponent(search)}&filter=${encodeURIComponent(filter)}`,
  siteOutageSummary:    (startIso: string, endIso: string) =>
    `${API_BASE}/network-uptime/site-summary?start=${encodeURIComponent(startIso)}&end=${encodeURIComponent(endIso)}`,
}

// ---------------------------------------------------------------------------
// Auth helpers (login / logout / current user / change password)
// ---------------------------------------------------------------------------

export interface CurrentUser {
  id: number
  username: string
  full_name: string | null
  enabled: boolean
  last_login_at: string | null
}

export async function logout(): Promise<void> {
  // The cookie is HttpOnly so we cannot clear it client-side — only the
  // backend can (Set-Cookie with Max-Age=0 in the logout response).
  await fetch(endpoints.authLogout, { method: 'POST' })
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const res = await fetch(endpoints.authChangePassword, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
}

export interface DiagResult { ok: boolean; message: string }

export async function runDiag(url: string): Promise<DiagResult> {
  const res = await fetch(url, { method: 'POST' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<DiagResult>
}

// Ping an arbitrary IP from an LR (jump host) — used to test a discovered
// modem candidate before it is saved as a device.
export async function pingTargetFromLr(lrId: number, target: string): Promise<DiagResult> {
  const res = await fetch(endpoints.pingTarget(lrId), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ target }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json() as Promise<DiagResult>
}

export interface ClientBlockResult {
  ok: boolean
  message: string
  client_blocked: boolean
  block_mode: BlockMode
  client_block_enforced_at: string | null
}

// Block / unblock a client's internet on its LR over SSH. `mode` picks the
// flavour: 'full' (shut LAN port — total cut) or 'whatsapp_only' (iptables
// allowlist leaving DNS + WhatsApp reachable). `block=false` ignores
// reason/mode and fully restores access.
export async function setClientBlock(
  lrId: number,
  block: boolean,
  reason?: string,
  mode?: BlockMode,
): Promise<ClientBlockResult> {
  const res = await fetch(
    block ? endpoints.blockClient(lrId) : endpoints.unblockClient(lrId),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: block
        ? JSON.stringify({ reason: reason ?? null, mode: mode ?? null })
        : undefined,
    },
  )
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<ClientBlockResult>
}

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export async function updateDevice(
  id: number,
  patch: Record<string, unknown>,
): Promise<Device> {
  const res = await fetch(endpoints.device(id), {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  return jsonOrThrow<Device>(res)
}

export async function createDevice(data: DeviceFormData): Promise<Device> {
  // Empty strings on optional text fields → null so Pydantic accepts them.
  const payload: Record<string, unknown> = { ...data }
  for (const key of Object.keys(payload)) {
    if (payload[key] === '') payload[key] = null
  }
  const res = await fetch(endpoints.devices, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return jsonOrThrow<Device>(res)
}

export async function deleteDevice(id: number): Promise<void> {
  const res = await fetch(endpoints.device(id), { method: 'DELETE' })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
}

export async function getThresholds(): Promise<Threshold[]> {
  const res = await fetch(`${API_BASE}/system/thresholds`)
  return jsonOrThrow<Threshold[]>(res)
}

export async function patchThresholds(updates: Record<string, number>): Promise<Threshold[]> {
  const res = await fetch(`${API_BASE}/system/thresholds`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  })
  return jsonOrThrow<Threshold[]>(res)
}

export async function resetThreshold(key: string): Promise<void> {
  const res = await fetch(`${API_BASE}/system/thresholds/${key}`, { method: 'DELETE' })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
}

export interface LanNeighbor {
  ip: string
  mac: string
  interface: string
  is_default_gateway: boolean
  vendor: string
  model_guess: string | null
}

export interface DiscoverModemsResponse {
  lr_id: number
  candidates: LanNeighbor[]
}

export async function discoverModemsViaLr(lrId: number): Promise<DiscoverModemsResponse> {
  const res = await fetch(endpoints.discoverModems(lrId), { method: 'POST' })
  return jsonOrThrow<DiscoverModemsResponse>(res)
}

// Typed wrappers for SWR (pass to useSWR as key)
export type {
  BadInstallationRow,
  BadInstallationsResponse,
  LiveLinkHealthResponse,
  Device,
  DowntimeLogResponse,
  HealthResponse,
  Incident,
  SystemInfo,
}
