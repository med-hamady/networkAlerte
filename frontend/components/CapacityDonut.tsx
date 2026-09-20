// Donut SVG (aucune lib de charts dans le projet) — clients consommés vs
// disponibles pour une famille radio. L'arc « utilisé » se dessine par-dessus
// une piste neutre via stroke-dasharray, départ en haut (rotation -90°).
//
// La place LIBRE est rendue en gris neutre, pas dans une couleur vive : c'est
// l'occupation qu'on lit, et une piste aussi voyante que l'arc attirait l'œil
// sur le mauvais chiffre. `free` reste la couleur « disponible » des autres
// vues (barres par site) — pas celle de l'anneau.

interface CapacityDonutProps {
  title: string
  consumed: number
  available: number
  used: string          // couleur de l'arc « utilisé » (hex)
  free: string          // couleur « disponible » des autres vues (non utilisée par l'anneau)
  rockets?: number       // nb de Rockets comptés
  unknown?: number       // nb de Rockets à capacité indéterminée (exclus)
}

const TRACK = '#e8eef2'

export default function CapacityDonut({
  title, consumed, available, used, rockets, unknown = 0,
}: CapacityDonutProps) {
  const total = consumed + available
  const pct = total > 0 ? Math.round((consumed / total) * 100) : 0

  const size = 200
  const stroke = 18
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  // Borné à la circonférence : un Rocket au-delà de sa capacité ne doit pas
  // faire « tourner » l'arc une seconde fois.
  const usedLen = total > 0 ? Math.min(1, consumed / total) * circumference : 0

  return (
    <div className="flex flex-col items-center">
      <h3 className="font-semibold text-blue-900 mb-3">{title}</h3>

      <div className="relative" style={{ width: size, height: size }}>
        {/* ⚠️ Rotation par l'attribut SVG `transform`, PAS par une classe CSS
            (-rotate-90) : html2canvas, qui fabrique le PDF de /reports, ignore
            le transform CSS d'un <svg> — l'arc partait alors de 3 h au lieu de
            midi dans le document exporté. */}
        <svg width={size} height={size}>
          <circle
            cx={size / 2} cy={size / 2} r={radius}
            fill="none" stroke={TRACK} strokeWidth={stroke}
          />
          {total > 0 && usedLen > 0 && (
            <circle
              cx={size / 2} cy={size / 2} r={radius}
              fill="none" stroke={used} strokeWidth={stroke}
              strokeDasharray={`${usedLen} ${circumference}`}
              strokeLinecap="round"
              transform={`rotate(-90 ${size / 2} ${size / 2})`}
              style={{ transition: 'stroke-dasharray 0.8s ease-out' }}
            />
          )}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          {total > 0 ? (
            <>
              <span className="text-4xl font-bold text-blue-900 tabular-nums leading-none">
                {pct}<span className="text-xl align-top">%</span>
              </span>
              <span className="mt-2 text-[11px] font-medium uppercase tracking-wider text-slate-400">
                occupé
              </span>
              <span className="mt-1 text-xs text-slate-500 tabular-nums">{consumed} / {total}</span>
            </>
          ) : (
            <span className="text-sm text-slate-400">Aucune donnée</span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-4 mt-4 text-xs">
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-full" style={{ background: used }} />
          <span className="text-slate-600">Utilisé</span>
          <span className="font-semibold text-slate-800 tabular-nums">{consumed}</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-full border border-slate-300" style={{ background: TRACK }} />
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
