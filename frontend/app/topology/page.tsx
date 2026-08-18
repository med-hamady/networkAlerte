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
// ⚠️ Cette page N'AVAIT PAS de `refreshInterval`, délibérément : elle
// n'affichait que du câblage, qui ne bouge que quand le terrain pose un
// backhaul. Cette raison est TOMBÉE le 2026-08-17 avec les routes vers
// Internet : le panneau porte désormais la charge de chaque liaison et la marge
// qui reste, lues à chaque poll. Une marge figée sur l'état d'il y a une heure
// serait pire que pas de marge du tout — c'est sur elle qu'on décide.
//
// 60 s, calé sur la CADENCE DU POLL et pas plus vite : les AF60 sont relevés
// une fois par minute (conteneur `scheduler-poll-af60`), donc rafraîchir
// davantage rejouerait les requêtes pour relire les mêmes valeurs.
//
// L'ancienne inquiétude — « un onglet oublié rejoue la requête indéfiniment » —
// portait sur une page qui appelait le CONTRÔLEUR à chaque affichage (~1300
// équipements, ~1400 sites, ~1300 liens). Ce n'est plus le cas depuis le sync
// quotidien : tout vient de notre base, et aucun équipement n'est interrogé.
//
// Cette page ne fait que le fetch et les états d'erreur ; le rendu vit dans
// components/TopologyView.tsx.

import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { NetworkTopology } from '@/lib/types'
import TopologyView from '@/components/TopologyView'

const REFRESH = 60_000

export default function TopologyPage() {
  const { data, error, isLoading } = useSWR<NetworkTopology>(
    endpoints.networkTopology, fetcher,
    // `keepPreviousData` : sans lui, chaque revalidation repasserait par l'état
    // « chargement » et ferait disparaître le graphe une fraction de seconde,
    // toutes les minutes.
    { refreshInterval: REFRESH, keepPreviousData: true },
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
