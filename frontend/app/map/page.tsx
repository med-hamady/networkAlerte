'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { ClientMapPoint, ClientMapResponse, MapCluster, MapSite } from '@/lib/types'

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

// Icône « antenne » d'un site — un pictogramme SVG inline (data URI), pas une
// image externe : la page doit rester autonome et le rendu identique hors ligne.
function antennaIcon(g: any) {
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="34" height="40" viewBox="0 0 34 40">
      <path d="M17 6 L27 34 H7 Z" fill="#1d4ed8" fill-opacity=".92" stroke="#fff" stroke-width="1.6"/>
      <circle cx="17" cy="5" r="3.6" fill="#1d4ed8" stroke="#fff" stroke-width="1.4"/>
      <path d="M9.5 3.5a10 10 0 000 3M24.5 3.5a10 10 0 010 3" stroke="#1d4ed8"
            stroke-width="1.6" fill="none" stroke-linecap="round"/>
    </svg>`
  return {
    url: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg.trim())}`,
    scaledSize: new g.maps.Size(34, 40),
    anchor: new g.maps.Point(17, 36),
  }
}

function sitePopupHtml(s: MapSite): string {
  const esc = (v: unknown) =>
    String(v ?? '—').replace(/[&<>"]/g, (c) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string))
  return `
    <div style="font:13px/1.5 system-ui;min-width:190px">
      <div style="font-weight:600;margin-bottom:4px">📡 ${esc(s.site)}</div>
      <div>Clients placés : <b>${s.client_count}</b></div>
      <div style="color:#6b7280;margin-top:4px">${s.latitude.toFixed(5)}, ${s.longitude.toFixed(5)}</div>
      <div style="color:#9ca3af;font-size:11px;margin-top:2px">
        Position du pylône (source : ${esc(s.source)})
      </div>
    </div>`
}

function clusterPopupHtml(c: MapCluster): string {
  const esc = (v: unknown) =>
    String(v ?? '—').replace(/[&<>"]/g, (ch) =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch] as string))
  const list = c.clients
    .slice(0, 12)
    .map((x) => `<li>${esc(x.name)} <span style="color:#9ca3af">— ${esc(x.site)}</span></li>`)
    .join('')
  const more = c.clients.length > 12 ? `<li style="color:#9ca3af">… et ${c.clients.length - 12} autre(s)</li>` : ''
  return `
    <div style="font:13px/1.5 system-ui;max-width:290px">
      <div style="font-weight:600;color:#b45309">⚠️ ${c.count} clients à cette position</div>
      <div style="margin:4px 0;color:#374151">
        Rattachés à <b>${c.sites.length} site(s) différents</b> — une même adresse ne peut pas
        être servie par autant de pylônes. Cette position a été <b>recopiée au provisioning</b> :
        elle ne localise personne. Aucune liaison n'est tracée.
      </div>
      <ul style="margin:6px 0 0 16px;padding:0">${list}${more}</ul>
      <div style="color:#6b7280;margin-top:6px">${c.latitude.toFixed(5)}, ${c.longitude.toFixed(5)}</div>
    </div>`
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
  const linesRef = useRef<any[]>([])
  const infoRef = useRef<any>(null)
  const [mapsError, setMapsError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  // Filtre par site : à 1100 clients la carte devient un nuage illisible et le
  // faisceau de liaisons se change en bouillie. Isoler un site est le geste
  // naturel de l'exploitant ("qui est accroché à ce pylône ?").
  const [siteFilter, setSiteFilter] = useState<string>('')
  const [showLinks, setShowLinks] = useState(true)

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
    linesRef.current.forEach((l) => l.setMap(null))
    markersRef.current = []
    linesRef.current = []

    const sites = siteFilter ? data.sites.filter((s) => s.site === siteFilter) : data.sites
    const points = siteFilter ? data.points.filter((p) => p.site === siteFilter) : data.points
    const siteByName = new Map(data.sites.map((s) => [s.site, s]))
    const bounds = new g.maps.LatLngBounds()

    // 1) Les liaisons d'abord, pour qu'elles passent SOUS les marqueurs.
    if (showLinks) {
      points.forEach((p) => {
        const s = p.site ? siteByName.get(p.site) : undefined
        if (!s) return // site sans position connue : pas de liaison traçable
        const line = new g.maps.Polyline({
          path: [
            { lat: s.latitude, lng: s.longitude },
            { lat: p.latitude, lng: p.longitude },
          ],
          map: mapRef.current,
          strokeColor: '#3b82f6',
          strokeOpacity: 0.28,
          strokeWeight: 1,
          clickable: false,
        })
        linesRef.current.push(line)
      })
    }

    // 2) Les clients.
    points.forEach((p) => {
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

    // 2 bis) Les positions PARTAGÉES : un marqueur compté, en ambre, sans
    // liaison. Elles ne localisent personne — les tracer ferait croire à des
    // adresses réelles et produirait le faisceau qui rend la carte illisible.
    const clusters = siteFilter
      ? data.clusters.filter((c) => c.sites.includes(siteFilter))
      : data.clusters
    clusters.forEach((c) => {
      const marker = new g.maps.Marker({
        position: { lat: c.latitude, lng: c.longitude },
        map: mapRef.current,
        title: `${c.count} clients à cette position — position recopiée`,
        label: { text: String(c.count), color: '#ffffff', fontSize: '11px', fontWeight: '600' },
        icon: {
          path: g.maps.SymbolPath.CIRCLE,
          scale: 11,
          fillColor: '#f59e0b',
          fillOpacity: 0.95,
          strokeColor: '#ffffff',
          strokeWeight: 2,
        },
        zIndex: 500,
      })
      marker.addListener('click', () => {
        infoRef.current.setContent(clusterPopupHtml(c))
        infoRef.current.open(mapRef.current, marker)
      })
      markersRef.current.push(marker)
      bounds.extend(marker.getPosition()!)
    })

    // 3) Les sites par-dessus (zIndex) : ce sont les repères de lecture.
    sites.forEach((s) => {
      const marker = new g.maps.Marker({
        position: { lat: s.latitude, lng: s.longitude },
        map: mapRef.current,
        title: `${s.site} — ${s.client_count} client(s)`,
        icon: antennaIcon(g),
        zIndex: 1000,
      })
      marker.addListener('click', () => {
        infoRef.current.setContent(sitePopupHtml(s))
        infoRef.current.open(mapRef.current, marker)
      })
      markersRef.current.push(marker)
      bounds.extend(marker.getPosition()!)
    })

    // Cadrer sur ce qui est affiché. Sans rien, on reste sur Nouakchott.
    if (points.length || sites.length || clusters.length) mapRef.current.fitBounds(bounds)
  }, [ready, data, siteFilter, showLinks])

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
          <Stat label="Sites" value={`${stats.sites}`} />
          <Stat label="Clients localisés" value={`${stats.plotted}`} tone="ok" />
          <Stat label="Couverture" value={`${coverage} %`} />
          <Stat
            label="Positions recopiées"
            value={`${stats.stacked_clients}`}
            tone={stats.stacked_clients ? 'warn' : undefined}
          />
          <Stat label="Sans position" value={`${stats.without_position}`} tone="muted" />
          <Stat label="À corriger" value={`${stats.outliers}`} tone={stats.outliers ? 'warn' : undefined} />
        </div>
      )}

      {!!data?.sites.length && (
        <div className="flex flex-wrap items-center gap-4 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm">
          <label className="flex items-center gap-2">
            <span className="text-gray-600">Site :</span>
            <select
              className="rounded border border-gray-300 px-2 py-1"
              value={siteFilter}
              onChange={(e) => setSiteFilter(e.target.value)}
            >
              <option value="">Tous ({data.sites.length})</option>
              {data.sites.map((s) => (
                <option key={s.site} value={s.site}>
                  {s.site} ({s.client_count})
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-gray-600">
            <input
              type="checkbox"
              checked={showLinks}
              onChange={(e) => setShowLinks(e.target.checked)}
            />
            Afficher les liaisons
          </label>
          <span className="ml-auto flex flex-wrap items-center gap-3 text-xs text-gray-500">
            <Legend color="#1d4ed8" label="Site (pylône)" square />
            <Legend color="#22c55e" label="Client en ligne" />
            <Legend color="#ef4444" label="Hors ligne" />
            <Legend color="#f97316" label="Bloqué" />
            <Legend color="#f59e0b" label="Position recopiée (N clients)" />
          </span>
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

      {!!data?.clusters.length && (
        <section className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <h2 className="font-semibold text-amber-900">
            Positions recopiées ({stats?.stacked_clients} clients sur {data.clusters.length} points)
          </h2>
          <p className="mt-1 text-sm text-amber-800">
            Ces coordonnées sont portées par plusieurs clients à la fois, souvent rattachés à des
            sites différents — une même adresse ne peut pas être servie par autant de pylônes.
            Ce sont des valeurs dupliquées au provisioning : elles ne localisent personne, donc
            aucune liaison n&apos;est tracée. Un point ambré marque leur emplacement.
          </p>
          <div className="mt-3 overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-amber-900">
                  <th className="py-1 pr-4">Position</th>
                  <th className="py-1 pr-4">Clients</th>
                  <th className="py-1">Sites concernés</th>
                </tr>
              </thead>
              <tbody>
                {data.clusters.slice(0, 15).map((c) => (
                  <tr key={`${c.latitude},${c.longitude}`} className="border-t border-amber-200/70">
                    <td className="py-1 pr-4 font-mono text-xs">
                      {c.latitude.toFixed(4)}, {c.longitude.toFixed(4)}
                    </td>
                    <td className="py-1 pr-4 font-semibold">{c.count}</td>
                    <td className="py-1 text-amber-800">{c.sites.join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {!!stats && stats.plotted > stats.linked && (
        <p className="text-sm text-gray-500">
          {stats.plotted - stats.linked} client(s) sont placés mais sans liaison : leur site
          n&apos;a pas encore de position connue. Ajoute-la dans{' '}
          <code>site_locations</code> pour que le trait apparaisse.
        </p>
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

function Legend({ color, label, square }: { color: string; label: string; square?: boolean }) {
  return (
    <span className="flex items-center gap-1.5">
      <span
        className={square ? 'inline-block h-3 w-3' : 'inline-block h-2.5 w-2.5 rounded-full'}
        style={{ background: color }}
      />
      {label}
    </span>
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
