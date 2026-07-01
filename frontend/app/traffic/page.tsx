'use client'

import { useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { TopDestinations, Throughput, ThroughputHistory } from '@/lib/types'

// Palette pour les séries opérateurs du graphe empilé ("Autres" = gris, en dernier).
const SERIES_COLORS = ['#22c55e', '#3b82f6', '#f59e0b', '#8b5cf6', '#ec4899', '#14b8a6', '#94a3b8']

const PERIODS: { value: '24h' | '7d' | '30d'; label: string }[] = [
  { value: '24h', label: '24 heures' },
  { value: '7d', label: '7 jours' },
  { value: '30d', label: '30 jours' },
]

// Octets → unité lisible (base 1000, comme les débits réseau).
function fmtBytes(n: number): string {
  if (!n) return '0 o'
  const u = ['o', 'Ko', 'Mo', 'Go', 'To', 'Po']
  const i = Math.min(Math.floor(Math.log10(n) / 3), u.length - 1)
  const v = n / 1000 ** i
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`
}

// Débit en Mbps → Mb/s ou Gb/s.
function fmtRate(mbps: number): string {
  if (mbps >= 1000) return `${(mbps / 1000).toFixed(2)} Gb/s`
  if (mbps >= 10) return `${Math.round(mbps)} Mb/s`
  return `${mbps.toFixed(1)} Mb/s`
}

export default function TrafficPage() {
  return (
    <div className="space-y-6">
      <div className="min-w-0">
        <h1 className="font-bold text-blue-900 text-2xl tracking-tight">Destinations Internet</h1>
        <p className="text-blue-400 text-sm mt-1">
          Opérateurs / CDN les plus consultés par les clients. Le débit montre comment la bande
          passante WAN se partage en temps réel ; le volume, ce qui a le plus consommé.
          Repère les candidats à un serveur de cache (Google&nbsp;GGC, Facebook&nbsp;FNA, Netflix&nbsp;OCA).
        </p>
      </div>

      <ThroughputSection />
      <HistorySection />
      <VolumeSection />
    </div>
  )
}

function ThroughputSection() {
  const { data, error, isLoading } = useSWR<Throughput>(
    endpoints.trafficThroughput, fetcher, { refreshInterval: 30_000 },
  )

  const ops = data?.operators ?? []
  const maxDown = ops.reduce((m, o) => Math.max(m, o.down_mbps), 0)

  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm overflow-hidden">
      <div className="px-5 pt-4 pb-3 flex items-center gap-2 flex-wrap">
        <span className="inline-block w-2.5 h-2.5 rounded-full bg-green-500 animate-pulse" />
        <h3 className="font-semibold text-blue-900">Débit en direct</h3>
        <span className="text-xs text-blue-400">
          (moyenne sur la dernière minute — descendant = download / montant = upload)
        </span>
      </div>

      {error && <p className="px-5 pb-4 text-red-600 text-sm">Erreur de chargement du débit.</p>}
      {isLoading && <p className="px-5 pb-4 text-slate-400 text-sm">Chargement…</p>}

      {data != null && !isLoading && (
        <>
          {/* Totaux : la bande passante WAN et son partage */}
          <div className="px-5 pb-4 grid grid-cols-2 gap-4 max-w-md">
            <div>
              <p className="text-xs uppercase tracking-wide text-blue-400 font-semibold">↓ Descendant</p>
              <p className="text-2xl font-bold text-green-600 tabular-nums">{fmtRate(data.total_down_mbps)}</p>
            </div>
            <div>
              <p className="text-xs uppercase tracking-wide text-blue-400 font-semibold">↑ Montant</p>
              <p className="text-2xl font-bold text-blue-600 tabular-nums">{fmtRate(data.total_up_mbps)}</p>
            </div>
          </div>

          {ops.length === 0 ? (
            <p className="px-5 pb-8 text-center text-slate-400 text-sm">
              Aucun trafic sur le dernier bucket. Le collecteur NetFlow est-il actif et le routeur configuré&nbsp;?
            </p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-blue-50 text-blue-700 text-xs uppercase tracking-wide">
                  <th className="text-left font-semibold px-5 py-2.5 w-8">#</th>
                  <th className="text-left font-semibold px-5 py-2.5">Opérateur / CDN</th>
                  <th className="text-right font-semibold px-5 py-2.5">↓ Descendant</th>
                  <th className="text-right font-semibold px-5 py-2.5">↑ Montant</th>
                  <th className="text-left font-semibold px-5 py-2.5 w-48">Part du download</th>
                </tr>
              </thead>
              <tbody>
                {ops.map((o, i) => (
                  <tr key={o.asn ?? `unknown-${i}`} className="border-t border-blue-50">
                    <td className="px-5 py-2.5 text-slate-400 tabular-nums">{i + 1}</td>
                    <td className="px-5 py-2.5 font-medium text-slate-800 truncate max-w-[16rem]" title={o.operator}>
                      {o.operator}
                      {o.asn != null && <span className="text-slate-400 font-normal"> · AS{o.asn}</span>}
                    </td>
                    <td className="px-5 py-2.5 text-right font-semibold text-green-700 tabular-nums">{fmtRate(o.down_mbps)}</td>
                    <td className="px-5 py-2.5 text-right text-blue-600 tabular-nums">{fmtRate(o.up_mbps)}</td>
                    <td className="px-5 py-2.5">
                      <div className="flex items-center gap-2">
                        <div className="flex-1 bg-slate-100 rounded h-3 overflow-hidden">
                          <div className="h-full rounded bg-green-500"
                            style={{ width: `${maxDown > 0 ? Math.max((o.down_mbps / maxDown) * 100, 2) : 0}%` }} />
                        </div>
                        <span className="w-12 text-right text-[11px] text-slate-500 tabular-nums">{o.share_pct}%</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}

const HISTORY_PERIODS: { value: '1h' | '6h' | '24h'; label: string }[] = [
  { value: '1h', label: '1 heure' },
  { value: '6h', label: '6 heures' },
  { value: '24h', label: '24 heures' },
]

function HistorySection() {
  const [period, setPeriod] = useState<'1h' | '6h' | '24h'>('24h')
  const { data, error, isLoading } = useSWR<ThroughputHistory>(
    endpoints.trafficThroughputHistory(period), fetcher, { refreshInterval: 60_000 },
  )

  const hasData = (data?.times.length ?? 0) > 0 && (data?.series.length ?? 0) > 0

  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm overflow-hidden">
      <div className="px-5 pt-4 pb-3 flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h3 className="font-semibold text-blue-900">Débit descendant par opérateur</h3>
          <p className="text-xs text-blue-400 mt-0.5">
            Évolution du download (Gb/s) et de son partage entre opérateurs sur la période.
          </p>
        </div>
        <div className="flex rounded-lg border border-blue-200 overflow-hidden shrink-0">
          {HISTORY_PERIODS.map(p => (
            <button key={p.value} onClick={() => setPeriod(p.value)}
              className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                period === p.value ? 'bg-blue-600 text-white' : 'bg-white text-blue-600 hover:bg-blue-50'}`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="px-5 pb-4 text-red-600 text-sm">Erreur de chargement de l’historique.</p>}
      {isLoading && <p className="px-5 pb-4 text-slate-400 text-sm">Chargement…</p>}

      {data != null && !isLoading && (
        !hasData ? (
          <p className="py-10 text-center text-slate-400 text-sm">Aucune donnée sur cette période.</p>
        ) : (
          <div className="px-5 pb-5">
            <StackedAreaChart times={data.times} series={data.series} />
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
              {data.series.map((s, i) => (
                <span key={s.asn ?? `o-${i}`} className="inline-flex items-center gap-1.5 text-xs text-slate-600">
                  <span className="inline-block w-3 h-3 rounded-sm" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} />
                  {s.operator}
                </span>
              ))}
            </div>
          </div>
        )
      )}
    </div>
  )
}

// Graphe d'aires empilées en SVG pur (pas de lib de charts, comme les donuts).
// X = temps, Y = débit descendant (Mb/s), une aire par opérateur empilée.
function StackedAreaChart({ times, series }: { times: string[]; series: { operator: string; down_mbps: number[] }[] }) {
  const n = times.length

  const { yMax, yTicks } = useMemo(() => {
    let peak = 0
    for (let i = 0; i < n; i++) {
      let s = 0
      for (const serie of series) s += serie.down_mbps[i] ?? 0
      if (s > peak) peak = s
    }
    const top = peak > 0 ? peak * 1.1 : 1
    const ticks: number[] = []
    const step = top / 4
    for (let k = 0; k <= 4; k++) ticks.push(step * k)
    return { yMax: top, yTicks: ticks }
  }, [times, series, n])

  const M = { left: 52, right: 12, top: 10, bottom: 28 }
  const plotW = 900, plotH = 240
  const W = M.left + plotW + M.right
  const H = M.top + plotH + M.bottom
  const xAt = (i: number) => M.left + (n <= 1 ? plotW / 2 : plotW * (i / (n - 1)))
  const yAt = (v: number) => M.top + plotH * (1 - v / yMax)

  // Empilement : pour chaque série, aire entre la somme cumulée avant et après.
  const cum = new Array(n).fill(0)
  const areas = series.map((serie, k) => {
    const upper: string[] = []
    const lower: string[] = []
    for (let i = 0; i < n; i++) {
      const before = cum[i]
      const after = before + (serie.down_mbps[i] ?? 0)
      cum[i] = after
      upper.push(`${xAt(i).toFixed(1)},${yAt(after).toFixed(1)}`)
      lower.push(`${xAt(i).toFixed(1)},${yAt(before).toFixed(1)}`)
    }
    lower.reverse()
    return { points: [...upper, ...lower].join(' '), color: SERIES_COLORS[k % SERIES_COLORS.length] }
  })

  // Étiquettes X : ~5 repères de temps.
  const nLabels = Math.min(5, n)
  const xLabels = Array.from({ length: nLabels }, (_, j) => {
    const i = nLabels <= 1 ? 0 : Math.round((j * (n - 1)) / (nLabels - 1))
    const d = new Date(times[i])
    return { x: xAt(i), label: d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }) }
  })

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="block w-full h-auto" role="img" aria-label="Débit descendant par opérateur dans le temps">
      {/* Grille + libellés Y */}
      {yTicks.map((v, i) => (
        <g key={i}>
          <line x1={M.left} y1={yAt(v)} x2={M.left + plotW} y2={yAt(v)} stroke="#eef2f7" strokeWidth={1} />
          <text x={M.left - 6} y={yAt(v) + 3.5} textAnchor="end" fontSize={10} fill="#64748b" className="tabular-nums">
            {fmtRate(v)}
          </text>
        </g>
      ))}
      {/* Aires empilées */}
      {areas.map((a, k) => (
        <polygon key={k} points={a.points} fill={a.color} fillOpacity={0.85} stroke={a.color} strokeWidth={0.5} />
      ))}
      {/* Axe X + libellés */}
      <line x1={M.left} y1={M.top + plotH} x2={M.left + plotW} y2={M.top + plotH} stroke="#94a3b8" strokeWidth={1} />
      {xLabels.map((l, i) => (
        <text key={i} x={l.x} y={M.top + plotH + 16} textAnchor="middle" fontSize={10} fill="#64748b">{l.label}</text>
      ))}
    </svg>
  )
}

function VolumeSection() {
  const [period, setPeriod] = useState<'24h' | '7d' | '30d'>('24h')
  const { data, error, isLoading } = useSWR<TopDestinations>(
    endpoints.topDestinations(period), fetcher, { refreshInterval: 60_000 },
  )

  const dests = data?.destinations ?? []
  const maxTotal = dests.reduce((m, d) => Math.max(m, d.total_bytes), 0)
  const grandTotal = (data?.total_down_bytes ?? 0) + (data?.total_up_bytes ?? 0)

  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm overflow-hidden">
      <div className="px-5 pt-4 pb-3 flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h3 className="font-semibold text-blue-900">Volume par opérateur / CDN</h3>
          <p className="text-xs text-blue-400 mt-0.5">
            Trafic cumulé sur la période — total {fmtBytes(grandTotal)} (↓ {fmtBytes(data?.total_down_bytes ?? 0)} / ↑ {fmtBytes(data?.total_up_bytes ?? 0)}).
          </p>
        </div>
        <div className="flex rounded-lg border border-blue-200 overflow-hidden shrink-0">
          {PERIODS.map(p => (
            <button key={p.value} onClick={() => setPeriod(p.value)}
              className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                period === p.value ? 'bg-blue-600 text-white' : 'bg-white text-blue-600 hover:bg-blue-50'}`}>
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="px-5 pb-4 text-red-600 text-sm">Erreur de chargement du trafic.</p>}
      {isLoading && <p className="px-5 pb-4 text-slate-400 text-sm">Chargement…</p>}

      {data != null && !isLoading && (
        dests.length === 0 ? (
          <p className="py-10 text-center text-slate-400 text-sm">Aucune donnée de trafic sur cette période.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-blue-50 text-blue-700 text-xs uppercase tracking-wide">
                <th className="text-left font-semibold px-5 py-2.5 w-8">#</th>
                <th className="text-left font-semibold px-5 py-2.5">Opérateur / CDN</th>
                <th className="text-right font-semibold px-5 py-2.5">↓ Download</th>
                <th className="text-right font-semibold px-5 py-2.5">↑ Upload</th>
                <th className="text-right font-semibold px-5 py-2.5">Total</th>
                <th className="text-left font-semibold px-5 py-2.5 w-40">Part</th>
              </tr>
            </thead>
            <tbody>
              {dests.map((d, i) => (
                <tr key={d.asn ?? `unknown-${i}`} className="border-t border-blue-50">
                  <td className="px-5 py-2.5 text-slate-400 tabular-nums">{i + 1}</td>
                  <td className="px-5 py-2.5 font-medium text-slate-800 truncate max-w-[16rem]" title={d.operator}>
                    {d.operator}
                    {d.asn != null && <span className="text-slate-400 font-normal"> · AS{d.asn}</span>}
                  </td>
                  <td className="px-5 py-2.5 text-right text-green-700 tabular-nums">{fmtBytes(d.down_bytes)}</td>
                  <td className="px-5 py-2.5 text-right text-blue-600 tabular-nums">{fmtBytes(d.up_bytes)}</td>
                  <td className="px-5 py-2.5 text-right font-semibold text-slate-800 tabular-nums">{fmtBytes(d.total_bytes)}</td>
                  <td className="px-5 py-2.5">
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-slate-100 rounded h-3 overflow-hidden">
                        <div className="h-full rounded bg-blue-500"
                          style={{ width: `${maxTotal > 0 ? Math.max((d.total_bytes / maxTotal) * 100, 2) : 0}%` }} />
                      </div>
                      <span className="w-12 text-right text-[11px] text-slate-500 tabular-nums">{d.share_pct}%</span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      )}
    </div>
  )
}
