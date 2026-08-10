'use client'

import { Suspense, useEffect, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { Device, SiteOverviewItem } from '@/lib/types'
import SiteOverviewCard from '@/components/SiteOverviewCard'
import PanneDetailsModal from '@/components/PanneDetailsModal'
import DeviceDetailModal from '@/components/DeviceDetailModal'
import DeviceCard from '@/components/DeviceCard'
import SiteTopology from '@/components/SiteTopology'

const SITE_FALLBACK = 'Sans site'

// Network infrastructure — used only to filter the per-site equipment grid.
const INFRA_TYPES = new Set(['rocket', 'uisp_switch', 'uisp_power', 'airfiber', 'ptp_litebeam'])

function SitesPage() {
  const searchParams = useSearchParams()
  const router = useRouter()
  // Deep-link from the dashboard: /sites?site=AT2 opens that site's equipment.
  const [selectedSite, setSelectedSite] = useState<string | null>(() => searchParams.get('site'))
  const [drillFilter, setDrillFilter]   = useState<'all' | 'infra'>('all')
  const [pannesSite, setPannesSite]     = useState<string | null>(null)
  const [selected, setSelected]         = useState<Device | null>(null)
  // Deep-link ?device= : fiche en cours de résolution → overlay loader plein
  // écran jusqu'à l'ouverture de la modale (sinon rien n'indique le chargement).
  const [deviceLoading, setDeviceLoading] = useState(false)

  // Open a site's equipment, optionally filtered to infra only.
  const openEquipment = (name: string, filter: 'all' | 'infra' = 'all') => {
    setDrillFilter(filter)
    setSelectedSite(name)
  }

  // Site cards (grouping, counts, down_since, down/power device lists) are built
  // entirely in SQL — fn_site_overview(). This is the ONLY request the landing
  // page makes, so it loads fast (no full-fleet /devices fetch anymore).
  const { data: overview, isLoading: overviewLoading, mutate } = useSWR<SiteOverviewItem[]>(
    endpoints.sitesOverview, fetcher, { refreshInterval: 30_000 },
  )

  // Equipment of ONE site is loaded lazily — only when a site is drilled into
  // OR a device detail is open (its linked LRs need the site's devices). Filtered
  // server-side by the indexed `site` column → a small, fast response (tens of
  // rows) instead of the whole ~1000-device fleet.
  const activeSite = selectedSite ?? (selected ? selected.site?.trim() || SITE_FALLBACK : null)
  const { data: siteDevices } = useSWR<Device[]>(
    activeSite ? endpoints.devicesBySite(activeSite) : null,
    fetcher, { refreshInterval: 30_000 },
  )
  const siteDeviceList = siteDevices ?? []

  // Deep-link /sites?device=<id> : ouvre la fiche de cet équipement dans le
  // contexte de son site. Deux appelants — « Voir l'équipement → » de
  // /lr-health, et la RECHERCHE GLOBALE du bandeau (AppShell), qui est le seul
  // chemin depuis les autres pages. L'équipement est chargé par son id (aucune
  // liste à parcourir) ; poser le site charge ensuite la grille du site.
  //
  // ⚠️ AUCUN garde « déjà traité » ici, et c'est délibéré. Il y en avait un
  // (`lastHandledDevice`) : combiné au drapeau `cancelled`, il verrouillait la
  // fiche sur un chargement perpétuel. En **Strict Mode** (activé par défaut en
  // dev sur l'App Router) React invoque l'effet DEUX fois : la 1re passe posait
  // le garde et lançait le fetch, son nettoyage la marquait annulée, et la 2e
  // ressortait aussitôt sur le garde — donc plus personne pour poser la fiche
  // ni retirer l'overlay, alors même que la requête répondait 200. Le garde est
  // de toute façon devenu inutile depuis que le paramètre est CONSOMMÉ (retiré
  // de l'URL) ci-dessous : il ne peut plus redéclencher l'effet tout seul.
  const deviceParam = searchParams.get('device')
  useEffect(() => {
    if (!deviceParam) return
    let cancelled = false
    setDeviceLoading(true)
    ;(async () => {
      try {
        const dev: Device = await fetcher(endpoints.device(Number(deviceParam)))
        if (cancelled) return
        setSelected(dev)
        setSelectedSite(dev.site?.trim() || SITE_FALLBACK)
      } catch { /* device introuvable : on n'ouvre pas la fiche */ }
      finally {
        if (cancelled) return
        setDeviceLoading(false)
        // ⚠️ Le paramètre est CONSOMMÉ (retiré de l'URL) une fois la fiche
        // ouverte. Sans ça, rechercher DEUX FOIS le même équipement depuis le
        // bandeau ne rouvrirait pas sa fiche : la navigation viserait une URL
        // inchangée, donc aucun changement de `deviceParam`, donc aucun effet.
        const rest = new URLSearchParams(Array.from(searchParams.entries()))
        rest.delete('device')
        const qs = rest.toString()
        router.replace(qs ? `/sites?${qs}` : '/sites', { scroll: false })
      }
    })()
    return () => { cancelled = true }
    // `searchParams`/`router` sont stables pour une même URL : la dépendance
    // utile est le seul paramètre lu ici.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deviceParam])

  // "Sans site" (équipements sans site UISP) n'est pas un vrai site : on le
  // masque entièrement. Le sync UISP ignore aussi les équipements sans site
  // (cf. uisp_sync_service) → ce groupe ne se remplit plus.
  const sites = (overview ?? []).filter(s => s.name !== SITE_FALLBACK)
  const totalPannes = sites.reduce((s, x) => s + x.pannes, 0)

  // Pannes modal: render straight from fn_site_overview's down_devices — the
  // SAME source the card's panne count comes from, so the two can never disagree.
  const pannesItem = pannesSite != null ? sites.find(s => s.name === pannesSite) : undefined
  const pannesDevices = pannesItem?.down_devices ?? []

  // Drill-down grid: the selected site's equipment (optionally infra-only).
  // siteDeviceList is already scoped to the active site by the backend.
  const siteGrid = selectedSite != null
    ? siteDeviceList.filter(d => drillFilter === 'all' || INFRA_TYPES.has(d.device_type))
    : []

  // Split the drill-down grid into the infra topology (switch hub + equipment)
  // and, in "all" mode, the clients (LRs) shown separately below.
  const infraGrid  = siteGrid.filter(d => INFRA_TYPES.has(d.device_type))
  const clientGrid = siteGrid.filter(d => !INFRA_TYPES.has(d.device_type))

  const childrenMap: Record<number, number> = {}
  siteDeviceList.forEach(d => {
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
                  {siteGrid.length} équipement{siteGrid.length > 1 ? 's' : ''}
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

        {/* La recherche d'équipement vit maintenant dans le bandeau collant de
            l'application (AppShell) : elle est disponible depuis toutes les
            pages et arrive ici par le deep-link `?device=`. Un second champ
            identique à 60 px du premier n'avait plus de sens. */}

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
        ) : siteDevices == null ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className="rounded-xl bg-white border border-blue-100 h-40 animate-pulse" />
            ))}
          </div>
        ) : (
          <div className="space-y-8">
            {/* Topologie infra du site : switch en hub, équipements reliés par câble */}
            <SiteTopology devices={infraGrid} onSelect={setSelected} />

            {/* Clients (LR) — uniquement en vue « tous les équipements » */}
            {clientGrid.length > 0 && (
              <div className="space-y-3">
                <h2 className="text-sm font-semibold text-blue-900">
                  Clients
                  <span className="text-blue-400 font-normal ml-2">{clientGrid.length}</span>
                </h2>
                <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
                  {clientGrid.map(d => (
                    <DeviceCard
                      key={d.id}
                      device={d}
                      onClick={setSelected}
                      linkedLRCount={childrenMap[d.id] ?? 0}
                    />
                  ))}
                </div>
              </div>
            )}
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

      {/* Loader du deep-link ?device= : même backdrop que la modale, affiché
          tant que la fiche n'est pas résolue (fetch par id en cours). */}
      {deviceLoading && selected == null && (
        <div className="fixed inset-0 bg-blue-900/30 backdrop-blur-sm z-50 flex items-center justify-center animate-fade-in">
          <div className="bg-white rounded-xl shadow-2xl px-6 py-5 flex items-center gap-3">
            <span className="w-5 h-5 rounded-full border-2 border-blue-200 border-t-blue-600 animate-spin shrink-0" />
            <span className="text-sm font-medium text-blue-900">Chargement de l'équipement…</span>
          </div>
        </div>
      )}

      <DeviceDetailModal
        device={selected}
        devices={siteDeviceList}
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
