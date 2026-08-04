'use client'

// Rendu de la topologie inter-sites — la partie PRÉSENTATION, séparée du fetch.
//
// Volontairement dépouillée : le graphe, et c'est tout. Pas de tuiles de
// comptage, pas de légende, pas de liste des liaisons — la carte se lit d'un
// coup d'œil, et le détail d'une liaison est au survol du trait.
//
// La couleur ne code qu'une chose : l'équipement répond ou ne répond pas
// (cf. `edgeColor` dans TopologyGraph). Les notions de capacité et de plancher
// vivent sur /lr-health, section « Liaisons entre sites » — les afficher ici
// aussi obligeait à une légende à cinq entrées pour un écran de vue d'ensemble.

import { useState } from 'react'
import type { NetworkTopology, TopologyEdge } from '@/lib/types'
import TopologyGraph from '@/components/TopologyGraph'

export default function TopologyView({ topo }: { topo: NetworkTopology }) {
  const [selectedSite, setSelectedSite] = useState<string | null>(null)

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <h1 className="text-xl font-bold text-slate-900">Topologie du réseau</h1>
        {selectedSite && (
          <button onClick={() => setSelectedSite(null)}
                  className="text-xs text-blue-700 hover:underline shrink-0">
            Tout afficher
          </button>
        )}
      </div>

      <section className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <TopologyGraph topo={topo} onSelectSite={setSelectedSite} selectedSite={selectedSite} />

        {/* Deux fraîcheurs, et il faut le dire : le câblage date du dernier
            rapatriement, l'état des équipements est de maintenant. Une seule
            date laisserait croire que tout l'écran a le même âge. */}
        <p className="text-xs text-slate-400">
          Racine : {topo.root} ({topo.root_source}). Le lien Internet→HQ
          n&apos;existe pas dans le contrôleur — la racine est un réglage, pas une
          déduction. Câblage rapatrié le {formatSynced(topo.synced_at)} ; état des
          équipements relevé en direct.
        </p>
      </section>

      <Anomalies layout={topo.layout} stats={topo.stats} />
    </div>
  )
}

function formatSynced(iso: string | null | undefined): string {
  if (!iso) return 'jamais'
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? 'jamais'
    : d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
}

// Ce que le graphe ne peut PAS dire tout seul : un site flottant se lit comme un
// site sans panne. On les nomme — c'est la seule chose qui reste sous la carte,
// parce qu'elle n'est pas déductible du dessin.
function Anomalies({ layout, stats }: {
  layout: NetworkTopology['layout']
  stats: NetworkTopology['stats']
}) {
  const blocks: { title: string; body: React.ReactNode }[] = []

  if (layout.orphan_sites.length) {
    blocks.push({
      title: `${layout.orphan_sites.length} site(s) sans aucune liaison`,
      body: <>Aucun backhaul provisionné dans UISP les concernant — dessinés
        flottants, ils se liraient comme « pas de panne » : {layout.orphan_sites.join(', ')}</>,
    })
  }
  if (layout.components.length > 1) {
    blocks.push({
      title: `${layout.components.length} composantes séparées`,
      body: <>Le graphe ne se dessine pas d&apos;un seul tenant :{' '}
        {layout.components.map(g => `${g.length} site(s) (${g.join(', ')})`).join(' — ')}</>,
    })
  }
  if (stats.unsupervised_ends.length) {
    blocks.push({
      title: `${stats.unsupervised_ends.length} extrémité(s) non supervisée(s)`,
      body: <>Absentes de notre inventaire : leur état n&apos;est pas connu, elles
        ne peuvent donc jamais rendre une liaison rouge.{' '}
        {stats.unsupervised_ends.join(', ')}</>,
    })
  }

  if (!blocks.length) return null
  return (
    <section className="space-y-2">
      <h2 className="text-base font-semibold text-slate-900">Ce que la carte ne montre pas</h2>
      {blocks.map(b => (
        <div key={b.title} className="bg-amber-50 border border-amber-200 rounded-lg p-4">
          <div className="text-sm font-semibold text-amber-900">{b.title}</div>
          <div className="text-xs text-amber-800 mt-1">{b.body}</div>
        </div>
      ))}
    </section>
  )
}
