'use client'

import { Suspense, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, SiteOverviewItem } from '@/lib/types'
import SiteOverviewCard from '@/components/SiteOverviewCard'
import PanneDetailsModal from '@/components/PanneDetailsModal'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import DeviceCard from '@/components/DeviceCard'

const SITE_FALLBACK = 'Sans site'

// Network infrastructure — used only to filter the per-site equipment grid.
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

  // Site cards (grouping, counts, down_since, down/power device lists) are built
  // entirely in SQL — fn_site_overview(). The frontend only renders them.
  const { data: overview, isLoading: overviewLoading, mutate } = useSWR<SiteOverviewItem[]>(
    endpoints.sitesOverview, fetcher, { refreshInterval: 30_000 },
  )

  // The full device list is still loaded for the drill-down equipment grid and
  // the device/pannes modals (which need complete device objects). It is NO
  // longer aggregated client-side — each device carries its resolved `site`.
  const { data: devices } = useSWR<Device[]>(
    endpoints.devices, fetcher, { refreshInterval: 30_000 },
  )

  // Deep-link from /lr-health "Voir l'équipement →": /sites?device=<id> opens
  // that device's detail modal (and its site context). Each distinct device
  // param is handled once (so closing the modal doesn't re-open it), but a new
  // param value still fires.
  const deviceParam = searchParams.get('device')
  const lastHandledDevice = useRef<string | null>(null)
  useEffect(() => {
    if (!deviceParam || !devices?.length) return
    if (lastHandledDevice.current === deviceParam) return
    const dev = devices.find(d => d.id === Number(deviceParam))
    if (dev) {
      setSelected(dev)
      setSelectedSite(dev.site?.trim() || SITE_FALLBACK)
      lastHandledDevice.current = deviceParam
    }
  }, [deviceParam, devices])

  const sites = overview ?? []
  const totalPannes = sites.reduce((s, x) => s + x.pannes, 0)

  // Pannes modal: render straight from fn_site_overview's down_devices — the
  // SAME source the card's panne count comes from, so the two can never
  // disagree (previously this re-joined the down ids against the paginated
  // /devices list, which silently drops rows once the parc exceeds the page
  // limit → empty modal while the card still showed "N en panne").
  const pannesItem = pannesSite != null ? sites.find(s => s.name === pannesSite) : undefined
  const pannesDevices = pannesItem?.down_devices ?? []

  // Drill-down: equipment of the selected site (optionally infra-only). Uses the
  // backend-resolved `site` field — no client-side hierarchy resolution.
  const siteDevices = selectedSite != null
    ? (devices?.filter(d =>
        (d.site?.trim() || SITE_FALLBACK) === selectedSite &&
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
                {overview ? (
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
        {overviewLoading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from({ length: 3 }, (_, i) => (
              <div key={i} className="rounded-xl bg-white border border-blue-100 h-56 animate-pulse" />
            ))}
          </div>
        ) : sites.length === 0 ? (
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
        onSelect={async id => {
          // Fiche détaillée résolue à la source (lookup par id, jamais tronqué)
          // plutôt que filtrée dans la liste /devices paginée.
          setPannesSite(null)
          try {
            setSelected(await fetcher(endpoints.device(id)))
          } catch { /* device introuvable : on n'ouvre pas la fiche */ }
        }}
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
