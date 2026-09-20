'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import type { Device, SiteLinkHealthResponse, SiteLinkRow } from '@/lib/types'

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

// ─── Liaisons entre sites (Point-à-Point) — backhaul airFiber 60 ──────────────
// Critère UNIQUE : dernière capacité totale < plancher (1.95 Gb/s), lue en base.
function gbps(mbps: number | null): string {
  if (mbps === null) return '—'
  return `${(mbps / 1000).toFixed(2)} Gb/s`
}

// AF60 se lit en Gb/s ; un backhaul airMAX (capacité bien plus faible) en Mb/s.
function capDisplay(mbps: number | null, linkType: string): string {
  if (mbps === null) return '—'
  return linkType === 'af60' ? gbps(mbps) : `${mbps.toFixed(0)} Mb/s`
}

function SiteLinksSection({ onOpenDevice }: { onOpenDevice: (id: number) => void }) {
  const { data, isLoading } = useSWR<SiteLinkHealthResponse>(
    endpoints.siteLinks,
    fetcher,
    { refreshInterval: 60_000 },
  )

  const items: SiteLinkRow[] = data?.items ?? []
  const noData = data?.no_data_count ?? 0
  const pairs = pairSiteLinks(items)

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Point-à-Point</h1>
        {noData > 0 && (
          <p className="text-blue-300 text-xs mt-1">
            {noData} lien{noData > 1 ? 's' : ''} sans relevé de capacité — non évalué{noData > 1 ? 's' : ''}.
          </p>
        )}
      </div>

      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : items.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-green-600 font-semibold text-sm">✓ Toutes les liaisons entre sites sont au-dessus de leur plancher de capacité</p>
          <p className="text-blue-400 text-xs mt-1">Aucun lien P2P (AF60 ou airMAX) dégradé en ce moment</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="flex items-center gap-2 text-red-700">
            <span className="text-xs font-bold uppercase tracking-widest">Capacité dégradée</span>
            <span className="text-xs font-semibold opacity-70">
              {pairs.length} liaison{pairs.length > 1 ? 's' : ''}
            </span>
          </div>
          {pairs.map(p => (
            <SiteLinkDiagram key={p.key} pair={p} onOpenDevice={onOpenDevice} />
          ))}
        </div>
      )}
    </section>
  )
}

// ─── Schéma pylône ↔ pylône d'une liaison entre sites ─────────────────────────

/** Une liaison = les deux bouts d'un même lien P2P, chacun avec SA mesure. */
interface SiteLinkPair {
  key: string
  siteA: string
  siteB: string
  endA: SiteLinkRow | null
  endB: SiteLinkRow | null
}

/**
 * « F60 PK1-CT2 » → ['PK1', 'CT2'] : le site qui PORTE l'équipement, puis celui
 * qu'il vise. ⚠️ Déduit du NOM, faute de mieux : la réponse ne porte ni le site
 * ni le pair de chaque radio. Un nom qui ne suit pas la convention donne un
 * schéma à un seul bout (l'autre marqué « non identifié »), jamais un faux pair.
 */
function parseLinkName(name: string): [string, string] | null {
  const m = name.match(/([A-Za-z0-9]+)\s*[-–]\s*([A-Za-z0-9]+)\s*$/)
  return m ? [m[1].toUpperCase(), m[2].toUpperCase()] : null
}

function pairSiteLinks(items: SiteLinkRow[]): SiteLinkPair[] {
  const pairs = new Map<string, SiteLinkPair>()
  for (const row of items) {
    const parsed = parseLinkName(row.name)
    if (!parsed) {
      pairs.set(`solo-${row.device_id}`, {
        key: `solo-${row.device_id}`, siteA: row.name, siteB: '?', endA: row, endB: null,
      })
      continue
    }
    const [own, peer] = parsed
    const [a, b] = [own, peer].sort()
    const key = `${row.link_type}:${a}|${b}`
    const pair = pairs.get(key) ?? { key, siteA: a, siteB: b, endA: null, endB: null }
    if (own === a && !pair.endA) pair.endA = row
    else if (own === b && !pair.endB) pair.endB = row
    else pairs.set(`dup-${row.device_id}`, { key: `dup-${row.device_id}`, siteA: own, siteB: peer, endA: row, endB: null })
    pairs.set(key, pair)
  }
  return [...pairs.values()]
}

function SiteLinkDiagram({ pair, onOpenDevice }: {
  pair: SiteLinkPair
  onOpenDevice: (id: number) => void
}) {
  const ends = [pair.endA, pair.endB].filter((e): e is SiteLinkRow => e !== null)
  const ref = ends[0]
  const linkType = ref.link_type
  const radioLabel = linkType === 'af60' ? 'F60' : 'PTP'
  // Un lien vaut son extrémité la plus dégradée (même règle que /topology).
  const caps = ends.map(e => e.latest_total_capacity_mbps).filter((c): c is number => c !== null)
  const capacity = caps.length ? Math.min(...caps) : null
  const floor = ref.capacity_floor_mbps
  const degraded = capacity !== null && capacity < floor
  const distances = ends.map(e => e.distance_m).filter((d): d is number => d !== null)
  const distance = distances.length ? Math.max(...distances) : null
  const beam = degraded ? '#dc2626' : '#059669'

  return (
    <div className={`bg-white rounded-2xl border shadow-sm overflow-hidden flex flex-col md:flex-row md:items-start ${degraded ? 'border-red-200' : 'border-slate-200'}`}>
      {/* Mesure de CHAQUE extrémité, à l'extérieur de son pylône */}
      <EndInfo site={pair.siteA} end={pair.endA} onOpenDevice={onOpenDevice} align="left" />
      <svg viewBox="0 0 620 250" className="flex-1 min-w-0 w-full h-auto" role="img"
           aria-label={`Liaison ${radioLabel} entre ${pair.siteA} et ${pair.siteB}`}>
        <style>{`@keyframes slBeam { to { stroke-dashoffset: -28; } }`}</style>

        {/* Sol */}
        <line x1="20" y1="232" x2="600" y2="232" stroke="#e2e8f0" strokeWidth="2" />

        <LinkPylon x={70} down={false} dim={!pair.endA} />
        <LinkPylon x={550} down={false} dim={!pair.endB} />

        {/* Antennes radio montées en haut des pylônes, face à face */}
        <RadioDish x={92} y={70} facing="right" label={radioLabel} dim={!pair.endA} />
        <RadioDish x={528} y={70} facing="left" label={radioLabel} dim={!pair.endB} />

        {/* Faisceau radio : zone de Fresnel + ligne animée */}
        <ellipse cx="310" cy="70" rx="196" ry="22" fill={beam} opacity="0.06" />
        <line x1="118" y1="70" x2="502" y2="70" stroke={beam} strokeWidth="3" strokeLinecap="round"
              strokeDasharray="14 14" style={{ animation: 'slBeam 1.2s linear infinite' }} />

        {/* Étiquette de la liaison */}
        <g transform="translate(310 118)">
          <text textAnchor="middle" y="0" fontSize="20" fontWeight="700" fill={beam}>
            {capacity !== null ? capDisplay(capacity, linkType) : '—'}
          </text>
          <text textAnchor="middle" y="24" fontSize="12" fill="#64748b">
            {distance !== null ? `${(distance / 1000).toFixed(1).replace('.', ',')} km` : ''}
          </text>
        </g>
      </svg>
      <EndInfo site={pair.siteB} end={pair.endB} onOpenDevice={onOpenDevice} align="right" />
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

function EndInfo({ site, end, onOpenDevice, align }: {
  site: string
  end: SiteLinkRow | null
  onOpenDevice: (id: number) => void
  align: 'left' | 'right'
}) {
  const alignCls = align === 'left' ? 'items-start text-left' : 'items-end text-right'
  return (
    <div className={`md:w-52 shrink-0 flex flex-col gap-0.5 px-5 pt-5 pb-3 ${alignCls}`}>
      <p className="font-bold text-blue-900">{site}</p>
      {end ? (
        <>
          <p className="text-xs text-slate-600">{end.name}</p>
          <p className="text-[11px] font-mono text-blue-300">{end.ip ?? '—'}</p>
          <p className="text-xs text-slate-700">
            Signal <strong>{fmt(end.latest_signal_dbm, ' dBm')}</strong> · SNR <strong>{fmt(end.latest_snr_db, ' dB', 1)}</strong>
          </p>
          <p className="text-[11px] text-slate-500">
            Capacité mesurée ici : {capDisplay(end.latest_total_capacity_mbps, end.link_type)}
          </p>
          <button
            type="button"
            onClick={() => onOpenDevice(end.device_id)}
            className="mt-1 text-xs font-medium text-blue-600 hover:text-blue-800 hover:underline"
          >
            Voir l'équipement →
          </button>
        </>
      ) : (
        <p className="text-xs text-slate-400">Extrémité non listée — pas de relevé sous le plancher</p>
      )}
    </div>
  )
}

