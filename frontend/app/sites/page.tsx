'use client'

import { Suspense, useMemo, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device } from '@/lib/types'
import SiteOverviewCard, { type SiteOverview } from '@/components/SiteOverviewCard'
import PanneDetailsModal from '@/components/PanneDetailsModal'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import DeviceCard from '@/components/DeviceCard'

const SITE_FALLBACK = 'Sans site'

// Network infrastructure — these count as a site outage when down. LR clients
// are excluded (an unreachable client is never treated as an incident).
const INFRA_TYPES = new Set(['rocket', 'uisp_switch', 'uisp_power', 'airfiber'])

function SitesPage() {
  const searchParams = useSearchParams()
  // Deep-link from the dashboard: /sites?site=AT2 opens that site's equipment.
  const [selectedSite, setSelectedSite] = useState<string | null>(() => searchParams.get('site'))
  const [drillFilter, setDrillFilter]   = useState<'all' | 'infra'>('all')
  const [pannesSite, setPannesSite]     = useState<string | null>(null)
  const [selected, setSelected]         = useState<Device | null>(null)

  // Open a site's equipment, optionally filtered to infra only.
  const openEquipment = (name: string, filter: 'all' | 'infra' = 'all') => {
    setDrillFilter(filter)
    setSelectedSite(name)
  }

  const { data: devices, isLoading, mutate } = useSWR<Device[]>(
    endpoints.devices,
    fetcher,
    { refreshInterval: 30_000 },
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

  // Group devices into per-site summaries.
  const sites = useMemo<(SiteOverview & { downDevices: Device[]; powerDevices: Device[] })[]>(() => {
    const map = new Map<string, Device[]>()
    devices?.forEach(d => {
      const key = siteOf(d)
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(d)
    })
    return [...map.entries()]
      .map(([name, list]) => {
        const infra = list.filter(d => INFRA_TYPES.has(d.device_type))
        const downInfra = infra.filter(d => d.status === 'down')
        const lrs = list.filter(d => d.device_type === 'lr')
        const downSince = downInfra.reduce<string | null>((oldest, d) => {
          if (!d.last_seen) return oldest
          if (!oldest) return d.last_seen
          return new Date(d.last_seen) < new Date(oldest) ? d.last_seen : oldest
        }, null)
        return {
          name,
          infra: infra.length,
          clientsOnline: lrs.filter(d => d.status === 'up').length,
          clientsBlocked: lrs.filter(d => d.device_type === 'lr' && d.client_blocked).length,
          pannes: downInfra.length,
          downSince,
          downDevices: downInfra,
          powerDevices: list.filter(d => d.device_type === 'uisp_power'),
        }
      })
      .sort((a, b) => a.name.localeCompare(b.name, 'fr'))
  }, [devices, rocketById])

  const pannesDevices = pannesSite != null
    ? (sites.find(s => s.name === pannesSite)?.downDevices ?? [])
    : []

  const totalPannes = sites.reduce((s, x) => s + x.pannes, 0)

  // Drill-down: equipment of the selected site (optionally infra-only).
  const siteDevices = selectedSite != null
    ? (devices?.filter(d =>
        siteOf(d) === selectedSite &&
        (drillFilter === 'all' || INFRA_TYPES.has(d.device_type)),
      ) ?? [])
    : []

  const childrenMap: Record<number, number> = {}
  devices?.forEach(d => {
    if (d.device_type === 'lr' && d.rocket_id != null) {
      childrenMap[d.rocket_id] = (childrenMap[d.rocket_id] ?? 0) + 1
    }
  })

  return (
    <>
      <div className="space-y-6">

        {/* Header */}
        <div className="flex items-start justify-between gap-3">
          {selectedSite == null ? (
            <div>
              <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Sites</h1>
              <p className="text-blue-400 text-sm mt-1">
                {devices ? (
                  <span>
                    {sites.length} site{sites.length > 1 ? 's' : ''}
                    {totalPannes > 0 && (
                      <span className="text-red-500 font-medium"> · {totalPannes} panne{totalPannes > 1 ? 's' : ''}</span>
                    )}
                  </span>
                ) : 'Chargement…'}
              </p>
            </div>
          ) : (
            <div className="flex items-center gap-3 min-w-0">
              <button
                onClick={() => setSelectedSite(null)}
                className="text-sm text-blue-500 hover:text-blue-700 transition-colors flex items-center gap-1 shrink-0"
              >
                ← Sites
              </button>
              <span className="text-blue-200">/</span>
              <h1 className="text-2xl font-bold text-blue-900 tracking-tight truncate">
                {selectedSite}
                <span className="text-blue-400 font-normal text-sm ml-2">
                  {siteDevices.length} équipement{siteDevices.length > 1 ? 's' : ''}
                  {drillFilter === 'infra' ? ' infra' : ''}
                </span>
              </h1>
            </div>
          )}
          <button
            onClick={() => mutate()}
            className="text-sm text-blue-600 hover:text-blue-800 font-medium bg-white border border-blue-200 px-3 py-1.5 rounded-lg transition-colors shadow-sm shrink-0"
          >
            ↻ Rafraîchir
          </button>
        </div>

        {/* Sites grid OR equipment grid */}
        {isLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 3 }, (_, i) => (
              <div key={i} className="rounded-xl bg-white border border-blue-100 h-56 animate-pulse" />
            ))}
          </div>
        ) : !devices?.length ? (
          <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm space-y-2">
            <p className="text-blue-700 font-medium">Aucun équipement enregistré</p>
            <p className="text-blue-400 text-sm">Les sites apparaîtront ici dès que des équipements seront supervisés.</p>
          </div>
        ) : selectedSite == null ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {sites.map(s => (
              <SiteOverviewCard
                key={s.name}
                site={s}
                powerDevices={s.powerDevices}
                onShowPannes={setPannesSite}
                onShowEquipment={openEquipment}
              />
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

export default function SitesPageWrapper() {
  // useSearchParams requires a Suspense boundary in the app router.
  return (
    <Suspense fallback={<div className="px-6 py-12 text-center text-blue-300">Chargement…</div>}>
      <SitesPage />
    </Suspense>
  )
}
