'use client'

import { useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { DashboardSummary } from '@/lib/types'
import StatsBar from '@/components/StatsBar'
import SiteOutageCharts from '@/components/SiteOutageCharts'

const REFRESH = 15_000
const WINDOW_DAYS = 7

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

export default function DashboardPage() {
  // All counting (total/up/down/sites/pannes/clients/open incidents) is done in
  // SQL — fn_dashboard_summary(). This page only renders the returned values.
  const { data: summary } = useSWR<DashboardSummary>(
    endpoints.dashboardSummary, fetcher, { refreshInterval: REFRESH },
  )

  // Fenêtre des graphiques « Pannes par site ». Défaut = 7 derniers jours ;
  // le sélecteur Du/Au (bouton « Appliquer ») la remplace par une plage figée.
  const today = new Date()
  const sevenDaysAgo = new Date(today.getTime() - WINDOW_DAYS * 86_400_000)
  const [dateFrom, setDateFrom] = useState<string>(isoDate(sevenDaysAgo))
  const [dateTo, setDateTo] = useState<string>(isoDate(today))
  // `null` = pas de plage appliquée → SiteOutageCharts retombe sur ses 7 jours.
  const [applied, setApplied] = useState<{ from: string; to: string } | null>(null)

  return (
    <>
      <div className="space-y-7">

        {/* Page header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Dashboard</h1>
            <p className="text-blue-400 text-sm mt-1">
              Supervision réseau — actualisation toutes les {REFRESH / 1000}s
            </p>
          </div>
          <div className="text-blue-400 text-xs bg-white border border-blue-100 px-3 py-1.5 rounded-lg shadow-sm">
            {new Date().toLocaleDateString('fr-FR', {
              weekday: 'long', day: 'numeric', month: 'long',
            })}
          </div>
        </div>

        {/* KPI bar */}
        <StatsBar
          sites={summary?.sites ?? 0}
          pannes={summary?.pannes ?? 0}
          clients={summary?.clients ?? 0}
          total={summary?.total ?? 0}
          up={summary?.up ?? 0}
          down={summary?.down ?? 0}
          openIncidents={summary?.open_incidents ?? 0}
        />

        {/* Outage charts per site */}
        <section>
          <div className="flex flex-wrap items-end justify-between gap-4 mb-4">
            <h2 className="font-semibold text-blue-900 text-lg">Pannes par site</h2>

            {/* Sélecteur de période Du / Au — défaut 7 derniers jours */}
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-[11px] font-medium text-blue-500 uppercase tracking-wider">Du</span>
                <input
                  type="date"
                  value={dateFrom}
                  max={dateTo}
                  onChange={(e) => setDateFrom(e.target.value)}
                  className="border border-blue-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-[11px] font-medium text-blue-500 uppercase tracking-wider">Au</span>
                <input
                  type="date"
                  value={dateTo}
                  min={dateFrom}
                  max={isoDate(today)}
                  onChange={(e) => setDateTo(e.target.value)}
                  className="border border-blue-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
              </label>
              <button
                type="button"
                onClick={() => setApplied({ from: dateFrom, to: dateTo })}
                disabled={!dateFrom || !dateTo}
                className="bg-blue-600 text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:bg-blue-700 transition-colors disabled:bg-blue-300 disabled:cursor-not-allowed"
              >
                Appliquer
              </button>
              {applied && (
                <button
                  type="button"
                  onClick={() => setApplied(null)}
                  className="text-blue-500 px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-blue-50 transition-colors"
                >
                  7 derniers jours
                </button>
              )}
            </div>
          </div>

          {summary && summary.total === 0 ? (
            <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm space-y-2">
              <p className="text-blue-700 font-medium">Aucun équipement enregistré</p>
              <p className="text-blue-400 text-sm">
                <code className="bg-blue-50 px-2 py-0.5 rounded text-xs">POST /api/v1/devices</code>
              </p>
            </div>
          ) : applied ? (
            <SiteOutageCharts
              startIso={dayStartIso(applied.from)}
              endIso={dayEndIso(applied.to)}
              periodLabel={`${applied.from} → ${applied.to}`}
            />
          ) : (
            <SiteOutageCharts />
          )}
        </section>
      </div>
    </>
  )
}
