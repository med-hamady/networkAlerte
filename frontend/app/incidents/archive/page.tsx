'use client'

import Link from 'next/link'
import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Incident } from '@/lib/types'
import { alertTypeLabel, formatDate, probableCauseLabel } from '@/lib/types'
import SeverityBadge from '@/components/SeverityBadge'
import IncidentDetailModal from '@/components/IncidentDetailModal'

const SEVERITY_ORDER = ['critical', 'warning', 'info'] as const

const SEVERITY_META: Record<string, { label: string; header: string }> = {
  critical: { label: 'Critique',  header: 'bg-red-50 border-red-200 text-red-700'       },
  warning:  { label: 'Attention', header: 'bg-amber-50 border-amber-200 text-amber-700' },
  info:     { label: 'Info',      header: 'bg-blue-50 border-blue-200 text-blue-600'    },
}

export default function IncidentsArchivePage() {
  const [detail, setDetail] = useState<Incident | null>(null)

  const { data: incidents, isLoading } = useSWR<Incident[]>(
    `${endpoints.incidents}?status=resolved&limit=500`,
    fetcher,
    { refreshInterval: 60_000 },
  )

  const groups = SEVERITY_ORDER
    .map(sev => ({ severity: sev, items: incidents?.filter(i => i.severity === sev) ?? [] }))
    .filter(g => g.items.length > 0)

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Archive des incidents</h1>
          <p className="text-blue-400 text-sm mt-1">
            Incidents résolus — {isLoading ? '…' : (incidents?.length ?? 0)} enregistrement{(incidents?.length ?? 0) > 1 ? 's' : ''}
          </p>
        </div>
        <Link
          href="/incidents"
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-blue-500 border border-blue-200 rounded-xl bg-white hover:bg-blue-50 hover:border-blue-300 transition-all shrink-0"
        >
          <BackIcon className="w-4 h-4" />
          Incidents actifs
        </Link>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : !incidents?.length ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-blue-400 text-sm">Aucun incident résolu dans l'archive</p>
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map(({ severity, items }) => {
            const meta = SEVERITY_META[severity]
            return (
              <div key={severity} className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">

                {/* Severity group header */}
                <div className={`flex items-center gap-2 px-4 py-2.5 border-b ${meta.header}`}>
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
                        {['#', 'Détecté le', 'Résolu le', 'Durée', 'Équip.', 'Type', 'Cause probable', 'Sévérité'].map(h => (
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
                          <td className="px-4 py-3 text-green-600 whitespace-nowrap text-xs">{formatDate(inc.resolved_at)}</td>
                          <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
                            {inc.resolved_at ? duration(inc.detected_at, inc.resolved_at) : '—'}
                          </td>
                          <td className="px-4 py-3 text-xs">
                            <div className="text-slate-700 font-medium">{inc.device_name ?? `#${inc.device_id}`}</div>
                            <div className="text-blue-300 font-mono text-[10px]">{inc.device_ip ?? ''}</div>
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap">
                            {inc.alert_type ? (
                              <span
                                title={inc.alert_type}
                                className="text-xs font-medium px-2 py-0.5 rounded-full border bg-slate-50 text-slate-600 border-slate-200 whitespace-nowrap"
                              >
                                {alertTypeLabel(inc.alert_type)}
                              </span>
                            ) : (
                              <span className="text-blue-200 text-xs">—</span>
                            )}
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
                          <td className="px-4 py-3"><SeverityBadge severity={inc.severity} /></td>
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

function duration(from: string, to: string): string {
  const s = Math.floor((new Date(to).getTime() - new Date(from).getTime()) / 1000)
  if (s < 60)   return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60)   return `${m}min`
  const h = Math.floor(m / 60)
  const rm = m % 60
  if (h < 24)   return `${h}h ${rm.toString().padStart(2, '0')}min`
  return `${Math.floor(h / 24)}j ${(h % 24)}h`
}

function BackIcon({ className }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
    </svg>
  )
}
