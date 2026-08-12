'use client'

import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { SystemInfo } from '@/lib/types'

const REFRESH = 15_000

// Paliers de couleur. Au-delà de 85 % la machine est saturée et les pages
// mettent des secondes à s'ouvrir — c'est précisément ce que cet indicateur
// doit rendre visible sans avoir à ouvrir un terminal.
function levelClasses(pct: number): { text: string; bar: string } {
  if (pct >= 85) return { text: 'text-red-600', bar: 'bg-red-500' }
  if (pct >= 60) return { text: 'text-orange-500', bar: 'bg-orange-400' }
  return { text: 'text-green-600', bar: 'bg-green-500' }
}

export default function CpuBadge() {
  const { data, error } = useSWR<SystemInfo>(
    endpoints.systemInfo, fetcher, { refreshInterval: REFRESH },
  )

  // Rien plutôt qu'un « 0 % », qui se lirait comme un serveur au repos alors
  // qu'on n'a simplement pas la mesure.
  if (error || !data) return null

  const pct = Math.round(data.cpu_percent)
  const { text, bar } = levelClasses(pct)

  return (
    <div
      className="bg-white border border-blue-100 px-3 py-1.5 rounded-lg shadow-sm flex items-center gap-2.5"
      title={
        `CPU serveur : ${pct} % de ${data.cpu_count} cœurs\n` +
        `RAM : ${data.ram_percent} % (${data.ram_used_gb} / ${data.ram_total_gb} Go)\n` +
        `Disque : ${data.disk_percent} % (${data.disk_used_gb} / ${data.disk_total_gb} Go)`
      }
    >
      <CpuIcon />
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline gap-1.5 leading-none">
          <span className="text-[11px] font-medium text-blue-400 uppercase tracking-wider">CPU</span>
          <span className={`text-xs font-bold tabular-nums ${text}`}>{pct} %</span>
        </div>
        <div className="w-20 h-1 rounded-full bg-blue-50 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${bar}`}
            style={{ width: `${Math.min(Math.max(pct, 0), 100)}%` }}
          />
        </div>
      </div>
    </div>
  )
}

function CpuIcon() {
  return (
    <svg className="w-4 h-4 text-blue-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
      <rect x="7" y="7" width="10" height="10" rx="1.5" strokeLinejoin="round" />
      <path strokeLinecap="round" d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4" />
    </svg>
  )
}
