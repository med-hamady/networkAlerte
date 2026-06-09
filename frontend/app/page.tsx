'use client'

import React, { useMemo } from 'react'
import Link from 'next/link'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, Incident } from '@/lib/types'
import StatsBar from '@/components/StatsBar'
import SiteOutageCharts from '@/components/SiteOutageCharts'

const REFRESH = 15_000
const SITE_FALLBACK = 'Sans site'

// Equipment that counts as a site outage when down. LR clients are excluded —
// an unreachable LR is never treated as an incident (client-side problem).
const INFRA_TYPES = new Set(['rocket', 'uisp_switch', 'uisp_power'])

export default function DashboardPage() {
  const { data: devices } = useSWR<Device[]>(
    endpoints.devices, fetcher, { refreshInterval: REFRESH },
  )
  const { data: incidents } = useSWR<Incident[]>(
    `${endpoints.incidents}?status=open&limit=500`, fetcher, { refreshInterval: REFRESH },
  )

  const total   = devices?.length ?? 0
  const up      = devices?.filter(d => d.status === 'up').length   ?? 0
  const down    = devices?.filter(d => d.status === 'down').length ?? 0
  const openInc = incidents?.length ?? 0

  // Rocket lookup — used to attach an LR client to the site of its parent rocket.
  const rocketById = useMemo(
    () => new Map(devices?.filter(d => d.device_type === 'rocket').map(d => [d.id, d]) ?? []),
    [devices],
  )

  // Site of a device: infra by its own location; an LR by its parent rocket's site.
  const siteOf = (d: Device): string => {
    if (d.device_type === 'lr') {
      const rk = d.rocket_id != null ? rocketById.get(d.rocket_id) : undefined
      return rk?.location?.trim() || SITE_FALLBACK
    }
    return d.location?.trim() || SITE_FALLBACK
  }

  // KPI counts: distinct sites, live infra outages, client (LR) count.
  const siteCount = useMemo(() => {
    const set = new Set<string>()
    devices?.forEach(d => set.add(siteOf(d)))
    return set.size
  }, [devices, rocketById])
  const livePannes = devices?.filter(d => INFRA_TYPES.has(d.device_type) && d.status === 'down').length ?? 0
  const clientCount = devices?.filter(d => d.device_type === 'lr').length ?? 0

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
          sites={siteCount}
          pannes={livePannes}
          clients={clientCount}
          total={total}
          up={up}
          down={down}
          openIncidents={openInc}
        />

        {/* Outage charts per site */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-blue-900 text-lg">Pannes par site</h2>
            <Link href="/network-uptime" className="text-sm text-blue-500 hover:text-blue-700 transition-colors shrink-0">
              Journal des coupures →
            </Link>
          </div>

          {!devices?.length ? (
            <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm space-y-2">
              <p className="text-blue-700 font-medium">Aucun équipement enregistré</p>
              <p className="text-blue-400 text-sm">
                <code className="bg-blue-50 px-2 py-0.5 rounded text-xs">POST /api/v1/devices</code>
              </p>
            </div>
          ) : (
            <SiteOutageCharts devices={devices} />
          )}
        </section>
      </div>
    </>
  )
}
