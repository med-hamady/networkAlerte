import type { ReportPeriodSummary } from '@/lib/types'

interface Props {
  period: ReportPeriodSummary
}

export default function PeriodSummaryCard({ period }: Props) {
  const stats = [
    { label: 'Total incidents', value: period.total_incidents, accent: 'text-blue-900' },
    {
      label: 'Critiques',
      value: period.critical_count,
      accent: period.critical_count > 0 ? 'text-red-600' : 'text-blue-900',
    },
    {
      label: 'Avertissements',
      value: period.warning_count,
      accent: period.warning_count > 0 ? 'text-orange-500' : 'text-blue-900',
    },
    { label: 'Résolus', value: period.resolved_count, accent: 'text-green-600' },
    {
      label: 'Encore ouverts',
      value: period.open_count,
      accent: period.open_count > 0 ? 'text-red-500' : 'text-blue-900',
    },
    { label: 'Équipements supervisés', value: period.devices_supervised, accent: 'text-blue-900' },
  ]

  return (
    <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-blue-900 mb-1">Vue d'ensemble</h2>
      <p className="text-sm text-blue-500 mb-5">
        Période : du {period.date_from} au {period.date_to}
      </p>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        {stats.map((s) => (
          <div
            key={s.label}
            className="bg-blue-50/40 border border-blue-100 rounded-lg px-4 py-3"
          >
            <p className="text-[11px] font-medium text-blue-400 uppercase tracking-wider">
              {s.label}
            </p>
            <p className={`text-2xl font-bold mt-1 ${s.accent}`}>{s.value}</p>
          </div>
        ))}
      </div>
    </section>
  )
}
