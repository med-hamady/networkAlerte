'use client'

// Topologie inter-sites — le maillage des backhauls entre sites.
//
// Complète la topologie INTRA-site (composant SiteTopology, page /sites) qui
// montre ce qu'il y a derrière un switch. Ici c'est le niveau au-dessus : quel
// site est raccordé à quel autre.
//
// Tout vient de NOTRE base : le câblage depuis `site_links` (rapatrié 1×/jour
// depuis UISP), la santé de chaque liaison depuis nos polls. Aucun appel au
// contrôleur à l'affichage.
//
// ⚠️ PAS de `refreshInterval` ici, contrairement aux autres pages du projet.
// Elles affichent des métriques vivantes ; celle-ci affiche du câblage, qui ne
// bouge que quand le terrain pose un backhaul. Un rafraîchissement périodique
// n'apporterait rien et rejouerait la requête indéfiniment sur un onglet oublié.
//
// Cette page ne fait que le fetch et les états d'erreur ; le rendu vit dans
// components/TopologyView.tsx.

import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { NetworkTopology } from '@/lib/types'
import TopologyView from '@/components/TopologyView'

export default function TopologyPage() {
  const { data, error, isLoading } = useSWR<NetworkTopology>(
    endpoints.networkTopology, fetcher,
  )

  if (error) {
    return (
      <div className="space-y-2">
        <h1 className="text-xl font-bold text-slate-900">Topologie du réseau</h1>
        <p className="text-sm text-red-600">Erreur de chargement de la topologie.</p>
      </div>
    )
  }
  if (isLoading || !data) {
    return <p className="text-sm text-slate-500">Chargement…</p>
  }
  if (!data.available) {
    return (
      <div className="space-y-2">
        <h1 className="text-xl font-bold text-slate-900">Topologie du réseau</h1>
        <p className="text-sm text-amber-700">Topologie indisponible : {data.reason}</p>
        <p className="text-xs text-slate-500">
          Le câblage est rapatri&eacute; du contr&ocirc;leur UISP une fois par jour.
          Pour le forcer maintenant :{' '}
          <code>dc exec backend python scripts/dump_site_topology.py --sync</code>
        </p>
      </div>
    )
  }

  return <TopologyView topo={data} />
}
