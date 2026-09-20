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

  const openEquipment = () => onShowEquipment(site.name)

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Voir les équipements de ${site.name}`}
      onClick={openEquipment}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          openEquipment()
        }
      }}
      className="rounded-2xl overflow-hidden flex flex-col cursor-pointer
                 transition-all hover:bg-white hover:shadow-md
                 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
    >
      {/* Pylône — l'état du site se lit d'abord à sa couleur */}
      <div className="relative pt-5 pb-4 flex flex-col items-center">
        {/* 3 colonnes : la colonne vide de gauche équilibre celle de
            l'alimentation, pour que le pylône reste centré sur la carte. */}
        <div className="w-full grid grid-cols-[1fr_auto_1fr] items-center gap-2 px-4">
          <div />
          <SitePylon down={hasPannes} />
          <div className="min-w-0 justify-self-end space-y-2">
            {site.power_devices.map(d => (
              <SitePowerCompact key={d.id} device={d} showName={site.power_devices.length > 1} />
            ))}
          </div>
        </div>
        <p className="mt-3 font-bold text-blue-900 text-lg leading-tight truncate max-w-full px-4">
          {site.name}
        </p>
        {/* Aucune pastille d'état : la couleur du pylône le dit déjà, et le
            nombre de pannes est dans les chiffres juste en dessous. */}
      </div>

      {/* Chiffres clés */}
      <div className="px-4 pb-4 space-y-3 flex-1 flex flex-col">
        <div className="grid grid-cols-4 gap-1 text-center py-1">
          <Stat
            value={site.infra}
            label="Infra"
            tone="blue"
            onClick={site.infra > 0 ? () => onShowEquipment(site.name, 'infra') : undefined}
          />
          {/* Compteurs d'abonnés — absents (et non mis à zéro) quand le profil
              n'a pas le droit `sites.client_counts`. « Infra » et « Pannes »
              restent, eux : c'est ce qu'un profil de supervision vient chercher
              sur cette page. */}
          {site.clients_online !== null && (
            <Stat value={site.clients_online} label="En ligne" tone="green" />
          )}
          {site.clients_blocked !== null && (
            <Stat
              value={site.clients_blocked}
              label="Bloqués"
              tone={site.clients_blocked > 0 ? 'amber' : 'slate'}
            />
          )}
          <Stat value={site.pannes} label="Pannes" tone={hasPannes ? 'red' : 'slate'} />
        </div>

        <div className="mt-auto">
          {hasPannes && (
            <button
              onClick={(e) => { e.stopPropagation(); onShowPannes(site.name) }}
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
    </div>
  )
}

/**
 * Pylône d'antenne dessiné en SVG, coloré par l'état du site : vert quand il
 * fonctionne (avec des ondes qui pulsent), rouge avec des ondes éteintes quand
 * il a au moins un équipement d'infra en panne — même seuil que le reste de la
 * carte (`site.pannes > 0`), pour que le dessin ne dise jamais autre chose que
 * les chiffres en dessous.
 */
function SitePylon({ down }: { down: boolean }) {
  const tower = down ? 'text-red-500' : 'text-emerald-600'
  const waves = down ? 'text-red-300' : 'text-emerald-500'
  return (
    <svg viewBox="0 0 120 120" className="w-24 h-24" aria-hidden>
      {/* Ondes radio */}
      <g className={waves} fill="none" stroke="currentColor" strokeWidth={3} strokeLinecap="round">
        {[0, 1, 2].map((k) => {
          const r = 12 + k * 9
          const dx = r * 0.7
          return (
            <g
              key={k}
              className={down ? 'opacity-40' : 'animate-pulse'}
              style={down ? undefined : { animationDelay: `${k * 0.35}s` }}
            >
              <path d={`M ${60 - dx} ${22 - dx} A ${r} ${r} 0 0 0 ${60 - dx} ${22 + dx}`} />
              <path d={`M ${60 + dx} ${22 - dx} A ${r} ${r} 0 0 1 ${60 + dx} ${22 + dx}`} />
            </g>
          )
        })}
      </g>

      <g className={tower} stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" fill="none">
        {/* Mât et émetteur */}
        <line x1="60" y1="22" x2="60" y2="34" strokeWidth={3} />
        <circle cx="60" cy="22" r="4.5" fill="currentColor" stroke="none" />
        {/* Jambes */}
        <line x1="54" y1="34" x2="40" y2="112" strokeWidth={4} />
        <line x1="66" y1="34" x2="80" y2="112" strokeWidth={4} />
        {/* Traverses */}
        <line x1="54" y1="34" x2="66" y2="34" strokeWidth={3} />
        <line x1="51" y1="52" x2="69" y2="52" strokeWidth={2.5} />
        <line x1="47.5" y1="72" x2="72.5" y2="72" strokeWidth={2.5} />
        <line x1="44" y1="92" x2="76" y2="92" strokeWidth={2.5} />
        {/* Croisillons */}
        <path
          d="M54 34 L69 52 M66 34 L51 52 M51 52 L72.5 72 M69 52 L47.5 72 M47.5 72 L76 92 M72.5 72 L44 92 M44 92 L80 112 M76 92 L40 112"
          strokeWidth={1.8}
        />
        {/* Panneaux d'antenne */}
        <rect x="45" y="38" width="5" height="12" rx="1.5" fill="currentColor" stroke="none" />
        <rect x="70" y="38" width="5" height="12" rx="1.5" fill="currentColor" stroke="none" />
      </g>

      {/* Sol */}
      <line
        x1="28" y1="113" x2="92" y2="113"
        stroke="currentColor" strokeWidth={2} strokeLinecap="round" className="text-slate-300"
      />
    </svg>
  )
}

// Libellés courts + couleur de chaque batterie d'un UISP Power. Le slug,
// l'ordre, la source secteur et les pourcentages sont calculés côté serveur
// (fn_site_overview) ; ceci ne fait que les habiller pour l'affichage.
const BATTERY_LABELS: Record<string, string> = {
  li_ion:    'Li-Ion',
  lead_acid: 'Plomb',
}

function battColor(pct: number | null | undefined): { text: string; bar: string } {
  if (pct == null) return { text: 'text-slate-400', bar: 'bg-slate-300' }
  if (pct < 10) return { text: 'text-red-600', bar: 'bg-red-500' }
  if (pct < 25) return { text: 'text-amber-600', bar: 'bg-amber-500' }
  return { text: 'text-emerald-700', bar: 'bg-emerald-500' }
}

/**
 * Alimentation d'un UISP Power du site, en version COMPACTE posée à côté du
 * pylône : la source (secteur / batterie) puis une mini-jauge par batterie.
 */
function SitePowerCompact({ device, showName }: { device: SitePowerDevice; showName: boolean }) {
  const isUp = device.status === 'up'
  const source = device.power_source

  return (
    <div className="text-[11px] leading-tight space-y-1 w-[104px]">
      {showName && (
        <p className="text-slate-500 font-medium truncate" title={device.name}>{device.name}</p>
      )}
      {!isUp ? (
        <p className="text-red-500 font-medium">Alim. hors ligne</p>
      ) : (
        <>
          <p
            className={`font-semibold flex items-center gap-1 ${
              source == null ? 'text-slate-400'
              : source === 'mains' ? 'text-sky-700'
              : 'text-orange-600'
            }`}
            title={source === 'mains' ? 'Alimenté par le secteur (SOMELEC)' : undefined}
          >
            {source === 'mains' && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src="/brand/icons/source-alimentation.png" alt="" aria-hidden className="w-3.5 h-3.5 shrink-0" />
            )}
            {source === 'battery' && (
              // Icône noire au trait → utilisée en MASQUE pour prendre l'orange
              // du texte (une <img> resterait noire).
              <span
                aria-hidden
                className="inline-block w-4 h-4 shrink-0 bg-current"
                style={{
                  maskImage: 'url(/brand/icons/battery.png)',
                  WebkitMaskImage: 'url(/brand/icons/battery.png)',
                  maskSize: 'contain', WebkitMaskSize: 'contain',
                  maskRepeat: 'no-repeat', WebkitMaskRepeat: 'no-repeat',
                  maskPosition: 'center', WebkitMaskPosition: 'center',
                }}
              />
            )}
            {source == null ? 'Source —' : source === 'mains' ? 'SOMELEC' : 'Sur batterie'}
          </p>
          {device.batteries.map(b => {
            const c = battColor(b.pct)
            return (
              <div key={b.slug}>
                <div className="flex items-baseline justify-between gap-1">
                  <span className="text-slate-500">{BATTERY_LABELS[b.slug] ?? b.slug}</span>
                  <span className={`font-semibold tabular-nums ${c.text}`}>
                    {b.pct != null ? `${Math.round(b.pct)} %` : '—'}
                  </span>
                </div>
                <div className="mt-0.5 h-1 rounded-full bg-slate-100 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${c.bar}`}
                    style={{ width: `${Math.max(0, Math.min(100, b.pct ?? 0))}%` }}
                  />
                </div>
              </div>
            )
          })}
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
      <p className={`text-xl font-bold tabular-nums ${colors}`}>{value}</p>
      <p className="text-[10px] font-medium uppercase tracking-wide text-slate-500 mt-0.5">{label}</p>
    </>
  )
  if (onClick) {
    return (
      <button
        onClick={(e) => { e.stopPropagation(); onClick() }}
        className="rounded-lg px-1 py-1 hover:bg-slate-50 transition-colors cursor-pointer"
        title={`Voir les ${label.toLowerCase()}`}
      >
        {body}
      </button>
    )
  }
  return <div className="px-1 py-1">{body}</div>
}
