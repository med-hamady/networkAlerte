'use client'

import React, { useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, LatencyHistory, LatencyPoint } from '@/lib/types'

type Period = '24h' | '7d' | '30d'

const PERIOD_LABELS: Record<Period, string> = {
  '24h': '24 heures',
  '7d': '7 jours',
  '30d': '30 jours',
}

const isoDay = (d: Date) => d.toISOString().slice(0, 10)

/** Graphe de latence LR → Internet d'un client, sur période ou plage de dates.
 *
 * Ouvert depuis le bouton « Plus d'infos » de la fiche équipement. Ne s'affiche
 * que pour les LR : ce sont les seuls équipements sondés en RTT.
 *
 * L'historique démarre au déploiement de lr_latency_samples — un LR plus ancien
 * que ça n'a pas de courbe rétroactive, d'où l'état vide explicite.
 */
export default function LatencyHistoryModal({ device, onClose }: {
  device: Device
  onClose: () => void
}) {
  const [period, setPeriod] = useState<Period>('24h')
  // `range` est ce qui est appliqué (pilote la requête) ; les `draft` suivent
  // les sélecteurs avant « Appliquer » — même pattern que /clients.
  const today = isoDay(new Date())
  const weekAgo = isoDay(new Date(Date.now() - 7 * 86_400_000))
  const [draftStart, setDraftStart] = useState(weekAgo)
  const [draftEnd, setDraftEnd] = useState(today)
  const [range, setRange] = useState<{ start: string; end: string } | null>(null)

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const rangeInvalid = !draftStart || !draftEnd || draftEnd < draftStart

  const { data, isLoading } = useSWR<LatencyHistory>(
    range
      ? endpoints.deviceLatencyHistoryRange(device.id, range.start, range.end)
      : endpoints.deviceLatencyHistory(device.id, period),
    fetcher,
    // Auto-refresh seulement sur une fenêtre glissante : une plage de dates
    // passée est figée, la rafraîchir ne ferait que retélécharger l'identique.
    { refreshInterval: range ? 0 : 60_000 },
  )

  const applyRange = () => {
    if (rangeInvalid) return
    setRange({ start: draftStart, end: draftEnd })
  }

  const points = data?.points ?? []

  return (
    <>
      <div className="fixed inset-0 bg-blue-900/40 backdrop-blur-sm z-[60] animate-fade-in" onClick={onClose} />
      <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 pointer-events-none">
        <div className="bg-white border border-blue-100 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto pointer-events-auto animate-fade-in">
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-blue-100 sticky top-0 bg-white z-10">
            <div>
              <p className="font-bold text-slate-800 text-base">Latence Internet — {device.name}</p>
              <p className="text-blue-400 text-xs mt-0.5">
                Temps de réponse mesuré depuis le LR du client vers Internet
              </p>
            </div>
            <button
              onClick={onClose}
              className="w-8 h-8 flex items-center justify-center rounded-lg bg-blue-50 text-blue-400 hover:bg-blue-100 hover:text-blue-600 transition-colors"
              aria-label="Fermer"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>

          <div className="p-6 space-y-5">
            {/* Filtres — une seule rangée au-dessus du graphe */}
            <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
              <div className="flex gap-1.5">
                {(Object.keys(PERIOD_LABELS) as Period[]).map(p => (
                  <button
                    key={p}
                    onClick={() => { setPeriod(p); setRange(null) }}
                    className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                      !range && period === p
                        ? 'bg-blue-600 text-white'
                        : 'bg-blue-50 text-blue-500 hover:bg-blue-100'
                    }`}
                  >
                    {PERIOD_LABELS[p]}
                  </button>
                ))}
              </div>

              <div className="flex items-end gap-2">
                <label className="flex flex-col gap-1">
                  <span className="text-blue-400 text-[11px] font-semibold uppercase tracking-wider">Du</span>
                  <input
                    type="date" value={draftStart} max={today}
                    onChange={e => setDraftStart(e.target.value)}
                    className="border border-blue-100 rounded-lg px-2 py-1.5 text-xs text-slate-700 focus:outline-none focus:border-blue-400"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-blue-400 text-[11px] font-semibold uppercase tracking-wider">Au</span>
                  <input
                    type="date" value={draftEnd} max={today}
                    onChange={e => setDraftEnd(e.target.value)}
                    className="border border-blue-100 rounded-lg px-2 py-1.5 text-xs text-slate-700 focus:outline-none focus:border-blue-400"
                  />
                </label>
                <button
                  onClick={applyRange}
                  disabled={rangeInvalid}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                    range
                      ? 'bg-blue-600 text-white'
                      : 'bg-blue-50 text-blue-500 hover:bg-blue-100 disabled:opacity-40 disabled:cursor-not-allowed'
                  }`}
                >
                  Appliquer
                </button>
              </div>
            </div>
            {rangeInvalid && (
              <p className="text-xs text-red-500">La date de fin doit être postérieure ou égale à la date de début.</p>
            )}

            {/* Graphe */}
            {isLoading && !data ? (
              <div className="h-64 flex items-center justify-center text-blue-300 text-sm">Chargement…</div>
            ) : points.length === 0 ? (
              <EmptyState />
            ) : (
              <>
                <LatencyChart
                  points={points}
                  threshold={data!.threshold_ms}
                  binSeconds={data!.bin_seconds}
                  start={new Date(data!.start)}
                  end={new Date(data!.end)}
                />
                <Summary points={points} threshold={data!.threshold_ms} binSeconds={data!.bin_seconds} />
              </>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

function EmptyState() {
  return (
    <div className="h-64 flex flex-col items-center justify-center gap-2 text-center px-6">
      <p className="text-slate-600 text-sm font-semibold">Aucune mesure sur cette période</p>
      <p className="text-blue-400 text-xs max-w-md">
        La sonde n&apos;enregistre une latence que lorsque le client a du transit.
        Sur une période sans relevé, le client était hors ligne, sans accès Internet,
        ou l&apos;historique ne remonte pas encore aussi loin.
      </p>
    </div>
  )
}

/** Repères de latence, alignés sur le vocabulaire du reste de l'UI. */
function latencyColor(ms: number, threshold: number): string {
  if (ms >= threshold) return '#ef4444'          // red-500 — critique
  if (ms >= threshold * 0.6) return '#eab308'    // yellow-500 — dégradé
  return '#16a34a'                                // green-600 — sain
}

// Graphe en SVG pur (pas de lib de charts, comme les donuts et /traffic).
// X = temps RÉEL (pas l'index) : les trous de mesure doivent rester des trous,
// un axe indexé les écraserait et laisserait croire à une mesure continue.
function LatencyChart({ points, threshold, binSeconds, start, end }: {
  points: LatencyPoint[]
  threshold: number
  binSeconds: number
  start: Date
  end: Date
}) {
  const [hover, setHover] = useState<number | null>(null)

  const M = { left: 52, right: 14, top: 12, bottom: 30 }
  const plotW = 880, plotH = 260
  const W = M.left + plotW + M.right
  const H = M.top + plotH + M.bottom

  const t0 = start.getTime()
  const t1 = end.getTime()
  const times = useMemo(() => points.map(p => new Date(p.bucket_start).getTime()), [points])

  const { yMax, yTicks } = useMemo(() => {
    // Le seuil est inclus dans l'échelle pour que sa ligne reste toujours dans
    // le cadre, même quand le client va très bien.
    const peak = Math.max(threshold, ...points.map(p => p.max_ms))
    const top = peak > 0 ? peak * 1.15 : 1
    return { yMax: top, yTicks: Array.from({ length: 5 }, (_, k) => (top / 4) * k) }
  }, [points, threshold])

  const xAt = (t: number) => M.left + (t1 === t0 ? plotW / 2 : plotW * ((t - t0) / (t1 - t0)))
  const yAt = (v: number) => M.top + plotH * (1 - v / yMax)

  // Découpage en segments continus : deux buckets espacés de plus de 2 bins ont
  // un trou de mesure entre eux → on coupe la courbe au lieu de la faire
  // traverser le vide en ligne droite (ce qui inventerait des données).
  const segments = useMemo(() => {
    const gapMs = binSeconds * 2 * 1000
    const out: LatencyPoint[][] = []
    let current: LatencyPoint[] = []
    points.forEach((p, i) => {
      if (i > 0 && times[i] - times[i - 1] > gapMs) {
        out.push(current)
        current = []
      }
      current.push(p)
    })
    if (current.length) out.push(current)
    return out
  }, [points, times, binSeconds])

  const hovered = hover != null ? points[hover] : null

  // Étiquettes X : ~5 repères de temps. Sur plus de 24 h on date le repère,
  // sinon l'heure seule suffit (et reste lisible).
  const spanHours = (t1 - t0) / 3_600_000
  const fmtX = (t: number) => {
    const d = new Date(t)
    return spanHours > 24
      ? d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' })
      : d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
  }
  const xLabels = Array.from({ length: 5 }, (_, j) => {
    const t = t0 + ((t1 - t0) * j) / 4
    return { x: xAt(t), label: fmtX(t) }
  })

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`} className="block w-full h-auto"
        role="img"
        aria-label={`Latence Internet du client dans le temps, seuil critique ${threshold} ms`}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect()
          if (!rect.width) return
          const vbX = ((e.clientX - rect.left) / rect.width) * W
          const t = t0 + ((vbX - M.left) / plotW) * (t1 - t0)
          // Point le plus proche en TEMPS : avec des trous, l'index ne suit pas
          // la position horizontale.
          let best = 0
          let bestD = Infinity
          for (let i = 0; i < times.length; i++) {
            const d = Math.abs(times[i] - t)
            if (d < bestD) { bestD = d; best = i }
          }
          setHover(best)
        }}
        onMouseLeave={() => setHover(null)}
      >
        {/* Grille + libellés Y — récessifs */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={M.left} x2={M.left + plotW} y1={yAt(v)} y2={yAt(v)} stroke="#e5edff" strokeWidth={1} />
            <text x={M.left - 8} y={yAt(v) + 4} textAnchor="end" className="fill-blue-300" fontSize={11}>
              {v.toFixed(0)}
            </text>
          </g>
        ))}
        <text
          x={14} y={M.top + plotH / 2} textAnchor="middle" fontSize={11}
          className="fill-blue-400" transform={`rotate(-90 14 ${M.top + plotH / 2})`}
        >
          Latence (ms)
        </text>

        {/* Bande min/max : l'amplitude réelle dans chaque bucket. Sans elle, un
            pic de 2 min disparaîtrait dans la moyenne de 5 min. */}
        {segments.map((seg, s) => {
          const upper = seg.map(p => `${xAt(new Date(p.bucket_start).getTime()).toFixed(1)},${yAt(p.max_ms).toFixed(1)}`)
          const lower = seg.map(p => `${xAt(new Date(p.bucket_start).getTime()).toFixed(1)},${yAt(p.min_ms).toFixed(1)}`).reverse()
          return (
            <polygon key={`band-${s}`} points={[...upper, ...lower].join(' ')} fill="#3b82f6" opacity={0.14} />
          )
        })}

        {/* Seuil critique — même valeur que celle qui déclenche l'alerte */}
        <line
          x1={M.left} x2={M.left + plotW} y1={yAt(threshold)} y2={yAt(threshold)}
          stroke="#ef4444" strokeWidth={1.5} strokeDasharray="5 4" opacity={0.75}
        />
        <text x={M.left + plotW} y={yAt(threshold) - 5} textAnchor="end" fontSize={10} className="fill-red-500 font-semibold">
          Seuil critique {threshold} ms
        </text>

        {/* Courbe moyenne — un segment par plage continue */}
        {segments.map((seg, s) => (
          <polyline
            key={`line-${s}`}
            points={seg.map(p => `${xAt(new Date(p.bucket_start).getTime()).toFixed(1)},${yAt(p.avg_ms).toFixed(1)}`).join(' ')}
            fill="none" stroke="#2563eb" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
          />
        ))}
        {/* Une plage isolée (un seul bucket entre deux trous) n'a pas de segment
            à tracer : sans ce point elle serait invisible. */}
        {segments.filter(seg => seg.length === 1).map((seg, s) => (
          <circle
            key={`dot-${s}`}
            cx={xAt(new Date(seg[0].bucket_start).getTime())} cy={yAt(seg[0].avg_ms)}
            r={2.5} fill="#2563eb"
          />
        ))}

        {/* Curseur de survol */}
        {hovered && (
          <g>
            <line
              x1={xAt(times[hover!])} x2={xAt(times[hover!])} y1={M.top} y2={M.top + plotH}
              stroke="#93b4fc" strokeWidth={1} strokeDasharray="3 3"
            />
            <circle
              cx={xAt(times[hover!])} cy={yAt(hovered.avg_ms)} r={4.5}
              fill={latencyColor(hovered.avg_ms, threshold)} stroke="#fff" strokeWidth={2}
            />
          </g>
        )}

        {/* Axe X */}
        <line x1={M.left} x2={M.left + plotW} y1={M.top + plotH} y2={M.top + plotH} stroke="#dbe6ff" strokeWidth={1} />
        {xLabels.map((l, i) => (
          <text
            key={i} x={l.x} y={H - 10} fontSize={11} className="fill-blue-300"
            textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
          >
            {l.label}
          </text>
        ))}
      </svg>

      {/* Infobulle */}
      {hovered && (
        <div
          className="absolute -translate-x-1/2 -translate-y-full pointer-events-none bg-white border border-blue-100 rounded-lg shadow-lg px-3 py-2 text-xs whitespace-nowrap z-10"
          style={{ left: `${(xAt(times[hover!]) / W) * 100}%`, top: `${(yAt(hovered.avg_ms) / H) * 100}%`, marginTop: -10 }}
        >
          <p className="text-blue-400 mb-1">
            {new Date(hovered.bucket_start).toLocaleString('fr-FR', {
              day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
            })}
          </p>
          <p className="font-semibold" style={{ color: latencyColor(hovered.avg_ms, threshold) }}>
            {hovered.avg_ms.toFixed(1)} ms en moyenne
          </p>
          <p className="text-slate-500">
            min {hovered.min_ms.toFixed(1)} · max {hovered.max_ms.toFixed(1)} ms
          </p>
          <p className="text-blue-300 mt-0.5">
            {hovered.sample_count} mesure{hovered.sample_count > 1 ? 's' : ''}
          </p>
        </div>
      )}
    </div>
  )
}

/** Chiffres clés sous le graphe — ce qu'on regarde avant de lire la courbe. */
function Summary({ points, threshold, binSeconds }: {
  points: LatencyPoint[]
  threshold: number
  binSeconds: number
}) {
  const totalSamples = points.reduce((s, p) => s + p.sample_count, 0)
  // Moyenne pondérée par le nombre de mesures : une moyenne de moyennes
  // sur-pondérerait un bucket qui n'a reçu qu'un seul relevé.
  const avg = totalSamples
    ? points.reduce((s, p) => s + p.avg_ms * p.sample_count, 0) / totalSamples
    : 0
  const peak = Math.max(...points.map(p => p.max_ms))
  const best = Math.min(...points.map(p => p.min_ms))
  // Part du temps mesuré passée au-dessus du seuil — pondérée pareil.
  const overSamples = points.filter(p => p.avg_ms >= threshold).reduce((s, p) => s + p.sample_count, 0)
  const overPct = totalSamples ? (overSamples / totalSamples) * 100 : 0

  // Couverture : combien de buckets attendus ont réellement un relevé. Un taux
  // bas veut dire « client souvent hors ligne / sans transit », pas « bon réseau ».
  const spanMs = new Date(points[points.length - 1].bucket_start).getTime()
    - new Date(points[0].bucket_start).getTime()
  const expected = Math.max(1, Math.round(spanMs / (binSeconds * 1000)) + 1)
  const coverage = Math.min(100, (points.length / expected) * 100)

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <Stat label="Latence moyenne" value={`${avg.toFixed(1)} ms`} color={latencyColor(avg, threshold)} />
      <Stat label="Pic maximum" value={`${peak.toFixed(1)} ms`} color={latencyColor(peak, threshold)} />
      <Stat label="Meilleure mesure" value={`${best.toFixed(1)} ms`} color="#16a34a" />
      <Stat
        label="Temps au-dessus du seuil"
        value={`${overPct.toFixed(0)} %`}
        color={overPct > 0 ? '#ef4444' : '#16a34a'}
        hint={coverage < 90 ? `sur ${coverage.toFixed(0)} % de la période mesurée` : undefined}
      />
    </div>
  )
}

function Stat({ label, value, color, hint }: {
  label: string
  value: string
  color: string
  hint?: string
}) {
  return (
    <div className="bg-blue-50/50 border border-blue-100 rounded-xl px-3 py-2.5">
      <p className="text-blue-400 text-[11px] uppercase tracking-wider font-semibold">{label}</p>
      <p className="font-bold text-base mt-0.5" style={{ color }}>{value}</p>
      {hint && <p className="text-blue-300 text-[10px] mt-0.5">{hint}</p>}
    </div>
  )
}
