import type { AlertTypeFrequency } from '@/lib/types'

interface Props {
  data: AlertTypeFrequency[]
}

function formatMinutes(minutes: number | null): string {
  if (minutes === null) return '—'
  if (minutes < 1) return '< 1 min'
  if (minutes < 60) return `${Math.round(minutes)} min`
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return `${h}h ${m.toString().padStart(2, '0')}min`
}

export default function AlertFrequencyCard({ data }: Props) {
  if (data.length === 0) {
    return (
      <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-blue-900 mb-2">
          Problèmes les plus fréquents
        </h2>
        <p className="text-sm text-gray-500">
          Aucun incident détecté sur la période.
        </p>
      </section>
    )
  }

  const maxCount = Math.max(...data.map((d) => d.occurrence_count), 1)

  return (
    <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-blue-900 mb-1">
        Problèmes les plus fréquents
      </h2>
      <p className="text-sm text-blue-500 mb-4">
        Types d'alertes triés par fréquence — identifie les pannes récurrentes du réseau.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-blue-50 text-blue-900 text-left">
              <th className="px-3 py-2 font-medium">Type d'alerte</th>
              <th className="px-3 py-2 font-medium w-1/3">Fréquence</th>
              <th className="px-3 py-2 font-medium text-right">Occurrences</th>
              <th className="px-3 py-2 font-medium text-right">Équipements touchés</th>
              <th className="px-3 py-2 font-medium text-right">Temps résolution moy.</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => {
              const pct = (row.occurrence_count / maxCount) * 100
              return (
                <tr key={row.alert_type} className="border-b border-blue-50">
                  <td className="px-3 py-2 font-medium text-blue-900">
                    {row.alert_type_label}
                  </td>
                  <td className="px-3 py-2">
                    <div className="w-full bg-blue-50 rounded-full h-2 overflow-hidden">
                      <div
                        className="h-full bg-blue-500 rounded-full"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right font-semibold">
                    {row.occurrence_count}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-700">
                    {row.affected_device_count}
                  </td>
                  <td className="px-3 py-2 text-right text-gray-700">
                    {formatMinutes(row.avg_resolution_minutes)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
