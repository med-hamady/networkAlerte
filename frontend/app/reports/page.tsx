'use client'

import { useRef, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { CapacityBucket, NetworkCapacity, NetworkInfraCapacity } from '@/lib/types'
import CapacityDonut from '@/components/CapacityDonut'
import { SiteOutageTable } from '@/components/SiteOutageCharts'

// Couleurs par famille — mêmes teintes que la page /capacity.
const FAMILY = {
  ltu:    { label: 'LTU',    used: '#22c55e', free: '#fcd34d' },
  airmax: { label: 'airMAX', used: '#3b82f6', free: '#fdba74' },
} as const

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

  // Contenu imprimable (tout sauf l'en-tête/contrôles) → source du PDF.
  const reportRef = useRef<HTMLDivElement>(null)
  const [downloading, setDownloading] = useState(false)

  async function downloadPdf() {
    if (!reportRef.current || downloading) return
    setDownloading(true)
    try {
      // Import dynamique : html2pdf.js dépend de `window`, jamais en SSR.
      const html2pdf = (await import('html2pdf.js')).default
      await html2pdf()
        .set({
          margin: 10,
          filename: `rapport-supervision_${applied.from}_${applied.to}.pdf`,
          image: { type: 'jpeg', quality: 0.98 },
          html2canvas: { scale: 2, backgroundColor: '#ffffff', useCORS: true },
          jsPDF: { unit: 'mm', format: 'a4', orientation: 'portrait' },
          pagebreak: { mode: ['css', 'avoid-all'] },
        })
        .from(reportRef.current)
        .save()
    } finally {
      setDownloading(false)
    }
  }

  const sites = capacity?.sites ?? []

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
          onClick={downloadPdf}
          disabled={downloading || capacity == null}
          className="bg-blue-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-800 transition-colors flex items-center gap-2 disabled:bg-blue-300 disabled:cursor-not-allowed"
        >
          <DownloadIcon />
          {downloading ? 'Génération…' : 'Télécharger le rapport'}
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

      {/* Contenu imprimable / exporté en PDF */}
      <div ref={reportRef} className="space-y-6">
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
            <div className="mb-4 flex items-center gap-x-4 gap-y-1.5 flex-wrap">
              <h3 className="font-semibold text-blue-900">Capacité par site</h3>
              <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-500">
                <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: FAMILY.ltu.used }} />
                LTU
              </span>
              <span className="inline-flex items-center gap-1.5 text-[11px] text-slate-500">
                <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: FAMILY.airmax.used }} />
                airMAX
              </span>
              <p className="text-xs text-blue-400 w-full">
                Clients installés / capacité max par famille radio. Cellule en rouge = famille saturée.
              </p>
            </div>
            {sites.length === 0 ? (
              <p className="py-8 text-center text-slate-400 text-sm">Aucun site.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-blue-400 border-b border-blue-100">
                    <th className="py-2 pr-3 font-medium">Site</th>
                    <th className="py-2 px-3 font-medium text-right">LTU (inst./cap.)</th>
                    <th className="py-2 pl-3 font-medium text-right">airMAX (inst./cap.)</th>
                  </tr>
                </thead>
                <tbody>
                  {sites.map((s) => (
                    <tr key={s.site} className="border-b border-blue-50 last:border-0">
                      <td className="py-2 pr-3 font-medium text-slate-800">{s.site}</td>
                      <td className="py-2 px-3 text-right"><CapacityCell bucket={s.ltu} /></td>
                      <td className="py-2 pl-3 text-right"><CapacityCell bucket={s.airmax} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Capacité infra par site (Rockets + AF60 + PTP vs max) */}
          {capacity.infra != null && (
            <SiteInfraReportTable infra={capacity.infra} />
          )}
        </>
      )}

      {/* ── Section 2 : Coupures par site ──────────────────────────────── */}
      <SectionHeader
        title="Temps de coupure des sites"
        subtitle="Pannes et downtime cumulé de l'infrastructure (Rockets, Switches, UISP Power) sur la période."
      />
      <div className="print-card">
        <SiteOutageTable
          startIso={dayStartIso(applied.from)}
          endIso={dayEndIso(applied.to)}
          periodLabel={`${applied.from} → ${applied.to}`}
        />
      </div>
      </div>
    </div>
  )
}

// Cellule d'une famille radio dans le tableau « Capacité par site » :
// installés / capacité (rouge si saturée), + note des Rockets à capacité
// indéterminée. Tiret si la famille est absente du site.
function CapacityCell({ bucket }: { bucket: CapacityBucket }) {
  if (bucket.capacity <= 0) {
    if (bucket.unknown > 0) {
      return (
        <span className="text-[11px] text-amber-600 tabular-nums">
          {bucket.unknown} indéterminé{bucket.unknown > 1 ? 's' : ''}
        </span>
      )
    }
    return <span className="text-slate-300">—</span>
  }

  const saturated = bucket.consumed >= bucket.capacity
  return (
    <span className="tabular-nums">
      <span className={saturated ? 'font-bold text-red-600' : 'font-semibold text-slate-800'}>
        {bucket.consumed}
      </span>
      <span className="text-slate-400"> / {bucket.capacity}</span>
      {bucket.unknown > 0 && (
        <span className="ml-1 text-[10px] text-amber-600">+{bucket.unknown}</span>
      )}
    </span>
  )
}

// Tableau « Capacité infra par site » pour le rapport — même contenu que le PDF
// WhatsApp quotidien (Site / Équip. infra / Max / Marge), lignes en dépassement
// surlignées. Statique (pas de navigation, c'est un rapport imprimable).
function SiteInfraReportTable({ infra }: { infra: NetworkInfraCapacity }) {
  const overCount = infra.sites.filter((s) => s.over).length
  return (
    <div className="print-card bg-white border border-blue-100 rounded-xl shadow-sm p-5">
      <div className="mb-4 flex items-center gap-2 flex-wrap">
        <h3 className="font-semibold text-blue-900">Capacité infra par site</h3>
        <span className="text-xs font-semibold text-slate-600 bg-slate-50 border border-slate-200 rounded-full px-2 py-0.5 tabular-nums">
          max {infra.threshold}/site
        </span>
        {overCount > 0 && (
          <span className="text-xs font-semibold text-red-600 bg-red-50 border border-red-200 rounded-full px-2 py-0.5 tabular-nums">
            {overCount} en dépassement
          </span>
        )}
        <p className="text-xs text-blue-400 ml-1 w-full sm:w-auto">
          Rockets + AF60 + PTP (hors switch et UISP Power). +N = places libres, −N = dépassement.
        </p>
      </div>
      {infra.sites.length === 0 ? (
        <p className="py-6 text-center text-slate-400 text-sm">Aucun équipement infra.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-blue-400 border-b border-blue-100">
              <th className="py-2 pr-3 font-medium">Site</th>
              <th className="py-2 px-3 font-medium text-right">Équip. infra</th>
              <th className="py-2 px-3 font-medium text-right">Max</th>
              <th className="py-2 pl-3 font-medium text-right">Marge</th>
            </tr>
          </thead>
          <tbody>
            {infra.sites.map((s) => (
              <tr
                key={s.site}
                className={`border-b border-blue-50 last:border-0 ${s.over ? 'bg-red-50' : ''}`}
              >
                <td className="py-2 pr-3 font-medium text-slate-800">{s.site}</td>
                <td className="py-2 px-3 text-right tabular-nums text-slate-700">{s.count}</td>
                <td className="py-2 px-3 text-right tabular-nums text-slate-400">{infra.threshold}</td>
                <td className="py-2 pl-3 text-right">
                  <span
                    className={`inline-block text-xs font-semibold rounded-full px-2 py-0.5 tabular-nums ${
                      s.over
                        ? 'text-red-600 bg-red-50 border border-red-200'
                        : 'text-emerald-600 bg-emerald-50 border border-emerald-200'
                    }`}
                  >
                    {s.remaining >= 0 ? `+${s.remaining}` : `−${Math.abs(s.remaining)}`}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
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

function DownloadIcon() {
  return (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M4 16v1a2 2 0 002 2h12a2 2 0 002-2v-1m-4-5l-4 4m0 0l-4-4m4 4V4"
      />
    </svg>
  )
}
