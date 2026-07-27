'use client'

// « Santé du réseau » — nombre unique (%) affiché à côté du titre « Pannes par
// site ». = disponibilité moyenne des sites sur la MÊME fenêtre que les graphes
// de pannes (props start/end ; à défaut, 7 derniers jours — même défaut que
// SiteOutageCharts). Calcul côté DB : fn_network_health.

import { useEffect, useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { NetworkHealth } from '@/lib/types'

const WINDOW_DAYS = 7
// Mode « live » (aucune plage appliquée) : cadence du dashboard, la fenêtre suit
// l'heure courante. Mode « plage figée » : simple re-fetch périodique.
const LIVE_REFRESH = 15_000
const FIXED_REFRESH = 60_000

// Vert ≥ 99,5 % · ambre ≥ 98 % · rouge en dessous — seuils opérateur usuels.
function healthColor(pct: number): { text: string; dot: string } {
  if (pct >= 99.5) return { text: 'text-green-700', dot: 'bg-green-500' }
  if (pct >= 98) return { text: 'text-amber-700', dot: 'bg-amber-500' }
  return { text: 'text-red-700', dot: 'bg-red-500' }
}

export default function NetworkHealthBadge({
  startIso: startProp,
  endIso: endProp,
}: {
  startIso?: string
  endIso?: string
}) {
  const isLive = !(startProp && endProp)

  // En mode live, on fait avancer « maintenant » régulièrement pour que la
  // fenêtre glissante se termine toujours à l'instant présent (temps réel).
  const [nowTick, setNowTick] = useState(() => Date.now())
  useEffect(() => {
    if (!isLive) return
    const id = setInterval(() => setNowTick(Date.now()), LIVE_REFRESH)
    return () => clearInterval(id)
  }, [isLive])

  const { startIso, endIso } = useMemo(() => {
    if (startProp && endProp) return { startIso: startProp, endIso: endProp }
    const end = new Date(nowTick)
    const start = new Date(nowTick - WINDOW_DAYS * 24 * 3_600_000)
    return { startIso: start.toISOString(), endIso: end.toISOString() }
  }, [startProp, endProp, nowTick])

  const { data } = useSWR<NetworkHealth>(
    endpoints.networkHealth(startIso, endIso), fetcher,
    { refreshInterval: isLive ? LIVE_REFRESH : FIXED_REFRESH },
  )

  const pct = data?.network_health_pct ?? null
  const c = healthColor(pct ?? 100)

  return (
    <div
      className="inline-flex items-center gap-2 rounded-lg border border-blue-100 bg-white px-3 py-1.5 shadow-sm"
      title={
        data
          ? `Disponibilité moyenne de ${data.sites_measured} site(s) — ${
              isLive ? '7 derniers jours, temps réel' : 'sur la plage sélectionnée'
            }`
          : 'Calcul en cours…'
      }
    >
      <span className={`inline-block w-2 h-2 rounded-full ${pct != null ? c.dot : 'bg-slate-300'}`} />
      <span className="text-xs font-medium text-blue-500 uppercase tracking-wider">Santé du réseau</span>
      <span className={`text-lg font-bold tabular-nums ${pct != null ? c.text : 'text-slate-400'}`}>
        {pct != null ? `${pct.toFixed(1)}%` : '—'}
      </span>
    </div>
  )
}
