'use client'

// Rendu SVG du graphe inter-sites, en COUCHES et non en arbre.
//
// Le graphe porte de vraies boucles de redondance (mesurées sur le parc :
// SK1↔CT2 et KS1↔SM1). Un rendu arborescent devrait en jeter une sans le dire —
// exactement le mensonge qu'une supervision ne doit pas produire. On place donc
// les sites par PROFONDEUR (colonnes) en suivant les arêtes d'arbre, puis on
// trace les arêtes hors arbre par-dessus, en pointillé.
//
// ⚠️ LE DESSIN S'ADAPTE À L'ÉCRAN, IL N'EST JAMAIS MIS À L'ÉCHELLE.
// La disposition est calculée en unités (colonne, rangée) puis convertie en
// pixels d'après la place réellement mesurée : les colonnes s'étalent sur la
// largeur, les rangées sur la hauteur, et le pylône prend la plus grande taille
// qui tienne dans une rangée.
//
// C'est le contraire d'un `transform: scale()` sur le SVG entier. Réduire tout
// le dessin pour le faire tenir en hauteur rétrécit aussi sa largeur dans la
// même proportion : sur un écran large et peu haut — le cas normal — on obtient
// un graphe minuscule au milieu d'une moitié d'écran vide. Ici les deux
// dimensions sont remplies indépendamment.

import { useEffect, useMemo, useRef, useState } from 'react'
import type { NetworkTopology, TopologyEdge, TopologyPort } from '@/lib/types'
// Barème de couleurs partagé avec la vue CARTE — une liaison doit être de la
// même couleur dans les deux rendus (cf. lib/topologyColors).
import { SATURATION_COLOR, downSiteSet, edgeColor } from '@/lib/topologyColors'

// Bornes de la rangée. Le minimum évite l'illisible sur un parc très profond
// (mieux vaut défiler que rendre les libellés illisibles) ; le maximum évite un
// dessin caricatural sur un grand écran avec peu de sites.
const ROW_H_MIN = 40
const ROW_H_MAX = 96
const PAD = 16

// Part de la rangée occupée par la carte d'un site ; le reste est l'air entre
// deux sites voisins.
const NODE_RATIO = 0.82

// ⚠️ Écart MAXIMAL entre deux colonnes, au-delà de la carte elle-même.
// Étaler les colonnes sur toute la largeur disponible est un piège : l'espace
// gagné part intégralement dans la LONGUEUR DES TRAITS, pendant que les nœuds
// restent minuscules (ils sont bridés par la hauteur, pas par la largeur). On
// borne donc l'écart et on CENTRE le graphe — c'est ce que fait le contrôleur.
const COL_GAP_MAX = 130
const COL_GAP_MIN = 44

// Le pylône vit dans public/devices/ et NON à la racine de public/ : le
// middleware d'auth intercepte tout sauf `/devices/`, donc une image posée
// ailleurs serait redirigée vers /login et ne s'afficherait jamais.
const ANTENNA_SRC = '/devices/antenne.png'

type Pos = { x: number; y: number }

/** Tailles dérivées de la place disponible — tout le rendu s'y réfère. */
type Geom = {
  rowH: number
  colW: number
  nodeW: number
  nodeH: number
  icon: number
  nameFs: number
  countFs: number
  offsetX: number  // centrage horizontal du graphe
  width: number
  height: number
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

export default function TopologyGraph({
  topo,
  onSelectSite,
  selectedSite,
}: {
  topo: NetworkTopology
  onSelectSite: (site: string) => void
  selectedSite: string | null
}) {
  const hostRef = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState({ w: 0, h: 0 })
  const [hover, setHover] = useState<{ edge: TopologyEdge; x: number; y: number } | null>(null)

  // Mesure réelle du conteneur : seule façon de remplir la place dont on
  // dispose, qui change avec le repli du menu et la taille de la fenêtre.
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const update = () => setViewport({ w: host.clientWidth, h: host.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(host)
    return () => observer.disconnect()
  }, [])

  // Disposition en UNITÉS (colonne, rangée), indépendante des pixels.
  const { slots, rows, maxDepth } = useMemo(() => layoutUnits(topo), [topo])

  const g = useMemo<Geom>(() => {
    const availW = viewport.w || 1200
    const availH = viewport.h || 600

    // Hauteur d'une rangée telle que le graphe ENTIER tienne dans la place
    // disponible. Diviser bêtement la hauteur par le nombre de rangées ne suffit
    // pas : le dernier site ajoute encore sa carte SOUS la dernière rangée, plus
    // les marges — d'où un débordement d'une trentaine de pixels, donc un
    // défilement, pour un graphe censé tenir.
    const denom = Math.max(rows - 1, 0) + NODE_RATIO
    const rowH = clamp(
      (availH - 2 * PAD) / Math.max(denom, 1),
      ROW_H_MIN, ROW_H_MAX,
    )

    // ⚠️ Le pylône est À GAUCHE du texte, pas au-dessus. Empilés, l'icône et
    // les deux lignes doublaient la hauteur d'un nœud — or c'est la HAUTEUR qui
    // est rare (une rangée par site feuille) et la largeur qui est abondante.
    // Côte à côte, la carte tient dans une demi-rangée et la police peut monter
    // de ~8 px à ~11 px à hauteur d'écran égale.
    const nodeH = rowH * NODE_RATIO
    const icon = nodeH * 0.74
    const nameFs = clamp(nodeH * 0.30, 9, 14)
    const countFs = nameFs * 0.78
    const nodeW = icon + nameFs * 9.5 + 24

    // Écart borné, puis centrage : le surplus de largeur ne doit pas allonger
    // les traits.
    const colW = clamp(
      (availW - nodeW) / Math.max(maxDepth, 1),
      nodeW + COL_GAP_MIN,
      nodeW + COL_GAP_MAX,
    )
    const graphW = nodeW + maxDepth * colW
    return {
      rowH, colW, nodeW, nodeH, icon, nameFs, countFs,
      offsetX: Math.max(PAD, (availW - graphW) / 2),
      width: Math.max(availW, graphW + 2 * PAD),
      height: PAD * 2 + nodeH + Math.max(0, rows - 1) * rowH,
    }
  }, [viewport, rows, maxDepth])

  const positions = useMemo(() => {
    const out = new Map<string, Pos>()
    for (const [site, slot] of slots) {
      out.set(site, {
        x: g.offsetX + g.nodeW / 2 + slot.depth * g.colW,
        y: PAD + g.nodeH / 2 + slot.row * g.rowH,
      })
    }
    return out
  }, [slots, g])

  // Points d'attache répartis sur le bord des cartes (cf. `spreadPorts`).
  const ports = useMemo(() => spreadPorts(topo, positions, g), [topo, positions, g])

  // Les arêtes d'arbre d'abord, les hors-arbre PAR-DESSUS : une boucle doit
  // rester lisible quand elle croise une branche. (`true` trié en premier =
  // dessiné en premier = dessous.) On garde l'index d'origine : c'est la clé
  // des points d'attache.
  const ordered = topo.edges
    .map((edge, index) => ({ edge, index }))
    .sort((a, b) => Number(b.edge.is_tree_edge) - Number(a.edge.is_tree_edge))
  const downSites = downSiteSet(topo.sites)
  const parentOf = new Map(topo.sites.map(s => [s.site, s.parent]))

  // Position de la carte de survol, relative au conteneur (et non à la page) :
  // le conteneur défile, une position absolue en coordonnées de page dériverait.
  const track = (edge: TopologyEdge) => (e: React.MouseEvent) => {
    const host = hostRef.current
    if (!host) return
    const box = host.getBoundingClientRect()
    setHover({
      edge,
      x: e.clientX - box.left + host.scrollLeft,
      y: e.clientY - box.top + host.scrollTop,
    })
  }

  if (!slots.size) {
    return <p className="text-sm text-slate-500">Aucun site à afficher.</p>
  }

  return (
    <div ref={hostRef} className="relative h-full w-full overflow-auto">
      <svg width={g.width} height={g.height} role="img"
           aria-label="Graphe des liaisons entre sites">
        {ordered.map(({ edge, index }) => {
          const a = positions.get(edge.site_a)
          const b = positions.get(edge.site_b)
          if (!a || !b) return null
          const dim = selectedSite != null
            && selectedSite !== edge.site_a && selectedSite !== edge.site_b
          // Le STYLE du trait dit le SUPPORT : radio en tirets, fibre/cuivre en
          // trait plein. Les boucles de redondance, elles, se lisent maintenant
          // à leur COULEUR (gris) — les tirets ne les distinguaient plus, toutes
          // étant radio.
          const dyL = ports.get(`${index}:L`) ?? 0
          const dyR = ports.get(`${index}:R`) ?? 0
          const ends = endpoints(a, b, g, dyL, dyR)
          // `device_a` est l'extrémité de `site_a` (garanti par build_edges) ;
          // reste à savoir laquelle des deux est dessinée à gauche.
          const aIsLeft = a.x <= b.x
          const portFrom = aIsLeft ? edge.port_a : edge.port_b
          const portTo = aIsLeft ? edge.port_b : edge.port_a
          return (
            <g key={`${edge.site_a}|${edge.site_b}`} opacity={dim ? 0.18 : 1}>
              <path
                d={curve(a, b, !edge.is_tree_edge, g, dyL, dyR)}
                fill="none"
                stroke={edgeColor(edge, downSites)}
                strokeWidth={edge.redundant ? 3.5 : 2}
                strokeDasharray={edge.medium === 'wireless' ? '7 5' : undefined}
                strokeLinecap="round"
              />
              {/* Doublure de DÉTECTION — invisible par construction
                  (`stroke="transparent"`, aucun tiret) : elle ne change pas un
                  pixel du dessin.
                  ⚠️ Elle est indispensable, pas confortable. Le navigateur ne
                  détecte le survol d'un trait pointillé que sur ses segments
                  PEINTS, jamais dans les vides — et 15 des 18 liaisons sont en
                  tirets, épaisses de 2 px. Sans elle, la cible réelle fait ~7 px
                  sur 12, hauts de 2 : le survol paraît simplement mort.
                  9 px et non 14 : assez pour viser, assez étroit pour ne pas
                  capter la liaison voisine là où deux se croisent. */}
              <path
                d={curve(a, b, !edge.is_tree_edge, g, dyL, dyR)}
                fill="none" stroke="transparent" strokeWidth={9}
                className="cursor-pointer"
                onMouseEnter={track(edge)}
                onMouseMove={track(edge)}
                onMouseLeave={() => setHover(null)}
              />
              <PortDot at={ends.from} port={portFrom} g={g} />
              <PortDot at={ends.to} port={portTo} g={g} />
            </g>
          )
        })}

        {topo.sites.filter(s => positions.has(s.site)).map((site) => {
          const p = positions.get(site.site)!
          const isRoot = site.site === topo.root
          const selected = selectedSite === site.site
          const dim = selectedSite != null && !selected
          return (
            <g
              key={site.site}
              transform={`translate(${p.x}, ${p.y})`}
              onClick={() => onSelectSite(site.site)}
              className="cursor-pointer"
            >
              {/* Carte opaque : elle sert de repère de sélection ET masque les
                  liaisons qui passeraient derrière le texte. */}
              <rect
                x={-g.nodeW / 2} y={-g.nodeH / 2}
                width={g.nodeW} height={g.nodeH} rx={7}
                fill={selected ? '#dbeafe' : '#ffffff'}
                stroke={selected ? '#2563eb'
                  : site.is_down ? '#fca5a5'
                  : isRoot ? '#93c5fd'
                  : '#e2e8f0'}
                strokeWidth={selected || isRoot ? 1.8 : 1.2}
                opacity={dim ? 0.5 : 1}
              />
              <image
                href={ANTENNA_SRC}
                x={-g.nodeW / 2 + 7} y={-g.icon / 2}
                width={g.icon} height={g.icon}
                opacity={dim ? 0.35 : 1}
              />
              {/* SATURATION — anneau violet autour de l'icône.
                  ⚠️ Canal visuel SÉPARÉ, et c'est le point : la saturation est
                  orthogonale à la disponibilité (un site dont tout répond peut
                  être saturé — c'est même LE cas cherché). La bordure de la
                  carte encode déjà sélection/panne/racine et la couleur du nom
                  la panne : y ajouter la saturation ferait que chaque état
                  masquerait l'autre. L'anneau ceint l'icône, dont la position
                  est fixe — il ne peut donc jamais chevaucher un nom long. */}
              {site.saturated && (
                <circle
                  cx={-g.nodeW / 2 + 7 + g.icon / 2} cy={0} r={g.icon * 0.62}
                  fill="none" stroke={SATURATION_COLOR}
                  strokeWidth={dim ? 1.2 : 2}
                  opacity={dim ? 0.4 : 1}
                />
              )}
              {/* Un site ENTIÈREMENT tombé porte son nom en rouge — même
                  critère que la couleur de ses liaisons, pour qu'on ne cherche
                  pas deux lectures différentes de la même panne. */}
              <text
                x={-g.nodeW / 2 + g.icon + 13} y={-1}
                fontSize={g.nameFs} fontWeight={600}
                fill={selected ? '#1d4ed8'
                  : dim ? '#94a3b8'
                  : site.is_down ? '#dc2626'
                  : '#0f172a'}
              >
                {site.site}
              </text>
              {/* Compteur d'équipements façon contrôleur : « 14/1 », la part
                  en panne en rouge. C'est ce qui garde une panne isolée
                  visible alors que la liaison, elle, reste verte. */}
              <text
                x={-g.nodeW / 2 + g.icon + 13} y={g.countFs + 4}
                fontSize={g.countFs} fill={dim ? '#cbd5e1' : '#64748b'}
              >
                <tspan>{site.device_count}</tspan>
                {site.device_down_count > 0 && (
                  <tspan fill={dim ? '#fca5a5' : '#dc2626'}>
                    /{site.device_down_count}
                  </tspan>
                )}
                {/* Le CHIFFRE de la saturation, dans le même flux de texte que
                    le compteur : aucune collision possible avec un nom long, et
                    l'anneau seul ne dirait pas à quel point c'est plein. */}
                {site.saturated && site.occupancy_pct != null && (
                  <tspan fill={dim ? '#c4b5fd' : SATURATION_COLOR} fontWeight={700}>
                    {' · '}{site.occupancy_pct.toFixed(0)} %
                  </tspan>
                )}
                {isRoot && <tspan> · racine</tspan>}
              </text>
              <title>
                {site.saturated && site.occupancy_pct != null
                  ? `${site.site} — liaison saturée : `
                    + `${site.occupancy_pct.toFixed(0)} % du temps d'antenne. `
                    + `Survoler ses traits pour voir laquelle.`
                  : site.site}
              </title>
            </g>
          )
        })}
      </svg>

      {hover && (
        <HoverCard
          x={hover.x} y={hover.y}
          edge={hover.edge}
          parentOf={parentOf}
          host={hostRef.current}
        />
      )}
    </div>
  )
}

/**
 * Carte de survol d'une liaison — le détail que la carte épurée ne montre pas.
 *
 * `pointer-events-none` est essentiel : sans lui, la carte passe sous le
 * curseur, déclenche le `mouseleave` du trait, disparaît, ce qui la remet sous
 * le curseur… soit un clignotement perpétuel.
 */
function HoverCard({ x, y, edge, parentOf, host }: {
  x: number
  y: number
  edge: TopologyEdge
  parentOf: Map<string, string | null>
  host: HTMLDivElement | null
}) {
  const rows = edgeDetails(edge, parentOf)
  // Bascule à gauche / au-dessus quand on approche du bord, sinon la carte
  // sort du cadre et devient illisible près des sites de la dernière colonne.
  const flipX = host ? x - host.scrollLeft > host.clientWidth - 240 : false
  const flipY = host ? y - host.scrollTop > host.clientHeight - 190 : false

  return (
    <div
      className="pointer-events-none absolute z-30 w-56 rounded-lg border
                 border-slate-200 bg-white/95 p-2.5 text-xs shadow-lg backdrop-blur"
      style={{
        left: flipX ? undefined : x + 14,
        right: flipX && host ? host.scrollWidth - x + 14 : undefined,
        top: flipY ? undefined : y + 14,
        bottom: flipY && host ? host.scrollHeight - y + 14 : undefined,
      }}
    >
      <div className="mb-1.5 font-semibold text-slate-900">
        {edge.site_a} <span className="text-slate-400">↔</span> {edge.site_b}
      </div>
      <dl className="space-y-0.5">
        {rows.map(row => (
          <div key={row.label} className="flex justify-between gap-3">
            <dt className="text-slate-500">{row.label}</dt>
            <dd className={`text-right font-medium ${
              row.tone === 'down' ? 'text-red-600'
                : row.tone === 'muted' ? 'text-slate-400'
                : 'text-slate-800'
            }`}>
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/**
 * Tracé d'une liaison, toujours du nœud le PLUS À GAUCHE vers le plus à droite.
 *
 * ⚠️ Réordonner par x n'est pas cosmétique. Les arêtes arrivent triées par ordre
 * ALPHABÉTIQUE de nom de site, pas par position : pour « A2 CT2 ↔ A2 PK1 »,
 * `site_a` est CT2 (colonne 3) et `site_b` est PK1 (colonne 2). Tracer de a vers
 * b partait alors vers la droite depuis le nœud de droite puis revenait — une
 * grande boucle en S qui traversait la moitié du graphe. Trois liaisons du parc
 * étaient dans ce cas.
 */
/**
 * Pastille de PORT à l'extrémité d'une liaison : la vitesse négociée du port de
 * switch sur lequel cet équipement est câblé.
 *
 *   VERT   ≥ 1 Gb/s — la vitesse attendue sur un backhaul.
 *   JAUNE  100 Mb/s — le port est monté, mais dix fois trop lent.
 *   ROUGE  < 100 Mb/s (10 Mb/s) — pire encore.
 *
 * Ce n'est pas décoratif : le parc porte de vrais backhauls **bloqués à
 * 100 Mb/s alors qu'ils devraient être en gigabit** (`A2-AT2-SUD1`,
 * `A2-NR1-NORD`, `A2-SM1-OUEST`, `F60 CT1-NR1`, relevés le 2026-07-30). Une
 * carte qui ne montre que haut/bas ne peut pas le révéler.
 *
 * ⚠️ Pas de port connu, ou port sans vitesse lue ⇒ **AUCUNE pastille**. Les
 * liaisons fibre sont dans ce cas (leurs deux bouts sont des switches, dont
 * l'uplink inter-switch n'est volontairement pas attribué). Une pastille grise
 * « indéterminée » se lirait comme un diagnostic ; l'absence, non.
 */
function PortDot({ at, port, g }: { at: Pos; port: TopologyPort | null; g: Geom }) {
  if (!port || port.speed_mbps == null) return null
  const speed = port.speed_mbps
  const color = speed >= 1000 ? '#16a34a' : speed >= 100 ? '#eab308' : '#dc2626'
  const r = clamp(g.nodeH * 0.11, 3, 5.5)
  return (
    <circle
      cx={at.x} cy={at.y} r={r}
      fill={color} stroke="#ffffff" strokeWidth={1.2}
    >
      <title>
        {`Port ${port.port}${port.switch ? ` · ${port.switch}` : ''} — ${
          speed >= 1000 ? `${(speed / 1000).toFixed(0)} Gb/s` : `${speed.toFixed(0)} Mb/s`
        }`}
      </title>
    </circle>
  )
}

/** Les deux points d'accroche du trait — partagés par le tracé et les pastilles
 *  de port, pour qu'une pastille tombe exactement là où le câble sort. */
function endpoints(a: Pos, b: Pos, g: Geom, dyLeft: number, dyRight: number) {
  const [l, r] = a.x <= b.x ? [a, b] : [b, a]
  const half = g.nodeW / 2
  const sameColumn = Math.abs(r.x - l.x) < 1
  return {
    from: { x: l.x + half, y: l.y + dyLeft },
    // Même colonne : le tracé contourne par la droite, les deux accroches sont
    // donc sur ce bord-là.
    to: { x: sameColumn ? r.x + half : r.x - half, y: r.y + dyRight },
  }
}

function curve(
  a: Pos, b: Pos, bowOut: boolean, g: Geom, dyLeft: number, dyRight: number,
): string {
  const [l, r] = a.x <= b.x ? [a, b] : [b, a]
  const half = g.nodeW / 2
  // Chaque liaison a SON point d'attache sur le bord de la carte (cf.
  // `spreadPorts`) : sans ça les 5 liaisons du HQ sortaient toutes du même
  // pixel et se confondaient sur leurs premiers 50 px.
  const ly = l.y + dyLeft
  const ry = r.y + dyRight

  // Même colonne : une Bézier horizontale s'effondrerait en trait droit passant
  // sur les nœuds intermédiaires. On sort par la droite des deux et on bombe.
  if (Math.abs(r.x - l.x) < 1) {
    const off = Math.max(50, g.colW * 0.35)
    return `M ${l.x + half} ${ly} C ${l.x + half + off} ${ly}, ${r.x + half + off} ${ry}, ${r.x + half} ${ry}`
  }

  // Une boucle de redondance relie souvent des rangées éloignées : on écarte
  // davantage les tangentes pour qu'elle contourne les nœuds au lieu de les
  // traverser.
  const span = Math.abs(ry - ly)
  const pull = (r.x - l.x) * (bowOut ? 0.75 : 0.45)
    + (bowOut ? Math.min(span * 0.25, 90) : 0)
  return `M ${l.x + half} ${ly} C ${l.x + half + pull} ${ly}, ${r.x - half - pull} ${ry}, ${r.x - half} ${ry}`
}

/**
 * Répartit les points d'attache sur le bord vertical des cartes.
 *
 * Un site concentrateur porte plusieurs liaisons — cinq pour le HQ. Toutes
 * accrochées au milieu du bord, elles sortent du même pixel et restent
 * confondues sur leurs premiers 50 px : impossible de suivre une branche à
 * l'œil, ni de voir laquelle est rouge quand deux se superposent.
 *
 * Chaque liaison reçoit donc sa propre ordonnée sur le bord, et les attaches
 * sont **triées par la position de l'autre extrémité** : celle qui monte sort
 * par le haut, celle qui descend par le bas. Trier autrement (par nom, par
 * ordre d'arrivée) ferait se croiser les traits juste devant la carte, ce qui
 * est exactement le désordre qu'on cherche à supprimer.
 *
 * Renvoie une clé par extrémité : `${index}:L` pour le nœud de gauche,
 * `${index}:R` pour celui de droite (l'index est celui de `topo.edges`).
 */
function spreadPorts(
  topo: NetworkTopology, positions: Map<string, Pos>, g: Geom,
): Map<string, number> {
  type Port = { key: string; otherY: number }
  const rightEdgeOf = new Map<string, Port[]>()
  const leftEdgeOf = new Map<string, Port[]>()
  const push = (m: Map<string, Port[]>, site: string, port: Port) => {
    const list = m.get(site)
    if (list) list.push(port)
    else m.set(site, [port])
  }

  topo.edges.forEach((edge, index) => {
    const a = positions.get(edge.site_a)
    const b = positions.get(edge.site_b)
    if (!a || !b) return
    const aIsLeft = a.x <= b.x
    const leftSite = aIsLeft ? edge.site_a : edge.site_b
    const rightSite = aIsLeft ? edge.site_b : edge.site_a
    const leftPos = aIsLeft ? a : b
    const rightPos = aIsLeft ? b : a
    push(rightEdgeOf, leftSite, { key: `${index}:L`, otherY: rightPos.y })
    // Même colonne : les deux extrémités sortent par la DROITE (le tracé
    // contourne par la droite), donc les deux ports vivent sur ce bord-là.
    const sameColumn = Math.abs(a.x - b.x) < 1
    push(sameColumn ? rightEdgeOf : leftEdgeOf, rightSite,
         { key: `${index}:R`, otherY: leftPos.y })
  })

  const out = new Map<string, number>()
  // Bande d'attache : un peu moins que la hauteur de carte, pour que les
  // traits restent sur le bord et n'en débordent pas.
  const band = g.nodeH * 0.62
  for (const map of [rightEdgeOf, leftEdgeOf]) {
    for (const list of map.values()) {
      if (list.length < 2) continue   // une seule liaison → au milieu du bord
      list.sort((p, q) => p.otherY - q.otherY)
      const step = Math.min(9, band / (list.length - 1))
      const start = -(step * (list.length - 1)) / 2
      list.forEach((port, i) => out.set(port.key, start + i * step))
    }
  }
  return out
}

function fmtRate(mbps: number): string {
  return mbps >= 1000 ? `${(mbps / 1000).toFixed(2)} Gb/s` : `${mbps.toFixed(1)} Mb/s`
}

/**
 * Contenu de la carte de survol d'une liaison.
 *
 * Le débit est donné **par direction**. ⚠️ « Descendant / montant » n'a de sens
 * que par rapport à l'amont : on ne l'emploie donc que lorsque le graphe connaît
 * la relation parent→enfant (arête d'arbre). Sur une boucle de redondance, il
 * n'y a pas d'amont — les deux sens sont alors nommés par les sites, ce qui est
 * toujours exact.
 */
function edgeDetails(edge: TopologyEdge, parentOf: Map<string, string | null>) {
  const rows: { label: string; value: string; tone?: 'down' | 'muted' }[] = []

  // Le parent est l'amont : ce qui en descend est le « descendant ». Résolu une
  // seule fois — le débit ET l'occupation doivent nommer leurs sens de la MÊME
  // façon, sans quoi la carte de survol se lirait à l'envers d'une ligne à
  // l'autre.
  const aIsParent = parentOf.get(edge.site_b) === edge.site_a
  const bIsParent = parentOf.get(edge.site_a) === edge.site_b

  const aToB = edge.health.traffic_a_to_b_mbps
  const bToA = edge.health.traffic_b_to_a_mbps
  if (edge.health.traffic === 'unknown') {
    rows.push({ label: 'Trafic', value: 'non mesuré', tone: 'muted' })
  } else {
    const down = aIsParent ? aToB : bIsParent ? bToA : null
    const up = aIsParent ? bToA : bIsParent ? aToB : null
    if (down !== null || up !== null) {
      rows.push({ label: '↓ Descendant', value: down == null ? '—' : fmtRate(down) })
      rows.push({ label: '↑ Montant', value: up == null ? '—' : fmtRate(up) })
    } else {
      rows.push({
        label: `→ ${edge.site_b}`,
        value: aToB == null ? '—' : fmtRate(aToB),
      })
      rows.push({
        label: `→ ${edge.site_a}`,
        value: bToA == null ? '—' : fmtRate(bToA),
      })
    }
  }

  if (edge.health.capacity_mbps != null) {
    rows.push({ label: 'Capacité', value: fmtRate(edge.health.capacity_mbps) })
  }
  // OCCUPATION — la seule ligne qui distingue « ça passe » de « c'est plein ».
  // Un backhaul de secours qui encaisse toute une branche affiche un trafic
  // élevé ET une pleine capacité : rien d'autre dans cette infobulle ne révèle
  // qu'il est à bout de souffle. Absente si non mesurée (les liaisons fibre
  // n'ont pas d'occupation) — ne jamais afficher 0 %, qui se lirait « au repos ».
  if (edge.health.occupancy_pct != null) {
    rows.push({
      label: 'Occupation',
      value: `${edge.health.occupancy_pct.toFixed(0)} %`,
      tone: edge.health.saturated ? 'down' : undefined,
    })
    // PAR QUEL BOUT le lien se remplit. Le total dit qu'il est plein, cette
    // ligne dit à cause de quoi — 89/5 et 47/47 n'appellent pas le même geste.
    // Sens nommés exactement comme le trafic juste au-dessus (descendant/montant
    // sur une arête d'arbre, par site sur une boucle sans amont).
    const occAB = edge.health.occupancy_a_to_b_pct
    const occBA = edge.health.occupancy_b_to_a_pct
    if (occAB != null || occBA != null) {
      const dn = aIsParent ? occAB : bIsParent ? occBA : null
      const up = aIsParent ? occBA : bIsParent ? occAB : null
      const pct = (v: number | null) => (v == null ? '—' : `${v.toFixed(0)} %`)
      rows.push(
        dn !== null || up !== null
          ? { label: '· dont ↓ / ↑', value: `${pct(dn)} / ${pct(up)}`, tone: 'muted' }
          : {
              label: `· dont → ${edge.site_b} / → ${edge.site_a}`,
              value: `${pct(occAB)} / ${pct(occBA)}`,
              tone: 'muted',
            },
      )
    }
  }
  rows.push({
    label: 'Support',
    value: edge.medium === 'wireless' ? 'radio' : 'fibre / cuivre',
  })
  for (const side of ['port_a', 'port_b'] as const) {
    const port = edge[side]
    if (!port?.port) continue
    const site = side === 'port_a' ? edge.site_a : edge.site_b
    rows.push({
      label: `Port ${site}`,
      value: `n° ${port.port}${port.speed_mbps ? ` · ${fmtRate(port.speed_mbps)}` : ''}`,
    })
  }
  const down = [...new Set(edge.links.flatMap(l =>
    [l.device_a, l.device_b].filter(d => d.status === 'down').map(d => d.name)))]
  if (down.length) rows.push({ label: 'Hors ligne', value: down.join(', '), tone: 'down' })
  if (edge.redundant) {
    rows.push({ label: 'Redondance', value: `${edge.links.length} liens physiques` })
  }
  // Les tirets ne distinguent plus les boucles : la carte prend le relais.
  if (!edge.is_tree_edge) rows.push({ label: 'Rôle', value: 'boucle de redondance' })
  return rows
}

/**
 * Place chaque site en UNITÉS : sa colonne (profondeur) et sa rangée (un réel,
 * les parents étant centrés sur leurs enfants). Aucun pixel ici — c'est ce qui
 * permet au rendu de remplir l'écran sans redessiner la disposition.
 *
 * Les sites NON atteints depuis la racine (composante séparée) sont posés dans
 * une bande sous le graphe principal plutôt qu'omis : les cacher les ferait
 * passer pour inexistants, alors qu'ils sont réels et injoignables.
 */
function layoutUnits(topo: NetworkTopology) {
  const slots = new Map<string, { depth: number; row: number }>()
  const children = new Map<string, string[]>()
  const depthOf = new Map<string, number>()
  for (const site of topo.sites) {
    depthOf.set(site.site, site.depth ?? 0)
    if (site.parent) {
      children.set(site.parent, [...(children.get(site.parent) ?? []), site.site])
    }
  }

  let cursor = 0
  const place = (site: string): number => {
    const kids = (children.get(site) ?? []).slice().sort()
    let row: number
    if (kids.length === 0) {
      row = cursor
      cursor += 1
    } else {
      const rowsOfKids = kids.map(place)
      row = (Math.min(...rowsOfKids) + Math.max(...rowsOfKids)) / 2
    }
    slots.set(site, { depth: depthOf.get(site) ?? 0, row })
    return row
  }
  if (topo.root) place(topo.root)

  for (const site of topo.sites) {
    if (!slots.has(site.site)) {
      slots.set(site.site, { depth: 0, row: cursor })
      cursor += 1
    }
  }

  return {
    slots,
    rows: Math.max(cursor, 1),
    maxDepth: Math.max(0, ...topo.sites.map(s => s.depth ?? 0)),
  }
}
