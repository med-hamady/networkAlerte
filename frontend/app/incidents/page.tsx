'use client'

import Link from 'next/link'
import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher, updateIncidentStatus } from '@/lib/api'
import type { Incident } from '@/lib/types'
import { alertTypeLabel, formatDate, probableCauseLabel } from '@/lib/types'
import SeverityBadge from '@/components/SeverityBadge'
import IncidentStatusBadge from '@/components/IncidentStatusBadge'
import IncidentDetailModal from '@/components/IncidentDetailModal'

type StatusFilter = 'active' | 'open' | 'acknowledged'

const FILTER_LABELS: Record<StatusFilter, string> = {
  active:       'Tous actifs',
  open:         'Ouverts',
  acknowledged: 'Acquittés',
}

const SEVERITY_ORDER = ['critical', 'warning', 'info'] as const

const SEVERITY_META: Record<string, { label: string; header: string }> = {
  critical: { label: 'Critique',  header: 'bg-red-50 border-red-200 text-red-700'    },
  warning:  { label: 'Attention', header: 'bg-amber-50 border-amber-200 text-amber-700' },
  info:     { label: 'Info',      header: 'bg-blue-50 border-blue-200 text-blue-600'  },
}

export default function IncidentsPage() {
  const [filter, setFilter]           = useState<StatusFilter>('open')
  const [actionError, setActionError] = useState<string | null>(null)
  const [detail, setDetail]           = useState<Incident | null>(null)

  const { data: allIncidents, isLoading, mutate } = useSWR<Incident[]>(
    `${endpoints.incidents}?limit=200`,
    fetcher,
    { refreshInterval: 30_000 },
  )

  const active = allIncidents?.filter(i => i.status !== 'resolved') ?? []

  const displayed = filter === 'active'
    ? active
    : active.filter(i => i.status === filter)

  const counts = {
    active:       active.length,
    open:         active.filter(i => i.status === 'open').length,
    acknowledged: active.filter(i => i.status === 'acknowledged').length,
  }

  const groups = SEVERITY_ORDER
    .map(sev => ({ severity: sev, items: displayed.filter(i => i.severity === sev) }))
    .filter(g => g.items.length > 0)

  async function handleAction(id: number, status: 'acknowledged' | 'resolved', e: React.MouseEvent) {
    e.stopPropagation()
    setActionError(null)
    try {
      await updateIncidentStatus(id, status)
      await mutate()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Erreur inconnue')
    }
  }

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Incidents actifs</h1>
          <p className="text-blue-400 text-sm mt-1">
            Incidents ouverts et acquittés — actualisation toutes les 30s — cliquez sur une ligne pour le détail
          </p>
        </div>
        <Link
          href="/incidents/archive"
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-blue-500 border border-blue-200 rounded-xl bg-white hover:bg-blue-50 hover:border-blue-300 transition-all shrink-0"
        >
          <ArchiveIcon className="w-4 h-4" />
          Archives
        </Link>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-2 flex-wrap">
        {(Object.keys(FILTER_LABELS) as StatusFilter[]).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-all border ${
              filter === f
                ? 'bg-blue-600 border-blue-600 text-white shadow-sm'
                : 'bg-white border-blue-200 text-blue-500 hover:bg-blue-50 hover:border-blue-300'
            }`}
          >
            {FILTER_LABELS[f]}
            {counts[f] > 0 && (
              <span className={`ml-1.5 text-xs px-1.5 py-0.5 rounded-full ${
                filter === f ? 'bg-blue-500' : 'bg-blue-100 text-blue-500'
              }`}>
                {counts[f]}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Error */}
      {actionError && (
        <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-xl text-sm">
          Erreur : {actionError}
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : displayed.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          {filter === 'open' || filter === 'active' ? (
            <>
              <p className="text-green-600 font-semibold text-sm">✓ Aucun incident ouvert</p>
              <p className="text-blue-400 text-xs mt-1">Tous les équipements fonctionnent normalement</p>
            </>
          ) : (
            <p className="text-blue-400">Aucun incident pour ce filtre</p>
          )}
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map(({ severity, items }) => {
            const meta = SEVERITY_META[severity]
            return (
              <div key={severity} className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">

                {/* Severity group header */}
                <div className={`flex items-center gap-2 px-4 py-2.5 border-b ${meta.header}`}>
                  <SeverityGroupIcon severity={severity} />
                  <span className="text-xs font-bold uppercase tracking-widest">{meta.label}</span>
                  <span className="ml-auto text-xs font-semibold opacity-70">
                    {items.length} incident{items.length > 1 ? 's' : ''}
                  </span>
                </div>

                {/* Table */}
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead className="bg-blue-50 border-b border-blue-100">
                      <tr>
                        {['#', 'Détecté le', 'Équip.', 'Type', 'Métrique', 'Cause probable', 'Action recommandée', 'Notif.', 'Statut', 'Actions'].map(h => (
                          <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-blue-50">
                      {items.map(inc => (
                        <tr
                          key={inc.id}
                          className="hover:bg-blue-50 transition-colors align-top cursor-pointer"
                          onClick={() => setDetail(inc)}
                        >
                          <td className="px-4 py-3 text-blue-300 font-mono text-xs">{inc.id}</td>
                          <td className="px-4 py-3 text-blue-400 whitespace-nowrap text-xs">{formatDate(inc.detected_at)}</td>
                          <td className="px-4 py-3 text-xs">
                            <div className="text-slate-700 font-medium">{inc.device_name ?? `#${inc.device_id}`}</div>
                            <div className="text-blue-300 font-mono text-[10px]">{inc.device_ip ?? ''}</div>
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            {inc.alert_type ? (
                              <span
                                title={inc.alert_type}
                                className={`text-xs font-medium px-2 py-0.5 rounded-full border whitespace-nowrap ${
                                  inc.alert_type === 'lr_no_transit'
                                    ? 'bg-orange-50 text-orange-700 border-orange-200'
                                    : inc.severity === 'critical'
                                    ? 'bg-red-50 text-red-700 border-red-200'
                                    : 'bg-blue-50 text-blue-600 border-blue-200'
                                }`}
                              >
                                {alertTypeLabel(inc.alert_type)}
                              </span>
                            ) : (
                              <span className="text-blue-200 text-xs">—</span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                            {inc.metric_name && inc.metric_value !== null ? (
                              <span title={`Seuil : ${inc.threshold_value ?? '?'}`}>
                                {inc.metric_name} = <strong>{inc.metric_value}</strong>
                              </span>
                            ) : '—'}
                          </td>
                          <td className="px-4 py-3 text-xs whitespace-nowrap">
                            {inc.probable_cause ? (
                              <span className="bg-amber-50 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded text-xs">
                                {probableCauseLabel(inc.probable_cause)}
                              </span>
                            ) : (
                              <span className="text-blue-200">—</span>
                            )}
                          </td>
                          <td className="px-4 py-3 max-w-[200px]">
                            {inc.recommended_action ? (
                              <span className="text-xs text-slate-600 line-clamp-2" title={inc.recommended_action}>
                                {inc.recommended_action}
                              </span>
                            ) : (
                              <span className="text-blue-200 text-xs">—</span>
                            )}
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            {inc.notify_immediately ? (
                              <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-red-600" title="Notification immédiate">
                                <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                                Imm.
                              </span>
                            ) : (
                              <span className="text-[11px] text-blue-300" title="Différé / digest">Diff.</span>
                            )}
                          </td>
                          <td className="px-4 py-3"><IncidentStatusBadge status={inc.status} /></td>
                          <td className="px-4 py-3">
                            <div className="flex gap-1.5">
                              {inc.status === 'open' && (
                                <button
                                  onClick={(e) => handleAction(inc.id, 'acknowledged', e)}
                                  className="px-2.5 py-1 text-xs bg-orange-50 text-orange-600 border border-orange-200 rounded-lg hover:bg-orange-100 transition-colors whitespace-nowrap"
                                >
                                  Acquitter
                                </button>
                              )}
                              <button
                                onClick={(e) => handleAction(inc.id, 'resolved', e)}
                                className="px-2.5 py-1 text-xs bg-green-50 text-green-700 border border-green-200 rounded-lg hover:bg-green-100 transition-colors whitespace-nowrap"
                              >
                                Résoudre
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {detail && (
        <IncidentDetailModal incident={detail} onClose={() => setDetail(null)} />
      )}
    </div>
  )
}

function SeverityGroupIcon({ severity }: { severity: string }) {
  if (severity === 'critical') {
    return (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
      </svg>
    )
  }
  if (severity === 'warning') {
    return (
      <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    )
  }
  return (
    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

function ArchiveIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
    </svg>
  )
}
