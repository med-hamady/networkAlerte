// Donut SVG (aucune lib de charts dans le projet) — clients consommés vs
// disponibles pour une famille radio. L'arc « utilisé » se dessine par-dessus
// le cercle « disponible » via stroke-dasharray, départ en haut (rotation -90°).

interface CapacityDonutProps {
  title: string
  consumed: number
  available: number
  used: string          // couleur de l'arc « utilisé » (hex)
  free: string          // couleur de l'arc « disponible » (hex)
  rockets?: number       // nb de Rockets comptés
  unknown?: number       // nb de Rockets à capacité indéterminée (exclus)
}

export default function CapacityDonut({
  title, consumed, available, used, free, rockets, unknown = 0,
}: CapacityDonutProps) {
  const total = consumed + available
  const pct = total > 0 ? Math.round((consumed / total) * 100) : 0

  const size = 200
  const stroke = 30
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const usedLen = total > 0 ? (consumed / total) * circumference : 0

  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5 flex flex-col items-center">
      <h3 className="font-semibold text-blue-900 mb-3">{title}</h3>

      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} className="-rotate-90">
          <circle
            cx={size / 2} cy={size / 2} r={radius}
            fill="none" stroke={total > 0 ? free : '#e2e8f0'} strokeWidth={stroke}
          />
          {total > 0 && (
            <circle
              cx={size / 2} cy={size / 2} r={radius}
              fill="none" stroke={used} strokeWidth={stroke}
              strokeDasharray={`${usedLen} ${circumference - usedLen}`}
              strokeLinecap="butt"
            />
          )}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {total > 0 ? (
            <>
              <span className="text-3xl font-bold text-blue-900 tabular-nums">{pct}%</span>
              <span className="text-xs text-slate-500 tabular-nums">{consumed} / {total}</span>
            </>
          ) : (
            <span className="text-sm text-slate-400">Aucune donnée</span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-4 mt-4 text-xs">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: used }} />
          <span className="text-slate-600">Utilisé</span>
          <span className="font-semibold text-slate-800 tabular-nums">{consumed}</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-sm" style={{ background: free }} />
          <span className="text-slate-600">Disponible</span>
          <span className="font-semibold text-slate-800 tabular-nums">{available}</span>
        </span>
      </div>

      <p className="text-[11px] text-blue-400 mt-2 text-center">
        {rockets ?? 0} Rocket{(rockets ?? 0) > 1 ? 's' : ''}
        {unknown > 0 && (
          <span className="text-amber-600"> · {unknown} à capacité indéterminée</span>
        )}
      </p>
    </div>
  )
}
