'use client'

import React, { useMemo, useState } from 'react'
import type { Device, DeviceMetrics, SystemInfo } from '@/lib/types'
import DeviceImage from './DeviceImage'
import DeviceDetailModal from './DeviceDetailModal'

// ── Layout constants (HORIZONTAL tree: root left → children right) ───────────
const NODE_W         = 120
const H_GAP          = 100   // horizontal gap between levels
const V_GAP          = 36    // vertical gap between sibling nodes
const PAD            = 52

const NODE_H_DEFAULT = 82
const NODE_H_LTU     = 94
const NODE_H_SWITCH  = 108

function getNodeH(device: Device): number {
  if (device.device_type === 'uisp_switch') return NODE_H_SWITCH
  if (device.device_type === 'ltu_rocket' || device.device_type === 'ltu_lr') return NODE_H_LTU
  return NODE_H_DEFAULT
}

// ── Types ────────────────────────────────────────────────────────────────────
interface NodeLayout {
  device: Device
  x: number     // left edge
  y: number     // top edge
  cx: number    // center x
  cy: number    // center y
  right: number // x + NODE_W
  height: number
}
interface EdgeLayout {
  id: string
  from: NodeLayout
  to:   NodeLayout
  isRadio: boolean
}
type LinkStatus = 'up' | 'down' | 'unknown'

// ── Port extraction ──────────────────────────────────────────────────────────
interface PortInfo { n: number; up: boolean | null; speed_mbps: number | null }

function extractPorts(metrics: DeviceMetrics): PortInfo[] {
  const nums = new Set<number>()
  for (const key of Object.keys(metrics)) {
    const m = key.match(/^port_(\d+)_up$/)
    if (m) nums.add(parseInt(m[1]))
  }
  return Array.from(nums).sort((a, b) => a - b).map(n => ({
    n,
    up: metrics[`port_${n}_up`] !== undefined
      ? metrics[`port_${n}_up`].value === 1 : null,
    speed_mbps: metrics[`port_${n}_speed_mbps`]?.value ?? null,
  }))
}

// ── Link status ──────────────────────────────────────────────────────────────
function getLinkStatus(edge: EdgeLayout, mm: Record<number, DeviceMetrics>): LinkStatus {
  // For radio links: use parent (Rocket) metrics; for cable links: use child metrics
  const metricsDeviceId = edge.isRadio ? edge.from.device.id : edge.to.device.id
  const m = mm[metricsDeviceId] ?? {}
  if (edge.isRadio) {
    const v = m['radio_if_up']
    if (v !== undefined) return v.value === 1 ? 'up' : 'down'
  } else {
    const v = m['eth_if_up']
    if (v !== undefined) return v.value === 1 ? 'up' : 'down'
    const s = edge.to.device.status
    if (s === 'up') return 'up'
    if (s === 'down') return 'down'
  }
  return 'unknown'
}

function sigColor(dbm: number) {
  if (dbm >= -65) return '#16a34a'
  if (dbm >= -75) return '#d97706'
  return '#dc2626'
}
function dotCls(status: string) {
  if (status === 'up')   return 'bg-green-500'
  if (status === 'down') return 'bg-red-500 animate-pulse'
  return 'bg-slate-300'
}

// ── Horizontal tree layout ───────────────────────────────────────────────────
function subtreeH(
  id: number,
  childrenOf: Map<number, Device[]>,
  dm: Map<number, Device>,
): number {
  const kids = childrenOf.get(id) ?? []
  const h = getNodeH(dm.get(id)!)
  if (!kids.length) return h
  const total = kids.reduce((s, k) => s + subtreeH(k.id, childrenOf, dm), 0)
    + V_GAP * (kids.length - 1)
  return Math.max(h, total)
}

function placeH(
  d: Device,
  x: number,
  cy: number,
  childrenOf: Map<number, Device[]>,
  dm: Map<number, Device>,
  out: { id: number; x: number; cy: number }[],
) {
  out.push({ id: d.id, x, cy })
  const kids = childrenOf.get(d.id) ?? []
  if (!kids.length) return
  const total = kids.reduce((s, k) => s + subtreeH(k.id, childrenOf, dm), 0)
    + V_GAP * (kids.length - 1)
  let ky = cy - total / 2
  for (const kid of kids) {
    const h = subtreeH(kid.id, childrenOf, dm)
    placeH(kid, x + NODE_W + H_GAP, ky + h / 2, childrenOf, dm, out)
    ky += h + V_GAP
  }
}

function computeLayout(devices: Device[]): {
  nodes: NodeLayout[]; edges: EdgeLayout[]; width: number; height: number
} {
  if (!devices.length) return { nodes: [], edges: [], width: 600, height: 300 }

  const dm         = new Map(devices.map(d => [d.id, d]))
  const childrenOf = new Map<number, Device[]>()
  devices.forEach(d => childrenOf.set(d.id, []))

  const roots: Device[] = []
  for (const d of devices) {
    if (d.parent_id != null && dm.has(d.parent_id)) {
      childrenOf.get(d.parent_id)!.push(d)
    } else {
      roots.push(d)
    }
  }

  const raw: { id: number; x: number; cy: number }[] = []
  let startY = 0
  for (const root of roots) {
    const h = subtreeH(root.id, childrenOf, dm)
    placeH(root, 0, startY + h / 2, childrenOf, dm, raw)
    startY += h + V_GAP
  }

  const minCY = Math.min(...raw.map(r => r.cy - getNodeH(dm.get(r.id)!) / 2))
  const maxCY = Math.max(...raw.map(r => r.cy + getNodeH(dm.get(r.id)!) / 2))
  const maxX  = Math.max(...raw.map(r => r.x + NODE_W))
  const dy = PAD - minCY

  const nodes: NodeLayout[] = raw.map(r => {
    const device = dm.get(r.id)!
    const h  = getNodeH(device)
    const cy = r.cy + dy
    return {
      device,
      x:      r.x + PAD,
      y:      cy - h / 2,
      cx:     r.x + PAD + NODE_W / 2,
      cy,
      right:  r.x + PAD + NODE_W,
      height: h,
    }
  })

  const nodeById = new Map(nodes.map(n => [n.device.id, n]))
  const edges: EdgeLayout[] = devices
    .filter(d => d.parent_id != null && dm.has(d.parent_id))
    .map(d => {
      const from = nodeById.get(d.parent_id!)
      const to   = nodeById.get(d.id)
      if (!from || !to) return null
      const isRadio = d.device_type === 'ltu_lr'
        && dm.get(d.parent_id!)?.device_type === 'ltu_rocket'
      return { id: `${d.parent_id}-${d.id}`, from, to, isRadio }
    })
    .filter(Boolean) as EdgeLayout[]

  return {
    nodes,
    edges,
    width:  maxX + PAD * 2,
    height: maxCY - minCY + PAD * 2,
  }
}

// ── Node card (no border box, just icon + labels) ────────────────────────────
function NodeCard({
  node, selected, onClick, metrics,
}: {
  node: NodeLayout
  selected: boolean
  onClick: (d: Device) => void
  metrics: DeviceMetrics
}) {
  const { device, x, y, height } = node
  const isSwitch = device.device_type === 'uisp_switch'
  const isLTU    = device.device_type === 'ltu_rocket' || device.device_type === 'ltu_lr'
  const ports     = isSwitch ? extractPorts(metrics) : []
  const signalDbm = isLTU ? (metrics['signal_dbm']?.value ?? null) : null
  const ccqPct    = isLTU ? (metrics['ccq_pct']?.value    ?? null) : null

  return (
    <div
      role="button"
      tabIndex={0}
      style={{ left: x, top: y, width: NODE_W, height, position: 'absolute' }}
      className={[
        'cursor-pointer select-none flex flex-col items-center pt-2',
        'rounded-xl transition-all duration-200',
        'hover:drop-shadow-lg',
        selected ? 'drop-shadow-xl' : '',
      ].join(' ')}
      onClick={() => onClick(device)}
      onKeyDown={e => e.key === 'Enter' && onClick(device)}
    >
      {/* Icon + status dot */}
      <div className="relative w-11 h-11">
        <DeviceImage type={device.device_type} size="sm" />
        <span className={`absolute -top-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-white shadow-sm ${dotCls(device.status)}`} />
      </div>

      {/* Name */}
      <p className="text-[10px] font-bold text-slate-700 text-center truncate w-full px-1.5 mt-1.5 leading-tight">
        {device.name}
      </p>

      {/* IP */}
      <p className="text-[9px] font-mono text-blue-500 text-center leading-none">
        {device.ip_address}
      </p>

      {/* Signal + CCQ (LTU only) */}
      {isLTU && signalDbm !== null && (
        <p className="text-[9px] font-mono text-center mt-1 leading-none"
           style={{ color: sigColor(signalDbm) }}>
          ▲ {signalDbm} dBm{ccqPct !== null ? ` · ${Math.round(ccqPct)}%` : ''}
        </p>
      )}

      {/* Port dots (switch only) — displayed at the bottom */}
      {isSwitch && ports.length > 0 && (
        <div className="absolute bottom-1 left-1/2 transform -translate-x-1/2 flex gap-0.5">
          {ports.map(p => (
            <div
              key={p.n}
              className="flex flex-col items-center"
              title={`Port ${p.n}${p.speed_mbps ? ` — ${p.speed_mbps} Mbps` : ''}: ${
                p.up === true ? 'UP' : p.up === false ? 'DOWN' : '?'}`}
            >
              <span className={`w-2 h-2 rounded-full transition-all ${
                p.up === true ? 'bg-green-500 shadow-lg shadow-green-400/50' : p.up === false ? 'bg-red-500 shadow-lg shadow-red-400/50 animate-pulse' : 'bg-slate-300'
              }`} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Edges SVG (horizontal bezier curves) ─────────────────────────────────────
function EdgesSvg({
  edges, metricsMap, width, height,
}: {
  edges: EdgeLayout[]
  metricsMap: Record<number, DeviceMetrics>
  width: number
  height: number
}) {
  return (
    <svg
      style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none', overflow: 'visible' }}
      width={width} height={height}
    >
      <defs>
        {/* Arrow markers */}
        {[
          ['arr-blue',  '#3b82f6'],
          ['arr-orange','#f97316'],
          ['arr-red',   '#ef4444'],
          ['arr-grey',  '#94a3b8'],
          ['arr-peach', '#fdba74'],
        ].map(([id, fill]) => (
          <marker key={id} id={id} markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto">
            <polygon points="0 0,8 3,0 6" fill={fill} />
          </marker>
        ))}

        {/* Traffic flow animation */}
        <style>{`
          @keyframes traffic-flow {
            0% { stroke-dashoffset: 0; }
            100% { stroke-dashoffset: -20px; }
          }
          @keyframes traffic-pulse {
            0%, 100% { opacity: 0.3; }
            50% { opacity: 0.7; }
          }
          @keyframes radio-wave {
            0% { 
              opacity: 1;
              r: 0px;
            }
            100% { 
              opacity: 0;
              r: 40px;
            }
          }
          .link-traffic {
            animation: traffic-flow 1.5s linear infinite;
            stroke-linecap: round;
          }
          .link-active {
            animation: traffic-pulse 2s ease-in-out infinite;
          }
          .radio-wave-1 {
            animation: radio-wave 2s ease-out infinite;
          }
          .radio-wave-2 {
            animation: radio-wave 2s ease-out 0.6s infinite;
          }
          .radio-wave-3 {
            animation: radio-wave 2s ease-out 1.2s infinite;
          }
        `}</style>
      </defs>

      {edges.map(e => {
        const status = getLinkStatus(e, metricsMap)

        // For cable links (RJ45): find which port on the switch is UP and connected
        // For radio links: start from center-right of parent
        let x1 = e.from.right
        let y1 = e.from.cy
        
        if (!e.isRadio && e.from.device.device_type === 'uisp_switch') {
          // Cable link from switch: find the UP port and start from there
          const switchMetrics = metricsMap[e.from.device.id] ?? {}
          const ports = extractPorts(switchMetrics)
          
          // Find the first UP port that could be this link
          const upPorts = ports.filter(p => p.up === true)
          
          if (upPorts.length > 0) {
            // Use the first UP port; if multiple, use the one most likely connected
            // For now, assume the UP ports are in order and match the edge position
            const portIndex = upPorts[0].n - 1  // port numbers are 1-indexed
            
            // Calculate X position: distribute ports evenly across bottom of switch
            const portCount = ports.length
            const portSpacing = (NODE_W - 8) / portCount
            const portOffset = 4 + portSpacing * portIndex + portSpacing / 2
            
            x1 = e.from.x + portOffset
            y1 = e.from.y + e.from.height - 2  // Bottom of switch
          } else {
            // Fallback: center of switch bottom
            x1 = e.from.cx
            y1 = e.from.y + e.from.height - 2
          }
        }
        
        const x2 = e.to.x
        const y2 = e.to.cy
        
        // Adjust bezier control based on link type
        const ctrlMultiplier = (!e.isRadio && e.from.device.device_type === 'uisp_switch') ? 0.25 : 0.4
        const ctrl = (x2 - x1) * ctrlMultiplier
        const path = `M ${x1} ${y1} C ${x1+ctrl} ${y1} ${x2-ctrl} ${y2} ${x2} ${y2}`
        const midX = (x1 + x2) / 2
        const midY = (y1 + y2) / 2

        // For radio links, use parent (Rocket) metrics; for cable links, use child metrics
        const metricsDeviceId = e.isRadio ? e.from.device.id : e.to.device.id

        // Colors (arrows only for radio links)
        let stroke: string, markerEnd: string | null = null, opacity = 1
        if (status === 'down') {
          stroke = '#ef4444'
          markerEnd = e.isRadio ? 'arr-red' : null
        } else if (status === 'unknown') {
          stroke = e.isRadio ? '#fdba74' : '#94a3b8'
          markerEnd = e.isRadio ? 'arr-peach' : null
          opacity = 0.65
        } else {
          stroke = e.isRadio ? '#f97316' : '#3b82f6'
          markerEnd = e.isRadio ? 'arr-orange' : null
        }

        const typeLabel = status === 'down' ? '⚠ DOWN' : (e.isRadio ? '⟿⟿⟿' : 'RJ45')
        const pillW = status === 'down' ? 52 : (e.isRadio ? 54 : 38)

        // Radio link info (above the edge)
        // For radio links: use parent device metrics (Rocket collects the radio stats)
        // For cable links: use child device metrics
        const cm = metricsMap[metricsDeviceId] ?? {}
        const sigDbm  = e.isRadio ? (cm['signal_dbm']?.value   ?? null) : null
        const ccq     = e.isRadio ? (cm['ccq_pct']?.value      ?? null) : null
        const txMbps  = e.isRadio ? (cm['tx_rate_mbps']?.value ?? null) : null
        const rxMbps  = e.isRadio ? (cm['rx_rate_mbps']?.value ?? null) : null

        const radioLines: string[] = []
        // Toujours afficher signal + CCQ en priorité
        if (sigDbm !== null) {
          radioLines.push(`▲ ${sigDbm} dBm${ccq !== null ? ` · CCQ ${Math.round(ccq)}%` : ''}`)
        }
        // Afficher le débit
        if (txMbps !== null && rxMbps !== null) {
          radioLines.push(`↑${Math.round(txMbps)} ↓${Math.round(rxMbps)} Mbps`)
        } else if (txMbps !== null) {
          radioLines.push(`↑ ${Math.round(txMbps)} Mbps`)
        } else if (rxMbps !== null) {
          radioLines.push(`↓ ${Math.round(rxMbps)} Mbps`)
        }

        // Pour les liens radio, afficher toujours la boîte (même avec fallback)
        const shouldShowRadioBox = e.isRadio
        // Fallback message si aucune métrique
        if (shouldShowRadioBox && radioLines.length === 0) {
          radioLines.push('Radio Link')
        }
        
        const radioBoxH  = Math.max(24, radioLines.length * 13 + 10)
        const radioBoxW  = Math.max(100, radioLines.reduce((max, line) => Math.max(max, line.length * 6), 0) + 20)

        return (
          <g key={e.id} opacity={opacity}>
            {/* RADIO LINK: animated waves instead of lines */}
            {e.isRadio ? (
              <>
                {/* Radio wave ripples from parent to child */}
                {[0, 1, 2].map(i => (
                  <circle
                    key={`wave-${i}`}
                    cx={x1}
                    cy={y1}
                    r={5}
                    fill="none"
                    stroke={status === 'down' ? '#ef4444' : '#f97316'}
                    strokeWidth={1.5}
                    opacity={0.6}
                    className={`radio-wave-${i + 1}`}
                  />
                ))}
                
                {/* Wavy cable/link representation */}
                <path
                  d={path}
                  fill="none"
                  stroke={status === 'down' ? '#ef4444' : '#f97316'}
                  strokeWidth={2.5}
                  strokeDasharray="3 5"
                  opacity={0.4}
                />
                
                {/* Animated wave flow on the line */}
                {status === 'up' && (
                  <path
                    d={path}
                    fill="none"
                    stroke="#f97316"
                    strokeWidth={2}
                    strokeDasharray="8 4"
                    className="link-traffic"
                    opacity={0.8}
                  />
                )}
              </>
            ) : (
              <>
                {/* CABLE LINK: standard line representation */}
                {/* RJ45 connector at port (switch end) */}
                {e.from.device.device_type === 'uisp_switch' && (
                  <>
                    {/* Port connector visual indicator */}
                    <circle
                      cx={x1}
                      cy={y1}
                      r={3}
                      fill={status === 'up' ? '#22c55e' : status === 'down' ? '#ef4444' : '#94a3b8'}
                      stroke="white" strokeWidth={1.5}
                    />
                  </>
                )}
                
                {/* Base link line (static background) */}
                <path
                  d={path}
                  fill="none"
                  stroke="#e2e8f0"
                  strokeWidth={status === 'down' ? 2.5 : 3}
                  opacity={0.4}
                />

                {/* Animated traffic flow line */}
                {status === 'up' && (
                  <path
                    d={path}
                    fill="none"
                    stroke="#3b82f6"
                    strokeWidth={2.5}
                    strokeDasharray="8 4"
                    className="link-traffic"
                    opacity={0.8}
                  />
                )}

                {/* Main link line with status color */}
                <path
                  d={path}
                  fill="none"
                  stroke={stroke}
                  strokeWidth={2.5}
                  {...(markerEnd ? { markerEnd: `url(#${markerEnd})` } : {})}
                  opacity={0.6}
                />
              </>
            )}

            {/* Radio link info box — above the edge midpoint */}
            {shouldShowRadioBox && (
              <g>
                {/* Box shadow effect */}
                <rect
                  x={midX - radioBoxW / 2 + 1} y={midY - radioBoxH - 18 + 2}
                  width={radioBoxW} height={radioBoxH}
                  rx={6} fill="black" opacity={0.15}
                />
                
                {/* Main box */}
                <rect
                  x={midX - radioBoxW / 2} y={midY - radioBoxH - 18}
                  width={radioBoxW} height={radioBoxH}
                  rx={6} fill="white" stroke={stroke}
                  strokeWidth={1.5} opacity={0.99}
                  filter="drop-shadow(0 2px 4px rgba(0,0,0,0.1))"
                />
                
                {/* Info lines */}
                {radioLines.map((line, i) => (
                  <text
                    key={i}
                    x={midX}
                    y={midY - radioBoxH - 18 + 13 + i * 13}
                    textAnchor="middle"
                    fontSize="10"
                    fontFamily="ui-monospace, monospace"
                    fill={i === 0 && sigDbm !== null ? sigColor(sigDbm) : '#ea580c'}
                    fontWeight={i === 0 ? '700' : '600'}
                  >
                    {line}
                  </text>
                ))}
              </g>
            )}

            {/* Status dot at midpoint with pulse effect */}
            {status === 'up' && (
              <circle cx={midX} cy={midY} r={5}
                fill={e.isRadio ? '#f97316' : '#3b82f6'}
                opacity={0.3}
                className="link-active"
              />
            )}
            
            <circle cx={midX} cy={midY} r={5}
              fill={status === 'up' ? (e.isRadio ? '#f97316' : '#3b82f6') : status === 'down' ? '#ef4444' : '#94a3b8'}
              stroke="white" strokeWidth={2}
              filter="drop-shadow(0 0 3px rgba(0,0,0,0.3))"
            />

            {/* Link type / status pill — below the dot */}
            <rect
              x={midX - pillW / 2} y={midY + 9}
              width={pillW} height={18}
              rx={5} fill="white" stroke={stroke}
              strokeWidth={1.2} opacity={0.99}
              filter="drop-shadow(0 1px 2px rgba(0,0,0,0.1))"
            />
            <text
              x={midX} y={midY + 22}
              textAnchor="middle"
              fontSize="9"
              fontFamily="ui-monospace, monospace"
              fill={stroke}
              fontWeight="700"
            >
              {typeLabel}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ── Background grid ──────────────────────────────────────────────────────────
function GridSvg({ width, height }: { width: number; height: number }) {
  return (
    <svg style={{ position: 'absolute', top: 0, left: 0, pointerEvents: 'none' }} width={width} height={height}>
      <defs>
        <pattern id="topo-grid" width="28" height="28" patternUnits="userSpaceOnUse">
          <path d="M 28 0 L 0 0 0 28" fill="none" stroke="#e2e8f0" strokeWidth="0.6" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#topo-grid)" />
    </svg>
  )
}

// ── System info overlay ──────────────────────────────────────────────────────
function SystemInfoOverlay({ info }: { info: SystemInfo }) {
  function barColor(pct: number) {
    if (pct > 85) return 'bg-red-400'
    if (pct > 60) return 'bg-amber-400'
    return 'bg-green-400'
  }
  return (
    <div
      className="absolute top-2 right-2 z-10 bg-white/95 backdrop-blur border border-slate-200 rounded-xl shadow-md px-3 py-2.5 text-[11px]"
      style={{ minWidth: 196 }}
    >
      <div className="flex items-center gap-1.5 mb-2 pb-1.5 border-b border-slate-100">
        <svg className="w-3.5 h-3.5 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <rect x="2" y="3" width="20" height="14" rx="2" /><path d="M8 21h8M12 17v4" />
        </svg>
        <span className="font-semibold text-slate-700 truncate">{info.hostname}</span>
        <span className="ml-auto text-[9px] text-slate-400">{info.os_name}</span>
      </div>
      {([
        ['CPU',    info.cpu_percent,  `${info.cpu_percent.toFixed(0)}%`],
        ['RAM',    info.ram_percent,  `${info.ram_used_gb}/${info.ram_total_gb}G`],
        ['Disque', info.disk_percent, `${info.disk_percent.toFixed(0)}%`],
      ] as [string, number, string][]).map(([label, pct, val]) => (
        <div key={label} className="flex items-center gap-2 mb-1">
          <span className="w-12 text-slate-400 shrink-0">{label}</span>
          <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div className={`h-full rounded-full ${barColor(pct)}`} style={{ width: `${pct}%` }} />
          </div>
          <span className="w-10 text-right font-mono text-slate-600">{val}</span>
        </div>
      ))}
      {info.gpus.length > 0 ? (
        <div className="border-t border-slate-100 pt-1.5 space-y-1">
          {info.gpus.map((gpu, i) => (
            <div key={i}>
              <div className="flex items-center gap-1 mb-0.5">
                <svg className="w-3 h-3 text-purple-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <rect x="2" y="7" width="20" height="12" rx="2" /><path d="M6 7V5M10 7V5M14 7V5M18 7V5" />
                </svg>
                <span className="text-slate-500 truncate text-[10px]" title={gpu.name}>
                  {gpu.name.length > 22 ? gpu.name.slice(0, 22) + '…' : gpu.name}
                </span>
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[9px] font-mono text-slate-500 pl-4">
                {gpu.utilization_pct !== null && <span>GPU: <span className={gpu.utilization_pct > 80 ? 'text-red-500' : 'text-slate-600'}>{gpu.utilization_pct}%</span></span>}
                {gpu.temperature_c   !== null && <span>T°: <span className={gpu.temperature_c > 80 ? 'text-red-500' : 'text-slate-600'}>{gpu.temperature_c}°C</span></span>}
                {gpu.memory_used_mb  !== null && gpu.memory_total_mb !== null && (
                  <span>VRAM: {Math.round(gpu.memory_used_mb / 1024 * 10) / 10}/{Math.round(gpu.memory_total_mb / 1024 * 10) / 10}G</span>
                )}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="border-t border-slate-100 pt-1.5 flex items-center gap-1.5 text-slate-300">
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <rect x="2" y="7" width="20" height="12" rx="2" /><path d="M6 7V5M10 7V5M14 7V5M18 7V5" />
          </svg>
          <span className="text-[9px]">GPU non détecté</span>
        </div>
      )}
    </div>
  )
}

// ── Legend ───────────────────────────────────────────────────────────────────
function Legend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-slate-500 mt-3 px-1">
      <div className="flex items-center gap-1.5">
        <svg width="24" height="10"><line x1="0" y1="5" x2="24" y2="5" stroke="#3b82f6" strokeWidth="2.5"/></svg>
        <span>RJ45 UP</span>
      </div>
      <div className="flex items-center gap-1.5">
        <svg width="24" height="10"><line x1="0" y1="5" x2="24" y2="5" stroke="#f97316" strokeWidth="2" strokeDasharray="6 3"/></svg>
        <span>Radio UP</span>
      </div>
      <div className="flex items-center gap-1.5">
        <svg width="24" height="10"><line x1="0" y1="5" x2="24" y2="5" stroke="#ef4444" strokeWidth="2" strokeDasharray="5 3"/></svg>
        <span>Lien DOWN</span>
      </div>
      <div className="w-px h-4 bg-slate-200 mx-1" />
      <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-green-500 inline-block" /><span>UP</span></div>
      <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-red-500 inline-block" /><span>DOWN</span></div>
      <div className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-full bg-slate-300 inline-block" /><span>Inconnu</span></div>
      <div className="w-px h-4 bg-slate-200 mx-1" />
      <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-green-500 inline-block" /><span>Port UP</span></div>
      <div className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-red-500 inline-block" /><span>Port DOWN</span></div>
      <span className="text-slate-400 ml-auto italic">Cliquer sur un équipement pour le détail</span>
    </div>
  )
}

// ── Main export ──────────────────────────────────────────────────────────────
interface TopologyMapProps {
  devices: Device[]
  metricsMap?: Record<number, DeviceMetrics>
  systemInfo?: SystemInfo | null
}

export default function TopologyMap({ devices, metricsMap = {}, systemInfo }: TopologyMapProps) {
  const [selected, setSelected] = useState<Device | null>(null)

  const { nodes, edges, width, height } = useMemo(
    () => computeLayout(devices),
    [devices],
  )

  const canvasW = Math.max(width, 600)
  const canvasH = Math.max(height, 280)

  if (!devices.length) {
    return (
      <div className="flex flex-col items-center justify-center h-48 gap-2 text-blue-200">
        <svg className="w-10 h-10" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.2}>
          <path strokeLinecap="round" strokeLinejoin="round"
            d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18" />
        </svg>
        <p className="text-sm">Aucun équipement à afficher</p>
      </div>
    )
  }

  return (
    <>
      <div className="overflow-auto rounded-xl">
        <div style={{ position: 'relative', width: canvasW, height: canvasH, minWidth: '100%' }}>
          <GridSvg  width={canvasW} height={canvasH} />
          <EdgesSvg edges={edges} metricsMap={metricsMap} width={canvasW} height={canvasH} />
          {nodes.map(node => (
            <NodeCard
              key={node.device.id}
              node={node}
              selected={selected?.id === node.device.id}
              onClick={setSelected}
              metrics={metricsMap[node.device.id] ?? {}}
            />
          ))}
          {systemInfo && <SystemInfoOverlay info={systemInfo} />}
        </div>
      </div>

      <Legend />

      <DeviceDetailModal
        device={selected}
        devices={devices}
        onClose={() => setSelected(null)}
        onNavigate={setSelected}
      />
    </>
  )
}
