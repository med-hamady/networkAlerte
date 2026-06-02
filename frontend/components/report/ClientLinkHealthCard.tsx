import type { ClientLinkHealth } from '@/lib/types'

interface Props {
  data: ClientLinkHealth
}

/**
 * Synthèse décisionnelle des liens clients LR :
 *   1. bandeau KPI (santé du parc en un coup d'œil)
 *   2. table de triage — uniquement les clients dégradés, du pire au mieux,
 *      avec verdict + cause + action.
 *
 * Remplace l'ancien tableau exhaustif des moyennes radio (70+ lignes, sans
 * verdict ni priorité). Classement basé sur l'incident consolidé
 * `lr_link_substandard` (mêmes seuils par famille que l'alert engine).
 */
export default function ClientLinkHealthCard({ data }: Props) {
  const { total_clients, ok_count, warning_count, critical_count, items } = data
  const healthyPct = total_clients > 0 ? Math.round((ok_count / total_clients) * 100) : 0

  return (
    <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-blue-900 mb-1">Santé des liens clients</h2>
      <p className="text-sm text-blue-500 mb-4">
        Classement par sévérité du lien radio — seuls les clients à intervenir sont listés.
      </p>

      {/* Bandeau KPI parc */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-5">
        <Kpi label="Clients" value={total_clients} tone="neutral" />
        <Kpi label="🟢 Bon" value={ok_count} tone="green" sub={`${healthyPct}% du parc`} />
        <Kpi label="🟠 À surveiller" value={warning_count} tone="amber" />
        <Kpi label="🔴 Critiques" value={critical_count} tone="red" />
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-green-600">
          Aucun lien client dégradé sur la période — l'ensemble du parc est sain.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="bg-blue-50 text-blue-900 text-left">
                <th className="px-3 py-2 font-medium">Client</th>
                <th className="px-3 py-2 font-medium text-center">Verdict</th>
                <th className="px-3 py-2 font-medium">Cause principale</th>
                <th className="px-3 py-2 font-medium">Action recommandée</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.device_id}
                  className={`border-b border-blue-50 ${
                    it.severity === 'critical'
                      ? 'bg-red-50 border-l-4 border-red-500'
                      : 'bg-orange-50 border-l-4 border-orange-400'
                  }`}
                >
                  <td className="px-3 py-2 font-medium text-blue-900">
                    {it.device_name}
                    {!it.currently_open && (
                      <span className="ml-2 text-xs font-normal text-gray-500">(résolu)</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-center">{verdictBadge(it.severity)}</td>
                  <td className="px-3 py-2 text-gray-700">{it.cause}</td>
                  <td className="px-3 py-2 text-gray-700">{it.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Kpi({
  label,
  value,
  tone,
  sub,
}: {
  label: string
  value: number
  tone: 'neutral' | 'green' | 'amber' | 'red'
  sub?: string
}) {
  const toneCls = {
    neutral: 'bg-blue-50 text-blue-900 border-blue-100',
    green: 'bg-green-50 text-green-700 border-green-200',
    amber: 'bg-orange-50 text-orange-700 border-orange-200',
    red: 'bg-red-50 text-red-700 border-red-200',
  }[tone]
  return (
    <div className={`rounded-lg border px-4 py-3 ${toneCls}`}>
      <p className="text-xs font-medium uppercase tracking-wider opacity-80">{label}</p>
      <p className="text-2xl font-bold leading-tight">{value}</p>
      {sub && <p className="text-xs opacity-70 mt-0.5">{sub}</p>}
    </div>
  )
}

function verdictBadge(severity: 'critical' | 'warning') {
  const cls =
    severity === 'critical'
      ? 'bg-red-100 text-red-700'
      : 'bg-orange-100 text-orange-700'
  const label = severity === 'critical' ? 'Critique' : 'À surveiller'
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${cls}`}>
      {label}
    </span>
  )
}
