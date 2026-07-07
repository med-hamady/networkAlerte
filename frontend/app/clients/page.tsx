'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import { formatBytes } from '@/lib/types'
import IpLink from '@/components/IpLink'

type Period = '24h' | '7d' | '30d' | 'lifetime' | 'custom'

type ClientConsumption = {
  device_id: number
  name: string
  ip_address: string | null
  rocket_id: number | null
  rocket_name: string | null
  download_bytes: number
  upload_bytes: number
  total_bytes: number
  samples: number
  has_data: boolean
  first_sample_at: string | null
  plan_download_mbps: number | null
  plan_upload_mbps: number | null
}

type RocketConsumption = {
  rocket_id: number | null
  rocket_name: string | null
  download_bytes: number
  upload_bytes: number
  total_bytes: number
  client_count: number
  clients: ClientConsumption[]
}

type SiteConsumption = {
  site: string
  download_bytes: number
  upload_bytes: number
  total_bytes: number
  rocket_count: number
  client_count: number
  rockets: RocketConsumption[]
}

type ConsumptionResponse = {
  period: Period
  period_start: string | null
  period_end: string
  data_start: string | null
  sites: SiteConsumption[]
}

const PRESETS: { value: Exclude<Period, 'lifetime' | 'custom'>; label: string }[] = [
  { value: '24h', label: '24 heures' },
  { value: '7d',  label: '7 jours'   },
  { value: '30d', label: '30 jours'  },
]

// Local YYYY-MM-DD for a Date, for prefilling the date inputs.
function isoDay(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

// Stable key for a rocket bucket — rocket_id can be null (the "no parent"
// bucket), so we can't use the id alone as a selection key.
const rocketKey = (rocket_id: number | null): string =>
  rocket_id == null ? 'none' : String(rocket_id)

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
  })
}

// Subscription plan (forfait) badge — rate caps cached from the LR's traffic
// shaper. Plans are sold as round numbers; the firmware stores a small overhead
// margin (e.g. 20200 kbit/s for a 20 Mbps plan), so we round to the nominal.
function PlanBadge({ dl, ul }: { dl: number | null; ul: number | null }) {
  if (dl == null && ul == null) return null
  const fmt = (v: number | null) => (v == null ? '—' : String(Math.round(v)))
  return (
    <span
      className="inline-flex items-center gap-1 mt-1 px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 text-[10px] font-semibold whitespace-nowrap"
      title="Forfait provisionné sur le LR (traffic shaper) — download / upload"
    >
      <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
      </svg>
      Forfait {fmt(dl)} / {fmt(ul)} Mbps
    </span>
  )
}

// Relative-share bar reused at every level.
function ShareBar({ pct }: { pct: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 rounded-full bg-blue-50 overflow-hidden">
        <div className="h-full bg-blue-500" style={{ width: `${pct.toFixed(1)}%` }} />
      </div>
      <span className="text-[11px] text-slate-500 w-10 text-right tabular-nums">
        {pct.toFixed(0)}%
      </span>
    </div>
  )
}

export default function ClientsPage() {
  const [period, setPeriod] = useState<Period>('24h')
  // Custom date-range state. `range` is what's actually applied (drives the
  // query); the two `draft` values track the pickers before « Appliquer ».
  const today = isoDay(new Date())
  const weekAgo = isoDay(new Date(Date.now() - 7 * 86_400_000))
  const [draftStart, setDraftStart] = useState<string>(weekAgo)
  const [draftEnd, setDraftEnd] = useState<string>(today)
  const [range, setRange] = useState<{ start: string; end: string } | null>(null)
  // Drill-down state: site name, then rocket bucket key within that site.
  const [selectedSite, setSelectedSite] = useState<string | null>(null)
  const [selectedRocket, setSelectedRocket] = useState<string | null>(null)

  const isCustom = period === 'custom' && range != null

  const { data, isLoading } = useSWR<ConsumptionResponse>(
    isCustom
      ? endpoints.clientsConsumptionRange(range!.start, range!.end)
      : endpoints.clientsConsumption(period === 'custom' ? '24h' : period),
    fetcher,
    { refreshInterval: 60_000 },
  )

  const applyRange = () => {
    if (!draftStart || !draftEnd || draftEnd < draftStart) return
    setRange({ start: draftStart, end: draftEnd })
    setPeriod('custom')
  }

  const sites = data?.sites ?? []
  const isLifetime = period === 'lifetime'
  const rangeInvalid = !draftStart || !draftEnd || draftEnd < draftStart

  // Resolve the current drill-down level from the (possibly refreshed) data.
  const siteObj = selectedSite != null
    ? sites.find(s => s.site === selectedSite) ?? null
    : null
  const rocketObj = siteObj != null && selectedRocket != null
    ? siteObj.rockets.find(r => rocketKey(r.rocket_id) === selectedRocket) ?? null
    : null

  // Real measurement window — relevant only for sliding-window views, and only
  // when DB has less history than the requested period.
  const showPartial =
    !isLifetime &&
    data?.data_start != null &&
    data?.period_start != null &&
    new Date(data.data_start).getTime() > new Date(data.period_start).getTime() + 60_000

  const grandTotal = sites.reduce((s, x) => s + x.total_bytes, 0)
  const clientCount = sites.reduce((s, x) => s + x.client_count, 0)

  // Breadcrumb segments — clickable trail back up the hierarchy.
  const goSites   = () => { setSelectedSite(null); setSelectedRocket(null) }
  const goRockets = () => { setSelectedRocket(null) }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          {/* Breadcrumb */}
          <div className="flex items-center gap-2 text-sm mb-1">
            <button
              onClick={goSites}
              className={selectedSite == null
                ? 'font-bold text-blue-900 text-2xl tracking-tight'
                : 'text-blue-500 hover:text-blue-700 transition-colors'}
            >
              {selectedSite == null ? 'Consommation clients' : 'Sites'}
            </button>
            {selectedSite != null && (
              <>
                <span className="text-blue-200">/</span>
                <button
                  onClick={goRockets}
                  className={rocketObj == null
                    ? 'font-bold text-blue-900 text-2xl tracking-tight truncate'
                    : 'text-blue-500 hover:text-blue-700 transition-colors truncate'}
                >
                  {selectedSite}
                </button>
              </>
            )}
            {rocketObj != null && (
              <>
                <span className="text-blue-200">/</span>
                <span className="font-bold text-blue-900 text-2xl tracking-tight truncate">
                  {rocketObj.rocket_name ?? 'Sans Rocket parent'}
                </span>
              </>
            )}
          </div>
          <p className="text-blue-400 text-sm">
            {rocketObj != null ? (
              <>Volume par CPE connecté à cette Rocket.</>
            ) : siteObj != null ? (
              <>Volume par Rocket de ce site — clique une Rocket pour voir ses clients.</>
            ) : isCustom && data?.period_start != null ? (
              <>Volume par site du <strong>{shortDate(data.period_start)}</strong> au{' '}
              <strong>{range != null ? shortDate(range.end) : ''}</strong> (inclus) — clique un site
              pour descendre aux Rockets puis aux clients.</>
            ) : (
              <>Volume téléchargé / uploadé par site sur la fenêtre choisie — clique un site pour descendre
              aux Rockets puis aux clients.</>
            )}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="flex gap-1 rounded-lg bg-white border border-blue-100 p-1 shadow-sm">
            {PRESETS.map(({ value, label }) => (
              <button
                key={value}
                onClick={() => setPeriod(value)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                  period === value
                    ? 'bg-blue-600 text-white'
                    : 'text-blue-600 hover:bg-blue-50'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Custom date range — replaces the old « Depuis démarrage » preset. */}
          <div className={`flex items-center gap-2 rounded-lg bg-white border p-1.5 shadow-sm ${
            isCustom ? 'border-blue-400' : 'border-blue-100'
          }`}>
            <span className="text-[11px] font-semibold text-blue-500 uppercase tracking-wider pl-1">Du</span>
            <input
              type="date"
              value={draftStart}
              max={draftEnd || today}
              onChange={e => setDraftStart(e.target.value)}
              className="text-xs text-slate-700 border border-blue-100 rounded-md px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
            <span className="text-[11px] font-semibold text-blue-500 uppercase tracking-wider">Au</span>
            <input
              type="date"
              value={draftEnd}
              min={draftStart}
              max={today}
              onChange={e => setDraftEnd(e.target.value)}
              className="text-xs text-slate-700 border border-blue-100 rounded-md px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
            <button
              onClick={applyRange}
              disabled={rangeInvalid}
              className="px-3 py-1 text-xs font-semibold rounded-md bg-blue-600 text-white hover:bg-blue-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Appliquer
            </button>
          </div>
        </div>
      </div>

      {showPartial && data?.data_start && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-xs text-amber-800 flex items-start gap-2">
          <svg className="w-4 h-4 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4c-.77-1.33-2.69-1.33-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z" />
          </svg>
          <div>
            <strong>Fenêtre partielle</strong> — l'historique en base ne couvre pas toute la période demandée.
            Les totaux affichés ne couvrent que <strong>{formatDateTime(data.data_start)} → {
              isCustom ? formatDateTime(data.period_end) : 'maintenant'
            }</strong>.
            {isCustom
              ? ' La supervision n\'avait pas encore de relevés à la date de début choisie.'
              : ' La vue sera complète une fois assez d\'historique accumulé.'}
          </div>
        </div>
      )}

      {/* Summary — always reflects the whole fleet regardless of drill level. */}
      {sites.length > 0 && (
        <div className="bg-white border border-blue-100 rounded-xl px-4 py-3 shadow-sm flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-slate-600">
            <strong className="text-slate-800">{sites.length}</strong> site{sites.length > 1 ? 's' : ''}
          </span>
          <span className="text-slate-600">
            <strong className="text-slate-800">{clientCount}</strong> client{clientCount > 1 ? 's' : ''}
          </span>
          <span className="text-slate-600">
            Total flotte : <strong className="text-slate-800">{formatBytes(grandTotal)}</strong>
          </span>
        </div>
      )}

      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : sites.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-blue-400 text-sm">Aucun LR avec des relevés sur cette période.</p>
        </div>
      ) : rocketObj != null ? (
        <ClientsTable clients={rocketObj.clients} isLifetime={isLifetime} />
      ) : siteObj != null ? (
        <RocketsTable
          site={siteObj}
          onSelect={r => setSelectedRocket(rocketKey(r.rocket_id))}
        />
      ) : (
        <SitesTable sites={sites} onSelect={s => setSelectedSite(s.site)} />
      )}

      <p className="text-[11px] text-blue-400">
        {isLifetime
          ? <>Le compteur radio du firmware se remet à zéro à chaque réassociation du CPE. Le superviseur compense
            en sommant les deltas positifs entre chaque relevé — le total reste donc valide même après plusieurs
            redémarrages du Rocket ou du CPE. La colonne « Supervisé depuis » donne la date du tout premier relevé en base.</>
          : <>Volumes mesurés sur le lien radio entre le Rocket et chaque CPE (≠ trafic Internet effectif si NAT/local).
            Les CPE sans au moins deux relevés sur la fenêtre apparaissent sans valeur — il faut ~2 min après leur (re)connexion.</>
        }
      </p>
    </div>
  )
}

// ── Level 1 — sites ──────────────────────────────────────────────────────────
function SitesTable({ sites, onSelect }: {
  sites: SiteConsumption[]
  onSelect: (s: SiteConsumption) => void
}) {
  const maxTotal = sites.reduce((m, s) => Math.max(m, s.total_bytes), 0)
  return (
    <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-blue-50 border-b border-blue-100">
            <tr>
              {['Site', 'Rockets', 'Clients', 'Download ⬇', 'Upload ⬆', 'Total', 'Part relative'].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-blue-50">
            {sites.map(s => {
              const pct = maxTotal > 0 ? (s.total_bytes / maxTotal) * 100 : 0
              return (
                <tr
                  key={s.site}
                  onClick={() => onSelect(s)}
                  className="hover:bg-blue-50/60 cursor-pointer align-top"
                >
                  <td className="px-4 py-3 font-medium text-slate-800">{s.site}</td>
                  <td className="px-4 py-3 text-xs text-slate-600 tabular-nums">{s.rocket_count}</td>
                  <td className="px-4 py-3 text-xs text-slate-600 tabular-nums">{s.client_count}</td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-xs text-slate-700">{formatBytes(s.download_bytes)}</td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-xs text-slate-700">{formatBytes(s.upload_bytes)}</td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-sm font-semibold text-slate-800">{formatBytes(s.total_bytes)}</td>
                  <td className="px-4 py-3 min-w-[180px]"><ShareBar pct={pct} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Level 2 — rockets of a site ──────────────────────────────────────────────
function RocketsTable({ site, onSelect }: {
  site: SiteConsumption
  onSelect: (r: RocketConsumption) => void
}) {
  const maxTotal = site.rockets.reduce((m, r) => Math.max(m, r.total_bytes), 0)
  return (
    <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-blue-50 border-b border-blue-100">
            <tr>
              {['Rocket', 'Clients', 'Download ⬇', 'Upload ⬆', 'Total', 'Part relative'].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-blue-50">
            {site.rockets.map(r => {
              const pct = maxTotal > 0 ? (r.total_bytes / maxTotal) * 100 : 0
              return (
                <tr
                  key={rocketKey(r.rocket_id)}
                  onClick={() => onSelect(r)}
                  className="hover:bg-blue-50/60 cursor-pointer align-top"
                >
                  <td className="px-4 py-3 font-medium text-slate-800">
                    {r.rocket_name ?? <span className="text-blue-300">— sans parent —</span>}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-600 tabular-nums">{r.client_count}</td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-xs text-slate-700">{formatBytes(r.download_bytes)}</td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-xs text-slate-700">{formatBytes(r.upload_bytes)}</td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-sm font-semibold text-slate-800">{formatBytes(r.total_bytes)}</td>
                  <td className="px-4 py-3 min-w-[180px]"><ShareBar pct={pct} /></td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Level 3 — clients of a rocket ────────────────────────────────────────────
function ClientsTable({ clients, isLifetime }: {
  clients: ClientConsumption[]
  isLifetime: boolean
}) {
  const maxTotal = clients.reduce((m, c) => Math.max(m, c.total_bytes), 0)
  return (
    <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-blue-50 border-b border-blue-100">
            <tr>
              {['Client', 'Download ⬇', 'Upload ⬆', 'Total', isLifetime ? 'Supervisé depuis' : 'Part relative'].map(h => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-blue-50">
            {clients.map(row => {
              const pct = maxTotal > 0 ? (row.total_bytes / maxTotal) * 100 : 0
              return (
                <tr key={row.device_id} className="hover:bg-blue-50/60 align-top">
                  <td className="px-4 py-3">
                    <div className="text-slate-800 font-medium">{row.name}</div>
                    <div className="text-blue-300 font-mono text-[11px]"><IpLink ip={row.ip_address} /></div>
                    <PlanBadge dl={row.plan_download_mbps} ul={row.plan_upload_mbps} />
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-xs text-slate-700">
                    {row.has_data ? formatBytes(row.download_bytes) : <span className="text-blue-300">—</span>}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-xs text-slate-700">
                    {row.has_data ? formatBytes(row.upload_bytes) : <span className="text-blue-300">—</span>}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap font-mono text-sm font-semibold text-slate-800">
                    {row.has_data ? formatBytes(row.total_bytes) : <span className="text-blue-300">—</span>}
                  </td>
                  <td className="px-4 py-3 min-w-[180px]">
                    {isLifetime ? (
                      row.first_sample_at != null ? (
                        <span className="text-xs text-slate-700">{shortDate(row.first_sample_at)}</span>
                      ) : (
                        <span className="text-blue-300 text-xs">—</span>
                      )
                    ) : !row.has_data ? (
                      <span className="text-blue-300 text-xs">pas encore d'échantillons</span>
                    ) : (
                      <ShareBar pct={pct} />
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
