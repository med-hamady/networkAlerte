'use client'

import { useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { OutageSite, OutageSiteDevice, SiteOutageSummary } from '@/lib/types'
import { deviceTypeLabel } from '@/lib/types'

const WINDOW_DAYS = 7
const REFRESH = 60_000

// Format a downtime duration (seconds) — pure display formatting.
function fmtDuration(secs: number): string {
  if (secs < 60) return `${Math.round(secs)}s`
  if (secs < 3_600) return `${Math.floor(secs / 60)} min`
  const h = Math.floor(secs / 3_600)
  const m = Math.round((secs % 3_600) / 60)
  return m === 0 ? `${h}h` : `${h}h ${m.toString().padStart(2, '0')}min`
}

export default function SiteOutageCharts({
  startIso: startProp,
  endIso: endProp,
  periodLabel,
}: {
  // Optional explicit window. When omitted, defaults to the last 7 days
  // (dashboard usage). The /reports page passes its selected date range.
  startIso?: string
  endIso?: string
  periodLabel?: string
}) {
  // 7-day window — recomputed once (stable enough; SWR refresh keeps data fresh).
  const { startIso, endIso } = useMemo(() => {
    if (startProp && endProp) return { startIso: startProp, endIso: endProp }
    const end = new Date()
    const start = new Date(end.getTime() - WINDOW_DAYS * 24 * 3_600_000)
    return { startIso: start.toISOString(), endIso: end.toISOString() }
  }, [startProp, endProp])

  const period = periodLabel ?? `${WINDOW_DAYS} derniers jours`

  // Grouping by site + merge + sort all happen in SQL (fn_site_outage_summary).
  const { data, isLoading } = useSWR<SiteOutageSummary>(
    endpoints.siteOutageSummary(startIso, endIso), fetcher, { refreshInterval: REFRESH },
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
        subtitle={`Épisodes de coupure — ${period} · cliquer un site pour le détail`}
        sites={data?.by_pannes ?? []}
        valueOf={s => s.pannes}
        labelOf={s => `${s.pannes}`}
        deviceLabelOf={d => `${d.episodes_count} panne${d.episodes_count > 1 ? 's' : ''}`}
        barClass="bg-red-400"
      />
      <SiteOutageCard
        title="Temps de panne par site"
        subtitle={`Downtime du switch (équipement parent) — ${period} · cliquer un site pour le détail`}
        sites={data?.by_downtime ?? []}
        valueOf={s => s.downtime_seconds}
        labelOf={s => fmtDuration(s.downtime_seconds)}
        deviceLabelOf={d => fmtDuration(d.total_downtime_seconds)}
        barClass="bg-orange-400"
      />
    </div>
  )
}

function SiteOutageCard({
  title, subtitle, sites, valueOf, labelOf, deviceLabelOf, barClass,
}: {
  title: string
  subtitle: string
  sites: OutageSite[]
  valueOf: (s: OutageSite) => number
  labelOf: (s: OutageSite) => string
  deviceLabelOf: (d: OutageSiteDevice) => string
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
            const isOpen = openSite === s.site
            return (
              <div key={s.site}>
                <button
                  type="button"
                  onClick={() => setOpenSite(o => (o === s.site ? null : s.site))}
                  className="w-full flex items-center gap-3 py-1 group"
                  title="Cliquer pour voir les équipements en panne"
                >
                  <svg
                    className={`w-3.5 h-3.5 shrink-0 text-blue-400 transition-transform ${isOpen ? 'rotate-90' : ''}`}
                    fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                  <div className="w-24 shrink-0 text-xs text-slate-600 truncate text-right group-hover:text-blue-700" title={s.site}>
                    {s.site}
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

                {isOpen && <SiteDeviceList devices={s.devices} labelOf={deviceLabelOf} />}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// Variante TABLEAU pour le rapport/PDF : une ligne par site avec le nombre
// total de pannes et le temps total de coupure. Pas d'interactivité (le clic
// est impossible dans un PDF généré) ; ligne de totaux en pied.
export function SiteOutageTable({
  startIso: startProp,
  endIso: endProp,
  periodLabel,
}: {
  startIso?: string
  endIso?: string
  periodLabel?: string
}) {
  const { startIso, endIso } = useMemo(() => {
    if (startProp && endProp) return { startIso: startProp, endIso: endProp }
    const end = new Date()
    const start = new Date(end.getTime() - WINDOW_DAYS * 24 * 3_600_000)
    return { startIso: start.toISOString(), endIso: end.toISOString() }
  }, [startProp, endProp])

  const period = periodLabel ?? `${WINDOW_DAYS} derniers jours`

  const { data, isLoading } = useSWR<SiteOutageSummary>(
    endpoints.siteOutageSummary(startIso, endIso), fetcher, { refreshInterval: REFRESH },
  )

  // by_downtime contient tous les sites (avec pannes ET downtime), déjà triés
  // par downtime décroissant.
  const sites = data?.by_downtime ?? []
  const totalPannes = sites.reduce((m, s) => m + s.pannes, 0)
  const totalDowntime = sites.reduce((m, s) => m + s.downtime_seconds, 0)

  if (isLoading) {
    return <div className="rounded-xl bg-white border border-blue-100 h-48 animate-pulse" />
  }

  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5 break-inside-avoid">
      <div className="mb-4">
        <h3 className="font-semibold text-blue-900">Pannes et temps de coupure par site</h3>
        <p className="text-xs text-blue-400 mt-0.5">
          Nombre total de pannes et downtime cumulé de l'infrastructure — {period}.
        </p>
      </div>

      {sites.length === 0 ? (
        <div className="py-10 text-center">
          <p className="text-green-600 font-semibold text-sm">✓ Aucune coupure sur la période</p>
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-blue-400 border-b border-blue-100">
              <th className="py-2 pr-3 font-medium">Site</th>
              <th className="py-2 px-3 font-medium text-right">Nombre de pannes</th>
              <th className="py-2 pl-3 font-medium text-right">Temps total de coupure</th>
            </tr>
          </thead>
          <tbody>
            {sites.map(s => (
              <tr key={s.site} className="border-b border-blue-50">
                <td className="py-2 pr-3 font-medium text-slate-800">{s.site}</td>
                <td className="py-2 px-3 text-right tabular-nums text-slate-700">{s.pannes}</td>
                <td className="py-2 pl-3 text-right tabular-nums text-slate-700">
                  {fmtDuration(s.downtime_seconds)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t-2 border-blue-100 font-semibold text-slate-900">
              <td className="py-2 pr-3">Total</td>
              <td className="py-2 px-3 text-right tabular-nums">{totalPannes}</td>
              <td className="py-2 pl-3 text-right tabular-nums">{fmtDuration(totalDowntime)}</td>
            </tr>
          </tfoot>
        </table>
      )}
    </div>
  )
}

// Equipment of one site that was down at least once. Only the per-device total
// (panne count or downtime, depending on the card) — no per-episode detail.
function SiteDeviceList({
  devices, labelOf,
}: {
  devices: OutageSiteDevice[]
  labelOf: (d: OutageSiteDevice) => string
}) {
  return (
    <div className="ml-7 mr-2 mt-1 mb-2 space-y-1 border-l-2 border-blue-100 pl-3">
      {devices.map(d => (
        <div key={d.device_id} className="flex items-center gap-2 text-xs">
          <span className="font-medium text-slate-800 truncate">{d.device_name}</span>
          <span className="text-slate-400 shrink-0">· {deviceTypeLabel(d.device_type)}</span>
          {d.current_status === 'down' && (
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 border border-red-200">
              ENCORE DOWN
            </span>
          )}
          <span className="ml-auto shrink-0 font-semibold text-slate-700 tabular-nums">
            {labelOf(d)}
          </span>
        </div>
      ))}
    </div>
  )
}
