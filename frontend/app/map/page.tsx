'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { ClientMapPoint, ClientMapResponse } from '@/lib/types'

// La clé Google Maps est PUBLIQUE par nature (le script tourne dans le
// navigateur) — d'où NEXT_PUBLIC_. Elle doit donc être restreinte par référent
// HTTP côté Google Cloud, sinon n'importe qui peut la consommer sur notre
// facture. Ce n'est pas un secret : c'est un quota nominatif.
const MAPS_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? ''

// Nouakchott — centre par défaut tant qu'aucun point n'est chargé.
const DEFAULT_CENTER = { lat: 18.0858, lng: -15.9785 }

// Chargement du script partagé par tous les montages du composant. Sans ce
// verrou au niveau module, le double-rendu de React (StrictMode) injecterait
// deux <script> et Google hurlerait "You have included the Google Maps
// JavaScript API multiple times".
let mapsPromise: Promise<void> | null = null

function loadGoogleMaps(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve()
  if ((window as any).google?.maps) return Promise.resolve()
  if (mapsPromise) return mapsPromise
  mapsPromise = new Promise<void>((resolve, reject) => {
    const s = document.createElement('script')
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(MAPS_KEY)}&v=weekly`
    s.async = true
    s.onload = () => resolve()
    s.onerror = () => {
      mapsPromise = null // laisse une nouvelle tentative possible
      reject(new Error('script Google Maps injoignable'))
    }
    document.head.appendChild(s)
  })
  return mapsPromise
}

const STATUS_COLOR: Record<string, string> = {
  up: '#22c55e',
  down: '#ef4444',
  unknown: '#9ca3af',
}

function markerColor(p: ClientMapPoint): string {
  if (p.client_blocked) return '#f97316' // bloqué : orange, avant le statut
  return STATUS_COLOR[p.status] ?? STATUS_COLOR.unknown
}

function popupHtml(p: ClientMapPoint): string {
  const esc = (v: unknown) =>
    String(v ?? '—').replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string))
  const plan =
    p.plan_download_mbps != null || p.plan_upload_mbps != null
      ? `${p.plan_download_mbps ?? '—'} / ${p.plan_upload_mbps ?? '—'} Mbps`
      : '—'
  return `
    <div style="font:13px/1.5 system-ui;min-width:210px">
      <div style="font-weight:600;margin-bottom:4px">${esc(p.name)}</div>
      <div>Statut : <b>${esc(p.status)}</b>${p.client_blocked ? ' · <b>bloqué</b>' : ''}</div>
      <div>Site : ${esc(p.site)}</div>
      <div>AP : ${esc(p.ap_name)}</div>
      <div>Forfait : ${esc(plan)}</div>
      <div>IP : ${esc(p.ip_address)}</div>
      <div style="color:#6b7280;margin-top:4px">${p.latitude.toFixed(5)}, ${p.longitude.toFixed(5)}</div>
    </div>`
}

export default function MapPage() {
  const { data, error, isLoading } = useSWR<ClientMapResponse>(
    endpoints.clientMap, fetcher, { refreshInterval: 60_000 },
  )
  const divRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<any>(null)
  const markersRef = useRef<any[]>([])
  const infoRef = useRef<any>(null)
  const [mapsError, setMapsError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!MAPS_KEY) {
      setMapsError('missing-key')
      return
    }
    let cancelled = false
    loadGoogleMaps()
      .then(() => { if (!cancelled) setReady(true) })
      .catch(() => { if (!cancelled) setMapsError('script') })
    return () => { cancelled = true }
  }, [])

  // (Re)pose les marqueurs à chaque rafraîchissement des données.
  useEffect(() => {
    if (!ready || !divRef.current || !data) return
    const g = (window as any).google
    if (!mapRef.current) {
      mapRef.current = new g.maps.Map(divRef.current, {
        center: DEFAULT_CENTER,
        zoom: 12,
        mapTypeControl: false,
        streetViewControl: false,
      })
      infoRef.current = new g.maps.InfoWindow()
    }
    markersRef.current.forEach((m) => m.setMap(null))
    markersRef.current = []

    const bounds = new g.maps.LatLngBounds()
    data.points.forEach((p) => {
      const marker = new g.maps.Marker({
        position: { lat: p.latitude, lng: p.longitude },
        map: mapRef.current,
        title: p.name,
        icon: {
          path: g.maps.SymbolPath.CIRCLE,
          scale: 6,
          fillColor: markerColor(p),
          fillOpacity: 0.9,
          strokeColor: '#ffffff',
          strokeWeight: 1.5,
        },
      })
      marker.addListener('click', () => {
        infoRef.current.setContent(popupHtml(p))
        infoRef.current.open(mapRef.current, marker)
      })
      markersRef.current.push(marker)
      bounds.extend(marker.getPosition()!)
    })
    // Cadrer sur les clients réels. Sans points, on reste sur Nouakchott.
    if (data.points.length) mapRef.current.fitBounds(bounds)
  }, [ready, data])

  const stats = data?.stats
  const coverage = useMemo(() => {
    if (!stats || !stats.total) return 0
    return Math.round((stats.plotted / stats.total) * 100)
  }, [stats])

  return (
    <div className="p-6 space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-gray-900">Carte des clients</h1>
        <p className="text-sm text-gray-500">
          Position lue sur chaque équipement (airOS <code>system.cfg</code>), rafraîchie par le
          sync quotidien. Ce n&apos;est pas un relevé GPS : c&apos;est la valeur saisie au
          provisioning.
        </p>
      </div>

      {stats && (
        <div className="flex flex-wrap gap-3 text-sm">
          <Stat label="Clients placés" value={`${stats.plotted}`} tone="ok" />
          <Stat label="Couverture" value={`${coverage} %`} />
          <Stat label="Sans position" value={`${stats.without_position}`} tone="muted" />
          <Stat label="À corriger" value={`${stats.outliers}`} tone={stats.outliers ? 'warn' : undefined} />
        </div>
      )}

      {error && (
        <Banner tone="error">Impossible de charger les positions : {String(error.message ?? error)}</Banner>
      )}
      {mapsError === 'missing-key' && (
        <Banner tone="error">
          <b>Clé Google Maps absente.</b> Renseigne <code>NEXT_PUBLIC_GOOGLE_MAPS_API_KEY</code>{' '}
          dans le <code>.env</code>, puis reconstruis le frontend
          (<code>dc up -d --build frontend</code>) — cette variable est figée à la compilation.
          Les données ci-dessous restent exactes ; seule la carte ne peut pas s&apos;afficher.
        </Banner>
      )}
      {mapsError === 'script' && (
        <Banner tone="error">
          Le script Google Maps n&apos;a pas pu être chargé. Le navigateur doit joindre{' '}
          <code>maps.googleapis.com</code> ; vérifie aussi que la clé autorise ce domaine.
        </Banner>
      )}

      {!mapsError && (
        <div
          ref={divRef}
          className="w-full rounded-lg border border-gray-200 bg-gray-50"
          style={{ height: '60vh' }}
        >
          {(isLoading || !ready) && (
            <div className="flex h-full items-center justify-center text-sm text-gray-400">
              Chargement de la carte…
            </div>
          )}
        </div>
      )}

      {!!data?.outliers.length && (
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <h2 className="font-semibold text-amber-900">
            Positions à corriger ({data.outliers.length})
          </h2>
          <p className="mt-1 text-sm text-amber-800">
            Ces clients portent une position hors Mauritanie : elles ne sont pas affichées sur la
            carte, qui serait dézoomée à l&apos;échelle du monde. La valeur vient de l&apos;équipement
            tel quel — le correctif se fait au provisioning (sur l&apos;équipement ou dans UISP),
            pas ici.
          </p>
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-amber-900">
                  <th className="py-1 pr-4">Client</th>
                  <th className="py-1 pr-4">Site</th>
                  <th className="py-1 pr-4">Position lue</th>
                  <th className="py-1">Problème</th>
                </tr>
              </thead>
              <tbody>
                {data.outliers.map((o) => (
                  <tr key={o.id} className="border-t border-amber-200/70">
                    <td className="py-1 pr-4">{o.name}</td>
                    <td className="py-1 pr-4 text-amber-800">{o.site ?? '—'}</td>
                    <td className="py-1 pr-4 font-mono text-xs">
                      {o.latitude.toFixed(4)}, {o.longitude.toFixed(4)}
                    </td>
                    <td className="py-1 text-amber-800">{o.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!!stats?.without_position && (
        <p className="text-sm text-gray-500">
          {stats.without_position} client(s) n&apos;ont aucune position sur leur équipement — le
          champ existe mais n&apos;a jamais été rempli à l&apos;installation. Aucun code ne peut
          l&apos;inventer : il faut la saisir.
        </p>
      )}
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'ok' | 'warn' | 'muted' }) {
  const color =
    tone === 'ok' ? 'text-green-700' : tone === 'warn' ? 'text-amber-700' : tone === 'muted' ? 'text-gray-500' : 'text-gray-900'
  return (
    <div className="rounded-md border border-gray-200 bg-white px-3 py-2">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`text-lg font-semibold ${color}`}>{value}</div>
    </div>
  )
}

function Banner({ tone, children }: { tone: 'error'; children: React.ReactNode }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      {children}
    </div>
  )
}
