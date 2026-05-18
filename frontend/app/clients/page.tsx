'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import { formatBytes } from '@/lib/types'

type Period = '24h' | '7d' | '30d'

type ClientConsumption = {
  device_id: number
  name: string
  ip_address: string
  rocket_id: number | null
  rocket_name: string | null
  rx_bytes: number
  tx_bytes: number
  total_bytes: number
  samples: number
  counter_source: 'counter64' | 'counter32' | 'none'
}

type ConsumptionResponse = {
  period: Period
  period_start: string
  period_end: string
  items: ClientConsumption[]
}

const PERIODS: { value: Period; label: string }[] = [
  { value: '24h', label: '24 heures' },
  { value: '7d',  label: '7 jours'   },
  { value: '30d', label: '30 jours'  },
]

const SOURCE_LABEL: Record<ClientConsumption['counter_source'], string> = {
  counter64: 'Counter64',
  counter32: 'Counter32 (wrap géré)',
  none:      'Aucune donnée',
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

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Consommation clients</h1>
          <p className="text-blue-400 text-sm mt-1">
            Volume RX/TX cumulé par LR sur la fenêtre choisie — agrégé depuis les compteurs SNMP
            <span className="font-mono"> ifHCInOctets</span> / <span className="font-mono">ifHCOutOctets</span>.
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
                  {['Client', 'Rocket parent', 'RX', 'TX', 'Total', 'Part relative', 'Source'].map(h => (
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
                        {row.counter_source === 'none' ? '—' : formatBytes(row.rx_bytes)}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap font-mono text-xs text-slate-700">
                        {row.counter_source === 'none' ? '—' : formatBytes(row.tx_bytes)}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap font-mono text-sm font-semibold text-slate-800">
                        {row.counter_source === 'none' ? '—' : formatBytes(row.total_bytes)}
                      </td>
                      <td className="px-4 py-3 min-w-[180px]">
                        {row.counter_source === 'none' ? (
                          <span className="text-blue-300 text-xs">—</span>
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
                      <td className="px-4 py-3 whitespace-nowrap text-[11px]">
                        <span
                          className={`px-2 py-0.5 rounded border ${
                            row.counter_source === 'counter64'
                              ? 'bg-green-50 text-green-700 border-green-200'
                              : row.counter_source === 'counter32'
                              ? 'bg-amber-50 text-amber-700 border-amber-200'
                              : 'bg-slate-50 text-slate-400 border-slate-200'
                          }`}
                          title={`${row.samples} échantillons`}
                        >
                          {SOURCE_LABEL[row.counter_source]}
                        </span>
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
        Les compteurs SNMP comptent le trafic radio brut sur <span className="font-mono">ath0</span> (incluant trames de contrôle).
        Les nouveaux clients sans suffisamment d'échantillons pour la fenêtre apparaissent à « Aucune donnée ».
      </p>
    </div>
  )
}
