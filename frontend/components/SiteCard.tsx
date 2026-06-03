'use client'

import { formatUptime } from '@/lib/types'

interface SiteSummary {
  name: string
  pannes: number          // infra devices (rocket/switch/power) currently down
  clients: number         // LR clients attached to this site (via parent rocket)
  downSince: string | null // last_seen of the device down the longest (oldest)
}

interface Props {
  site: SiteSummary
  onClick: (name: string) => void
}

export default function SiteCard({ site, onClick }: Props) {
  const hasPannes = site.pannes > 0
  const downFor = site.downSince
    ? formatUptime(Math.max(0, Math.floor((Date.now() - new Date(site.downSince).getTime()) / 1000)))
    : null

  return (
    <button
      onClick={() => onClick(site.name)}
      className={`
        w-full text-left rounded-xl border transition-all duration-200
        hover:shadow-md hover:-translate-y-0.5 active:translate-y-0 cursor-pointer group
        bg-white
        ${hasPannes ? 'border-red-300' : 'border-blue-100 hover:border-blue-300'}
      `}
    >
      {/* Header band */}
      <div className={`rounded-t-xl flex items-center gap-3 py-5 px-5 ${
        hasPannes ? 'bg-red-50' : 'bg-blue-50'
      }`}>
        <span className={`flex items-center justify-center h-11 w-11 rounded-lg shrink-0 ${
          hasPannes ? 'bg-red-100 text-red-600' : 'bg-blue-100 text-blue-600'
        }`}>
          <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M17.657 16.657L13.414 20.9a2 2 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
            <path strokeLinecap="round" strokeLinejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
          </svg>
        </span>
        <div className="min-w-0">
          <p className="font-bold text-blue-900 text-base leading-tight truncate">{site.name}</p>
          <p className={`text-xs mt-0.5 ${hasPannes ? 'text-red-500 font-medium' : 'text-green-600'}`}>
            {hasPannes ? `${site.pannes} en panne` : 'Opérationnel'}
          </p>
        </div>
      </div>

      {/* Stats area */}
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-2 gap-2 text-center">
          <Stat value={site.pannes} label="Pannes" tone={hasPannes ? 'red' : 'slate'} />
          <Stat value={site.clients} label="Clients" tone="blue" />
        </div>

        {hasPannes && downFor && (
          <p className="text-center text-xs text-red-500">
            En panne depuis {downFor}
          </p>
        )}

        <div className="pt-2 border-t border-blue-50 flex items-center justify-between">
          <span className="text-xs text-blue-300">Voir les équipements</span>
          <span className="text-blue-300 group-hover:text-blue-500 transition-colors text-sm">→</span>
        </div>
      </div>
    </button>
  )
}

function Stat({ value, label, tone }: { value: number; label: string; tone: 'red' | 'blue' | 'slate' }) {
  const colors = {
    red: 'text-red-500',
    blue: 'text-blue-700',
    slate: 'text-slate-300',
  }[tone]
  return (
    <div>
      <p className={`text-xl font-bold ${colors}`}>{value}</p>
      <p className="text-[11px] text-blue-400 mt-0.5">{label}</p>
    </div>
  )
}
