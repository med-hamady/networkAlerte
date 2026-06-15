'use client'

import { formatUptime } from '@/lib/types'
import type { SiteOverviewItem, SitePowerDevice } from '@/lib/types'

interface Props {
  site: SiteOverviewItem
  onShowPannes: (name: string) => void
  onShowEquipment: (name: string, filter?: 'all' | 'infra') => void
}

export default function SiteOverviewCard({ site, onShowPannes, onShowEquipment }: Props) {
  const hasPannes = site.pannes > 0
  const downFor = site.down_since
    ? formatUptime(Math.max(0, Math.floor((Date.now() - new Date(site.down_since).getTime()) / 1000)))
    : null

  return (
    <div
      className={`rounded-xl border bg-white shadow-sm overflow-hidden ${
        hasPannes ? 'border-red-300' : 'border-blue-100'
      }`}
    >
      {/* Header band */}
      <div className={`flex items-center gap-3 py-5 px-5 ${hasPannes ? 'bg-red-50' : 'bg-blue-50'}`}>
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

      {/* Stats */}
      <div className="p-4 space-y-3">
        <div className="grid grid-cols-2 gap-x-2 gap-y-3 text-center">
          <Stat
            value={site.infra}
            label="Équipements infra"
            tone="blue"
            onClick={site.infra > 0 ? () => onShowEquipment(site.name, 'infra') : undefined}
          />
          <Stat value={site.clients_online}   label="Clients en ligne"  tone="green" />
          <Stat value={site.clients_blocked}  label="Clients bloqués"   tone={site.clients_blocked > 0 ? 'amber' : 'slate'} />
          <Stat value={site.pannes}           label="Pannes"            tone={hasPannes ? 'red' : 'slate'} />
        </div>

        {site.power_devices.length > 0 && (
          <div className="space-y-1.5 pt-1 border-t border-blue-50">
            {site.power_devices.map(d => (
              <SitePowerStats key={d.id} device={d} showName={site.power_devices.length > 1} />
            ))}
          </div>
        )}

        {hasPannes && (
          <button
            onClick={() => onShowPannes(site.name)}
            className="w-full rounded-lg bg-red-50 hover:bg-red-100 border border-red-200 text-red-600 text-xs font-medium py-2 px-3 flex items-center justify-center gap-1.5 transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z" />
            </svg>
            Voir le détail des pannes{downFor ? ` · depuis ${downFor}` : ''}
          </button>
        )}

        <button
          onClick={() => onShowEquipment(site.name)}
          className="w-full pt-2 border-t border-blue-50 flex items-center justify-between text-blue-300 hover:text-blue-600 transition-colors group"
        >
          <span className="text-xs">Voir les équipements</span>
          <span className="text-sm group-hover:translate-x-0.5 transition-transform">→</span>
        </button>
      </div>
    </div>
  )
}

// Friendly labels + colour for each battery a UISP Power reports. The slug,
// ordering, AC source and battery percentages are all resolved server-side
// (fn_site_overview); this only maps them to labels/colours for display.
const BATTERY_LABELS: Record<string, string> = {
  li_ion:    'Batterie interne (Li-Ion)',
  lead_acid: 'Batterie externe (plomb)',
}

function battColor(pct: number | null | undefined): string {
  if (pct == null) return 'text-blue-300'
  if (pct < 10) return 'text-red-500'
  if (pct < 25) return 'text-amber-500'
  return 'text-green-600'
}

// Power-source + per-battery readout for one UISP Power of the site. All values
// come pre-computed from the site overview payload.
function SitePowerStats({ device, showName }: { device: SitePowerDevice; showName: boolean }) {
  const isUp = device.status === 'up'
  const source = device.power_source

  return (
    <div className="space-y-1 text-xs">
      {showName && (
        <p className="text-blue-500 font-medium truncate">
          {device.name}
          {!isUp && <span className="text-red-400 font-normal ml-1">· hors ligne</span>}
        </p>
      )}
      {!showName && !isUp && (
        <p className="text-red-400">Alimentation · hors ligne</p>
      )}
      {isUp && (
        <>
          <div className="flex items-center justify-between gap-2">
            <span className="text-blue-400">Source d'alimentation</span>
            <span
              className={
                source == null ? 'text-blue-300'
                : source === 'mains' ? 'text-green-600 font-semibold'
                : 'text-orange-500 font-semibold'
              }
            >
              {source == null ? '—' : source === 'mains' ? '⚡ Secteur (SOMELEC)' : '🔋 Batterie'}
            </span>
          </div>
          {device.batteries.map(b => (
            <div key={b.slug} className="flex items-center justify-between gap-2">
              <span className="text-blue-400">{BATTERY_LABELS[b.slug] ?? `Batterie ${b.slug}`}</span>
              <span className={`font-semibold tabular-nums ${battColor(b.pct)}`}>
                {b.pct != null ? `${Math.round(b.pct)} %` : '—'}
              </span>
            </div>
          ))}
        </>
      )}
    </div>
  )
}

function Stat({ value, label, tone, onClick }: {
  value: number
  label: string
  tone: 'blue' | 'green' | 'amber' | 'red' | 'slate'
  onClick?: () => void
}) {
  const colors = {
    blue:  'text-blue-700',
    green: 'text-green-600',
    amber: 'text-amber-500',
    red:   'text-red-500',
    slate: 'text-slate-300',
  }[tone]
  const body = (
    <>
      <p className={`text-2xl font-bold ${colors}`}>{value}</p>
      <p className="text-[11px] text-blue-400 mt-0.5">{label}</p>
    </>
  )
  if (onClick) {
    return (
      <button
        onClick={onClick}
        className="rounded-lg px-1 py-1 hover:bg-blue-50 transition-colors cursor-pointer"
        title={`Voir les ${label.toLowerCase()}`}
      >
        {body}
      </button>
    )
  }
  return <div>{body}</div>
}
