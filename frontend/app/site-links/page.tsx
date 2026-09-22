'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import type {
  Device, SiteLinkEnd, SiteLinkEndState, SiteLinkHealthResponse, SiteLinkPair,
} from '@/lib/types'

/**
 * Liaisons ENTRE SITES (backhauls P2P AF60 / airMAX) dégradées, dessinées
 * pylône ↔ pylône. Sous-page de « Liaisons » dans le menu ; même source et
 * même droit (`lr_health.view`) que /lr-health, dont elle était un onglet.
 */
export default function SiteLinksPage() {
  // Fiche d'équipement ouverte SUR PLACE (sans redirection vers /sites).
  const [selected, setSelected] = useState<Device | null>(null)
  const [deviceLoading, setDeviceLoading] = useState(false)

  const openDevice = async (id: number) => {
    setDeviceLoading(true)
    try {
      setSelected(await fetcher(endpoints.device(id)))
    } catch { /* device introuvable : on n'ouvre pas la fiche */ }
    finally { setDeviceLoading(false) }
  }

  return (
    <>
      <SiteLinksSection onOpenDevice={openDevice} />

      {deviceLoading && selected == null && (
        <div className="fixed inset-0 bg-blue-900/30 backdrop-blur-sm z-50 flex items-center justify-center animate-fade-in">
          <div className="bg-white rounded-xl shadow-2xl px-6 py-5 flex items-center gap-3">
            <span className="w-5 h-5 rounded-full border-2 border-blue-200 border-t-blue-600 animate-spin shrink-0" />
            <span className="text-sm font-medium text-blue-900">Chargement de l'équipement…</span>
          </div>
        </div>
      )}

      <DeviceDetailModal
        device={selected}
        onClose={() => setSelected(null)}
        onNavigate={setSelected}
      />
    </>
  )
}

function fmt(value: number | null | undefined, suffix: string, digits = 0): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(digits)}${suffix}`
}

// AF60 se lit en Gb/s ; un backhaul airMAX (capacité bien plus faible) en Mb/s.
function capDisplay(mbps: number | null, linkType: string): string {
  if (mbps === null) return '—'
  return linkType === 'af60' ? `${(mbps / 1000).toFixed(2)} Gb/s` : `${mbps.toFixed(0)} Mb/s`
}

/** « A2 PK1 » → « PK1 » : le préfixe commun à tous les sites n'apporte rien. */
function siteLabel(site: string | null): string {
  if (!site) return '?'
  return site.replace(/^A2\s+/, '').trim() || site
}

// Raison de l'état d'un bout, rendue telle quelle. Les deux bouts sont APPARIÉS
// PAR LE BACKEND depuis le câblage UISP (MAC) : plus aucune déduction d'après le
// nom de la radio, et plus d'« extrémité non listée » qui recouvrait cinq cas.
const END_STATE_TEXT: Record<SiteLinkEndState, string> = {
  measured: '',
  no_data: 'En ligne — aucune capacité relevée',
  down: 'Hors ligne — dernière mesure ignorée',
  unknown: 'Statut inconnu — hors du ping (sans IP)',
  unsupervised: 'Non supervisé — absent de notre inventaire',
  uncabled: 'Autre extrémité inconnue — lien absent du câblage UISP',
}

function SiteLinksSection({ onOpenDevice }: { onOpenDevice: (id: number) => void }) {
  const { data, isLoading } = useSWR<SiteLinkHealthResponse>(
    endpoints.siteLinks,
    fetcher,
    { refreshInterval: 60_000 },
  )

  const links: SiteLinkPair[] = data?.links ?? []
  const unmeasured: SiteLinkPair[] = data?.unmeasured ?? []

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Point-à-Point</h1>

      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : links.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-green-600 font-semibold text-sm">✓ Toutes les liaisons mesurées sont au-dessus de leur plancher de capacité</p>
          <p className="text-blue-400 text-xs mt-1">Aucun lien P2P (AF60 ou airMAX) dégradé en ce moment</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-red-700">
            <span className="text-xs font-bold uppercase tracking-widest">Capacité dégradée</span>
            <span className="text-xs font-semibold opacity-70">
              {links.length} liaison{links.length > 1 ? 's' : ''}
            </span>
          </div>
          {links.map(l => (
            <SiteLinkDiagram key={l.key} link={l} onOpenDevice={onOpenDevice} />
          ))}
        </div>
      )}

      {unmeasured.length > 0 && (
        <UnmeasuredLinks links={unmeasured} onOpenDevice={onOpenDevice} />
      )}
    </section>
  )
}

// ─── Schéma pylône ↔ pylône d'une liaison entre sites ─────────────────────────

function SiteLinkDiagram({ link, onOpenDevice }: {
  link: SiteLinkPair
  onOpenDevice: (id: number) => void
}) {
  const radioLabel = link.link_type === 'af60' ? 'F60' : 'PTP'
  const beam = link.degraded ? '#dc2626' : '#059669'
  const { end_a: a, end_b: b } = link
  // Pylône estompé = bout qui n'entre pas dans le verdict ; rouge = hors ligne.
  const dimA = a.state !== 'measured' && a.state !== 'down'
  const dimB = b.state !== 'measured' && b.state !== 'down'

  return (
    <div className={`bg-white rounded-2xl border shadow-sm overflow-hidden flex flex-col md:flex-row md:items-start ${link.degraded ? 'border-red-200' : 'border-slate-200'}`}>
      {/* Mesure de CHAQUE extrémité, à l'extérieur de son pylône */}
      <EndInfo end={a} linkType={link.link_type} onOpenDevice={onOpenDevice} align="left" />
      <svg viewBox="0 0 620 250" className="flex-1 min-w-0 w-full h-auto" role="img"
           aria-label={`Liaison ${radioLabel} entre ${siteLabel(a.site)} et ${siteLabel(b.site)}`}>
        <style>{`@keyframes slBeam { to { stroke-dashoffset: -28; } }`}</style>

        {/* Sol */}
        <line x1="20" y1="232" x2="600" y2="232" stroke="#e2e8f0" strokeWidth="2" />

        <LinkPylon x={70} down={a.state === 'down'} dim={dimA} />
        <LinkPylon x={550} down={b.state === 'down'} dim={dimB} />

        {/* Antennes radio montées en haut des pylônes, face à face */}
        <RadioDish x={92} y={70} facing="right" label={radioLabel} dim={a.state !== 'measured'} />
        <RadioDish x={528} y={70} facing="left" label={radioLabel} dim={b.state !== 'measured'} />

        {/* Faisceau radio : zone de Fresnel + ligne animée */}
        <ellipse cx="310" cy="70" rx="196" ry="22" fill={beam} opacity="0.06" />
        <line x1="118" y1="70" x2="502" y2="70" stroke={beam} strokeWidth="3" strokeLinecap="round"
              strokeDasharray="14 14" style={{ animation: 'slBeam 1.2s linear infinite' }} />

        {/* Étiquette de la liaison : capacité du bout le plus dégradé */}
        <g transform="translate(310 118)">
          <text textAnchor="middle" y="0" fontSize="20" fontWeight="700" fill={beam}>
            {capDisplay(link.capacity_mbps, link.link_type)}
          </text>
          <text textAnchor="middle" y="24" fontSize="12" fill="#64748b">
            {link.distance_m !== null ? `${(link.distance_m / 1000).toFixed(1).replace('.', ',')} km · ` : ''}
            plancher {capDisplay(link.capacity_floor_mbps, link.link_type)}
          </text>
        </g>
      </svg>
      <EndInfo end={b} linkType={link.link_type} onOpenDevice={onOpenDevice} align="right" />
    </div>
  )
}

/** Liaisons dont AUCUN bout n'est évaluable — nommées, pas seulement comptées. */
function UnmeasuredLinks({ links, onOpenDevice }: {
  links: SiteLinkPair[]
  onOpenDevice: (id: number) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-sm">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between gap-3 px-5 py-3 text-left"
      >
        <span className="text-sm font-semibold text-slate-700">
          {links.length} liaison{links.length > 1 ? 's' : ''} non évaluée{links.length > 1 ? 's' : ''}
          <span className="font-normal text-slate-400"> — aucun bout en ligne avec une capacité relevée</span>
        </span>
        <span className="text-xs text-blue-600 shrink-0">{open ? 'Masquer' : 'Voir'}</span>
      </button>
      {open && (
        <ul className="divide-y divide-slate-100 border-t border-slate-100">
          {links.map(l => (
            <li key={l.key} className="px-5 py-2.5 grid grid-cols-1 md:grid-cols-2 gap-1 text-xs">
              {[l.end_a, l.end_b].map((e, i) => (
                <div key={i} className="flex items-baseline gap-2 min-w-0">
                  <span className="font-semibold text-blue-900 shrink-0">{siteLabel(e.site)}</span>
                  {e.device_id !== null ? (
                    <button type="button" onClick={() => onOpenDevice(e.device_id as number)}
                            className="text-blue-600 hover:underline truncate">{e.name}</button>
                  ) : (
                    <span className="text-slate-500 truncate">{e.name ?? ''}</span>
                  )}
                  <span className="text-slate-400 truncate">{END_STATE_TEXT[e.state] || 'Mesuré'}</span>
                </div>
              ))}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** Pylône en treillis (même dessin que la page Sites), posé au sol en y=232. */
function LinkPylon({ x, down, dim }: { x: number; down: boolean; dim: boolean }) {
  const c = dim ? '#cbd5e1' : down ? '#ef4444' : '#2f6177'
  return (
    <g transform={`translate(${x - 60} 112)`} stroke={c} strokeLinecap="round" strokeLinejoin="round" fill="none">
      <line x1="60" y1="-40" x2="60" y2="0" strokeWidth={3} />
      <line x1="54" y1="0" x2="36" y2="120" strokeWidth={4} />
      <line x1="66" y1="0" x2="84" y2="120" strokeWidth={4} />
      <line x1="54" y1="0" x2="66" y2="0" strokeWidth={3} />
      <line x1="50" y1="30" x2="70" y2="30" strokeWidth={2.5} />
      <line x1="45" y1="62" x2="75" y2="62" strokeWidth={2.5} />
      <line x1="40.5" y1="92" x2="79.5" y2="92" strokeWidth={2.5} />
      <path d="M54 0 L70 30 M66 0 L50 30 M50 30 L75 62 M70 30 L45 62 M45 62 L79.5 92 M75 62 L40.5 92 M40.5 92 L84 120 M79.5 92 L36 120" strokeWidth={1.8} />
    </g>
  )
}

/** Radio P2P (F60 / PTP) : boîtier + réflecteur tourné vers l'autre site. */
function RadioDish({ x, y, facing, label, dim }: {
  x: number; y: number; facing: 'left' | 'right'; label: string; dim: boolean
}) {
  const s = facing === 'right' ? 1 : -1
  const body = dim ? '#cbd5e1' : '#295364'
  return (
    <g transform={`translate(${x} ${y})`}>
      {/* Bras de fixation vers le mât */}
      <line x1={-22 * s} y1="0" x2={-4 * s} y2="0" stroke={body} strokeWidth="3" strokeLinecap="round" />
      {/* Réflecteur */}
      <path d={`M ${0} -20 Q ${26 * s} 0 ${0} 20`} fill={dim ? '#f1f5f9' : '#e0f2fe'} stroke={body} strokeWidth="2.5" />
      <rect x={facing === 'right' ? -6 : -2} y="-14" width="8" height="28" rx="2" fill={body} />
      <text x={6 * s} y="-28" textAnchor="middle" fontSize="13" fontWeight="700" fill={body}>{label}</text>
    </g>
  )
}

function EndInfo({ end, linkType, onOpenDevice, align }: {
  end: SiteLinkEnd
  linkType: 'af60' | 'airmax'
  onOpenDevice: (id: number) => void
  align: 'left' | 'right'
}) {
  const alignCls = align === 'left' ? 'items-start text-left' : 'items-end text-right'
  return (
    <div className={`md:w-56 shrink-0 flex flex-col gap-0.5 px-5 pt-5 pb-3 ${alignCls}`}>
      <p className="font-bold text-blue-900">{siteLabel(end.site)}</p>
      {end.name && <p className="text-xs text-slate-600">{end.name}</p>}
      {end.ip && <p className="text-[11px] font-mono text-blue-300">{end.ip}</p>}
      {end.state === 'measured' ? (
        <>
          <p className="text-xs text-slate-700">
            Signal <strong>{fmt(end.signal_dbm, ' dBm')}</strong> · SNR <strong>{fmt(end.snr_db, ' dB', 1)}</strong>
          </p>
          <p className="text-[11px] text-slate-500">
            Capacité mesurée ici : {capDisplay(end.capacity_mbps, linkType)}
          </p>
          {(end.dl_capacity_mbps !== null || end.ul_capacity_mbps !== null) && (
            <p className="text-[11px] text-slate-500">
              ↓ {capDisplay(end.dl_capacity_mbps, linkType)} · ↑ {capDisplay(end.ul_capacity_mbps, linkType)}
            </p>
          )}
        </>
      ) : (
        <p className={`text-xs ${end.state === 'down' ? 'text-red-600 font-medium' : 'text-slate-400'}`}>
          {END_STATE_TEXT[end.state]}
        </p>
      )}
      {end.device_id !== null && (
        <button
          type="button"
          onClick={() => onOpenDevice(end.device_id as number)}
          className="mt-1 text-xs font-medium text-blue-600 hover:text-blue-800 hover:underline"
        >
          Voir l'équipement →
        </button>
      )}
    </div>
  )
}
