'use client'

import type { Device } from '@/lib/types'
import { deviceLabel, timeAgo } from '@/lib/types'
import DeviceImage from './DeviceImage'

interface Props {
  devices: Device[]
  onSelect: (device: Device) => void
}

// Géométrie de la disposition (px) — sert à tracer les câbles en SVG.
const CHILD_W  = 256  // largeur d'un nœud enfant (w-64)
const SWITCH_W = 288  // largeur du nœud switch (w-72)
const GAP      = 28   // espace horizontal entre nœuds enfants
const SW_GAP   = 24   // espace entre switches (gap-6)
const DROP     = 64   // hauteur de la zone de câbles entre le switch et les enfants
const PHOTO_CX = 72   // centre horizontal de la photo dans un nœud (px-2 = 8 + 128/2)
const OVERLAP  = 16   // prolongement des câbles pour « plonger » dans la photo enfant
// L'image du switch est large et courte : object-contain la centre dans le carré
// de 128 px et laisse du vide dessous. On remonte la sortie des câbles jusqu'au
// bas du switch *visible* (≈ bas du nœud − 52 px) au lieu du bord du carré.
const SWITCH_OUT_Y = -52

// Vue topologie d'un site : le(s) switch(es) forment le hub en haut, tous les
// autres équipements d'infra (Rockets, UISP Power, AF60, PTP) pendent dessous,
// chacun relié au switch par un « câble » courbe façon UISP.
//
// Le modèle de données ne lie pas un Rocket à un switch précis (seul l'LR porte
// rocket_id) : dans un site il n'y a qu'un hub, donc tout l'infra non-switch se
// rattache au(x) switch(es). Sans switch → repli sur une grille simple.
export default function SiteTopology({ devices, onSelect }: Props) {
  const switches = devices.filter(d => d.device_type === 'uisp_switch')
  const children = devices.filter(d => d.device_type !== 'uisp_switch')

  // Pas de hub identifiable → on retombe sur une grille classique.
  if (switches.length === 0) {
    return (
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
        {children.map(d => <TopoNode key={d.id} device={d} onSelect={onSelect} />)}
      </div>
    )
  }

  const n          = children.length
  const rowWidth   = n > 0 ? n * CHILD_W + (n - 1) * GAP : CHILD_W
  const switchesW  = switches.length * SWITCH_W + (switches.length - 1) * SW_GAP
  // Le contenu (switch + enfants) est centré : on travaille dans un repère de
  // largeur = la plus large des deux rangées, et on décale chacune de moitié.
  const containerW = Math.max(rowWidth, switchesW)
  const childOff   = (containerW - rowWidth) / 2
  const switchOff  = (containerW - switchesW) / 2
  // Sortie des câbles = centre de la photo du switch (un seul hub en pratique) ;
  // sinon centre du groupe de switches.
  const originX = switches.length === 1 ? switchOff + PHOTO_CX : containerW / 2
  const childX  = (i: number) => childOff + i * (CHILD_W + GAP) + PHOTO_CX

  return (
    <div className="overflow-x-auto pb-2">
      <div className="inline-flex min-w-full flex-col items-center px-4">

        {/* Hub : le(s) switch(es) en haut */}
        <div className="flex justify-center gap-6">
          {switches.map(sw => (
            <div key={sw.id} className="w-72">
              <TopoNode device={sw} onSelect={onSelect} />
            </div>
          ))}
        </div>

        {n > 0 && (
          <>
            {/* Câbles UISP : courbes bézier rayonnant du switch vers chaque enfant.
                Les extrémités sont prolongées de OVERLAP px (overflow-visible) pour
                plonger dans la photo du switch (haut) et de chaque équipement (bas). */}
            <svg
              width={containerW}
              height={DROP}
              className="block overflow-visible"
              aria-hidden="true"
            >
              {children.map((d, i) => {
                const cx   = childX(i)
                const down = d.status === 'down'
                const col  = down ? '#ef4444' : '#f59e0b'
                return (
                  <g key={d.id}>
                    <path
                      d={`M ${originX} ${SWITCH_OUT_Y} C ${originX} ${DROP * 0.5}, ${cx} ${DROP * 0.5}, ${cx} ${DROP + OVERLAP}`}
                      fill="none"
                      stroke={col}
                      strokeWidth={3}
                      strokeLinecap="round"
                      opacity={0.9}
                    />
                    {/* point de raccordement dans la photo de l'équipement */}
                    <circle cx={cx} cy={DROP + OVERLAP} r={3} fill={col} />
                  </g>
                )
              })}
              {/* point de sortie dans la photo du switch */}
              <circle cx={originX} cy={SWITCH_OUT_Y} r={3.5} fill="#f59e0b" />
            </svg>

            {/* Équipements enfants */}
            <div className="flex" style={{ gap: GAP }}>
              {children.map(d => (
                <div key={d.id} style={{ width: CHILD_W }}>
                  <TopoNode device={d} onSelect={onSelect} />
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// Nœud épuré : la photo de l'équipement (sans carré ni fond) et, à côté, son
// nom + modèle + dernière vue. Cliquable → ouvre la fiche détail.
function TopoNode({ device, onSelect }: { device: Device; onSelect: (d: Device) => void }) {
  const isDown = device.status === 'down'
  const isUp   = device.status === 'up'

  return (
    <button
      onClick={() => onSelect(device)}
      className="group w-full flex items-center gap-3 rounded-xl px-2 py-2 text-left transition-colors hover:bg-blue-50/70"
    >
      {/* Photo seule — pas de cadre, pas de fond */}
      <DeviceImage type={device.device_type} size="xl" className="shrink-0" />

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span
            className={`h-2 w-2 rounded-full shrink-0 ${
              isDown ? 'bg-red-500' : isUp ? 'bg-green-500' : 'bg-blue-200'
            }`}
          />
          <p className={`font-semibold text-sm leading-tight truncate ${isDown ? 'text-red-600' : 'text-slate-800'}`}>
            {device.name}
          </p>
        </div>
        <p className="text-xs text-blue-400 mt-0.5 truncate">{deviceLabel(device)}</p>
        <p className={`text-xs mt-0.5 truncate ${isDown ? 'text-red-500' : 'text-slate-500'}`}>
          Vu {timeAgo(device.last_seen)}
        </p>
      </div>
    </button>
  )
}
