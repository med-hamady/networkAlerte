'use client'

import React, { useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, MetricHistPoint, MetricHistory } from '@/lib/types'

type Period = '24h' | '7d' | '30d'

const PERIOD_LABELS: Record<Period, string> = {
  '24h': '24 heures',
  '7d': '7 jours',
  '30d': '30 jours',
}

const isoDay = (d: Date) => d.toISOString().slice(0, 10)

/** Écart médian entre deux points consécutifs, en ms (0 si moins de 2 points).
 *
 * C'est la **cadence réelle** du poll, telle qu'observée dans les données — et
 * pas celle qu'on suppose. Elle ne vaut PAS `bin_seconds` : chaque source a la
 * sienne (la sonde SSH ne rend ses mesures qu'à la fin de son fan-out sur tout
 * le parc, soit un tour complet). Un seuil de trou codé sur `bin_seconds`
 * découperait chaque point en segment isolé et il n'y aurait plus de courbe.
 *
 * Médiane et pas moyenne : une vraie coupure (un trou de 3 h) tirerait la
 * moyenne vers le haut et masquerait les coupures suivantes.
 */
function medianGapMs(times: number[]): number {
  if (times.length < 2) return 0
  const deltas = times.slice(1).map((t, i) => t - times[i]).sort((a, b) => a - b)
  return deltas[Math.floor(deltas.length / 2)]
}

function median(values: number[]): number {
  if (!values.length) return 0
  const s = [...values].sort((a, b) => a - b)
  const m = Math.floor(s.length / 2)
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2
}

/** Quantile (0-1) d'une série, pour mesurer la dispersion sans se faire piéger
 *  par un pic isolé (contrairement à min/max). */
function quantile(values: number[], q: number): number {
  if (!values.length) return 0
  const s = [...values].sort((a, b) => a - b)
  const pos = (s.length - 1) * q
  const lo = Math.floor(pos)
  return s[lo] + (s[Math.min(lo + 1, s.length - 1)] - s[lo]) * (pos - lo)
}

/** Tendance : médiane glissante centrée sur `window` points.
 *
 * MÉDIANE et pas moyenne : un seul pic à 400 ms tirerait une moyenne glissante
 * vers le haut sur toute sa fenêtre et créerait une bosse de tendance là où il
 * n'y a qu'un accident. La médiane l'ignore — c'est précisément ce qu'on veut
 * d'une ligne censée dire « le niveau habituel est ici ».
 *
 * La tendance NE REMPLACE PAS la mesure brute : celle-ci reste tracée en fond.
 * Lisser en écrasant les pics ferait disparaître exactement ce qu'on cherche
 * quand un client se plaint.
 */
function rollingMedian(values: number[], window: number): number[] {
  if (values.length < 3 || window < 3) return values
  const half = Math.floor(window / 2)
  return values.map((_, i) => {
    const from = Math.max(0, i - half)
    const to = Math.min(values.length, i + half + 1)
    return median(values.slice(from, to))
  })
}

/** Graphes d'historique d'un équipement : latence, capacité du lien, débits.
 *
 * Ouvert depuis le bouton « Plus d'infos » de la fiche. Les onglets suivent
 * `available_metrics` renvoyé par l'API : on n'affiche que les courbes que CE
 * device a réellement (un LTU LR et un LiteBeam ne rapportent pas le même jeu).
 *
 * L'historique démarre au déploiement — un équipement plus ancien que ça n'a pas
 * de courbe rétroactive, d'où l'état vide explicite.
 */
export default function MetricHistoryModal({ device, onClose }: {
  device: Device
  onClose: () => void
}) {
  const [metric, setMetric] = useState('lr_latency_ms')
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

  const { data, isLoading } = useSWR<MetricHistory>(
    range
      ? endpoints.deviceMetricHistoryRange(device.id, metric, range.start, range.end)
      : endpoints.deviceMetricHistory(device.id, metric, period),
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
  const tabs = data?.available_metrics ?? []

  return (
    <>
      <div className="fixed inset-0 bg-blue-900/40 backdrop-blur-sm z-[60] animate-fade-in" onClick={onClose} />
      <div className="fixed inset-0 z-[70] flex items-center justify-center p-4 pointer-events-none">
        <div className="bg-white border border-blue-100 rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] overflow-y-auto pointer-events-auto animate-fade-in">
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-blue-100 sticky top-0 bg-white z-10">
            <div>
              <p className="font-bold text-slate-800 text-base">
                {data?.label ?? 'Historique'} — {device.name}
              </p>
              <p className="text-blue-400 text-xs mt-0.5">
                Mesuré sur le lien radio du client, historique agrégé par tranches courtes
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
            {/* Onglets de courbe — uniquement celles que ce device possède */}
            {tabs.length > 1 && (
              <div className="flex flex-wrap gap-1.5 border-b border-blue-100 pb-3">
                {tabs.map(t => (
                  <button
                    key={t.name}
                    onClick={() => setMetric(t.name)}
                    className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                      metric === t.name
                        ? 'bg-blue-600 text-white'
                        : 'bg-blue-50 text-blue-500 hover:bg-blue-100'
                    }`}
                  >
                    {t.label}
                  </button>
                ))}
              </div>
            )}

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
                <VerdictBanner
                  points={points}
                  unit={data!.unit}
                  threshold={data!.threshold}
                  thresholdDirection={data!.threshold_direction}
                />
                <MetricChart
                  points={points}
                  unit={data!.unit}
                  zeroBased={data!.zero_based}
                  threshold={data!.threshold}
                  thresholdDirection={data!.threshold_direction}
                  binSeconds={data!.bin_seconds}
                  start={new Date(data!.start)}
                  end={new Date(data!.end)}
                />
                <Summary
                  points={points}
                  unit={data!.unit}
                  threshold={data!.threshold}
                  thresholdDirection={data!.threshold_direction}
                />
              </>
            )}
          </div>
        </div>
      </div>
    </>
  )
}

/** Bannière de lecture — la conclusion, avant la courbe. */
function VerdictBanner({ points, unit, threshold, thresholdDirection }: {
  points: MetricHistPoint[]
  unit: string
  threshold: number | null
  thresholdDirection: 'max' | 'min' | null
}) {
  const v = verdict(points, unit, threshold, thresholdDirection)
  return (
    <div className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 border rounded-xl px-4 py-3 ${v.bg}`}>
      <span className="font-bold text-sm" style={{ color: v.color }}>{v.text}</span>
      <span className="text-slate-600 text-xs">{v.detail}</span>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="h-64 flex flex-col items-center justify-center gap-2 text-center px-6">
      <p className="text-slate-600 text-sm font-semibold">Aucune mesure sur cette période</p>
      <p className="text-blue-400 text-xs max-w-md">
        Rien n&apos;est enregistré quand l&apos;équipement est injoignable — et pour la
        latence, quand le client n&apos;a pas de transit. Sur une période sans relevé,
        le client était hors ligne, ou l&apos;historique ne remonte pas encore aussi loin.
      </p>
    </div>
  )
}

/** Lecture en une phrase : « stable », « instable », « élevée »…
 *
 * C'est la question qu'on se pose en ouvrant le graphe. La répondre en clair
 * évite que chacun interprète la même courbe différemment.
 *
 * Bâti sur des statistiques ROBUSTES (médiane, écart interquartile) et pas sur
 * moyenne/écart-type : un unique pic à 400 ms suffirait à faire déclarer
 * « instable » un lien parfaitement régulier.
 */
function verdict(
  points: MetricHistPoint[],
  unit: string,
  threshold: number | null,
  direction: 'max' | 'min' | null,
): { text: string; detail: string; color: string; bg: string } {
  const vals = points.map(p => p.avg_value)
  const med = median(vals)
  const q1 = quantile(vals, 0.25)
  const q3 = quantile(vals, 0.75)
  // Dispersion relative : l'écart interquartile rapporté au niveau habituel.
  // En relatif, parce que ±10 ms sur 20 ms est erratique alors que ±10 ms sur
  // 300 ms ne se remarque pas.
  const spread = med > 0 ? (q3 - q1) / med : 0

  const breaching = threshold == null || direction == null
    ? 0
    : points.filter(p =>
        direction === 'max' ? p.avg_value >= threshold : p.avg_value <= threshold,
      ).length
  const breachPct = points.length ? (breaching / points.length) * 100 : 0

  const fmt = (v: number) => `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${unit}`
  const level = `Niveau habituel ${fmt(med)}`

  // « Ça s'est dégradé récemment » se traite AVANT le reste : c'est une panne
  // qui commence, pas un état. Un lien sain puis mauvais et un lien mauvais
  // depuis toujours donnent le même « % hors seuil », mais appellent des
  // actions différentes — les confondre ferait passer une dégradation en cours
  // pour une fatalité. On compare le dernier quart au reste (médianes, donc
  // insensible aux pics), et seulement si l'écart est net (>35 %).
  const cut = Math.floor(vals.length * 0.75)
  if (vals.length >= 12) {
    const before = median(vals.slice(0, cut))
    const recent = median(vals.slice(cut))
    const worse = direction === 'min'
      ? before > 0 && recent < before * 0.65
      : before > 0 && recent > before * 1.35
    if (worse) {
      return {
        text: 'Dégradation récente',
        detail: `passé de ${fmt(before)} à ${fmt(recent)} sur la fin de la période`,
        color: '#b91c1c', bg: 'bg-red-50 border-red-200',
      }
    }
  }

  // Franchir le seuil prime sur la régularité : un lien régulièrement mauvais
  // reste mauvais, même s'il est « stable ».
  if (breachPct >= 50) {
    return {
      // Pas « en permanence » : à 50 % ce serait faux. Le pourcentage exact est
      // juste à côté, il dit la nuance mieux qu'un adverbe.
      text: direction === 'min' ? 'En dessous du seuil' : 'Élevée',
      detail: `${level} — hors seuil ${breachPct.toFixed(0)} % du temps`,
      color: '#b91c1c', bg: 'bg-red-50 border-red-200',
    }
  }
  if (breachPct >= 10) {
    return {
      text: 'Dégradations fréquentes',
      detail: `${level} — hors seuil ${breachPct.toFixed(0)} % du temps`,
      color: '#b45309', bg: 'bg-amber-50 border-amber-200',
    }
  }
  if (breachPct > 0) {
    return {
      text: 'Quelques pics',
      detail: `${level} — hors seuil ${breachPct.toFixed(0)} % du temps`,
      color: '#b45309', bg: 'bg-amber-50 border-amber-200',
    }
  }
  if (spread > 0.4) {
    return {
      text: 'Irrégulière',
      detail: `${level}, mais fortes variations`,
      color: '#b45309', bg: 'bg-amber-50 border-amber-200',
    }
  }
  return {
    text: 'Stable',
    detail: `${level}, peu de variations`,
    color: '#15803d', bg: 'bg-green-50 border-green-200',
  }
}

/** Vert/jaune/rouge selon le seuil ET son sens.
 *
 * `direction` est indispensable : pour la latence l'alerte est AU-DESSUS du
 * seuil, pour la capacité elle est EN DESSOUS. Un code couleur qui ignorerait
 * ça peindrait en rouge un lien excellent.
 */
function valueColor(
  v: number, threshold: number | null, direction: 'max' | 'min' | null,
): string {
  if (threshold == null || direction == null) return '#2563eb'  // blue-600 — neutre
  if (direction === 'max') {
    if (v >= threshold) return '#ef4444'
    if (v >= threshold * 0.6) return '#eab308'
    return '#16a34a'
  }
  if (v <= threshold) return '#ef4444'
  if (v <= threshold * 1.4) return '#eab308'
  return '#16a34a'
}

// Graphe en SVG pur (pas de lib de charts, comme les donuts et /traffic).
// X = temps RÉEL (pas l'index) : les trous de mesure doivent rester des trous,
// un axe indexé les écraserait et laisserait croire à une mesure continue.
function MetricChart({
  points, unit, zeroBased, threshold, thresholdDirection, binSeconds, start, end,
}: {
  points: MetricHistPoint[]
  unit: string
  zeroBased: boolean
  threshold: number | null
  thresholdDirection: 'max' | 'min' | null
  binSeconds: number
  start: Date
  end: Date
}) {
  const [hover, setHover] = useState<number | null>(null)

  const M = { left: 56, right: 14, top: 12, bottom: 30 }
  const plotW = 880, plotH = 260
  const W = M.left + plotW + M.right
  const H = M.top + plotH + M.bottom

  const t0 = start.getTime()
  const t1 = end.getTime()
  const times = useMemo(() => points.map(p => new Date(p.bucket_start).getTime()), [points])

  const { yMax, yTicks } = useMemo(() => {
    // Le seuil est inclus dans l'échelle pour que sa ligne reste toujours dans
    // le cadre, même quand la métrique est loin de lui.
    const peak = Math.max(threshold ?? 0, ...points.map(p => p.max_value))
    const top = peak > 0 ? peak * 1.15 : 1
    return { yMax: top, yTicks: Array.from({ length: 5 }, (_, k) => (top / 4) * k) }
  }, [points, threshold])

  const xAt = (t: number) => M.left + (t1 === t0 ? plotW / 2 : plotW * ((t - t0) / (t1 - t0)))
  // zeroBased est toujours vrai pour nos métriques (ms, Mb/s sont des grandeurs) :
  // tronquer l'axe transformerait une variation de 5 % en falaise visuelle.
  const yAt = (v: number) => M.top + plotH * (1 - (zeroBased ? v / yMax : v / yMax))

  // Découpage en segments continus : au-delà d'un certain écart entre deux
  // points, il y a eu un trou de mesure → on coupe la courbe au lieu de la faire
  // traverser le vide en ligne droite (ce qui inventerait des données).
  //
  // Le seuil suit la cadence OBSERVÉE, pas `bin_seconds` (cf. medianGapMs). Le
  // plancher à `2 × bin` garde le cas d'un poll rapide et régulier.
  const segments = useMemo(() => {
    const cadence = medianGapMs(times)
    const gapMs = Math.max(binSeconds * 2 * 1000, cadence * 2.5)
    const out: MetricHistPoint[][] = []
    let current: MetricHistPoint[] = []
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

  // Tendance calculée PAR SEGMENT : lisser à travers un trou de mesure ferait
  // traverser le vide à la ligne de tendance, ce que le découpage en segments
  // sert précisément à éviter.
  //
  // Fenêtre ~5 % des points (impaire, bornée 3-15) : assez large pour gommer
  // l'oscillation d'un relevé à l'autre, assez courte pour qu'une vraie
  // dégradation d'une heure reste visible au lieu d'être moyennée.
  const trendSegments = useMemo(() => {
    const w = Math.min(15, Math.max(3, Math.round(points.length * 0.05) | 1))
    return segments
      .filter(seg => seg.length >= 3)
      .map(seg => {
        const smooth = rollingMedian(seg.map(p => p.avg_value), w)
        return seg.map((p, i) =>
          `${xAt(new Date(p.bucket_start).getTime()).toFixed(1)},${yAt(smooth[i]).toFixed(1)}`,
        )
      })
  }, [segments, points.length, yMax])

  const cadenceMin = medianGapMs(times) / 60_000
  const hovered = hover != null ? points[hover] : null

  // Étiquettes X : ~5 repères. Sur plus de 24 h on date le repère, sinon l'heure
  // seule suffit (et reste lisible).
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
        aria-label={`Historique (${unit}) dans le temps${threshold != null ? `, seuil ${threshold} ${unit}` : ''}`}
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
              {v >= 100 ? v.toFixed(0) : v.toFixed(1)}
            </text>
          </g>
        ))}
        <text
          x={14} y={M.top + plotH / 2} textAnchor="middle" fontSize={11}
          className="fill-blue-400" transform={`rotate(-90 14 ${M.top + plotH / 2})`}
        >
          {unit}
        </text>

        {/* Bande min/max : l'amplitude réelle dans chaque bucket. Sans elle, un
            pic de 2 min disparaîtrait dans la moyenne de 5 min. */}
        {segments.map((seg, s) => {
          const upper = seg.map(p => `${xAt(new Date(p.bucket_start).getTime()).toFixed(1)},${yAt(p.max_value).toFixed(1)}`)
          const lower = seg.map(p => `${xAt(new Date(p.bucket_start).getTime()).toFixed(1)},${yAt(p.min_value).toFixed(1)}`).reverse()
          return (
            <polygon key={`band-${s}`} points={[...upper, ...lower].join(' ')} fill="#3b82f6" opacity={0.14} />
          )
        })}

        {/* Seuil — même valeur que celle qui déclenche l'alerte */}
        {threshold != null && (
          <>
            <line
              x1={M.left} x2={M.left + plotW} y1={yAt(threshold)} y2={yAt(threshold)}
              stroke="#ef4444" strokeWidth={1.5} strokeDasharray="5 4" opacity={0.75}
            />
            <text x={M.left + plotW} y={yAt(threshold) - 5} textAnchor="end" fontSize={10} className="fill-red-500 font-semibold">
              {thresholdDirection === 'min' ? 'Plancher' : 'Seuil'} {threshold} {unit}
            </text>
          </>
        )}

        {/* DEUX COUCHES.
            1) La mesure brute, en trait fin et pâle : c'est la vérité, pics
               compris. On ne la remplace jamais par du lissé — un pic de 2 min
               est souvent l'explication de la plainte du client.
            2) La TENDANCE (médiane glissante) par-dessus, en trait franc : c'est
               elle qui rend le graphe lisible et répond à « stable ou élevée ? ».
            Une seule couche lissée mentirait ; une seule couche brute est
            illisible dès que la métrique oscille. */}
        {segments.map((seg, s) => (
          <polyline
            key={`raw-${s}`}
            points={seg.map(p => `${xAt(new Date(p.bucket_start).getTime()).toFixed(1)},${yAt(p.avg_value).toFixed(1)}`).join(' ')}
            fill="none" stroke="#93b4fc" strokeWidth={1} strokeLinejoin="round" strokeLinecap="round"
          />
        ))}
        {trendSegments.map((seg, s) => (
          <polyline
            key={`trend-${s}`}
            points={seg.join(' ')}
            fill="none" stroke="#1d4ed8" strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round"
          />
        ))}
        {/* Marqueurs. Sur une série clairsemée, sans marqueur on ne voit pas où
            sont les vraies mesures et on lit la ligne comme une mesure continue.
            Au-delà, ils empâteraient la courbe → seules les plages isolées, qui
            n'ont aucun segment à tracer et seraient sinon invisibles. */}
        {(points.length <= 60 ? points : segments.filter(s => s.length === 1).map(s => s[0])).map((p, i) => (
          <circle
            key={`dot-${i}`}
            cx={xAt(new Date(p.bucket_start).getTime())} cy={yAt(p.avg_value)}
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
              cx={xAt(times[hover!])} cy={yAt(hovered.avg_value)} r={4.5}
              fill={valueColor(hovered.avg_value, threshold, thresholdDirection)}
              stroke="#fff" strokeWidth={2}
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

      {/* Cadence réelle. Affichée parce qu'elle n'est PAS un détail : elle est
          dictée par la durée d'un tour de poll, pas par un réglage du graphe.
          Sans ce repère, on croirait la mesure continue et on daterait un
          incident bien trop précisément. */}
      <div className="flex flex-wrap items-center justify-between gap-2 mt-1">
        {/* Légende : deux traits de la même couleur seraient indéchiffrables. */}
        <div className="flex items-center gap-4 text-[11px] text-blue-400">
          <span className="flex items-center gap-1.5">
            <svg width="18" height="4" aria-hidden="true"><line x1="0" y1="2" x2="18" y2="2" stroke="#1d4ed8" strokeWidth="2.5" /></svg>
            Tendance
          </span>
          <span className="flex items-center gap-1.5">
            <svg width="18" height="4" aria-hidden="true"><line x1="0" y1="2" x2="18" y2="2" stroke="#93b4fc" strokeWidth="1" /></svg>
            Mesures brutes
          </span>
        </div>
        {cadenceMin > 0 && (
          <p className="text-blue-300 text-[11px]">
            {points.length} mesure{points.length > 1 ? 's' : ''} · une environ toutes les{' '}
            {cadenceMin < 1 ? '< 1 min' : `${Math.round(cadenceMin)} min`} (cadence du relevé)
          </p>
        )}
      </div>

      {/* Infobulle */}
      {hovered && (
        <div
          className="absolute -translate-x-1/2 -translate-y-full pointer-events-none bg-white border border-blue-100 rounded-lg shadow-lg px-3 py-2 text-xs whitespace-nowrap z-10"
          style={{ left: `${(xAt(times[hover!]) / W) * 100}%`, top: `${(yAt(hovered.avg_value) / H) * 100}%`, marginTop: -10 }}
        >
          <p className="text-blue-400 mb-1">
            {new Date(hovered.bucket_start).toLocaleString('fr-FR', {
              day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
            })}
          </p>
          <p className="font-semibold" style={{ color: valueColor(hovered.avg_value, threshold, thresholdDirection) }}>
            {hovered.avg_value.toFixed(1)} {unit} en moyenne
          </p>
          <p className="text-slate-500">
            min {hovered.min_value.toFixed(1)} · max {hovered.max_value.toFixed(1)} {unit}
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
function Summary({ points, unit, threshold, thresholdDirection }: {
  points: MetricHistPoint[]
  unit: string
  threshold: number | null
  thresholdDirection: 'max' | 'min' | null
}) {
  const totalSamples = points.reduce((s, p) => s + p.sample_count, 0)
  // Moyenne pondérée par le nombre de mesures : une moyenne de moyennes
  // sur-pondérerait un bucket qui n'a reçu qu'un seul relevé.
  const avg = totalSamples
    ? points.reduce((s, p) => s + p.avg_value * p.sample_count, 0) / totalSamples
    : 0
  const peak = Math.max(...points.map(p => p.max_value))
  const low = Math.min(...points.map(p => p.min_value))

  // Part du temps mesuré passée du mauvais côté du seuil — pondérée pareil.
  const bad = threshold == null || thresholdDirection == null
    ? 0
    : points
        .filter(p => thresholdDirection === 'max' ? p.avg_value >= threshold : p.avg_value <= threshold)
        .reduce((s, p) => s + p.sample_count, 0)
  const badPct = totalSamples ? (bad / totalSamples) * 100 : 0

  // Couverture : quelle part des relevés attendus est là. Calculée sur la
  // cadence OBSERVÉE, pas sur bin_seconds — sinon un client parfaitement sain
  // afficherait une couverture ridicule dès que le poll est plus lent que le
  // bucket. Un taux bas veut dire « souvent hors ligne », pas « bon réseau ».
  const times = points.map(p => new Date(p.bucket_start).getTime())
  const cadence = medianGapMs(times)
  const spanMs = times[times.length - 1] - times[0]
  const expected = cadence > 0 ? Math.max(1, Math.round(spanMs / cadence) + 1) : points.length
  const coverage = Math.min(100, (points.length / expected) * 100)

  const fmt = (v: number) => `${v >= 100 ? v.toFixed(0) : v.toFixed(1)} ${unit}`

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <Stat label="Moyenne" value={fmt(avg)} color={valueColor(avg, threshold, thresholdDirection)} />
      <Stat label="Maximum" value={fmt(peak)} color={valueColor(peak, threshold, thresholdDirection)} />
      <Stat label="Minimum" value={fmt(low)} color={valueColor(low, threshold, thresholdDirection)} />
      {threshold != null ? (
        <Stat
          label={thresholdDirection === 'min' ? 'Temps sous le plancher' : 'Temps au-dessus du seuil'}
          value={`${badPct.toFixed(0)} %`}
          color={badPct > 0 ? '#ef4444' : '#16a34a'}
          hint={coverage < 90 ? `sur ${coverage.toFixed(0)} % de la période mesurée` : undefined}
        />
      ) : (
        <Stat
          label="Couverture"
          value={`${coverage.toFixed(0)} %`}
          color={coverage < 90 ? '#eab308' : '#16a34a'}
          hint="part de la période réellement mesurée"
        />
      )}
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
