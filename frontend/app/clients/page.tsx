'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import { formatBytes, formatUptime } from '@/lib/types'

type Period = '24h' | '7d' | '30d' | 'lifetime'

type ClientConsumption = {
  device_id: number
  name: string
  ip_address: string
  rocket_id: number | null
  rocket_name: string | null
  download_bytes: number
  upload_bytes: number
  total_bytes: number
  samples: number
  has_data: boolean
  peer_uptime_s: number | null
}

type ConsumptionResponse = {
  period: Period
  period_start: string | null
  period_end: string
  data_start: string | null
  items: ClientConsumption[]
}

const PERIODS: { value: Period; label: string }[] = [
  { value: '24h',      label: '24 heures' },
  { value: '7d',       label: '7 jours'   },
  { value: '30d',      label: '30 jours'  },
  { value: 'lifetime', label: 'Depuis démarrage' },
]

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function formatDuration(seconds: number): string {
  if (seconds < 3600) return formatUptime(seconds)
  const d = Math.floor(seconds / 86400)
  if (d >= 1) {
    const h = Math.floor((seconds % 86400) / 3600)
    return `${d}j ${h}h`
  }
  return formatUptime(seconds)
}

export default function ClientsPage() {
  const [period, setPeriod] = useState<Period>('24h')
  const { data, isLoading } = useSWR<ConsumptionResponse>(
    endpoints.clientsConsumption(period),
    fetcher,
    { refreshInterval: 60_000 },
  )

  const items = data?.items ?? []
  const maxTotal = items.reduce((m, i) => Math.max(m, i.total_bytes), 0)
  const grandTotal = items.reduce((s, i) => s + i.total_bytes, 0)

  const isLifetime = period === 'lifetime'
  // Real measurement window — relevant only for sliding-window views, and
  // only when DB has less history than the requested period (after a fresh
  // deploy or for a long window like 30d on a young dataset).
  const showPartial =
    !isLifetime &&
    data?.data_start != null &&
    data?.period_start != null &&
    new Date(data.data_start).getTime() > new Date(data.period_start).getTime() + 60_000

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Consommation clients</h1>
          <p className="text-blue-400 text-sm mt-1">
            {isLifetime ? (
              <>Volume cumulé par CPE <strong>depuis la dernière association au Rocket</strong> — compteur radio,
              remis à zéro si l'AP ou le CPE redémarre.</>
            ) : (
              <>Volume téléchargé / uploadé par CPE sur la fenêtre choisie — agrégé depuis les compteurs
              <span className="font-mono"> txBytes</span> / <span className="font-mono">rxBytes</span> de l'API LTU du Rocket.</>
            )}
          </p>
        </div>

        <div className="flex gap-1 rounded-lg bg-white border border-blue-100 p-1 shadow-sm">
          {PERIODS.map(({ value, label }) => (
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
      </div>

      {showPartial && data?.data_start && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-xs text-amber-800 flex items-start gap-2">
          <svg className="w-4 h-4 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M5.07 19h13.86c1.54 0 2.5-1.67 1.73-3L13.73 4c-.77-1.33-2.69-1.33-3.46 0L3.34 16c-.77 1.33.19 3 1.73 3z" />
          </svg>
          <div>
            <strong>Fenêtre partielle</strong> — l'historique en base ne couvre pas encore toute la période demandée.
            Les totaux affichés ne couvrent que <strong>{formatDateTime(data.data_start)} → maintenant</strong>.
            La vue sera complète une fois assez d'historique accumulé.
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div className="bg-white border border-blue-100 rounded-xl px-4 py-3 shadow-sm flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-slate-600">
            <strong className="text-slate-800">{items.length}</strong> client{items.length > 1 ? 's' : ''}
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
      ) : items.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-blue-400 text-sm">Aucun LR avec des relevés sur cette période.</p>
        </div>
      ) : (
        <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-blue-50 border-b border-blue-100">
                <tr>
                  {[
                    'Client',
                    'Rocket parent',
                    'Download ⬇',
                    'Upload ⬆',
                    'Total',
                    isLifetime ? 'Lien actif depuis' : 'Part relative',
                  ].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-50">
                {items.map(row => {
                  const pct = maxTotal > 0 ? (row.total_bytes / maxTotal) * 100 : 0
                  return (
                    <tr key={row.device_id} className="hover:bg-blue-50/60 align-top">
                      <td className="px-4 py-3">
                        <div className="text-slate-800 font-medium">{row.name}</div>
                        <div className="text-blue-300 font-mono text-[11px]">{row.ip_address}</div>
                      </td>
                      <td className="px-4 py-3 text-xs whitespace-nowrap">
                        {row.rocket_name ?? <span className="text-blue-300">— sans parent —</span>}
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
                          row.peer_uptime_s != null ? (
                            <span className="text-xs text-slate-700">{formatDuration(row.peer_uptime_s)}</span>
                          ) : (
                            <span className="text-blue-300 text-xs">—</span>
                          )
                        ) : !row.has_data ? (
                          <span className="text-blue-300 text-xs">pas encore d'échantillons</span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <div className="flex-1 h-2 rounded-full bg-blue-50 overflow-hidden">
                              <div
                                className="h-full bg-blue-500"
                                style={{ width: `${pct.toFixed(1)}%` }}
                              />
                            </div>
                            <span className="text-[11px] text-slate-500 w-10 text-right tabular-nums">
                              {pct.toFixed(0)}%
                            </span>
                          </div>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="text-[11px] text-blue-400">
        {isLifetime
          ? <>Le compteur radio reflète le trafic depuis la dernière association du CPE à l'AP. Une coupure radio,
            un redémarrage CPE ou AP remet ce compteur à zéro — la colonne « Lien actif depuis » donne le contexte.</>
          : <>Volumes mesurés sur le lien radio entre le Rocket et chaque CPE (≠ trafic Internet effectif si NAT/local).
            Les CPE sans au moins deux relevés sur la fenêtre apparaissent sans valeur — il faut ~2 min après leur (re)connexion.</>
        }
      </p>
    </div>
  )
}
