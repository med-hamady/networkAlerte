// Couleurs de la topologie inter-sites — SOURCE UNIQUE, partagée par les deux
// rendus (le graphe SVG et la carte Google Maps).
//
// ⚠️ Ce module existe pour qu'une liaison ait la MÊME couleur dans les deux
// vues. Recopier le barème dans le second rendu les ferait diverger au premier
// ajustement, et deux écrans du même écran de supervision se contrediraient sur
// l'état d'une dorsale — exactement ce que la doc du projet interdit pour les
// seuils de capacité.

import type { TopologyEdge, TopologySite } from '@/lib/types'

/**
 * Couleur d'une liaison — cinq valeurs, dans cet ORDRE DE PRIORITÉ strict.
 *
 *  1. ROUGE  un des deux sites est ENTIÈREMENT tombé. En tête parce qu'une
 *            PANNE ne doit jamais être masquée par une couleur de support ou de
 *            rôle : une dorsale fibre coupée doit crier, pas rester bleue.
 *            ⚠️ Un équipement HS ne met pas un site à terre — peindre en rouge
 *            les liaisons d'un site qui fonctionne enverrait chercher une panne
 *            de backhaul là où il n'y en a pas. La panne isolée reste visible
 *            par le compteur « 14/1 » du nœud.
 *  2. GRIS   boucle de redondance : un chemin de secours, pas la dorsale.
 *  3. BLEU   fibre / cuivre.
 *  4. JAUNE  radio debout mais INERTE — un débit relevé, et proche de zéro.
 *  5. VERT   radio qui écoule du trafic.
 *
 * ⚠️ `traffic === 'unknown'` n'est JAMAIS jaune. Les liaisons fibre ont des
 * switches aux deux bouts et un switch n'expose aucun débit en SNMP : les
 * jaunir signalerait des pannes de trafic permanentes sur la dorsale du HQ, ce
 * qui est faux. Jaune veut dire « mesuré à zéro », jamais « pas mesuré ».
 * (Le point est désormais doublement couvert : ces liaisons sont bleues.)
 */
export function edgeColor(edge: TopologyEdge, downSites: Set<string>): string {
  if (downSites.has(edge.site_a) || downSites.has(edge.site_b)) return '#dc2626'
  if (!edge.is_tree_edge) return '#94a3b8'
  if (edge.medium === 'wired') return '#2563eb'
  if (edge.health.traffic === 'idle') return '#eab308'
  return '#16a34a'
}

/** Les sites ENTIÈREMENT tombés — le seul état qui rougit des liaisons. */
export function downSiteSet(sites: TopologySite[]): Set<string> {
  return new Set(sites.filter(s => s.is_down).map(s => s.site))
}

/**
 * Couleur du marqueur d'un site.
 *
 * Trois états, et la nuance du milieu compte : un site dont UNE partie est en
 * panne n'est pas un site à terre. Le confondre avec le rouge enverrait
 * chercher une panne de site là où un seul secteur est HS — c'est la raison
 * pour laquelle le graphe affiche « 14/1 » plutôt qu'un simple voyant.
 */
export function siteColor(site: TopologySite): string {
  if (site.is_down) return '#dc2626'
  if (site.device_down_count > 0) return '#f59e0b'
  return '#16a34a'
}
