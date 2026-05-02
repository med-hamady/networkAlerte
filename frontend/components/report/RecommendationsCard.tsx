import type { Recommendation } from '@/lib/types'

interface Props {
  data: Recommendation[]
}

const PRIORITY_STYLES: Record<string, { border: string; badge: string; label: string }> = {
  critique: {
    border: 'border-l-4 border-red-500 bg-red-50',
    badge: 'bg-red-600 text-white',
    label: 'CRITIQUE',
  },
  élevé: {
    border: 'border-l-4 border-orange-500 bg-orange-50',
    badge: 'bg-orange-500 text-white',
    label: 'ÉLEVÉ',
  },
  moyen: {
    border: 'border-l-4 border-blue-500 bg-blue-50',
    badge: 'bg-blue-500 text-white',
    label: 'MOYEN',
  },
}

const CATEGORY_LABELS: Record<string, string> = {
  disponibilite: 'Disponibilité',
  radio: 'Radio',
  alimentation: 'Alimentation',
  transit: 'Transit',
  switch: 'Switch',
  performance: 'Performance',
}

export default function RecommendationsCard({ data }: Props) {
  if (data.length === 0) {
    return (
      <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-blue-900 mb-2">Recommandations</h2>
        <p className="text-sm text-green-600">
          Aucune recommandation — le réseau ne présente pas de problème nécessitant une action.
        </p>
      </section>
    )
  }

  return (
    <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-blue-900 mb-1">
        Recommandations pour l'amélioration du réseau
      </h2>
      <p className="text-sm text-blue-500 mb-4">
        Actions concrètes priorisées, basées sur l'analyse des incidents et métriques.
      </p>
      <div className="space-y-3">
        {data.map((rec, idx) => {
          const style = PRIORITY_STYLES[rec.priority] ?? PRIORITY_STYLES.moyen
          const category = CATEGORY_LABELS[rec.category] ?? rec.category
          return (
            <div
              key={`${rec.priority}-${rec.title}-${idx}`}
              className={`rounded-lg p-4 ${style.border}`}
            >
              <div className="flex items-start gap-3 mb-2">
                <span
                  className={`text-xs font-bold px-2 py-1 rounded ${style.badge} shrink-0`}
                >
                  {style.label}
                </span>
                <span className="text-xs font-medium text-gray-500 uppercase tracking-wider mt-1">
                  {category}
                </span>
              </div>
              <h3 className="text-base font-semibold text-blue-900 mb-1">{rec.title}</h3>
              <p className="text-sm text-gray-700 mb-3">{rec.description}</p>
              {rec.affected_devices.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-medium text-gray-500 mr-1">
                    Équipements concernés :
                  </span>
                  {rec.affected_devices.map((name) => (
                    <span
                      key={name}
                      className="text-xs font-medium bg-white border border-gray-300 text-gray-700 px-2 py-0.5 rounded"
                    >
                      {name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
