'use client'

import { useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { CapacityBucket, NetworkCapacity } from '@/lib/types'
import CapacityDonut from '@/components/CapacityDonut'
import SiteOutageCharts from '@/components/SiteOutageCharts'

// Couleurs par famille — mêmes teintes que la page /capacity.
const FAMILY = {
  ltu:    { label: 'LTU',    used: '#22c55e', free: '#fcd34d' },
  airmax: { label: 'airMAX', used: '#3b82f6', free: '#fdba74' },
} as const

type Family = keyof typeof FAMILY

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

// Convertit une date "YYYY-MM-DD" en bornes ISO couvrant la journée entière.
function dayStartIso(d: string): string {
  return new Date(`${d}T00:00:00`).toISOString()
}
function dayEndIso(d: string): string {
  return new Date(`${d}T23:59:59.999`).toISOString()
}

export default function ReportsPage() {
  const today = new Date()
  const thirtyDaysAgo = new Date(today.getTime() - 30 * 86_400_000)

  const [dateFrom, setDateFrom] = useState<string>(isoDate(thirtyDaysAgo))
  const [dateTo, setDateTo] = useState<string>(isoDate(today))
  // Plage appliquée (figée au clic « Générer ») — sert d'en-tête de période
  // stable à l'impression et alimente la section downtime.
  const [applied, setApplied] = useState<{ from: string; to: string }>({
    from: isoDate(thirtyDaysAgo),
    to: isoDate(today),
  })

  const { data: capacity, isLoading: capacityLoading } = useSWR<NetworkCapacity>(
    endpoints.networkCapacity, fetcher, { refreshInterval: 30_000 },
  )

  const sites = capacity?.sites ?? []
  const globalMax = useMemo(
    () => sites.reduce((m, s) => Math.max(m, s.ltu.capacity, s.airmax.capacity), 0),
    [sites],
  )

  return (
    <div className="space-y-6">
      {/* Header — masqué à l'impression */}
      <div className="no-print flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-blue-900">Rapport de supervision</h1>
          <p className="text-sm text-blue-500 mt-1">
            Capacité actuelle du réseau et coupures par site sur la période choisie.
          </p>
        </div>
        <button
          onClick={() => window.print()}
          className="bg-blue-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-800 transition-colors flex items-center gap-2"
        >
          <PrintIcon />
          Imprimer / Exporter PDF
        </button>
      </div>

      {/* Contrôles de période — masqués à l'impression */}
      <div className="no-print bg-white border border-blue-100 rounded-xl p-4 shadow-sm">
        <div className="flex flex-wrap gap-4 items-end">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-blue-500 uppercase tracking-wider">Du</span>
            <input
              type="date"
              value={dateFrom}
              max={dateTo}
              onChange={(e) => setDateFrom(e.target.value)}
              className="border border-blue-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-blue-500 uppercase tracking-wider">Au</span>
            <input
              type="date"
              value={dateTo}
              min={dateFrom}
              max={isoDate(today)}
              onChange={(e) => setDateTo(e.target.value)}
              className="border border-blue-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </label>
          <button
            onClick={() => setApplied({ from: dateFrom, to: dateTo })}
            disabled={!dateFrom || !dateTo}
            className="bg-blue-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:bg-blue-300 disabled:cursor-not-allowed"
          >
            Générer le rapport
          </button>
        </div>
      </div>

      {/* Bandeau d'en-tête (visible à l'impression) */}
      <div className="print-card bg-blue-900 text-white rounded-xl p-6 shadow-sm">
        <h2 className="text-xl font-bold">Rapport de supervision réseau</h2>
        <p className="text-sm text-blue-200 mt-2">
          Coupures analysées : <strong>{applied.from}</strong> → <strong>{applied.to}</strong>
        </p>
        <p className="text-xs text-blue-300 mt-1">
          Capacité : instantané au {new Date().toLocaleString('fr-FR')}
        </p>
      </div>

      {/* ── Section 1 : Capacité du réseau ─────────────────────────────── */}
      <SectionHeader
        title="Capacité du réseau"
        subtitle="Clients connectés vs maximum avant saturation — par famille radio et par site."
      />

      {capacityLoading && <p className="text-slate-400 text-sm">Chargement de la capacité…</p>}

      {capacity != null && (
        <>
          <div className="print-card grid grid-cols-1 md:grid-cols-2 gap-5 max-w-3xl">
            <CapacityDonut
              title="LTU" used={FAMILY.ltu.used} free={FAMILY.ltu.free}
              consumed={capacity.families.ltu.consumed}
              available={capacity.families.ltu.available}
              rockets={capacity.families.ltu.rockets}
              unknown={capacity.families.ltu.unknown}
            />
            <CapacityDonut
              title="airMAX" used={FAMILY.airmax.used} free={FAMILY.airmax.free}
              consumed={capacity.families.airmax.consumed}
              available={capacity.families.airmax.available}
              rockets={capacity.families.airmax.rockets}
              unknown={capacity.families.airmax.unknown}
            />
          </div>

          <div className="print-card bg-white border border-blue-100 rounded-xl shadow-sm p-5">
            <div className="mb-4">
              <h3 className="font-semibold text-blue-900">Capacité par site</h3>
              <p className="text-xs text-blue-400 mt-0.5">
                Longueur = capacité totale du site ; partie pleine = clients connectés.
              </p>
            </div>
            {sites.length === 0 ? (
              <p className="py-8 text-center text-slate-400 text-sm">Aucun site.</p>
            ) : (
              <div className="space-y-3">
                {sites.map((s) => (
                  <div key={s.site} className="rounded-lg px-2 py-2">
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-sm font-semibold text-slate-800 truncate">{s.site}</span>
                      {s.unknown > 0 && (
                        <span className="shrink-0 text-[10px] font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                          {s.unknown} indéterminé{s.unknown > 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                    <div className="space-y-1">
                      <SiteFamilyBar family="ltu" bucket={s.ltu} globalMax={globalMax} />
                      <SiteFamilyBar family="airmax" bucket={s.airmax} globalMax={globalMax} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* ── Section 2 : Coupures par site ──────────────────────────────── */}
      <SectionHeader
        title="Temps de coupure des sites"
        subtitle="Pannes et downtime cumulé de l'infrastructure (Rockets, Switches, UISP Power) sur la période."
      />
      <div className="print-card">
        <SiteOutageCharts
          startIso={dayStartIso(applied.from)}
          endIso={dayEndIso(applied.to)}
          periodLabel={`${applied.from} → ${applied.to}`}
        />
      </div>
    </div>
  )
}

function SiteFamilyBar({
  family, bucket, globalMax,
}: { family: Family; bucket: CapacityBucket; globalMax: number }) {
  const { label, used, free } = FAMILY[family]

  if (bucket.capacity <= 0) {
    if (bucket.unknown <= 0) return null
    return (
      <div className="flex items-center gap-2">
        <span className="w-14 shrink-0 text-[11px] text-slate-500 text-right">{label}</span>
        <span className="text-[11px] text-amber-600">{bucket.unknown} Rocket(s) — capacité indéterminée</span>
      </div>
    )
  }

  const trackPct = globalMax > 0 ? (bucket.capacity / globalMax) * 100 : 0
  const usedPct = bucket.capacity > 0 ? (bucket.consumed / bucket.capacity) * 100 : 0

  return (
    <div className="flex items-center gap-2">
      <span className="w-14 shrink-0 text-[11px] text-slate-500 text-right">{label}</span>
      <div className="flex-1 bg-slate-50 rounded h-4 overflow-hidden relative">
        <div className="absolute inset-y-0 left-0 flex" style={{ width: `${Math.max(trackPct, 1)}%` }}>
          <div className="h-full" style={{ width: `${usedPct}%`, background: used }} />
          <div className="h-full flex-1" style={{ background: free }} />
        </div>
      </div>
      <span className="w-16 shrink-0 text-[11px] font-semibold text-slate-800 text-right tabular-nums">
        {bucket.consumed}/{bucket.capacity}
      </span>
      <span className="w-8 shrink-0 text-[10px] text-amber-600 text-right tabular-nums">
        {bucket.unknown > 0 ? `+${bucket.unknown}` : ''}
      </span>
    </div>
  )
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="print-card border-l-4 rounded-r-xl px-5 py-3 mt-4 bg-blue-50 border-blue-300 text-blue-900">
      <h2 className="text-base font-bold uppercase tracking-wider">{title}</h2>
      <p className="text-sm mt-1 opacity-80">{subtitle}</p>
    </div>
  )
}

function PrintIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"
      />
    </svg>
  )
}
