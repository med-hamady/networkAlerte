import type { WeakPoint } from '@/lib/types'

interface Props {
  data: WeakPoint[]
}

export default function WeakPointsCard({ data }: Props) {
  if (data.length === 0) {
    return (
      <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-blue-900 mb-2">
          Points de faiblesse du réseau
        </h2>
        <p className="text-sm text-green-600">
          Aucun point de faiblesse identifié sur la période — le réseau est stable.
        </p>
      </section>
    )
  }

  return (
    <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-blue-900 mb-1">
        Points de faiblesse du réseau
      </h2>
      <p className="text-sm text-blue-500 mb-4">
        Patterns récurrents identifiés — équipements ou liens qui méritent une attention
        particulière.
      </p>
      <ul className="space-y-3">
        {data.map((wp, idx) => (
          <li
            key={`${wp.device_id}-${wp.alert_type ?? 'pattern'}-${idx}`}
            className="flex items-start gap-3 p-3 bg-amber-50 border border-amber-200 rounded-lg"
          >
            <AlertIcon />
            <div className="flex-1">
              <p className="font-semibold text-blue-900">{wp.device_name}</p>
              <p className="text-sm text-gray-700 mt-0.5">{wp.pattern_description}</p>
            </div>
            <span className="text-xs font-bold text-amber-700 bg-amber-200 px-2 py-1 rounded shrink-0">
              {wp.occurrence_count}×
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function AlertIcon() {
  return (
    <svg
      className="w-5 h-5 text-amber-600 shrink-0 mt-0.5"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
      />
    </svg>
  )
}
