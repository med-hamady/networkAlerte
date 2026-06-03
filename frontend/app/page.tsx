'use client'

import React, { useMemo, useState } from 'react'
import Link from 'next/link'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device } from '@/lib/types'
import StatsBar from '@/components/StatsBar'
import DeviceCard from '@/components/DeviceCard'
import SiteCard from '@/components/SiteCard'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import PanneDetailsModal from '@/components/PanneDetailsModal'

const REFRESH = 15_000
const SITE_FALLBACK = 'Sans site'

// Equipment that counts as a site outage when down. LR clients are excluded —
// an unreachable LR is never treated as an incident (client-side problem).
const INFRA_TYPES = new Set(['rocket', 'uisp_switch', 'uisp_power'])

export default function DashboardPage() {
  const [selected, setSelected] = useState<Device | null>(null)
  const [selectedSite, setSelectedSite] = useState<string | null>(null)
  const [pannesSite, setPannesSite] = useState<string | null>(null)

  const { data: devices, isLoading: loadingDevices } = useSWR<Device[]>(
    endpoints.devices, fetcher, { refreshInterval: REFRESH },
  )

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

  // Group devices into site summaries: outage count (+ oldest downtime) and client count.
  const sites = useMemo(() => {
    const map = new Map<string, Device[]>()
    devices?.forEach(d => {
      const key = siteOf(d)
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(d)
    })
    return [...map.entries()]
      .map(([name, list]) => {
        const downInfra = list.filter(d => INFRA_TYPES.has(d.device_type) && d.status === 'down')
        const downSince = downInfra.reduce<string | null>((oldest, d) => {
          if (!d.last_seen) return oldest
          if (!oldest) return d.last_seen
          return new Date(d.last_seen) < new Date(oldest) ? d.last_seen : oldest
        }, null)
        return {
          name,
          pannes: downInfra.length,
          clients: list.filter(d => d.device_type === 'lr').length,
          downSince,
          downDevices: downInfra,
        }
      })
      .sort((a, b) => a.name.localeCompare(b.name, 'fr'))
  }, [devices, rocketById])

  const totalPannes  = sites.reduce((s, x) => s + x.pannes, 0)
  const totalClients = sites.reduce((s, x) => s + x.clients, 0)

  const pannesDevices = pannesSite != null
    ? (sites.find(s => s.name === pannesSite)?.downDevices ?? [])
    : []

  const siteDevices = selectedSite != null
    ? (devices?.filter(d => siteOf(d) === selectedSite) ?? [])
    : []

  const childrenMap: Record<number, number> = {}
  devices?.forEach(d => {
    if (d.device_type === 'lr' && d.rocket_id != null) {
      childrenMap[d.rocket_id] = (childrenMap[d.rocket_id] ?? 0) + 1
    }
  })

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
        <StatsBar sites={sites.length} pannes={totalPannes} clients={totalClients} />

        {/* Sites / Equipment grid */}
        <section>
          <div className="flex items-center justify-between mb-4">
            {selectedSite == null ? (
              <h2 className="font-semibold text-blue-900 text-lg">Sites</h2>
            ) : (
              <div className="flex items-center gap-3 min-w-0">
                <button
                  onClick={() => setSelectedSite(null)}
                  className="text-sm text-blue-500 hover:text-blue-700 transition-colors flex items-center gap-1 shrink-0"
                >
                  ← Sites
                </button>
                <span className="text-blue-200">/</span>
                <h2 className="font-semibold text-blue-900 text-lg truncate">
                  {selectedSite}
                  <span className="text-blue-400 font-normal text-sm ml-2">
                    {siteDevices.length} équipement{siteDevices.length > 1 ? 's' : ''}
                  </span>
                </h2>
              </div>
            )}
            <Link href="/devices" className="text-sm text-blue-500 hover:text-blue-700 transition-colors shrink-0">
              Vue tableau →
            </Link>
          </div>

          {loadingDevices ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {Array.from({ length: 4 }, (_, i) => (
                <div key={i} className="rounded-xl bg-white border border-blue-100 h-64 animate-pulse" />
              ))}
            </div>
          ) : !devices?.length ? (
            <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm space-y-2">
              <p className="text-blue-700 font-medium">Aucun équipement enregistré</p>
              <p className="text-blue-400 text-sm">
                <code className="bg-blue-50 px-2 py-0.5 rounded text-xs">POST /api/v1/devices</code>
              </p>
            </div>
          ) : selectedSite == null ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {sites.map(s => (
                <SiteCard key={s.name} site={s} onClick={setSelectedSite} onShowPannes={setPannesSite} />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
              {siteDevices.map(d => (
                <DeviceCard
                  key={d.id}
                  device={d}
                  onClick={setSelected}
                  linkedLRCount={childrenMap[d.id] ?? 0}
                />
              ))}
            </div>
          )}
        </section>
      </div>

      <PanneDetailsModal
        site={pannesSite}
        devices={pannesDevices}
        onClose={() => setPannesSite(null)}
        onSelect={d => { setPannesSite(null); setSelected(d) }}
      />

      <DeviceDetailModal
        device={selected}
        devices={devices ?? []}
        onClose={() => setSelected(null)}
        onNavigate={setSelected}
      />
    </>
  )
}
