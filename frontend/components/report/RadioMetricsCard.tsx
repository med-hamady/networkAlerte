import type { RadioMetrics } from '@/lib/types'

interface Props {
  data: RadioMetrics[]
}

function colorSignal(v: number | null): string {
  if (v === null) return 'text-gray-400'
  if (v < -80) return 'text-red-600 font-bold'
  if (v < -70) return 'text-orange-500 font-semibold'
  return 'text-green-600 font-semibold'
}

function colorCinr(v: number | null): string {
  if (v === null) return 'text-gray-400'
  if (v < 10) return 'text-red-600 font-bold'
  if (v < 20) return 'text-orange-500 font-semibold'
  return 'text-green-600 font-semibold'
}

function colorCcq(v: number | null): string {
  if (v === null) return 'text-gray-400'
  if (v < 50) return 'text-red-600 font-bold'
  if (v < 75) return 'text-orange-500 font-semibold'
  return 'text-green-600 font-semibold'
}

function fmt(v: number | null, suffix: string, digits = 1): string {
  if (v === null) return '—'
  return `${v.toFixed(digits)} ${suffix}`
}

export default function RadioMetricsCard({ data }: Props) {
  if (data.length === 0) {
    return (
      <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-blue-900 mb-2">Qualité radio</h2>
        <p className="text-sm text-gray-500">
          Aucune métrique radio collectée sur la période.
        </p>
      </section>
    )
  }

  return (
    <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-blue-900 mb-1">Qualité radio</h2>
      <p className="text-sm text-blue-500 mb-4">
        Moyennes des métriques RF collectées par équipement —{' '}
        <span className="text-green-600 font-semibold">vert = bon</span>,{' '}
        <span className="text-orange-500 font-semibold">orange = à surveiller</span>,{' '}
        <span className="text-red-600 font-semibold">rouge = critique</span>.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="bg-blue-50 text-blue-900 text-left">
              <th className="px-3 py-2 font-medium">Équipement</th>
              <th className="px-3 py-2 font-medium text-right">Signal moy.</th>
              <th className="px-3 py-2 font-medium text-right">Signal min.</th>
              <th className="px-3 py-2 font-medium text-right">CINR moy.</th>
              <th className="px-3 py-2 font-medium text-right">CCQ moy.</th>
            </tr>
          </thead>
          <tbody>
            {data.map((m) => (
              <tr key={m.device_id} className="border-b border-blue-50">
                <td className="px-3 py-2 font-medium text-blue-900">{m.device_name}</td>
                <td className={`px-3 py-2 text-right ${colorSignal(m.avg_signal_dbm)}`}>
                  {fmt(m.avg_signal_dbm, 'dBm')}
                </td>
                <td className={`px-3 py-2 text-right ${colorSignal(m.min_signal_dbm)}`}>
                  {fmt(m.min_signal_dbm, 'dBm')}
                </td>
                <td className={`px-3 py-2 text-right ${colorCinr(m.avg_cinr_db)}`}>
                  {fmt(m.avg_cinr_db, 'dB')}
                </td>
                <td className={`px-3 py-2 text-right ${colorCcq(m.avg_ccq_pct)}`}>
                  {fmt(m.avg_ccq_pct, '%', 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
