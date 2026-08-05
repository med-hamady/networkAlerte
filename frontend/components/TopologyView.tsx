'use client'

// Rendu de la topologie inter-sites — la partie PRÉSENTATION, séparée du fetch.
//
// Volontairement dépouillée : le graphe, et c'est tout. Pas de tuiles de
// comptage, pas de légende, pas de liste des liaisons, pas de bloc d'anomalies
// sous la carte — elle se lit d'un coup d'œil, et le détail d'une liaison est au
// survol du trait.
//
// Ce que le graphe ne dit plus explicitement (site sans aucune liaison,
// composante séparée, extrémité non supervisée) reste EXACT dans la réponse
// d'API (`layout.orphan_sites`, `layout.components`, `stats.unsupervised_ends`)
// et visible sur le dessin : un site orphelin y est dessiné, simplement flottant.
// `scripts/dump_site_topology.py` continue de les nommer en clair.

import { useState } from 'react'
import type { NetworkTopology } from '@/lib/types'
import TopologyGraph from '@/components/TopologyGraph'

export default function TopologyView({ topo }: { topo: NetworkTopology }) {
  const [selectedSite, setSelectedSite] = useState<string | null>(null)

  return (
    <>
      {/*
        Colonne flex haute d'exactement un écran (moins le seul padding que
        AppShell ajoute autour du contenu, py-6 = 3rem).

        ⚠️ Ne PAS revenir à une hauteur devinée du type `h-[calc(100vh-9rem)]`
        sur la seule zone du graphe. Le nombre réservé doit couvrir le titre, les
        marges, le padding de la carte ET la note de bas — cinq valeurs qui
        changent au moindre retouche de style. Sous-estimé d'une trentaine de
        pixels, la section dépasse le viewport et c'est la PAGE qui défile : le
        graphe tient dans son cadre, mais on ne le voit pas entier. Ici c'est le
        navigateur qui calcule le reste (`flex-1`), donc le compte est toujours
        juste.

        `min-h-0` est indispensable sur les enfants flex : sans lui un enfant
        qui contient un `overflow-auto` refuse de se comprimer et repousse la
        colonne au-delà de l'écran — le même symptôme, par un autre chemin.
      */}
      <div className="flex h-[calc(100vh-3rem)] flex-col gap-3">
        <div className="flex shrink-0 items-start justify-between gap-4">
          <h1 className="text-xl font-bold text-slate-900">Topologie du réseau</h1>
          {selectedSite && (
            <button onClick={() => setSelectedSite(null)}
                    className="text-xs text-blue-700 hover:underline shrink-0">
              Tout afficher
            </button>
          )}
        </div>

        <section className="flex min-h-0 flex-1 flex-col rounded-xl border
                            border-slate-200 bg-white p-3 shadow-sm">
          <div className="min-h-0 flex-1">
            <TopologyGraph topo={topo} onSelectSite={setSelectedSite}
                           selectedSite={selectedSite} />
          </div>

          {/* Deux fraîcheurs, et il faut le dire : le câblage date du dernier
              rapatriement, l'état des équipements est de maintenant. Une seule
              date laisserait croire que tout l'écran a le même âge. */}
          <p className="shrink-0 pt-2 text-xs text-slate-400">
            Racine : {topo.root} ({topo.root_source}). Le lien Internet→HQ
            n&apos;existe pas dans le contrôleur — la racine est un réglage, pas
            une déduction. Câblage rapatrié le {formatSynced(topo.synced_at)} ;
            état des équipements relevé en direct.
          </p>
        </section>
      </div>
    </>
  )
}

function formatSynced(iso: string | null | undefined): string {
  if (!iso) return 'jamais'
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? 'jamais'
    : d.toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })
}
