'use client'

import { useMemo } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, DowntimeLogResponse } from '@/lib/types'

const SITE_FALLBACK = 'Sans site'
const WINDOW_DAYS = 7
const REFRESH = 60_000

// Only network infrastructure counts as a site outage. LR clients are excluded —
// an unreachable client is never an infra incident.
const INFRA_TYPES = new Set(['rocket', 'uisp_switch', 'uisp_power', 'airfiber'])

// Format a downtime duration (seconds) the same way as /network-uptime.
function fmtDuration(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`
  if (secs < 3_600) return `${Math.floor(secs / 60)} min`
  const h = Math.floor(secs / 3_600)
  const m = Math.round((secs % 3_600) / 60)
  return m === 0 ? `${h}h` : `${h}h ${m.toString().padStart(2, '0')}min`
}

interface SiteAgg {
  name: string
  pannes: number   // total merged outage episodes over the window
  downtime: number // cumulated downtime in seconds over the window
}

export default function SiteOutageCharts({ devices }: { devices: Device[] | undefined }) {
  // 7-day window — recomputed once (stable enough; SWR refresh keeps data fresh).
  const { startIso, endIso } = useMemo(() => {
    const end = new Date()
    const start = new Date(end.getTime() - WINDOW_DAYS * 24 * 3_600_000)
    return { startIso: start.toISOString(), endIso: end.toISOString() }
  }, [])

  const { data: log, isLoading } = useSWR<DowntimeLogResponse>(
    endpoints.downtimeLog(startIso, endIso), fetcher, { refreshInterval: REFRESH },
  )

  // device_id → site name. Infra by its own location; an LR by its parent rocket.
  const siteByDeviceId = useMemo(() => {
    const rocketById = new Map(
      devices?.filter(d => d.device_type === 'rocket').map(d => [d.id, d]) ?? [],
    )
    const map = new Map<number, string>()
    devices?.forEach(d => {
      let site: string
      if (d.device_type === 'lr') {
        const rk = d.rocket_id != null ? rocketById.get(d.rocket_id) : undefined
        site = rk?.location?.trim() || SITE_FALLBACK
      } else {
        site = d.location?.trim() || SITE_FALLBACK
      }
      map.set(d.id, site)
    })
    return map
  }, [devices])

  // Aggregate downtime episodes / cumulated seconds per site.
  const sites = useMemo<SiteAgg[]>(() => {
    const map = new Map<string, SiteAgg>()
    log?.items.forEach(it => {
      if (!INFRA_TYPES.has(it.device_type)) return // exclude LR clients
      const name = siteByDeviceId.get(it.device_id) ?? SITE_FALLBACK
      const agg = map.get(name) ?? { name, pannes: 0, downtime: 0 }
      agg.pannes += it.episodes_count
      agg.downtime += it.total_downtime_seconds
      map.set(name, agg)
    })
    return [...map.values()]
  }, [log, siteByDeviceId])

  const byPannes = useMemo(
    () => [...sites].filter(s => s.pannes > 0).sort((a, b) => b.pannes - a.pannes),
    [sites],
  )
  const byDowntime = useMemo(
    () => [...sites].filter(s => s.downtime > 0).sort((a, b) => b.downtime - a.downtime),
    [sites],
  )

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {Array.from({ length: 2 }, (_, i) => (
          <div key={i} className="rounded-xl bg-white border border-blue-100 h-80 animate-pulse" />
        ))}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <ChartCard
        title="Nombre de pannes par site"
        subtitle={`Épisodes de coupure — ${WINDOW_DAYS} derniers jours`}
        rows={byPannes.map(s => ({
          name: s.name,
          value: s.pannes,
          label: `${s.pannes}`,
        }))}
        barClass="bg-red-400"
      />
      <ChartCard
        title="Temps de panne par site"
        subtitle={`Downtime cumulé — ${WINDOW_DAYS} derniers jours`}
        rows={byDowntime.map(s => ({
          name: s.name,
          value: s.downtime,
          label: fmtDuration(s.downtime),
        }))}
        barClass="bg-orange-400"
      />
    </div>
  )
}

interface ChartRow {
  name: string
  value: number
  label: string
}

function ChartCard({
  title, subtitle, rows, barClass,
}: {
  title: string
  subtitle: string
  rows: ChartRow[]
  barClass: string
}) {
  const max = rows.reduce((m, r) => Math.max(m, r.value), 0) || 1

  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5">
      <div className="mb-4">
        <h3 className="font-semibold text-blue-900">{title}</h3>
        <p className="text-xs text-blue-400 mt-0.5">{subtitle}</p>
      </div>

      {rows.length === 0 ? (
        <div className="py-12 text-center">
          <p className="text-green-600 font-semibold text-sm">✓ Aucune coupure sur la période</p>
        </div>
      ) : (
        <div className="space-y-2.5 max-h-80 overflow-y-auto pr-1">
          {rows.map(r => (
            <div key={r.name} className="flex items-center gap-3">
              <div className="w-28 shrink-0 text-xs text-slate-600 truncate text-right" title={r.name}>
                {r.name}
              </div>
              <div className="flex-1 bg-slate-100 rounded h-5 overflow-hidden">
                <div
                  className={`h-full ${barClass} rounded transition-all`}
                  style={{ width: `${Math.max(2, (r.value / max) * 100)}%` }}
                />
              </div>
              <div className="w-16 shrink-0 text-xs font-semibold text-slate-800 text-right tabular-nums">
                {r.label}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
