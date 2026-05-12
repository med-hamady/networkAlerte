import type { JSX } from 'react'

interface Props {
  type: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

const sizeClass = { sm: 'w-10 h-10', md: 'w-24 h-24', lg: 'w-36 h-36' }

export default function DeviceImage({ type, size = 'md', className = '' }: Props) {
  const SvgComp = svgMap[type] ?? DefaultSvg
  return (
    <div className={`${sizeClass[size]} mx-auto flex items-center justify-center ${className}`}>
      <SvgComp />
    </div>
  )
}

/* ─── LTU Rocket ──────────────────────────────────────────────────────────── */
function RocketSvg() {
  return (
    <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg" className="w-full h-full drop-shadow-sm">
      {/* Main body */}
      <rect x="30" y="18" width="40" height="58" rx="8" fill="#eff6ff" stroke="#2563eb" strokeWidth="2"/>
      {/* Directional horn / antenna cap */}
      <path d="M30 26 Q50 10 70 26" fill="#dbeafe" stroke="#2563eb" strokeWidth="1.8" strokeLinejoin="round"/>
      {/* Inner panel */}
      <rect x="36" y="30" width="28" height="36" rx="4" fill="white" stroke="#bfdbfe" strokeWidth="1"/>
      {/* Status LEDs */}
      <circle cx="44" cy="38" r="3.5" fill="#22c55e"/>
      <circle cx="50" cy="38" r="3.5" fill="#22c55e"/>
      <circle cx="56" cy="38" r="3.5" fill="#3b82f6"/>
      {/* Signal arcs */}
      <path d="M42 52 Q50 46 58 52" stroke="#93c5fd" strokeWidth="1.5" fill="none" strokeLinecap="round"/>
      <path d="M38 57 Q50 49 62 57" stroke="#bfdbfe" strokeWidth="1.2" fill="none" strokeLinecap="round"/>
      {/* Mounting bracket */}
      <rect x="25" y="74" width="50" height="9" rx="4" fill="#dbeafe" stroke="#93c5fd" strokeWidth="1.2"/>
      <rect x="37" y="83" width="26" height="7" rx="3" fill="#bfdbfe"/>
      {/* Ubiquiti-style side tabs */}
      <rect x="22" y="30" width="8" height="20" rx="3" fill="#dbeafe" stroke="#93c5fd" strokeWidth="1"/>
      <rect x="70" y="30" width="8" height="20" rx="3" fill="#dbeafe" stroke="#93c5fd" strokeWidth="1"/>
    </svg>
  )
}

/* ─── LTU LR ──────────────────────────────────────────────────────────────── */
function LRSvg() {
  return (
    <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg" className="w-full h-full drop-shadow-sm">
      {/* Body — slightly slimmer panel */}
      <rect x="28" y="16" width="44" height="62" rx="8" fill="#f0fdf4" stroke="#16a34a" strokeWidth="2"/>
      {/* Flat antenna face at top */}
      <rect x="32" y="16" width="36" height="14" rx="4" fill="#bbf7d0" stroke="#16a34a" strokeWidth="1.2"/>
      {/* Grid pattern on face (antenna element) */}
      <line x1="40" y1="17" x2="40" y2="29" stroke="#86efac" strokeWidth="0.8"/>
      <line x1="50" y1="17" x2="50" y2="29" stroke="#86efac" strokeWidth="0.8"/>
      <line x1="60" y1="17" x2="60" y2="29" stroke="#86efac" strokeWidth="0.8"/>
      <line x1="33" y1="22" x2="67" y2="22" stroke="#86efac" strokeWidth="0.8"/>
      {/* Inner panel */}
      <rect x="34" y="34" width="32" height="34" rx="4" fill="white" stroke="#bbf7d0" strokeWidth="1"/>
      {/* Status LEDs */}
      <circle cx="44" cy="42" r="3.5" fill="#22c55e"/>
      <circle cx="50" cy="42" r="3.5" fill="#22c55e"/>
      <circle cx="56" cy="42" r="3.5" fill="#94a3b8"/>
      {/* RJ45 port */}
      <rect x="41" y="54" width="18" height="10" rx="2" fill="#dbeafe" stroke="#93c5fd" strokeWidth="1"/>
      <rect x="43" y="56" width="14" height="2" rx="0.5" fill="#93c5fd"/>
      <rect x="43" y="59" width="14" height="2" rx="0.5" fill="#93c5fd"/>
      {/* Mount */}
      <rect x="24" y="74" width="52" height="9" rx="4" fill="#dcfce7" stroke="#86efac" strokeWidth="1.2"/>
      <rect x="38" y="83" width="24" height="7" rx="3" fill="#bbf7d0"/>
    </svg>
  )
}

/* ─── UISP Switch ─────────────────────────────────────────────────────────── */
function SwitchSvg() {
  return (
    <svg viewBox="0 0 100 70" fill="none" xmlns="http://www.w3.org/2000/svg" className="w-full h-full drop-shadow-sm">
      {/* Chassis */}
      <rect x="4" y="10" width="92" height="50" rx="7" fill="#eff6ff" stroke="#2563eb" strokeWidth="1.8"/>
      <rect x="8" y="14" width="84" height="42" rx="5" fill="white"/>
      {/* 8 RJ45 ports */}
      {Array.from({ length: 8 }, (_, i) => (
        <g key={i} transform={`translate(${14 + i * 10}, 22)`}>
          <rect x="-4" y="0" width="8" height="12" rx="2" fill="#dbeafe" stroke="#93c5fd" strokeWidth="0.8"/>
          <rect x="-2.5" y="2" width="5" height="1.2" rx="0.5" fill="#93c5fd"/>
          <rect x="-2.5" y="4.5" width="5" height="1.2" rx="0.5" fill="#93c5fd"/>
          <rect x="-2.5" y="7" width="5" height="1.2" rx="0.5" fill="#93c5fd"/>
        </g>
      ))}
      {/* Port LEDs */}
      {Array.from({ length: 8 }, (_, i) => (
        <circle key={i} cx={14 + i * 10} cy={42} r="2.5"
          fill={i < 5 ? '#22c55e' : i === 5 ? '#f97316' : '#94a3b8'}
        />
      ))}
      {/* SFP port (right side) */}
      <rect x="82" y="22" width="10" height="12" rx="2" fill="#e0e7ff" stroke="#818cf8" strokeWidth="0.8"/>
      <rect x="83" y="24" width="8" height="4" rx="1" fill="#a5b4fc"/>
      {/* Power LED */}
      <circle cx="88" cy="42" r="3" fill="#22c55e"/>
      {/* Label strip */}
      <rect x="10" y="48" width="40" height="4" rx="2" fill="#eff6ff"/>
    </svg>
  )
}

/* ─── UISP Power ──────────────────────────────────────────────────────────── */
function PowerSvg() {
  return (
    <svg viewBox="0 0 100 80" fill="none" xmlns="http://www.w3.org/2000/svg" className="w-full h-full drop-shadow-sm">
      {/* Chassis */}
      <rect x="5" y="8" width="90" height="64" rx="8" fill="#fefce8" stroke="#ca8a04" strokeWidth="1.8"/>
      <rect x="9" y="12" width="82" height="56" rx="6" fill="white"/>
      {/* 4 output ports */}
      {Array.from({ length: 4 }, (_, i) => (
        <g key={i} transform={`translate(${18 + i * 20}, 28)`}>
          <rect x="-9" y="-9" width="18" height="22" rx="4" fill="#fef9c3" stroke="#fde047" strokeWidth="0.9"/>
          <rect x="-5" y="-5" width="10" height="12" rx="1.5" fill="white"/>
          <circle cx="0" cy="10" r="3.5" fill="white" stroke="#fde047" strokeWidth="0.8"/>
        </g>
      ))}
      {/* Battery indicator bar */}
      <rect x="12" y="54" width="56" height="10" rx="4" fill="#fef9c3" stroke="#fde047" strokeWidth="0.8"/>
      <rect x="14" y="56" width="46" height="6" rx="2.5" fill="#22c55e" opacity="0.75"/>
      <rect x="68" y="57" width="5" height="4" rx="1.5" fill="#fde047"/>
      {/* Power ON LED */}
      <circle cx="82" cy="28" r="5" fill="#22c55e" opacity="0.9"/>
      {/* Voltage display */}
      <rect x="72" y="38" width="20" height="12" rx="3" fill="#fef9c3" stroke="#fde047" strokeWidth="0.8"/>
      <text x="82" y="47" textAnchor="middle" fontSize="6" fontFamily="monospace" fill="#92400e" fontWeight="700">24V</text>
    </svg>
  )
}

/* ─── Default fallback ────────────────────────────────────────────────────── */
function DefaultSvg() {
  return (
    <svg viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg" className="w-full h-full">
      <rect x="18" y="18" width="64" height="64" rx="10" fill="white" stroke="#3b82f6" strokeWidth="1.5"/>
      <circle cx="50" cy="50" r="18" stroke="#3b82f6" strokeWidth="1.5" fill="none"/>
      <circle cx="50" cy="50" r="10" stroke="#93c5fd" strokeWidth="1" fill="none"/>
      <circle cx="50" cy="50" r="3" fill="#2563eb"/>
      <line x1="50" y1="32" x2="50" y2="40" stroke="#3b82f6" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="50" y1="60" x2="50" y2="68" stroke="#3b82f6" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="32" y1="50" x2="40" y2="50" stroke="#3b82f6" strokeWidth="1.5" strokeLinecap="round"/>
      <line x1="60" y1="50" x2="68" y2="50" stroke="#3b82f6" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  )
}

const svgMap: Record<string, () => JSX.Element> = {
  rocket:      RocketSvg,
  lr:          LRSvg,
  uisp_switch: SwitchSvg,
  uisp_power:  PowerSvg,
}
