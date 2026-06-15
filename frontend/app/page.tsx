'use client'

import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { DashboardSummary } from '@/lib/types'
import StatsBar from '@/components/StatsBar'
import SiteOutageCharts from '@/components/SiteOutageCharts'

const REFRESH = 15_000

export default function DashboardPage() {
  // All counting (total/up/down/sites/pannes/clients/open incidents) is done in
  // SQL — fn_dashboard_summary(). This page only renders the returned values.
  const { data: summary } = useSWR<DashboardSummary>(
    endpoints.dashboardSummary, fetcher, { refreshInterval: REFRESH },
  )

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
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-blue-900 text-lg">Pannes par site</h2>
          </div>

          {summary && summary.total === 0 ? (
            <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm space-y-2">
              <p className="text-blue-700 font-medium">Aucun équipement enregistré</p>
              <p className="text-blue-400 text-sm">
                <code className="bg-blue-50 px-2 py-0.5 rounded text-xs">POST /api/v1/devices</code>
              </p>
            </div>
          ) : (
            <SiteOutageCharts />
          )}
        </section>
      </div>
    </>
  )
}
