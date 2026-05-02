'use client'

import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { AlertPolicy } from '@/lib/types'
import { alertTypeLabel } from '@/lib/types'
import SeverityBadge from '@/components/SeverityBadge'

const CHANNEL_STYLES: Record<string, string> = {
  slack:   'bg-purple-50 text-purple-700 border-purple-200',
  email:   'bg-amber-50  text-amber-700  border-amber-200',
  webhook: 'bg-sky-50    text-sky-700    border-sky-200',
}

function ChannelChip({ channel }: { channel: string }) {
  const style = CHANNEL_STYLES[channel] ?? 'bg-slate-50 text-slate-600 border-slate-200'
  return (
    <span className={`inline-flex px-2 py-0.5 rounded text-[11px] font-mono border ${style}`}>
      {channel}
    </span>
  )
}

function YesNo({ value }: { value: boolean }) {
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${
      value ? 'text-green-600' : 'text-blue-300'
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full ${value ? 'bg-green-500' : 'bg-blue-200'}`} />
      {value ? 'Oui' : 'Non'}
    </span>
  )
}

export default function AlertPoliciesPage() {
  const { data: policies, isLoading, error } = useSWR<AlertPolicy[]>(
    endpoints.alertPolicies, fetcher, { refreshInterval: 60_000 },
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Politiques d&apos;alerte</h1>
        <p className="text-blue-400 text-sm mt-1">
          Référence des {policies?.length ?? '…'} alert_types : sévérité, action recommandée,
          canaux et politique de notification.
        </p>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-600 px-4 py-3 rounded-xl text-sm">
          Erreur : {error instanceof Error ? error.message : 'Chargement impossible'}
        </div>
      )}

      <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
        {isLoading ? (
          <div className="px-6 py-12 text-center text-blue-300">Chargement…</div>
        ) : !policies?.length ? (
          <div className="px-6 py-12 text-center text-blue-400">Aucune policy retournée</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-blue-50 border-b border-blue-100">
                <tr>
                  {['Alert type', 'Sévérité', 'Action recommandée', 'Notify imm.', 'Canaux', 'Groupable', 'Recovery'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-50">
                {policies.map(p => (
                  <tr key={p.alert_type} className="hover:bg-blue-50/50 transition-colors align-top">
                    <td className="px-4 py-3">
                      <code className="font-mono text-xs bg-blue-50 text-blue-700 border border-blue-200 px-1.5 py-0.5 rounded">
                        {p.alert_type}
                      </code>
                      <div className="text-[11px] text-blue-400 mt-0.5">
                        {alertTypeLabel(p.alert_type)}
                      </div>
                    </td>
                    <td className="px-4 py-3"><SeverityBadge severity={p.severity} /></td>
                    <td className="px-4 py-3 text-slate-700 max-w-md text-xs">
                      {p.recommended_action}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap"><YesNo value={p.notify_immediately} /></td>
                    <td className="px-4 py-3">
                      <div className="flex gap-1 flex-wrap">
                        {p.channels.map(c => <ChannelChip key={c} channel={c} />)}
                      </div>
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap"><YesNo value={p.groupable} /></td>
                    <td className="px-4 py-3 whitespace-nowrap"><YesNo value={p.recovery_notification} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="text-xs text-blue-400">
        <strong>Note :</strong> sévérité <code className="font-mono">dynamic</code> = la règle décide
        warning ou critical selon la valeur ; <em>notify immédiat</em> est alors forcé à
        true uniquement quand l&apos;incident est critical.
      </div>
    </div>
  )
}
