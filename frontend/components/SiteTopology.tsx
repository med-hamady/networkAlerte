'use client'

import { useEffect, useState } from 'react'
import type { Device } from '@/lib/types'
import { deviceLabel, timeAgo } from '@/lib/types'
import DeviceImage, { devicePhotoVariant } from './DeviceImage'

interface Props {
  devices: Device[]
  onSelect: (device: Device) => void
}

const ZOOM_MIN  = 0.35
const ZOOM_MAX  = 1.2
const ZOOM_STEP = 0.1
const ZOOM_DEF  = 0.75  // un peu réduit par défaut pour voir plus d'un coup

// Géométrie de la disposition (px) — sert à tracer les câbles en SVG.
const SWITCH_W = 288  // largeur du nœud switch (w-72)
const CHILD_W  = 256  // largeur d'un nœud enfant (w-64)
const ROW_H    = 144  // hauteur d'une ligne d'équipement (photo 128 + py-2)
const GAP_V    = 20   // espace vertical entre équipements
const CONN_W   = 150  // largeur de la zone de câbles (entre switch et colonne)
const OVERLAP  = 16   // prolongement des câbles pour « plonger » dans les photos

// Vue topologie d'un site, façon UISP : le switch (hub) à gauche, les autres
// équipements d'infra (Rockets, UISP Power, AF60, PTP) empilés en colonne à
// droite, chacun relié au switch par un « câble » courbe ambré (rouge si down).
//
// Le modèle de données ne lie pas un Rocket à un switch précis (seul l'LR porte
// rocket_id) : dans un site il n'y a qu'un hub, donc tout l'infra non-switch se
// rattache au switch. Sans switch → repli sur une grille simple.
export default function SiteTopology({ devices, onSelect }: Props) {
  // Zoom (hooks avant tout return conditionnel — règle des hooks). Le niveau est
  // mémorisé d'un site à l'autre pour retrouver l'ajustement voulu.
  const [zoom, setZoom] = useState(ZOOM_DEF)
  useEffect(() => {
    const saved = parseFloat(localStorage.getItem('topoZoom') ?? '')
    if (!Number.isNaN(saved)) setZoom(saved)
  }, [])
  const applyZoom = (z: number) => {
    const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 100) / 100))
    setZoom(clamped)
    localStorage.setItem('topoZoom', String(clamped))
  }

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

  const n = children.length

  // Pas d'enfant → on affiche juste le(s) switch(es).
  if (n === 0) {
    return (
      <div className="flex flex-col gap-4" style={{ width: SWITCH_W }}>
        {switches.map(sw => <TopoNode key={sw.id} device={sw} onSelect={onSelect} />)}
      </div>
    )
  }

  const colH    = n * ROW_H + (n - 1) * GAP_V   // hauteur de la colonne d'équipements
  const switchY = colH / 2                      // sortie des câbles (centre vertical du switch)
  const cy      = (i: number) => i * (ROW_H + GAP_V) + ROW_H / 2

  return (
    <div>
      {/* Contrôle de zoom — pour tout voir sans scroller */}
      <div className="flex items-center justify-end gap-1 mb-2">
        <ZoomButton label="Réduire" disabled={zoom <= ZOOM_MIN} onClick={() => applyZoom(zoom - ZOOM_STEP)}>−</ZoomButton>
        <button
          onClick={() => applyZoom(ZOOM_DEF)}
          title="Réinitialiser le zoom"
          className="min-w-[3rem] text-xs font-medium text-blue-600 hover:text-blue-800 tabular-nums px-1 py-1"
        >
          {Math.round(zoom * 100)}%
        </button>
        <ZoomButton label="Agrandir" disabled={zoom >= ZOOM_MAX} onClick={() => applyZoom(zoom + ZOOM_STEP)}>+</ZoomButton>
      </div>

      <div className="overflow-x-auto pb-2" style={{ zoom } as React.CSSProperties}>
        <div className="inline-flex items-stretch">

          {/* Hub : le(s) switch(es) à gauche, centré(s) verticalement */}
          <div className="flex flex-col justify-center gap-4 shrink-0" style={{ width: SWITCH_W }}>
            {switches.map(sw => <TopoNode key={sw.id} device={sw} onSelect={onSelect} />)}
          </div>

        {/* Câbles UISP : courbes bézier rayonnant du switch vers chaque équipement.
            overflow-visible → les extrémités plongent dans les photos. */}
        <div className="relative shrink-0" style={{ width: CONN_W, height: colH }}>
          <svg width={CONN_W} height={colH} className="absolute inset-0 overflow-visible" aria-hidden="true">
            {children.map((d, i) => {
              const y    = cy(i)
              const down = d.status === 'down'
              const col  = down ? '#ef4444' : '#f59e0b'
              return (
                <g key={d.id}>
                  <path
                    d={`M ${-OVERLAP} ${switchY} C ${CONN_W * 0.5} ${switchY}, ${CONN_W * 0.5} ${y}, ${CONN_W + OVERLAP} ${y}`}
                    fill="none"
                    stroke={col}
                    strokeWidth={3}
                    strokeLinecap="round"
                    opacity={0.9}
                  />
                  {/* point de raccordement dans la photo de l'équipement */}
                  <circle cx={CONN_W + OVERLAP} cy={y} r={3} fill={col} />
                </g>
              )
            })}
            {/* point de sortie côté switch */}
            <circle cx={-OVERLAP} cy={switchY} r={3.5} fill="#f59e0b" />
          </svg>
        </div>

        {/* Équipements empilés en colonne à droite */}
        <div className="flex flex-col shrink-0" style={{ gap: GAP_V }}>
          {children.map(d => (
            <div key={d.id} className="flex items-center" style={{ width: CHILD_W, height: ROW_H }}>
              <TopoNode device={d} onSelect={onSelect} />
            </div>
          ))}
        </div>
        </div>
      </div>
    </div>
  )
}

// Petit bouton carré du contrôle de zoom.
function ZoomButton({ children, label, onClick, disabled }: {
  children: React.ReactNode
  label: string
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="h-7 w-7 flex items-center justify-center rounded-lg border border-blue-200 bg-white text-blue-600 text-lg leading-none hover:bg-blue-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
    >
      {children}
    </button>
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
      <DeviceImage type={device.device_type} variant={devicePhotoVariant(device)} size="xl" className="shrink-0" />

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
