'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { acknowledgeManualAlert, endpoints, fetcher } from '@/lib/api'
import type { ManualAlert, ManualAlertList } from '@/lib/types'
import { alertTypeLabel, formatDate, timeAgo } from '@/lib/types'

/**
 * Bandeau d'anomalies à ACQUITTER À LA MAIN, en haut de toutes les pages du
 * dashboard (porté par AppShell).
 *
 * Trois anomalies y sont répétées — liaison F60 dégradée, vitesse d'un port de
 * switch dégradée, équipement instable — et n'en partent QUE sur un clic
 * « Résoudre ».
 *
 * ⚠️ Canal PARALLÈLE à /incidents, qui n'est pas modifié : ces anomalies
 * continuent d'ouvrir leur incident, de se résoudre toutes seules au retour à
 * la normale et de notifier comme avant. Ce bandeau existe parce qu'un incident
 * résolu est PURGÉ de la base : une dégradation de quelques minutes ne laissait
 * aucune trace qu'un opérateur puisse voir puis écarter sciemment.
 *
 * Corollaire à ne pas « corriger » : une ligne peut désigner une anomalie déjà
 * rétablie. C'est voulu — elle atteste que c'est ARRIVÉ, pas que ça dure. La
 * page /incidents reste la vue de ce qui se passe MAINTENANT.
 */

// Au-delà, on replie : un bandeau de 12 lignes n'est plus un bandeau, il pousse
// le contenu de la page hors de l'écran.
const COLLAPSED_COUNT = 3

const SEVERITY_STYLE: Record<string, { row: string; dot: string; label: string }> = {
  critical: {
    row:   'bg-red-50 border-red-200',
    dot:   'bg-red-500',
    label: 'text-red-800',
  },
  warning: {
    row:   'bg-amber-50 border-amber-200',
    dot:   'bg-amber-500',
    label: 'text-amber-800',
  },
  info: {
    row:   'bg-blue-50 border-blue-200',
    dot:   'bg-blue-500',
    label: 'text-blue-800',
  },
}

function styleFor(severity: string) {
  return SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.info
}

export default function AlertBanner() {
  const { data, mutate } = useSWR<ManualAlertList>(
    endpoints.manualAlerts,
    fetcher,
    { refreshInterval: 30_000 },
  )
  const [expanded, setExpanded] = useState(false)
  // Ids en cours d'acquittement : le bouton se désarme le temps de l'aller-
  // retour, sinon un double-clic envoie deux POST pour la même ligne.
  const [pending, setPending] = useState<Set<number>>(new Set())
  const [error, setError] = useState<string | null>(null)

  const alerts = data?.alerts ?? []
  if (alerts.length === 0) return null

  const shown = expanded ? alerts : alerts.slice(0, COLLAPSED_COUNT)
  const hidden = alerts.length - shown.length

  const resolve = async (alert: ManualAlert) => {
    setPending(prev => new Set(prev).add(alert.id))
    setError(null)
    try {
      await acknowledgeManualAlert(alert.id)
      // Retrait optimiste local + revalidation : la ligne disparaît tout de
      // suite, et le serveur reste l'arbitre (un collègue a pu l'acquitter).
      await mutate(
        current => current
          ? { alerts: current.alerts.filter(a => a.id !== alert.id),
              count: Math.max(0, current.count - 1) }
          : current,
        { revalidate: true },
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Échec de la résolution')
    } finally {
      setPending(prev => {
        const next = new Set(prev)
        next.delete(alert.id)
        return next
      })
    }
  }

  return (
    // Hauteur BORNÉE : le bandeau vit dans l'en-tête collant, et une liste
    // dépliée de 30 anomalies y recouvrirait la page entière.
    <div className="px-6 pt-3 pb-1 space-y-1.5 max-h-[45vh] overflow-y-auto">
      {shown.map(alert => {
        const style = styleFor(alert.severity)
        const busy = pending.has(alert.id)
        return (
          <div
            key={alert.id}
            className={`flex items-center gap-3 px-3 py-2 rounded-lg border ${style.row}`}
          >
            <span className={`shrink-0 w-2 h-2 rounded-full ${style.dot}`} />

            <div className="min-w-0 flex-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className={`text-sm font-semibold ${style.label}`}>
                {alertTypeLabel(alert.alert_type)}
              </span>
              <span className="text-sm text-slate-700 truncate">
                {alert.device_name ?? `Équipement #${alert.device_id}`}
                {alert.device_site ? ` — ${alert.device_site}` : ''}
              </span>
              {/* Le titre porte le détail actionnable (quels ports, quelle
                  capacité) : les champs structurés ne nomment que l'équipement. */}
              <span className="text-xs text-slate-500 truncate">{alert.title}</span>
              <span
                className="text-xs text-slate-400 whitespace-nowrap"
                title={formatDate(alert.detected_at)}
              >
                {timeAgo(alert.detected_at)}
              </span>
            </div>

            <button
              onClick={() => resolve(alert)}
              disabled={busy}
              className="shrink-0 px-3 py-1 text-xs font-semibold rounded-md
                         border border-slate-300 bg-white text-slate-700 shadow-sm
                         hover:bg-slate-50 hover:border-slate-400
                         disabled:opacity-50 disabled:cursor-not-allowed
                         transition-colors"
            >
              {busy ? '…' : 'Résoudre'}
            </button>
          </div>
        )
      })}

      {(hidden > 0 || expanded) && (
        <button
          onClick={() => setExpanded(v => !v)}
          className="text-xs text-slate-500 hover:text-slate-700 hover:underline px-1"
        >
          {expanded
            ? 'Réduire'
            : `Voir ${hidden} autre${hidden > 1 ? 's' : ''} anomalie${hidden > 1 ? 's' : ''}`}
        </button>
      )}

      {error && (
        <p className="text-xs text-red-600 px-1">{error}</p>
      )}
    </div>
  )
}
