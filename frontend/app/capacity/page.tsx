'use client'

import { useMemo, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { CapacityBucket, NetworkCapacity, RocketCapacity, SiteCapacity } from '@/lib/types'
import CapacityDonut from '@/components/CapacityDonut'

// Un Rocket saturé = la même ligne que dans le drill-down, + le site auquel il
// appartient (perdu dans le nesting par site, ré-attaché ici pour la liste plate).
type SaturatedRocket = RocketCapacity & { site: string }

// Couleurs par famille (utilisé / disponible) — mêmes teintes que les donuts.
const FAMILY = {
  ltu:    { label: 'LTU',    used: '#22c55e', free: '#fcd34d' },
  airmax: { label: 'airMAX', used: '#3b82f6', free: '#fdba74' },
} as const

type Family = keyof typeof FAMILY

export default function CapacityPage() {
  const { data, error, isLoading } = useSWR<NetworkCapacity>(
    endpoints.networkCapacity, fetcher, { refreshInterval: 30_000 },
  )
  const [selectedSite, setSelectedSite] = useState<string | null>(null)

  const sites = data?.sites ?? []
  const siteObj = selectedSite != null ? sites.find(s => s.site === selectedSite) ?? null : null

  // Échelle commune des barres par site = plus grande capacité (famille × site).
  const globalMax = useMemo(
    () => sites.reduce((m, s) => Math.max(m, s.ltu.capacity, s.airmax.capacity), 0),
    [sites],
  )

  // Rockets saturés : connectés ≥ max (le critère exact de rocket_client_overload).
  // Capacité indéterminée (max_clients null) = jamais saturé. Trié du plus
  // surchargé au moins (ratio connectés/max décroissant).
  const saturatedRockets = useMemo<SaturatedRocket[]>(() => {
    const out: SaturatedRocket[] = []
    for (const s of sites) {
      for (const r of s.rockets) {
        if (r.max_clients != null && r.max_clients > 0 && r.current_clients >= r.max_clients) {
          out.push({ ...r, site: s.site })
        }
      }
    }
    return out.sort(
      (a, b) => b.current_clients / b.max_clients! - a.current_clients / a.max_clients!,
    )
  }, [sites])

  if (error) {
    return <p className="text-red-600 text-sm">Erreur de chargement de la capacité réseau.</p>
  }

  return (
    <div className="space-y-6">
      {/* Breadcrumb / header */}
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-sm mb-1">
          <button
            onClick={() => setSelectedSite(null)}
            className={selectedSite == null
              ? 'font-bold text-blue-900 text-2xl tracking-tight'
              : 'text-blue-500 hover:text-blue-700 transition-colors'}
          >
            {selectedSite == null ? 'Capacité du réseau' : 'Sites'}
          </button>
          {selectedSite != null && (
            <>
              <span className="text-blue-200">/</span>
              <span className="font-bold text-blue-900 text-2xl tracking-tight truncate">
                {selectedSite}
              </span>
            </>
          )}
        </div>
        <p className="text-blue-400 text-sm">
          {siteObj != null
            ? <>Rockets de ce site — clients connectés vs maximum avant saturation.</>
            : <>Clients consommés vs disponibles par famille radio et par site — clique un site pour voir ses Rockets.</>}
        </p>
      </div>

      {isLoading && <p className="text-slate-400 text-sm">Chargement…</p>}

      {data != null && siteObj == null && (
        <>
          {/* Cercles globaux */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 max-w-3xl">
            <CapacityDonut
              title="LTU" used={FAMILY.ltu.used} free={FAMILY.ltu.free}
              consumed={data.families.ltu.consumed}
              available={data.families.ltu.available}
              rockets={data.families.ltu.rockets}
              unknown={data.families.ltu.unknown}
            />
            <CapacityDonut
              title="airMAX" used={FAMILY.airmax.used} free={FAMILY.airmax.free}
              consumed={data.families.airmax.consumed}
              available={data.families.airmax.available}
              rockets={data.families.airmax.rockets}
              unknown={data.families.airmax.unknown}
            />
          </div>

          {/* Rockets saturés */}
          <SaturatedRocketsSection
            rockets={saturatedRockets}
            onSelectSite={setSelectedSite}
          />

          {/* Barres par site */}
          <div className="bg-white border border-blue-100 rounded-xl shadow-sm p-5">
            <div className="mb-4">
              <h3 className="font-semibold text-blue-900">Capacité par site</h3>
              <p className="text-xs text-blue-400 mt-0.5">
                Longueur = capacité totale du site ; partie pleine = clients connectés.
                Clique un site pour le détail.
              </p>
            </div>
            {sites.length === 0 ? (
              <p className="py-8 text-center text-slate-400 text-sm">Aucun site.</p>
            ) : (
              <div className="space-y-3 max-h-[28rem] overflow-y-auto pr-1">
                {sites.map(s => (
                  <button
                    key={s.site}
                    onClick={() => setSelectedSite(s.site)}
                    className="w-full text-left rounded-lg px-2 py-2 hover:bg-blue-50 transition-colors"
                  >
                    <div className="flex items-center gap-2 mb-1.5">
                      <span className="text-sm font-semibold text-slate-800 truncate">{s.site}</span>
                      {s.unknown > 0 && (
                        <span
                          className="shrink-0 text-[10px] font-medium text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5"
                          title="Rockets à capacité indéterminée (largeur de canal inconnue, exclus des totaux)"
                        >
                          {s.unknown} indéterminé{s.unknown > 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                    <div className="space-y-1">
                      <SiteFamilyBar family="ltu" bucket={s.ltu} globalMax={globalMax} />
                      <SiteFamilyBar family="airmax" bucket={s.airmax} globalMax={globalMax} />
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* Drill-down : Rockets du site */}
      {siteObj != null && <SiteRocketsTable site={siteObj} />}
    </div>
  )
}

function SaturatedRocketsSection({
  rockets, onSelectSite,
}: { rockets: SaturatedRocket[]; onSelectSite: (site: string) => void }) {
  return (
    <div className="bg-white border border-red-100 rounded-xl shadow-sm p-5">
      <div className="mb-4 flex items-center gap-2">
        <span className="inline-block w-2.5 h-2.5 rounded-full bg-red-500" />
        <h3 className="font-semibold text-blue-900">Rockets saturés</h3>
        <span className="text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded-full px-2 py-0.5 tabular-nums">
          {rockets.length}
        </span>
        <p className="text-xs text-blue-400 ml-1">
          Clients connectés ≥ maximum — capacité atteinte ou dépassée.
        </p>
      </div>
      {rockets.length === 0 ? (
        <p className="py-6 text-center text-slate-400 text-sm">Aucun Rocket saturé. 🎉</p>
      ) : (
        <div className="space-y-2 max-h-[24rem] overflow-y-auto pr-1">
          {rockets.map(r => {
            const fam = FAMILY[r.family]
            const pct = Math.round((r.current_clients / r.max_clients!) * 100)
            return (
              <button
                key={r.id}
                onClick={() => onSelectSite(r.site)}
                className="w-full text-left flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-red-50 transition-colors"
              >
                <span className="inline-block w-2.5 h-2.5 shrink-0 rounded-sm" style={{ background: fam.used }} title={fam.label} />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-semibold text-slate-800 truncate" title={r.name}>{r.name}</div>
                  <div className="text-[11px] text-blue-400 truncate">
                    {r.site} · {fam.label}
                    {r.channel_width_mhz != null ? ` · ${Math.round(r.channel_width_mhz)} MHz` : ''}
                  </div>
                </div>
                <span className="shrink-0 text-sm font-bold text-red-600 tabular-nums">
                  {r.current_clients} / {r.max_clients}
                </span>
                <span className="w-12 shrink-0 text-right text-xs font-semibold text-red-500 tabular-nums">
                  {pct}%
                </span>
              </button>
            )
          })}
        </div>
      )}
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
      <span
        className="w-8 shrink-0 text-[10px] text-amber-600 text-right tabular-nums"
        title={bucket.unknown > 0
          ? `${bucket.unknown} Rocket(s) à capacité indéterminée (largeur inconnue, exclus du total)`
          : undefined}
      >
        {bucket.unknown > 0 ? `+${bucket.unknown}` : ''}
      </span>
    </div>
  )
}

function SiteRocketsTable({ site }: { site: SiteCapacity }) {
  return (
    <div className="bg-white border border-blue-100 rounded-xl shadow-sm overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="bg-blue-50 text-blue-700 text-xs uppercase tracking-wide">
            <th className="text-left font-semibold px-4 py-2.5">Rocket</th>
            <th className="text-left font-semibold px-4 py-2.5">Famille</th>
            <th className="text-right font-semibold px-4 py-2.5">Largeur</th>
            <th className="text-right font-semibold px-4 py-2.5">Connectés / Max</th>
            <th className="text-left font-semibold px-4 py-2.5 w-40">Charge</th>
          </tr>
        </thead>
        <tbody>
          {site.rockets.map(r => {
            const fam = FAMILY[r.family]
            const pct = r.max_clients && r.max_clients > 0
              ? Math.min(Math.round((r.current_clients / r.max_clients) * 100), 100)
              : null
            const over = r.max_clients != null && r.current_clients >= r.max_clients
            return (
              <tr key={r.id} className="border-t border-blue-50">
                <td className="px-4 py-2.5 text-slate-800 truncate max-w-[16rem]" title={r.name}>{r.name}</td>
                <td className="px-4 py-2.5">
                  <span className="inline-flex items-center gap-1.5 text-xs text-slate-600">
                    <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: fam.used }} />
                    {fam.label}
                  </span>
                </td>
                <td className="px-4 py-2.5 text-right text-slate-600 tabular-nums">
                  {r.channel_width_mhz != null ? `${Math.round(r.channel_width_mhz)} MHz` : '—'}
                </td>
                <td className="px-4 py-2.5 text-right tabular-nums">
                  {r.max_clients != null ? (
                    <span className={over ? 'font-bold text-red-600' : 'text-slate-800'}>
                      {r.current_clients} / {r.max_clients}
                    </span>
                  ) : (
                    <span className="text-amber-600">{r.current_clients} / indéterminé</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  {pct != null ? (
                    <div className="flex items-center gap-2">
                      <div className="flex-1 bg-slate-100 rounded h-3 overflow-hidden">
                        <div
                          className="h-full rounded"
                          style={{ width: `${Math.max(pct, 2)}%`, background: over ? '#dc2626' : fam.used }}
                        />
                      </div>
                      <span className="w-9 text-right text-[11px] text-slate-500 tabular-nums">{pct}%</span>
                    </div>
                  ) : (
                    <span className="text-[11px] text-slate-400">—</span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
