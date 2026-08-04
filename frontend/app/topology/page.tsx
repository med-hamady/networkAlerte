'use client'

// Topologie inter-sites — le maillage des backhauls entre sites.
//
// Complète la topologie INTRA-site (composant SiteTopology, page /sites) qui
// montre ce qu'il y a derrière un switch. Ici c'est le niveau au-dessus : quel
// site est raccordé à quel autre.
//
// La donnée est lue EN DIRECT sur le contrôleur UISP (le câblage n'est stocké
// nulle part chez nous), mais la SANTÉ de chaque liaison vient de notre poll —
// c'est ce que la carte du contrôleur ne sait pas montrer.
//
// Cette page ne fait que le fetch et les états d'erreur ; le rendu vit dans
// components/TopologyView.tsx, que l'aperçu réutilise tel quel.

import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { NetworkTopology } from '@/lib/types'
import TopologyView from '@/components/TopologyView'

export default function TopologyPage() {
  const { data, error, isLoading } = useSWR<NetworkTopology>(
    endpoints.networkTopology, fetcher, { refreshInterval: 120_000 },
  )

  if (error) {
    return (
      <div className="space-y-2">
        <h1 className="text-xl font-bold text-slate-900">Topologie du réseau</h1>
        <p className="text-sm text-red-600">
          Impossible de lire la topologie : le contrôleur UISP est injoignable ou
          a refusé l&apos;authentification. Le câblage inter-sites n&apos;existe que
          là-bas — rien à afficher depuis notre base.
        </p>
      </div>
    )
  }
  if (isLoading || !data) {
    return <p className="text-sm text-slate-500">Lecture du contrôleur UISP…</p>
  }
  if (!data.available) {
    return <p className="text-sm text-amber-700">Topologie indisponible : {data.reason}</p>
  }

  return <TopologyView topo={data} />
}
