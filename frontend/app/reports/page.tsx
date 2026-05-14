'use client'

import { useState } from 'react'
import { generateReport } from '@/lib/api'
import type { SupervisionReport } from '@/lib/types'
import { formatDate } from '@/lib/types'
import PeriodSummaryCard from '@/components/report/PeriodSummaryCard'
import DeviceReliabilityCard from '@/components/report/DeviceReliabilityCard'
import AlertFrequencyCard from '@/components/report/AlertFrequencyCard'
import RadioMetricsCard from '@/components/report/RadioMetricsCard'
import WeakPointsCard from '@/components/report/WeakPointsCard'
import RecommendationsCard from '@/components/report/RecommendationsCard'

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

export default function ReportsPage() {
  const today = new Date()
  const thirtyDaysAgo = new Date(today.getTime() - 30 * 86_400_000)

  const [dateFrom, setDateFrom] = useState<string>(isoDate(thirtyDaysAgo))
  const [dateTo, setDateTo] = useState<string>(isoDate(today))
  const [report, setReport] = useState<SupervisionReport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleGenerate() {
    setLoading(true)
    setError(null)
    try {
      const data = await generateReport(dateFrom, dateTo)
      setReport(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erreur inconnue')
      setReport(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header — masqué à l'impression */}
      <div className="no-print flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-blue-900">Rapport de supervision</h1>
          <p className="text-sm text-blue-500 mt-1">
            Synthèse des incidents, équipements défaillants et recommandations pour
            piloter l'évolution du réseau.
          </p>
        </div>
        {report && (
          <button
            onClick={() => window.print()}
            className="bg-blue-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-800 transition-colors flex items-center gap-2"
          >
            <PrintIcon />
            Imprimer / Exporter PDF
          </button>
        )}
      </div>

      {/* Contrôles — masqués à l'impression */}
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
            onClick={handleGenerate}
            disabled={loading || !dateFrom || !dateTo}
            className="bg-blue-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:bg-blue-300 disabled:cursor-not-allowed"
          >
            {loading ? 'Génération…' : 'Générer le rapport'}
          </button>
        </div>
      </div>

      {error && (
        <div className="no-print bg-red-50 border border-red-200 text-red-700 rounded-xl p-4 text-sm">
          Erreur : {error}
        </div>
      )}

      {loading && (
        <div className="no-print bg-white border border-blue-100 rounded-xl p-8 text-center text-blue-500 text-sm">
          Génération du rapport en cours…
        </div>
      )}

      {report && (
        <>
          {/* Bandeau d'en-tête (visible à l'impression) */}
          <div className="print-card bg-blue-900 text-white rounded-xl p-6 shadow-sm">
            <h2 className="text-xl font-bold">Rapport de supervision réseau</h2>
            <p className="text-sm text-blue-200 mt-2">
              Période analysée : <strong>{report.period.date_from}</strong> →{' '}
              <strong>{report.period.date_to}</strong>
            </p>
            <p className="text-xs text-blue-300 mt-1">
              Généré le {formatDate(report.generated_at)}
            </p>
          </div>

          <PeriodSummaryCard period={report.period} />

          {(() => {
            const typeById = new Map<number, string>(
              report.device_reliability.map((d) => [d.device_id, d.device_type]),
            )
            const isClient = (deviceType: string | undefined) => deviceType === 'lr'

            const clientReliability = report.device_reliability.filter((d) => isClient(d.device_type))
            const networkReliability = report.device_reliability.filter((d) => !isClient(d.device_type))

            const clientRadio = report.radio_metrics.filter((m) => isClient(typeById.get(m.device_id)))
            const networkRadio = report.radio_metrics.filter((m) => !isClient(typeById.get(m.device_id)))

            const clientWeak = report.weak_points.filter((w) => isClient(typeById.get(w.device_id)))
            const networkWeak = report.weak_points.filter((w) => !isClient(typeById.get(w.device_id)))

            const hasClient =
              clientReliability.length + clientRadio.length + clientWeak.length > 0
            const hasNetwork =
              networkReliability.length + networkRadio.length + networkWeak.length > 0

            return (
              <>
                <SectionHeader
                  title="Côté clients — LTU LR"
                  subtitle="Équipements installés chez les clients : problèmes liés à la qualité du service délivré."
                  accent="amber"
                />
                {hasClient ? (
                  <>
                    <DeviceReliabilityCard data={clientReliability} />
                    <RadioMetricsCard data={clientRadio} />
                    <WeakPointsCard data={clientWeak} />
                  </>
                ) : (
                  <EmptySection message="Aucun problème détecté côté clients sur la période." />
                )}

                <SectionHeader
                  title="Côté réseau — Rockets, Switches, UISP Power"
                  subtitle="Équipements d'infrastructure : problèmes liés au cœur de réseau et à l'alimentation."
                  accent="blue"
                />
                {hasNetwork ? (
                  <>
                    <DeviceReliabilityCard data={networkReliability} />
                    <RadioMetricsCard data={networkRadio} />
                    <WeakPointsCard data={networkWeak} />
                  </>
                ) : (
                  <EmptySection message="Aucun problème détecté côté réseau sur la période." />
                )}
              </>
            )
          })()}

          <SectionHeader
            title="Synthèse globale"
            subtitle="Vue agrégée des alertes et recommandations sur l'ensemble du réseau."
            accent="slate"
          />
          <AlertFrequencyCard data={report.alert_frequencies} />
          <RecommendationsCard data={report.recommendations} />
        </>
      )}
    </div>
  )
}

function SectionHeader({
  title,
  subtitle,
  accent,
}: {
  title: string
  subtitle: string
  accent: 'amber' | 'blue' | 'slate'
}) {
  const styles = {
    amber: 'bg-amber-50 border-amber-300 text-amber-900',
    blue: 'bg-blue-50 border-blue-300 text-blue-900',
    slate: 'bg-slate-50 border-slate-300 text-slate-900',
  }[accent]
  return (
    <div className={`print-card border-l-4 rounded-r-xl px-5 py-3 mt-4 ${styles}`}>
      <h2 className="text-base font-bold uppercase tracking-wider">{title}</h2>
      <p className="text-sm mt-1 opacity-80">{subtitle}</p>
    </div>
  )
}

function EmptySection({ message }: { message: string }) {
  return (
    <section className="print-card bg-white border border-blue-100 rounded-xl p-6 shadow-sm">
      <p className="text-sm text-green-600">{message}</p>
    </section>
  )
}

function PrintIcon() {
  return (
    <svg
      className="w-4 h-4"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z"
      />
    </svg>
  )
}
