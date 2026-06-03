'use client'

interface SiteSummary {
  name: string
  total: number
  up: number
  down: number
  openIncidents: number
}

interface Props {
  site: SiteSummary
  onClick: (name: string) => void
}

export default function SiteCard({ site, onClick }: Props) {
  const hasDown = site.down > 0
  const hasIncidents = site.openIncidents > 0

  return (
    <button
      onClick={() => onClick(site.name)}
      className={`
        w-full text-left rounded-xl border transition-all duration-200
        hover:shadow-md hover:-translate-y-0.5 active:translate-y-0 cursor-pointer group
        bg-white
        ${hasDown ? 'border-red-300' : 'border-blue-100 hover:border-blue-300'}
      `}
    >
      {/* Header band */}
      <div className={`rounded-t-xl flex items-center gap-3 py-5 px-5 ${
        hasDown ? 'bg-red-50' : 'bg-blue-50'
      }`}>
        <span className={`flex items-center justify-center h-11 w-11 rounded-lg shrink-0 ${
          hasDown ? 'bg-red-100 text-red-600' : 'bg-blue-100 text-blue-600'
        }`}>
          <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17.657 16.657L13.414 20.9a2 2 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        </span>
        <div className="min-w-0">
          <p className="font-bold text-blue-900 text-base leading-tight truncate">{site.name}</p>
          <p className="text-blue-400 text-xs mt-0.5">
            {site.total} équipement{site.total > 1 ? 's' : ''}
          </p>
        </div>
      </div>

      {/* Stats area */}
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-3 gap-2 text-center">
          <Stat value={site.up} label="En ligne" tone="green" />
          <Stat value={site.down} label="Hors ligne" tone={site.down > 0 ? 'red' : 'slate'} />
          <Stat value={site.openIncidents} label="Incidents" tone={hasIncidents ? 'amber' : 'slate'} />
        </div>

        <div className="pt-2 border-t border-blue-50 flex items-center justify-between">
          <span className="text-xs text-blue-300">Voir les équipements</span>
          <span className="text-blue-300 group-hover:text-blue-500 transition-colors text-sm">→</span>
        </div>
      </div>
    </button>
  )
}

function Stat({ value, label, tone }: { value: number; label: string; tone: 'green' | 'red' | 'amber' | 'slate' }) {
  const colors = {
    green: 'text-green-600',
    red: 'text-red-500',
    amber: 'text-amber-500',
    slate: 'text-slate-300',
  }[tone]
  return (
    <div>
      <p className={`text-xl font-bold ${colors}`}>{value}</p>
      <p className="text-[11px] text-blue-400 mt-0.5">{label}</p>
    </div>
  )
}
