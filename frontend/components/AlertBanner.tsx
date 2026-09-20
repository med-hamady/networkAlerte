'use client'

import { useEffect, useRef, useState } from 'react'
import useSWR from 'swr'
import { acknowledgeManualAlert, endpoints, fetcher } from '@/lib/api'
import { PERM, usePermissions } from '@/lib/permissions'
import type { ManualAlert, ManualAlertList } from '@/lib/types'
import { alertTypeLabel, formatDate, timeAgo } from '@/lib/types'

/**
 * Anomalies à ACQUITTER À LA MAIN, derrière la CLOCHE de l'en-tête (portée par
 * AppShell, donc présente sur toutes les pages du dashboard).
 *
 * Trois anomalies y arrivent — liaison F60 dégradée, vitesse d'un port de
 * switch dégradée, équipement instable — et n'en partent QUE sur un clic
 * « Résoudre ».
 *
 * ⚠️ Elles étaient dépliées en bandeau rouge jusqu'au 2026-09-20 : trois lignes
 * pleine largeur poussaient le contenu de CHAQUE page vers le bas. Le compteur
 * de la cloche garde le signal (on voit qu'il y a quelque chose, et combien)
 * sans occuper l'écran ; la liste est à un clic.
 *
 * ⚠️ Canal PARALLÈLE à /incidents, qui n'est pas modifié : ces anomalies
 * continuent d'ouvrir leur incident, de se résoudre toutes seules au retour à
 * la normale et de notifier comme avant. Ce panneau existe parce qu'un incident
 * résolu est PURGÉ de la base : une dégradation de quelques minutes ne laissait
 * aucune trace qu'un opérateur puisse voir puis écarter sciemment.
 *
 * Corollaire à ne pas « corriger » : une ligne peut désigner une anomalie déjà
 * rétablie. C'est voulu — elle atteste que c'est ARRIVÉ, pas que ça dure. La
 * page /incidents reste la vue de ce qui se passe MAINTENANT.
 */

// Au-delà, on replie : la liste entière reste à un clic, mais un panneau qui
// s'ouvre sur 30 lignes ne se lit plus d'un coup d'œil.
const COLLAPSED_COUNT = 5

const SEVERITY_STYLE: Record<string, { dot: string; label: string }> = {
  critical: { dot: 'bg-red-500',   label: 'text-red-800' },
  warning:  { dot: 'bg-amber-500', label: 'text-amber-800' },
  info:     { dot: 'bg-blue-500',  label: 'text-blue-800' },
}

function styleFor(severity: string) {
  return SEVERITY_STYLE[severity] ?? SEVERITY_STYLE.info
}

export default function AlertBanner() {
  const { can } = usePermissions()
  // La cloche reste VISIBLE pour tout le monde — être au courant d'une anomalie
  // n'est pas un privilège. Seul l'acquittement l'est : il retire la ligne pour
  // TOUTE l'équipe, donc c'est un geste, pas une préférence.
  const canAcknowledge = can(PERM.manualAlertAck)
  const { data, mutate } = useSWR<ManualAlertList>(
    endpoints.manualAlerts,
    fetcher,
    { refreshInterval: 30_000 },
  )
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  // Ids en cours d'acquittement : le bouton se désarme le temps de l'aller-
  // retour, sinon un double-clic envoie deux POST pour la même ligne.
  const [pending, setPending] = useState<Set<number>>(new Set())
  const [error, setError] = useState<string | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)

  const alerts = data?.alerts ?? []

  // Fermeture par clic ailleurs ou par Échap — un panneau ouvert ne doit pas
  // rester dans le dos de l'opérateur pendant qu'il travaille.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Réouvrir le panneau le montre toujours replié : sinon il resterait déplié
  // d'une fois sur l'autre sans que rien ne le rappelle.
  useEffect(() => {
    if (!open) setExpanded(false)
  }, [open])

  // Plus rien à acquitter : le panneau se referme tout seul plutôt que de
  // rester ouvert sur un vide.
  useEffect(() => {
    if (alerts.length === 0) setOpen(false)
  }, [alerts.length])

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

  const count = alerts.length
  const shown = expanded ? alerts : alerts.slice(0, COLLAPSED_COUNT)
  const hidden = count - shown.length

  return (
    <div ref={boxRef} className="relative shrink-0">
      <button
        onClick={() => setOpen(v => !v)}
        aria-label={count > 0
          ? `${count} anomalie${count > 1 ? 's' : ''} à acquitter`
          : 'Aucune anomalie à acquitter'}
        aria-expanded={open}
        className={`relative w-10 h-10 rounded-full flex items-center justify-center transition-colors ${
          open ? 'bg-slate-100 text-blue-900' : 'text-slate-500 hover:bg-slate-100 hover:text-blue-900'
        }`}
      >
        <BellIcon />
        {count > 0 && (
          <span
            className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 rounded-full
                       bg-red-600 text-white text-[10px] font-bold leading-[18px] text-center
                       tabular-nums ring-2 ring-white"
          >
            {count > 99 ? '99+' : count}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 top-12 z-50 w-[26rem] max-w-[calc(100vw-2rem)]
                     rounded-xl border border-slate-200 bg-white shadow-xl animate-fade-in"
        >
          <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-slate-100">
            <p className="text-sm font-semibold text-blue-900">Anomalies à acquitter</p>
            <span className="text-xs font-semibold text-slate-500 tabular-nums">{count}</span>
          </div>

          {count === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-slate-400">
              Rien à acquitter.
            </p>
          ) : (
            // Hauteur BORNÉE : 30 anomalies ne doivent pas faire un panneau plus
            // haut que l'écran.
            <div className="max-h-[60vh] overflow-y-auto divide-y divide-slate-50">
              {shown.map(alert => {
                const style = styleFor(alert.severity)
                const busy = pending.has(alert.id)
                return (
                  <div key={alert.id} className="flex items-start gap-2.5 px-4 py-3">
                    <span className={`shrink-0 mt-1.5 w-2 h-2 rounded-full ${style.dot}`} />
                    <div className="min-w-0 flex-1">
                      <p className={`text-sm font-semibold ${style.label}`}>
                        {alertTypeLabel(alert.alert_type)}
                      </p>
                      <p className="text-sm text-slate-700 truncate">
                        {alert.device_name ?? `Équipement #${alert.device_id}`}
                        {alert.device_site ? ` — ${alert.device_site}` : ''}
                      </p>
                      {/* Le titre porte le détail actionnable (quels ports, quelle
                          capacité) : les champs structurés ne nomment que l'équipement. */}
                      <p className="text-xs text-slate-500">{alert.title}</p>
                      <p
                        className="text-xs text-slate-400 mt-0.5"
                        title={formatDate(alert.detected_at)}
                      >
                        {timeAgo(alert.detected_at)}
                      </p>
                    </div>
                    <button
                      onClick={() => resolve(alert)}
                      hidden={!canAcknowledge}
                      disabled={busy}
                      className="shrink-0 px-2.5 py-1 text-xs font-semibold rounded-md
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
                  className="w-full px-4 py-2.5 text-xs font-semibold text-blue-700
                             hover:bg-slate-50 transition-colors"
                >
                  {expanded
                    ? 'Réduire'
                    : `Voir ${hidden} autre${hidden > 1 ? 's' : ''} anomalie${hidden > 1 ? 's' : ''}`}
                </button>
              )}
            </div>
          )}

          {error && <p className="px-4 py-2 text-xs text-red-600 border-t border-slate-100">{error}</p>}
        </div>
      )}
    </div>
  )
}

function BellIcon() {
  return (
    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M15 17h5l-1.4-1.4A2 2 0 0118 14.2V11a6 6 0 10-12 0v3.2a2 2 0 01-.6 1.4L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
    </svg>
  )
}
