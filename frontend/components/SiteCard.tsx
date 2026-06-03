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
  onShowPannes?: (name: string) => void
}

export default function SiteCard({ site, onShowPannes }: Props) {
  const hasPannes = site.pannes > 0
  const downFor = site.downSince
    ? formatUptime(Math.max(0, Math.floor((Date.now() - new Date(site.downSince).getTime()) / 1000)))
    : null

  return (
    <div
      className={`
        w-full text-left rounded-xl border bg-white
        ${hasPannes ? 'border-red-300' : 'border-blue-100'}
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

        {hasPannes && (
          <button
            onClick={() => onShowPannes?.(site.name)}
            className="w-full rounded-lg bg-red-50 hover:bg-red-100 border border-red-200 text-red-600 text-xs font-medium py-2 px-3 flex items-center justify-center gap-1.5 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
            </svg>
            Voir le détail des pannes{downFor ? ` · depuis ${downFor}` : ''}
          </button>
        )}
      </div>
    </div>
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
