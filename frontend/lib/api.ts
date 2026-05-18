import type {
  AlertPolicy,
  AlertRecord,
  BadInstallationRow,
  BadInstallationsResponse,
  Device,
  DeviceFormData,
  DowntimeLogResponse,
  HealthResponse,
  Incident,
  NotificationChannel,
  NotificationChannelInput,
  SupervisionReport,
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
  devices:              `${API_BASE}/devices`,
  device:               (id: number) => `${API_BASE}/devices/${id}`,
  incidents:            `${API_BASE}/incidents`,
  incident:             (id: number) => `${API_BASE}/incidents/${id}`,
  deviceMetrics:        (id: number) => `${API_BASE}/devices/${id}/metrics/latest`,
  deviceMetricsLive:    (id: number) => `${API_BASE}/devices/${id}/metrics/live`,
  checkSsh:             (id: number) => `${API_BASE}/devices/${id}/check-ssh`,
  checkPing:            (id: number) => `${API_BASE}/devices/${id}/check-ping`,
  pingFromLr:           (id: number) => `${API_BASE}/devices/${id}/ping-from-lr`,
  pingTarget:           (lrId: number) => `${API_BASE}/devices/${lrId}/ping-target`,
  shellTicket:          (id: number) => `${API_BASE}/devices/${id}/shell-ticket`,
  discoverModems:       (lrId: number) => `${API_BASE}/devices/${lrId}/discover-modems`,
  systemInfo:           `${API_BASE}/system/info`,
  alertPolicies:        `${API_BASE}/alert-policies`,
  alertPolicy:          (alertType: string) => `${API_BASE}/alert-policies/${alertType}`,
  notificationChannels: `${API_BASE}/notification-channels`,
  notificationChannel:  (id: number) => `${API_BASE}/notification-channels/${id}`,
  alertRecords:         (params?: string) => `${API_BASE}/notifications${params ? `?${params}` : ''}`,
  testEmail:            `${API_BASE}/notifications/test-email`,
  reportGenerate:       (params: string) => `${API_BASE}/reports/generate?${params}`,
  thresholds:           `${API_BASE}/system/thresholds`,
  badInstallations:     (days = 30) => `${API_BASE}/lr-health/bad-installations?days=${days}`,
  clientsConsumption:   (period: '24h' | '7d' | '30d' | 'lifetime') => `${API_BASE}/clients/consumption?period=${period}`,
  downtimeLog:          (startIso: string, endIso: string) =>
    `${API_BASE}/network-uptime/downtime-log?start=${encodeURIComponent(startIso)}&end=${encodeURIComponent(endIso)}`,
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

export async function createNotificationChannel(
  data: NotificationChannelInput,
): Promise<NotificationChannel> {
  const res = await fetch(endpoints.notificationChannels, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return jsonOrThrow<NotificationChannel>(res)
}

export async function updateNotificationChannel(
  id: number,
  patch: Partial<NotificationChannelInput>,
): Promise<NotificationChannel> {
  const res = await fetch(endpoints.notificationChannel(id), {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  return jsonOrThrow<NotificationChannel>(res)
}

export async function deleteNotificationChannel(id: number): Promise<void> {
  const res = await fetch(endpoints.notificationChannel(id), {
    method: 'DELETE',
  })
  if (!res.ok && res.status !== 204) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `HTTP ${res.status}`)
  }
}

export async function generateReport(
  dateFrom: string,
  dateTo: string,
): Promise<SupervisionReport> {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo })
  const res = await fetch(endpoints.reportGenerate(params.toString()))
  return jsonOrThrow<SupervisionReport>(res)
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

export interface TestEmailResult {
  status: string
  recipients: string[]
  smtp_host: string
}

export async function sendTestEmail(): Promise<TestEmailResult> {
  const res = await fetch(endpoints.testEmail, { method: 'POST' })
  return jsonOrThrow<TestEmailResult>(res)
}

export interface ShellTicketResponse {
  ticket: string
  expires_in: number
}

export async function requestShellTicket(deviceId: number): Promise<ShellTicketResponse> {
  const res = await fetch(endpoints.shellTicket(deviceId), { method: 'POST' })
  return jsonOrThrow<ShellTicketResponse>(res)
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
  AlertPolicy,
  AlertRecord,
  BadInstallationRow,
  BadInstallationsResponse,
  Device,
  DowntimeLogResponse,
  HealthResponse,
  Incident,
  NotificationChannel,
  NotificationChannelInput,
  SupervisionReport,
  SystemInfo,
}
