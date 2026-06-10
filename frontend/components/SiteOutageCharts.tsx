'use client'

import { useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, DeviceDowntime, DowntimeEpisode, DowntimeLogResponse } from '@/lib/types'
import { deviceTypeLabel } from '@/lib/types'

const SITE_FALLBACK = 'Sans site'
const WINDOW_DAYS = 7
const REFRESH = 60_000

// Only network infrastructure counts as a site outage. LR clients are excluded —
// an unreachable client is never an infra incident.
const INFRA_TYPES = new Set(['rocket', 'uisp_switch', 'uisp_power', 'airfiber'])

// Format a downtime duration (seconds).
function fmtDuration(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`
  if (secs < 3_600) return `${Math.floor(secs / 60)} min`
  const h = Math.floor(secs / 3_600)
  const m = Math.round((secs % 3_600) / 60)
  return m === 0 ? `${h}h` : `${h}h ${m.toString().padStart(2, '0')}min`
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

interface SiteAgg {
  name: string
  pannes: number              // total merged outage episodes over the window
  downtime: number            // cumulated downtime in seconds over the window
  devices: DeviceDowntime[]   // equipment that was down at least once
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

  // Aggregate downtime episodes / cumulated seconds per site, keeping the
  // list of affected devices so a site can be expanded to its equipment.
  const sites = useMemo<SiteAgg[]>(() => {
    const map = new Map<string, SiteAgg>()
    log?.items.forEach(it => {
      if (!INFRA_TYPES.has(it.device_type)) return // exclude LR clients
      const name = siteByDeviceId.get(it.device_id) ?? SITE_FALLBACK
      const agg = map.get(name) ?? { name, pannes: 0, downtime: 0, devices: [] }
      agg.pannes += it.episodes_count
      agg.downtime += it.total_downtime_seconds
      agg.devices.push(it)
      map.set(name, agg)
    })
    // Inside each site, worst downtime first.
    map.forEach(s => s.devices.sort((a, b) => b.total_downtime_seconds - a.total_downtime_seconds))
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
      <SiteOutageCard
        title="Nombre de pannes par site"
        subtitle={`Épisodes de coupure — ${WINDOW_DAYS} derniers jours · cliquer un site pour le détail`}
        sites={byPannes}
        valueOf={s => s.pannes}
        labelOf={s => `${s.pannes}`}
        barClass="bg-red-400"
      />
      <SiteOutageCard
        title="Temps de panne par site"
        subtitle={`Downtime cumulé — ${WINDOW_DAYS} derniers jours · cliquer un site pour le détail`}
        sites={byDowntime}
        valueOf={s => s.downtime}
        labelOf={s => fmtDuration(s.downtime)}
        barClass="bg-orange-400"
      />
    </div>
  )
}

function SiteOutageCard({
  title, subtitle, sites, valueOf, labelOf, barClass,
}: {
  title: string
  subtitle: string
  sites: SiteAgg[]
  valueOf: (s: SiteAgg) => number
  labelOf: (s: SiteAgg) => string
  barClass: string
}) {
  const [openSite, setOpenSite] = useState<string | null>(null)
  const max = sites.reduce((m, s) => Math.max(m, valueOf(s)), 0) || 1

  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5">
      <div className="mb-4">
        <h3 className="font-semibold text-blue-900">{title}</h3>
        <p className="text-xs text-blue-400 mt-0.5">{subtitle}</p>
      </div>

      {sites.length === 0 ? (
        <div className="py-12 text-center">
          <p className="text-green-600 font-semibold text-sm">✓ Aucune coupure sur la période</p>
        </div>
      ) : (
        <div className="space-y-1.5 max-h-96 overflow-y-auto pr-1">
          {sites.map(s => {
            const isOpen = openSite === s.name
            return (
              <div key={s.name}>
                <button
                  type="button"
                  onClick={() => setOpenSite(o => (o === s.name ? null : s.name))}
                  className="w-full flex items-center gap-3 py-1 group"
                  title="Cliquer pour voir les équipements en panne"
                >
                  <svg
                    className={`w-3.5 h-3.5 shrink-0 text-blue-400 transition-transform ${isOpen ? 'rotate-90' : ''}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                  <div className="w-24 shrink-0 text-xs text-slate-600 truncate text-right group-hover:text-blue-700" title={s.name}>
                    {s.name}
                  </div>
                  <div className="flex-1 bg-slate-100 rounded h-5 overflow-hidden">
                    <div
                      className={`h-full ${barClass} rounded transition-all`}
                      style={{ width: `${Math.max(2, (valueOf(s) / max) * 100)}%` }}
                    />
                  </div>
                  <div className="w-16 shrink-0 text-xs font-semibold text-slate-800 text-right tabular-nums">
                    {labelOf(s)}
                  </div>
                </button>

                {isOpen && <SiteDeviceList devices={s.devices} />}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// Equipment of one site that was down at least once, each with its downtime
// total and the individual outage episodes (start → end · duration).
function SiteDeviceList({ devices }: { devices: DeviceDowntime[] }) {
  return (
    <div className="ml-7 mr-2 mt-1 mb-2 space-y-2 border-l-2 border-blue-100 pl-3">
      {devices.map(d => (
        <div key={d.device_id} className="text-xs">
          <div className="flex items-center gap-2">
            <span className="font-medium text-slate-800 truncate">{d.device_name}</span>
            <span className="text-blue-300 font-mono text-[10px]">{d.device_ip}</span>
            <span className="text-slate-400">· {deviceTypeLabel(d.device_type)}</span>
            {d.current_status === 'down' && (
              <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 border border-red-200">
                ENCORE DOWN
              </span>
            )}
            <span className="ml-auto shrink-0 font-semibold text-slate-700 tabular-nums">
              {fmtDuration(d.total_downtime_seconds)}
            </span>
          </div>
          <div className="text-[11px] text-slate-500 mt-0.5">
            {d.episodes_count} épisode{d.episodes_count > 1 ? 's' : ''} · disponibilité {d.availability_pct.toFixed(2)} %
          </div>
          <ul className="mt-1 space-y-0.5">
            {d.episodes.map(ep => <EpisodeLine key={ep.incident_id} episode={ep} />)}
          </ul>
        </div>
      ))}
    </div>
  )
}

function EpisodeLine({ episode: ep }: { episode: DowntimeEpisode }) {
  const dotCls = ep.severity === 'critical' ? 'bg-red-500' : 'bg-amber-400'
  return (
    <li className="flex items-center gap-2 text-[11px] text-slate-600">
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotCls}`} />
      <span>
        {fmtDateTime(ep.started_at)}
        {' → '}
        {ep.is_ongoing
          ? <span className="text-red-600 font-semibold">en cours</span>
          : fmtDateTime(ep.ended_at!)}
        <span className="text-slate-400"> · {fmtDuration(ep.duration_seconds)}</span>
        {ep.flap_count > 1 && (
          <span className="ml-1 text-orange-600 font-semibold">⚡ instable ×{ep.flap_count}</span>
        )}
      </span>
    </li>
  )
}
