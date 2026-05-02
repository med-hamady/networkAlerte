import type { DeviceReliability } from '@/lib/types'
import { deviceTypeLabel } from '@/lib/types'

interface Props {
  data: DeviceReliability[]
}

function formatMinutes(minutes: number | null): string {
  if (minutes === null) return '—'
  if (minutes < 1) return '< 1 min'
  if (minutes < 60) return `${Math.round(minutes)} min`
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return `${h}h ${m.toString().padStart(2, '0')}min`
}

function rowClass(downtime: number): string {
  if (downtime > 5) return 'bg-red-50 border-l-4 border-red-500'
  if (downtime >= 2) return 'bg-orange-50 border-l-4 border-orange-400'
  return ''
}

function statusBadge(status: string) {
  const map: Record<string, string> = {
    up: 'bg-green-100 text-green-700',
    down: 'bg-red-100 text-red-700',
    unknown: 'bg-gray-100 text-gray-600',
  }
  const cls = map[status] ?? 'bg-gray-100 text-gray-600'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {status.toUpperCase()}
    </span>
  )
}

export default function DeviceReliabilityCard({ data }: Props) {
  if (data.length === 0) {
    return (
      <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-blue-900 mb-2">Fiabilité des équipements</h2>
        <p className="text-sm text-gray-500">Aucun équipement supervisé.</p>
      </section>
    )
  }

  return (
    <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-blue-900 mb-1">Fiabilité des équipements</h2>
      <p className="text-sm text-blue-500 mb-4">
        Classement par nombre d'incidents — les lignes en rouge signalent les équipements
        défaillants (plus de 5 pannes), en orange les équipements à surveiller.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-blue-50 text-blue-900 text-left">
              <th className="px-3 py-2 font-medium">Équipement</th>
              <th className="px-3 py-2 font-medium">Type</th>
              <th className="px-3 py-2 font-medium">Localisation</th>
              <th className="px-3 py-2 font-medium text-center">Statut</th>
              <th className="px-3 py-2 font-medium text-right">Incidents</th>
              <th className="px-3 py-2 font-medium text-right">Pannes</th>
              <th className="px-3 py-2 font-medium text-right">Temps résolution moy.</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr
                key={d.device_id}
                className={`border-b border-blue-50 ${rowClass(d.downtime_incidents)}`}
              >
                <td className="px-3 py-2 font-medium text-blue-900">{d.device_name}</td>
                <td className="px-3 py-2 text-gray-600">{deviceTypeLabel(d.device_type)}</td>
                <td className="px-3 py-2 text-gray-600">{d.location ?? '—'}</td>
                <td className="px-3 py-2 text-center">{statusBadge(d.current_status)}</td>
                <td className="px-3 py-2 text-right font-semibold">{d.total_incidents}</td>
                <td className="px-3 py-2 text-right">
                  <span
                    className={
                      d.downtime_incidents > 5
                        ? 'text-red-700 font-bold'
                        : d.downtime_incidents >= 2
                        ? 'text-orange-600 font-semibold'
                        : 'text-gray-700'
                    }
                  >
                    {d.downtime_incidents}
                  </span>
                </td>
                <td className="px-3 py-2 text-right text-gray-700">
                  {formatMinutes(d.avg_resolution_minutes)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
