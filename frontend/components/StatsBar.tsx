import type { ReactNode } from 'react'

/**
 * Icône PNG de `public/brand/icons/`, en deux familles :
 *
 * - `MaskIcon` — dessin NOIR au trait (équipements, client, pylône) : servi en
 *   MASQUE CSS pour prendre la couleur du chiffre (`currentColor`). Une `<img>`
 *   resterait noire quelle que soit la couleur d'état.
 * - `ColorIcon` — dessin DÉJÀ colorié (hors-ligne, incident, pannes) : servi
 *   tel quel, ses couleurs sont voulues. Le passer en masque l'aplatirait.
 */
function MaskIcon({ file, className }: { file: string; className?: string }) {
  const src = `url(/brand/icons/${file})`
  return (
    <span
      aria-hidden
      className={`inline-block bg-current ${className ?? ''}`}
      style={{
        maskImage: src, WebkitMaskImage: src,
        maskSize: 'contain', WebkitMaskSize: 'contain',
        maskRepeat: 'no-repeat', WebkitMaskRepeat: 'no-repeat',
        maskPosition: 'center', WebkitMaskPosition: 'center',
      }}
    />
  )
}

function ColorIcon({ file, className }: { file: string; className?: string }) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={`/brand/icons/${file}`} alt="" aria-hidden className={className} />
}

interface StatProps {
  label: string
  value: number | string
  accent?: string
  icon: ReactNode
}

/**
 * Un chiffre du bandeau. ⚠️ Pas une carte : les 7 statistiques vivent dans UN
 * seul bloc, séparées par un filet — sept cartes blanches sur fond gris
 * découpaient le haut du tableau de bord sans rien dire de plus.
 */
function Stat({ label, value, accent = 'text-blue-900', icon }: StatProps) {
  return (
    <div className="flex items-center gap-3 px-5 py-4 min-w-0">
      <span className="flex items-center justify-center shrink-0">
        {icon}
      </span>
      <div className="min-w-0">
        <p className={`text-[26px] font-bold leading-none tabular-nums ${accent}`}>{value}</p>
        <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider mt-1 truncate">
          {label}
        </p>
      </div>
    </div>
  )
}

interface StatsBarProps {
  sites: number
  pannes: number
  clients: number
  total: number
  up: number
  down: number
  openIncidents: number
}

export default function StatsBar({
  sites, pannes, clients, total, up, down, openIncidents,
}: StatsBarProps) {
  return (
    <div className="overflow-hidden">
      {/* Rangée 1 — le parc et sa disponibilité */}
      <div className="grid grid-cols-2 lg:grid-cols-4 divide-x divide-y lg:divide-y-0 divide-slate-200">
        <Stat label="Total équipements" value={total} icon={<MaskIcon file="equipements.png" className="w-5 h-5 text-blue-700" />} />
        <Stat label="En ligne" value={up} accent="text-green-600" icon={<CheckIcon />} />
        <Stat
          label="Hors ligne"
          value={down}
          accent={down > 0 ? 'text-red-500' : 'text-blue-900'}
          icon={<ColorIcon file="hors-ligne.png" className="w-5 h-5" />}
        />
        <Stat
          label="Incidents ouverts"
          value={openIncidents}
          accent={openIncidents > 0 ? 'text-orange-500' : 'text-blue-900'}
          icon={<ColorIcon file="incident.png" className="w-5 h-5" />}
        />
      </div>

      {/* Rangée 2 — la vue par site et le parc abonné */}
      <div className="grid grid-cols-3 divide-x divide-slate-200 border-t border-slate-200">
        <Stat label="Sites" value={sites} icon={<MaskIcon file="site.png" className="w-5 h-5 text-blue-700" />} />
        <Stat
          label="Pannes"
          value={pannes}
          accent={pannes > 0 ? 'text-red-500' : 'text-blue-900'}
          icon={<ColorIcon file="pannes.png" className="w-5 h-5" />}
        />
        <Stat label="Clients" value={clients} icon={<MaskIcon file="client.png" className="w-5 h-5 text-blue-700" />} />
      </div>
    </div>
  )
}

function CheckIcon() {
  return (
    <svg className="w-5 h-5 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  )
}

