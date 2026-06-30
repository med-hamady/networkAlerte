'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { TopDestinations } from '@/lib/types'

const PERIODS: { value: '24h' | '7d' | '30d'; label: string }[] = [
  { value: '24h', label: '24 heures' },
  { value: '7d', label: '7 jours' },
  { value: '30d', label: '30 jours' },
]

// Octets → unité lisible (base 1000, comme les débits réseau).
function formatBytes(n: number): string {
  if (!n) return '0 o'
  const units = ['o', 'Ko', 'Mo', 'Go', 'To', 'Po']
  const i = Math.min(Math.floor(Math.log10(n) / 3), units.length - 1)
  const v = n / 1000 ** i
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export default function TrafficPage() {
  const [period, setPeriod] = useState<'24h' | '7d' | '30d'>('24h')
  const { data, error, isLoading } = useSWR<TopDestinations>(
    endpoints.topDestinations(period), fetcher, { refreshInterval: 60_000 },
  )

  const destinations = data?.destinations ?? []
  const maxBytes = destinations.reduce((m, d) => Math.max(m, d.bytes), 0)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <h1 className="font-bold text-blue-900 text-2xl tracking-tight">
            Destinations Internet
          </h1>
          <p className="text-blue-400 text-sm mt-1">
            Opérateurs / CDN les plus consultés par les clients, par volume de trafic.
            Repère les candidats à un serveur de cache (Google&nbsp;GGC, Facebook&nbsp;FNA, Netflix&nbsp;OCA).
          </p>
        </div>
        {/* Sélecteur de période */}
        <div className="flex rounded-lg border border-blue-200 overflow-hidden shrink-0">
          {PERIODS.map(p => (
            <button
              key={p.value}
              onClick={() => setPeriod(p.value)}
              className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                period === p.value
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-blue-600 hover:bg-blue-50'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <p className="text-red-600 text-sm">Erreur de chargement du trafic.</p>
      )}
      {isLoading && <p className="text-slate-400 text-sm">Chargement…</p>}

      {data != null && !isLoading && (
        <>
          {/* Total */}
          <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5">
            <p className="text-xs uppercase tracking-wide text-blue-400 font-semibold">
              Trafic total sortant ({PERIODS.find(p => p.value === period)?.label})
            </p>
            <p className="text-2xl font-bold text-blue-900 mt-1 tabular-nums">
              {formatBytes(data.total_bytes)}
            </p>
          </div>

          {/* Top destinations */}
          <div className="bg-white border border-blue-100 rounded-xl shadow-sm overflow-hidden">
            <div className="px-5 pt-4 pb-2">
              <h3 className="font-semibold text-blue-900">Top opérateurs / CDN</h3>
              <p className="text-xs text-blue-400 mt-0.5">
                Trafic agrégé par numéro de système autonome (ASN). Longueur de barre = part du total.
              </p>
            </div>
            {destinations.length === 0 ? (
              <p className="py-10 text-center text-slate-400 text-sm">
                Aucune donnée de trafic sur cette période.
                {' '}Le collecteur NetFlow est-il activé et le routeur configuré&nbsp;?
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-blue-50 text-blue-700 text-xs uppercase tracking-wide">
                    <th className="text-left font-semibold px-5 py-2.5 w-8">#</th>
                    <th className="text-left font-semibold px-5 py-2.5">Opérateur / CDN</th>
                    <th className="text-left font-semibold px-5 py-2.5">ASN</th>
                    <th className="text-right font-semibold px-5 py-2.5">Volume</th>
                    <th className="text-left font-semibold px-5 py-2.5 w-48">Part</th>
                  </tr>
                </thead>
                <tbody>
                  {destinations.map((d, i) => (
                    <tr key={d.asn ?? `unknown-${i}`} className="border-t border-blue-50">
                      <td className="px-5 py-2.5 text-slate-400 tabular-nums">{i + 1}</td>
                      <td className="px-5 py-2.5 font-medium text-slate-800 truncate max-w-[18rem]" title={d.operator}>
                        {d.operator}
                      </td>
                      <td className="px-5 py-2.5 text-slate-500 tabular-nums">
                        {d.asn != null ? `AS${d.asn}` : '—'}
                      </td>
                      <td className="px-5 py-2.5 text-right font-semibold text-slate-800 tabular-nums">
                        {formatBytes(d.bytes)}
                      </td>
                      <td className="px-5 py-2.5">
                        <div className="flex items-center gap-2">
                          <div className="flex-1 bg-slate-100 rounded h-3 overflow-hidden">
                            <div
                              className="h-full rounded bg-blue-500"
                              style={{ width: `${maxBytes > 0 ? Math.max((d.bytes / maxBytes) * 100, 2) : 0}%` }}
                            />
                          </div>
                          <span className="w-12 text-right text-[11px] text-slate-500 tabular-nums">
                            {d.share_pct}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  )
}
