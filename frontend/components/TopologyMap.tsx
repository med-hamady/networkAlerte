'use client'

// Vue CARTE de la topologie inter-sites — les mêmes sites et les mêmes
// liaisons que le graphe, posés à leurs coordonnées réelles sur Google Maps.
//
// Ce que la carte apporte que le graphe ne peut pas : la DISTANCE et la
// DIRECTION. Un backhaul de 400 m et un de 12 km sont deux traits identiques
// sur un graphe en couches ; ici on voit lequel traverse la ville. C'est aussi
// ce qui permet de juger un tracé (un lien qui contourne, deux sites plus
// proches qu'on ne le croyait).
//
// ⚠️ AUCUNE donnée nouvelle n'est demandée au terrain : tout vient de la MÊME
// réponse `/network-topology` que le graphe — y compris les coordonnées, jointes
// côté service depuis `site_locations`. Les deux vues ne peuvent donc pas se
// contredire, et basculer de l'une à l'autre ne coûte pas une requête.
//
// ⚠️ Les COULEURS viennent de `lib/topologyColors`, partagé avec le graphe. Ne
// pas réécrire le barème ici : deux vues du même réseau qui se contredisent sur
// l'état d'une dorsale, c'est pire que pas de carte du tout.

import { useEffect, useMemo, useRef, useState } from 'react'
import type { NetworkTopology, TopologyEdge, TopologySite } from '@/lib/types'
import { DEFAULT_CENTER, MAPS_KEY, loadGoogleMaps } from '@/lib/googleMaps'
import { SATURATION_COLOR, downSiteSet, edgeColor, siteColor } from '@/lib/topologyColors'

interface Props {
  topo: NetworkTopology
  selectedSite: string | null
  onSelectSite: (site: string | null) => void
}

export default function TopologyMap({ topo, selectedSite, onSelectSite }: Props) {
  const divRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<any>(null)
  const infoRef = useRef<any>(null)
  const markersRef = useRef<any[]>([])
  const linesRef = useRef<any[]>([])
  // Le cadrage automatique n'a lieu qu'UNE fois (cf. plus bas) : le rejouer à
  // chaque sélection annulerait le zoom et le déplacement de l'opérateur.
  const fittedRef = useRef(false)
  const [ready, setReady] = useState(false)
  const [mapsError, setMapsError] = useState<'missing-key' | 'script' | null>(null)

  // Sites plaçables / non plaçables. La seconde liste n'est pas un détail :
  // une carte qui omet un site sans le dire se lit comme un réseau qui n'a pas
  // ce site — le même principe que les « outliers » de la carte des clients.
  const { placeable, missing } = useMemo(() => {
    const placeable: TopologySite[] = []
    const missing: TopologySite[] = []
    for (const s of topo.sites) {
      if (s.latitude != null && s.longitude != null) placeable.push(s)
      else missing.push(s)
    }
    return { placeable, missing }
  }, [topo.sites])

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

  // (Re)pose marqueurs et traits à chaque changement de données ou de sélection.
  useEffect(() => {
    if (!ready || !divRef.current) return
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
    const map = mapRef.current

    markersRef.current.forEach((m) => m.setMap(null))
    linesRef.current.forEach((l) => l.setMap(null))
    markersRef.current = []
    linesRef.current = []

    const byName = new Map(placeable.map((s) => [s.site, s]))
    const downSites = downSiteSet(topo.sites)
    const bounds = new g.maps.LatLngBounds()

    // Sélection d'un site = on ne garde que SES liaisons, comme sur le graphe.
    const edges = selectedSite
      ? topo.edges.filter((e) => e.site_a === selectedSite || e.site_b === selectedSite)
      : topo.edges

    // 1) Les liaisons d'abord, pour qu'elles passent SOUS les marqueurs.
    edges.forEach((edge) => {
      const a = byName.get(edge.site_a)
      const b = byName.get(edge.site_b)
      // Une extrémité sans position = liaison non traçable. On ne l'invente pas
      // et on ne la rabat pas sur un point arbitraire : elle est simplement
      // absente du dessin, et ses deux sites figurent dans la liste ci-dessous.
      if (!a || !b) return
      const color = edgeColor(edge, downSites)
      const wireless = edge.medium === 'wireless'
      const line = new g.maps.Polyline({
        path: [
          { lat: a.latitude!, lng: a.longitude! },
          { lat: b.latitude!, lng: b.longitude! },
        ],
        map,
        strokeColor: color,
        // Radio en TIRETS, fibre/cuivre en trait plein — même convention que le
        // graphe. Sur Google Maps un trait discontinu se fait en rendant la
        // ligne invisible et en répétant un symbole le long du tracé.
        strokeOpacity: wireless ? 0 : 0.9,
        strokeWeight: edge.redundant ? 5 : 3,
        icons: wireless
          ? [{
              icon: {
                path: 'M 0,-1 0,1',
                strokeColor: color,
                strokeOpacity: 0.9,
                strokeWeight: edge.redundant ? 5 : 3,
                scale: 3,
              },
              offset: '0',
              repeat: '14px',
            }]
          : undefined,
        zIndex: 1,
      })
      line.addListener('click', (ev: any) => {
        infoRef.current.setContent(edgeHtml(edge))
        infoRef.current.setPosition(ev.latLng)
        infoRef.current.open(map)
      })
      linesRef.current.push(line)
    })

    // 2) Les sites. Tous sont posés, même quand une sélection filtre les
    //    liaisons : masquer les autres pylônes ferait croire à un réseau réduit.
    placeable.forEach((site) => {
      const isSelected = selectedSite === site.site
      // SATURATION — anneau violet AUTOUR de la pastille, le même signal que
      // l'anneau du graphe. ⚠️ Marqueur séparé plutôt qu'une bordure : le
      // `strokeColor` de la pastille encode déjà la sélection, et sa couleur de
      // remplissage l'état de panne — or la saturation est ORTHOGONALE aux
      // deux (un site dont tout répond peut être saturé). Non cliquable, pour
      // ne pas voler le clic de sélection au marqueur qu'il entoure.
      if (site.saturated) {
        markersRef.current.push(new g.maps.Marker({
          position: { lat: site.latitude!, lng: site.longitude! },
          map,
          clickable: false,
          zIndex: 1,
          icon: {
            path: g.maps.SymbolPath.CIRCLE,
            scale: isSelected ? 17 : 14,
            fillOpacity: 0,
            strokeColor: SATURATION_COLOR,
            strokeOpacity: 0.95,
            strokeWeight: 2.5,
          },
        }))
      }
      const marker = new g.maps.Marker({
        position: { lat: site.latitude!, lng: site.longitude! },
        map,
        title: site.site,
        zIndex: isSelected ? 3 : 2,
        icon: {
          path: g.maps.SymbolPath.CIRCLE,
          scale: isSelected ? 11 : 8,
          fillColor: siteColor(site),
          fillOpacity: 0.95,
          strokeColor: isSelected ? '#1d4ed8' : '#ffffff',
          strokeWeight: isSelected ? 3 : 2,
          // ⚠️ Sans `labelOrigin`, Google dessine l'étiquette CENTRÉE sur le
          // point : le nom du site recouvrirait sa propre pastille et la
          // couleur d'état — la seule information de la carte — deviendrait
          // illisible. Les unités sont celles du symbole (rayon = 1), donc
          // 2,8 × `scale` pixels sous le centre.
          labelOrigin: new g.maps.Point(0, 2.8),
        },
        label: {
          text: site.site,
          color: '#0f172a',
          fontSize: '11px',
          fontWeight: '600',
        },
      })
      marker.addListener('click', () => {
        // Re-cliquer le site sélectionné le désélectionne — sans ça, filtrer
        // sur un site serait un aller sans retour depuis la carte.
        onSelectSite(isSelected ? null : site.site)
        infoRef.current.setContent(siteHtml(site, topo.root))
        infoRef.current.open(map, marker)
      })
      markersRef.current.push(marker)
      bounds.extend(marker.getPosition()!)
    })

    // Cadrage sur ce qui est réellement posé — mais UNE SEULE FOIS. Recadrer à
    // chaque sélection annulerait le zoom et le déplacement de l'opérateur.
    if (!fittedRef.current && !bounds.isEmpty()) {
      map.fitBounds(bounds, 48)
      fittedRef.current = true
    }
  }, [ready, topo, placeable, selectedSite, onSelectSite])

  if (mapsError === 'missing-key') {
    return (
      <Notice>
        <b>Clé Google Maps absente.</b> Renseigne <code>NEXT_PUBLIC_GOOGLE_MAPS_API_KEY</code>{' '}
        puis reconstruis le frontend. Le graphe reste disponible : il ne dépend
        d&apos;aucun service externe.
      </Notice>
    )
  }
  if (mapsError === 'script') {
    return (
      <Notice>
        Le script Google Maps n&apos;a pas pu être chargé. Le navigateur doit
        joindre <code>maps.googleapis.com</code> ; vérifie aussi que la clé
        autorise ce domaine.
      </Notice>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div ref={divRef} className="min-h-0 flex-1 rounded-lg bg-slate-100" />
      {missing.length > 0 && (
        <p className="shrink-0 text-xs text-amber-700">
          {missing.length} site{missing.length > 1 ? 's' : ''} sans position connue,
          donc absent{missing.length > 1 ? 's' : ''} de la carte :{' '}
          <span className="font-medium">{missing.map((s) => s.site).join(', ')}</span>.
          {' '}Leurs liaisons ne sont pas traçables non plus — le graphe, lui, les montre.
        </p>
      )}
    </div>
  )
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
      {children}
    </div>
  )
}

const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

function siteHtml(site: TopologySite, root: string | null): string {
  const rows: string[] = []
  rows.push(
    `<div style="font-weight:600;color:#0f172a">${esc(site.site)}${
      site.site === root ? ' <span style="font-weight:400;color:#64748b">· racine</span>' : ''
    }</div>`,
  )
  // Le compteur « 14/1 » du graphe, mot pour mot : équipements d'infra du site,
  // et combien ne répondent plus.
  rows.push(
    `<div style="color:#475569">Équipements infra : ${site.device_count}` +
      (site.device_down_count > 0
        ? ` · <span style="color:#dc2626;font-weight:600">${site.device_down_count} en panne</span>`
        : '') +
      '</div>',
  )
  rows.push(`<div style="color:#475569">Liaisons : ${site.degree}</div>`)
  // SATURATION — la liaison la plus chargée du site. Affichée SEULEMENT si
  // mesurée : une liaison fibre n'a pas d'occupation, et écrire « 0 % » se
  // lirait « au repos » alors qu'on n'en sait rien.
  if (site.occupancy_pct != null) {
    const sat = site.saturated
    rows.push(
      `<div style="color:${sat ? SATURATION_COLOR : '#475569'};${sat ? 'font-weight:600' : ''}">` +
        `Liaison la plus chargée : ${site.occupancy_pct.toFixed(0)} %` +
        (sat ? ' — saturée' : '') +
        '</div>',
    )
  }
  if (site.position_source === 'manual') {
    rows.push('<div style="color:#64748b;font-size:11px">Position corrigée à la main</div>')
  }
  return `<div style="font-size:12px;line-height:1.6;min-width:170px">${rows.join('')}</div>`
}

function edgeHtml(edge: TopologyEdge): string {
  const rows: string[] = []
  rows.push(
    `<div style="font-weight:600;color:#0f172a">${esc(edge.site_a)} ↔ ${esc(edge.site_b)}</div>`,
  )
  rows.push(
    `<div style="color:#475569">Support : ${
      edge.medium === 'wireless' ? 'radio' : 'fibre / cuivre'
    }</div>`,
  )
  if (edge.redundant) {
    rows.push(
      `<div style="color:#475569">${edge.links.length} liens physiques (redondance)</div>`,
    )
  }
  if (!edge.is_tree_edge) {
    rows.push('<div style="color:#475569">Rôle : boucle de redondance</div>')
  }
  // « Non mesurée » est dit en toutes lettres : un lien qu'on ne mesure pas
  // n'est pas un lien sain, et c'est le mensonge le plus coûteux d'une carte.
  // ⚠️ `degraded` est un verdict à part, PAS une valeur de `state` : il est
  // calculé côté service contre les planchers réels (AF60 1,95 Gb/s, PTP
  // 150 Mb/s) — ne pas le redériver ici d'une capacité et d'un seuil recopiés.
  const { state, degraded } = edge.health
  const label =
    state === 'down' ? 'hors service'
      : state === 'unmeasured' ? 'non mesurée'
      : degraded ? 'dégradée'
      : 'nominale'
  rows.push(`<div style="color:#475569">État : ${label}</div>`)
  // OCCUPATION + la répartition par sens. ⚠️ Nommée PAR LES SITES et non
  // « descendant/montant » : la carte n'a pas la relation parent→enfant sous la
  // main, et ces mots n'ont de sens que vu d'un bout. Le nom du site est
  // toujours exact.
  const { occupancy_pct: occ, saturated } = edge.health
  if (occ != null) {
    rows.push(
      `<div style="color:${saturated ? SATURATION_COLOR : '#475569'};` +
        `${saturated ? 'font-weight:600' : ''}">` +
        `Occupation : ${occ.toFixed(0)} %${saturated ? ' — saturée' : ''}</div>`,
    )
    const ab = edge.health.occupancy_a_to_b_pct
    const ba = edge.health.occupancy_b_to_a_pct
    if (ab != null || ba != null) {
      const pct = (v: number | null) => (v == null ? '—' : `${v.toFixed(0)} %`)
      rows.push(
        `<div style="color:#64748b;font-size:11px">` +
          `→ ${esc(edge.site_b)} ${pct(ab)} · → ${esc(edge.site_a)} ${pct(ba)}</div>`,
      )
    }
  }
  rows.push(
    '<div style="color:#94a3b8;font-size:11px;margin-top:2px">' +
      'Détail complet au survol dans la vue graphe.</div>',
  )
  return `<div style="font-size:12px;line-height:1.6;min-width:190px">${rows.join('')}</div>`
}
