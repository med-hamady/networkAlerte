'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { AlertRecord } from '@/lib/types'
import { alertTypeLabel, formatDate } from '@/lib/types'
import SeverityBadge from '@/components/SeverityBadge'

type StatusFilter = 'all' | 'sent' | 'failed' | 'pending_digest' | 'pending'

const FILTER_LABELS: Record<StatusFilter, string> = {
  all:            'Toutes',
  sent:           'Envoyées',
  failed:         'Échouées',
  pending_digest: 'En attente digest',
  pending:        'En attente envoi',
}

export default function NotificationsPage() {
  const [filter, setFilter] = useState<StatusFilter>('all')

  const queryParam = filter === 'all' ? '' : `status=${filter}`
  const { data: records, isLoading } = useSWR<AlertRecord[]>(
    endpoints.alertRecords(queryParam),
    fetcher,
    { refreshInterval: 30_000 },
  )

  const all            = records?.length ?? 0
  const sent           = records?.filter(r => r.status === 'sent').length           ?? 0
  const failed         = records?.filter(r => r.status === 'failed').length         ?? 0
  const pendingDigest  = records?.filter(r => r.status === 'pending_digest').length ?? 0
  const pending        = records?.filter(r => r.status === 'pending').length        ?? 0
  const successRate    = (sent + failed) > 0 ? Math.round((sent / (sent + failed)) * 100) : null

  return (
    <div className="space-y-6">

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Notifications</h1>
        <p className="text-blue-400 text-sm mt-1">
          Toutes les alertes générées par le système — actualisation toutes les 30s
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        <StatCard label="Total"            value={all}           color="blue"   />
        <StatCard label="Envoyées"         value={sent}          color="green"  />
        <StatCard label="Échouées"         value={failed}        color="red"    />
        <StatCard label="Attente digest"   value={pendingDigest} color="amber"  />
        <StatCard
          label="Taux de succès"
          value={successRate !== null ? `${successRate}%` : '—'}
          color={successRate === null ? 'blue' : successRate < 80 ? 'red' : 'green'}
        />
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
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
        {isLoading ? (
          <div className="px-6 py-12 text-center text-blue-300 text-sm">Chargement…</div>
        ) : !records?.length ? (
          <div className="px-6 py-12 text-center">
            <p className="text-blue-400 text-sm">Aucune notification pour ce filtre</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-blue-50 border-b border-blue-100">
                <tr>
                  {['#', 'Date', 'Équipement', "Type d'alerte", 'Sévérité', 'Message', 'Statut'].map(h => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-50">
                {records.map(rec => (
                  <tr
                    key={`${rec.is_pending_digest ? 'p' : 'a'}-${rec.id}`}
                    className={`hover:bg-blue-50/40 transition-colors align-top ${
                      rec.is_pending_digest ? 'bg-amber-50/30' : ''
                    }`}
                  >
                    {/* ID */}
                    <td className="px-4 py-3 text-blue-300 font-mono text-xs">
                      {rec.is_pending_digest ? `i${rec.incident_id}` : rec.id}
                    </td>

                    {/* Date */}
                    <td className="px-4 py-3 text-blue-400 whitespace-nowrap text-xs">
                      {formatDate(rec.sent_at ?? rec.created_at)}
                    </td>

                    {/* Équipement */}
                    <td className="px-4 py-3">
                      {rec.device_name ? (
                        <>
                          <div className="text-slate-700 font-medium text-xs">{rec.device_name}</div>
                          <div className="text-blue-300 font-mono text-[10px]">{rec.device_ip ?? ''}</div>
                        </>
                      ) : (
                        <span className="text-blue-200 text-xs">—</span>
                      )}
                    </td>

                    {/* Type d'alerte */}
                    <td className="px-4 py-3 whitespace-nowrap">
                      {rec.incident_alert_type ? (
                        <span className={`text-xs font-medium px-2 py-0.5 rounded-full border whitespace-nowrap ${
                          rec.incident_severity === 'critical'
                            ? 'bg-red-50 text-red-700 border-red-200'
                            : rec.incident_severity === 'warning'
                            ? 'bg-amber-50 text-amber-700 border-amber-200'
                            : 'bg-blue-50 text-blue-600 border-blue-200'
                        }`}>
                          {alertTypeLabel(rec.incident_alert_type)}
                        </span>
                      ) : (
                        <span className="text-blue-200 text-xs">—</span>
                      )}
                    </td>

                    {/* Sévérité */}
                    <td className="px-4 py-3">
                      {rec.incident_severity
                        ? <SeverityBadge severity={rec.incident_severity} />
                        : <span className="text-blue-200 text-xs">—</span>
                      }
                    </td>

                    {/* Message */}
                    <td className="px-4 py-3 max-w-xs">
                      <span className="text-xs text-slate-600 line-clamp-2" title={rec.message}>
                        {rec.message}
                      </span>
                    </td>

                    {/* Statut */}
                    <td className="px-4 py-3">
                      <NotifStatusBadge status={rec.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

    </div>
  )
}

// ---------------------------------------------------------------------------

function StatCard({
  label, value, color,
}: {
  label: string
  value: string | number
  color: 'blue' | 'green' | 'red' | 'amber'
}) {
  const colors = {
    blue:  'text-blue-700  bg-blue-50  border-blue-100',
    green: 'text-green-700 bg-green-50 border-green-100',
    red:   'text-red-700   bg-red-50   border-red-100',
    amber: 'text-amber-700 bg-amber-50 border-amber-100',
  }
  return (
    <div className={`rounded-xl border px-4 py-4 shadow-sm ${colors[color]}`}>
      <p className="text-xs font-medium opacity-70 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-bold mt-1">{value}</p>
    </div>
  )
}

function NotifStatusBadge({ status }: { status: string }) {
  if (status === 'sent') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-green-700 bg-green-50 border border-green-200 px-2 py-0.5 rounded-full">
        <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
        Envoyée
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-red-700 bg-red-50 border border-red-200 px-2 py-0.5 rounded-full">
        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
        Échouée
      </span>
    )
  }
  if (status === 'pending_digest') {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
        Attente digest
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-500 bg-slate-50 border border-slate-200 px-2 py-0.5 rounded-full">
      <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
      En attente
    </span>
  )
}
