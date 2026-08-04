'use client'

// Rendu SVG du graphe inter-sites, en COUCHES et non en arbre.
//
// Le graphe porte de vraies boucles de redondance (mesurées sur le parc :
// SK1↔CT2 et KS1↔SM1). Un rendu arborescent devrait en jeter une sans le dire —
// exactement le mensonge qu'une supervision ne doit pas produire. On place donc
// les sites par PROFONDEUR (colonnes) en suivant les arêtes d'arbre, puis on
// trace les arêtes hors arbre par-dessus, en pointillé.
//
// La couleur d'un trait vient du backend (`health.state` / `health.degraded`),
// jamais d'un seuil recopié ici : la ligne tracée doit être celle qui déclenche
// l'alerte.

import { useMemo } from 'react'
import type { NetworkTopology, TopologyEdge } from '@/lib/types'

const COL_W = 215      // écart horizontal entre deux couches
const ROW_H = 118      // hauteur d'un emplacement de site (icône + 2 lignes + air)
const ICON = 52        // côté du pylône
const NODE_W = 62      // largeur d'accroche des liaisons (un peu > l'icône)
const NODE_H = 92      // icône + libellé + compteur
const LABEL_W = 130    // largeur réservée au libellé sous l'icône
const PAD = 34

// Le pylône vit dans public/devices/ et NON à la racine de public/ : le
// middleware d'auth intercepte tout sauf `/devices/`, donc une image posée
// ailleurs serait redirigée vers /login et ne s'afficherait jamais.
const ANTENNA_SRC = '/devices/antenne.png'

type Pos = { x: number; y: number }

// Couleurs des liaisons. `unmeasured` est délibérément NEUTRE (gris) et non
// vert : un lien qu'on ne mesure pas n'est pas un lien sain.
function edgeColor(edge: TopologyEdge): string {
  if (edge.health.state === 'down') return '#dc2626'
  if (edge.health.state === 'unmeasured') return '#9ca3af'
  if (edge.health.degraded) return '#f59e0b'
  return '#16a34a'
}

export default function TopologyGraph({
  topo,
  onSelectSite,
  selectedSite,
}: {
  topo: NetworkTopology
  onSelectSite: (site: string) => void
  selectedSite: string | null
}) {
  const { positions, width, height } = useMemo(() => layout(topo), [topo])

  if (!positions.size) {
    return <p className="text-sm text-slate-500">Aucun site à afficher.</p>
  }

  // Les arêtes d'arbre d'abord, les hors-arbre PAR-DESSUS : une boucle doit
  // rester lisible quand elle croise une branche. (`true` trié en premier =
  // dessiné en premier = dessous.)
  const ordered = [...topo.edges].sort(
    (a, b) => Number(b.is_tree_edge) - Number(a.is_tree_edge),
  )

  return (
    <div className="overflow-x-auto">
      <svg width={width} height={height} className="min-w-full" role="img"
           aria-label="Graphe des liaisons entre sites">
        {ordered.map((edge) => {
          const a = positions.get(edge.site_a)
          const b = positions.get(edge.site_b)
          if (!a || !b) return null
          const color = edgeColor(edge)
          const dashed = !edge.is_tree_edge
          const dim = selectedSite != null
            && selectedSite !== edge.site_a && selectedSite !== edge.site_b
          return (
            <g key={`${edge.site_a}|${edge.site_b}`} opacity={dim ? 0.18 : 1}>
              <path
                d={curve(a, b, !edge.is_tree_edge)}
                fill="none"
                stroke={color}
                strokeWidth={edge.redundant ? 3.5 : 2}
                strokeDasharray={dashed ? '7 5' : undefined}
                strokeLinecap="round"
              />
              <title>{edgeTooltip(edge)}</title>
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
              {/* Pastille de sélection derrière le pylône — l'icône n'a pas de
                  cadre, il faut donc un repère visuel qui ne la déforme pas. */}
              {(selected || isRoot) && (
                <circle
                  cx={0} cy={0} r={ICON / 2 + 7}
                  fill={selected ? '#dbeafe' : 'transparent'}
                  stroke={selected ? '#2563eb' : '#93c5fd'}
                  strokeWidth={selected ? 2 : 1.5}
                />
              )}
              <image
                href={ANTENNA_SRC}
                x={-ICON / 2} y={-ICON / 2}
                width={ICON} height={ICON}
                opacity={dim ? 0.35 : 1}
              />
              {/* Cartouche blanc sous le libellé : une liaison qui passe
                  derrière ne doit pas barrer le nom du site. */}
              <rect
                x={-LABEL_W / 2} y={ICON / 2 + 4}
                width={LABEL_W} height={34} rx={4}
                fill="#ffffff" opacity={dim ? 0.55 : 0.92}
              />
              <text x={0} y={ICON / 2 + 18} textAnchor="middle"
                    className={`text-[13px] font-semibold ${
                      selected ? 'fill-blue-700' : dim ? 'fill-slate-400' : 'fill-slate-900'
                    }`}>
                {site.site}
              </text>
              <text x={0} y={ICON / 2 + 33} textAnchor="middle"
                    className={`text-[11px] ${dim ? 'fill-slate-300' : 'fill-slate-500'}`}>
                {site.degree} liaison{site.degree > 1 ? 's' : ''}
                {isRoot ? ' · racine' : ''}
              </text>
            </g>
          )
        })}
      </svg>
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
 *
 * Les tangentes sont horizontales, comme les couches : une liaison sort par le
 * flanc droit d'un site et entre par le flanc gauche du suivant.
 */
function curve(a: Pos, b: Pos, bowOut: boolean): string {
  const [l, r] = a.x <= b.x ? [a, b] : [b, a]
  const half = NODE_W / 2

  // Même colonne : une Bézier horizontale s'effondrerait en trait droit passant
  // sur les nœuds intermédiaires. On sort par la droite des deux et on bombe.
  if (Math.abs(r.x - l.x) < 1) {
    const off = 78
    return `M ${l.x + half} ${l.y} C ${l.x + half + off} ${l.y}, ${r.x + half + off} ${r.y}, ${r.x + half} ${r.y}`
  }

  // Une boucle de redondance relie souvent des rangées éloignées : on écarte
  // davantage les tangentes pour qu'elle contourne les nœuds au lieu de les
  // traverser.
  const span = Math.abs(r.y - l.y)
  const pull = (r.x - l.x) * (bowOut ? 0.75 : 0.45) + (bowOut ? Math.min(span * 0.25, 90) : 0)
  return `M ${l.x + half} ${l.y} C ${l.x + half + pull} ${l.y}, ${r.x - half - pull} ${r.y}, ${r.x - half} ${r.y}`
}

function edgeTooltip(edge: TopologyEdge): string {
  const parts = [`${edge.site_a} ↔ ${edge.site_b}`]
  if (edge.health.state === 'down') parts.push('liaison DOWN')
  else if (edge.health.state === 'unmeasured') parts.push('aucune mesure de notre côté')
  else {
    if (edge.health.capacity_mbps != null) {
      parts.push(`${Math.round(edge.health.capacity_mbps)} Mb/s`)
    }
    if (edge.health.link_potential_pct != null) {
      parts.push(`potentiel ${Math.round(edge.health.link_potential_pct)} %`)
    }
    if (edge.health.degraded) parts.push(`sous le plancher (${edge.health.floor_mbps} Mb/s)`)
  }
  if (edge.redundant) parts.push(`${edge.links.length} liens physiques`)
  if (!edge.is_tree_edge) parts.push('boucle de redondance')
  return parts.join(' · ')
}

/**
 * Place chaque site : x par profondeur, y par rangement des feuilles.
 *
 * Les sites NON atteints depuis la racine (composante séparée) sont posés dans
 * une bande sous le graphe principal plutôt qu'omis : les cacher les ferait
 * passer pour inexistants, alors qu'ils sont réels et injoignables.
 */
function layout(topo: NetworkTopology) {
  const positions = new Map<string, Pos>()
  const children = new Map<string, string[]>()
  for (const site of topo.sites) {
    if (site.parent) {
      children.set(site.parent, [...(children.get(site.parent) ?? []), site.site])
    }
  }

  let cursor = 0
  const place = (site: string): number => {
    const kids = (children.get(site) ?? []).slice().sort()
    const depth = topo.sites.find(s => s.site === site)?.depth ?? 0
    let y: number
    if (kids.length === 0) {
      y = cursor * ROW_H
      cursor += 1
    } else {
      const ys = kids.map(place)
      y = (Math.min(...ys) + Math.max(...ys)) / 2
    }
    // y repéré sur le CENTRE DE L'ICÔNE (et non du nœud entier) : c'est là que
    // les liaisons s'accrochent, elles doivent viser le pylône, pas le libellé.
    positions.set(site, { x: PAD + ICON / 2 + depth * COL_W, y: y + PAD + ICON / 2 })
    return y
  }
  if (topo.root) place(topo.root)

  // Composantes séparées / sites non atteints : une bande dédiée en dessous.
  const stray = topo.sites.filter(s => !positions.has(s.site))
  if (stray.length) {
    cursor += 1
    for (const site of stray) {
      positions.set(site.site, {
        x: PAD + ICON / 2,
        y: cursor * ROW_H + PAD + ICON / 2,
      })
      cursor += 1
    }
  }

  const maxDepth = Math.max(0, ...topo.sites.map(s => s.depth ?? 0))
  return {
    positions,
    // La largeur réservée à une colonne est celle du LIBELLÉ (plus large que
    // l'icône), sinon le dernier nom déborderait du cadre SVG.
    width: PAD * 2 + LABEL_W + maxDepth * COL_W,
    height: PAD * 2 + Math.max(0, cursor - 1) * ROW_H + NODE_H,
  }
}
