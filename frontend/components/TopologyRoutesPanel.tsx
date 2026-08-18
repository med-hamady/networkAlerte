'use client'

// Les routes d'un site vers Internet — le panneau latéral de /topology.
//
// Ce que cet écran répond, et que le graphe seul ne pouvait pas : par où ce
// site sort vers Internet, lequel de ses chemins est le meilleur, et sur chacun
// QUEL MAILLON cédera en premier. La mesure existait déjà (l'occupation en
// temps d'antenne des AF60) ; personne ne la lisait le long d'un chemin.
//
// ⚠️ Ce sont les chemins que le CÂBLAGE PERMET. Ni OSPF ni la table de routage
// ne sont lus — la phrase de bas de panneau n'est pas décorative : sans elle,
// l'écran se lit comme un diagnostic de routage, ce qu'il n'est pas.
//
// ⚠️ Aucun barème de couleur n'est recopié ici. `saturated` est un booléen du
// backend, calculé contre le seuil qui déclenche l'incident — c'est la règle qui
// a fait exister `lib/topologyColors`, et les bandes 70/90 de
// `DeviceDetailModal` en divergent déjà.

import { useState } from 'react'
import type {
  NetworkTopology,
  TopologyRoute,
  TopologyRouteHop,
  TopologySiteRoutes,
} from '@/lib/types'
import { SATURATION_COLOR } from '@/lib/topologyColors'

/**
 * Ce que le site EST, avant ce qu'il peut faire.
 *
 * ⚠️ Un site à une seule sortie ne choisit rien : lui lister « 3 routes » lui
 * prête un arbitrage qu'il n'a pas. VEL1 remet tout à TS1, et c'est TS1 qui
 * décide entre ARF1 et SM1. L'écran doit donc renvoyer vers le site où l'on
 * peut AGIR, sinon l'opérateur cherche un levier là où il n'y en a pas.
 */
function SiteRole({ group, topo }: { group: TopologySiteRoutes; topo: NetworkTopology }) {
  if (group.role === 'decider') {
    return (
      <p className="shrink-0 rounded-lg bg-blue-50 p-2 text-xs text-blue-900">
        <strong>Point de décision</strong> — ce site arbitre entre{' '}
        {joinFr(group.exits.map(shortSite))}.
      </p>
    )
  }
  if (group.role !== 'child') return null

  const decider = group.decider ? topo.routes?.[group.decider] : undefined
  return (
    <div className="shrink-0 space-y-1 rounded-lg bg-amber-50 p-2 text-xs text-amber-900">
      <p>
        <strong>Site enfant</strong> — une seule sortie, par{' '}
        <strong>{shortSite(group.exits[0])}</strong>. Ce site ne choisit pas sa
        direction, et cette liaison tombée, il est isolé.
      </p>
      {group.decider && decider ? (
        <p>
          Le choix se fait à <strong>{shortSite(group.decider)}</strong>, qui
          arbitre entre {joinFr(decider.exits.map(shortSite))} — c&apos;est
          là qu&apos;on agit.
        </p>
      ) : (
        // ⚠️ Ni redondance, ni décideur en amont : un blanc se lirait comme un
        // calcul manquant, alors que c'est un fait du réseau.
        <p>
          Sa sortie va directement à la racine : aucun site en amont ne peut le
          rerouter.
        </p>
      )}
    </div>
  )
}

/** Routes dépliées d'emblée ; le reste est à un clic, jamais absent. */
const VISIBLE = 3

export default function TopologyRoutesPanel({
  topo,
  site,
  selectedRouteId,
  onSelectRoute,
}: {
  topo: NetworkTopology
  site: string
  selectedRouteId: string | null
  onSelectRoute: (id: string | null) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const group = topo.routes?.[site]

  return (
    <div className="flex min-h-0 flex-col gap-2">
      <div className="shrink-0">
        <h2 className="text-sm font-semibold text-slate-900">Routes vers Internet</h2>
        <p className="truncate text-xs text-slate-500" title={site}>{site}</p>
      </div>

      {!group ? (
        <p className="text-xs text-slate-400">Chemins non calculés pour ce site.</p>
      ) : group.reason ? (
        // Jamais une liste vide muette : une absence de route se lirait comme
        // un oubli du calcul plutôt que comme un fait du réseau.
        <p className="rounded-lg bg-slate-50 p-2 text-xs text-slate-600">
          {group.reason === 'racine'
            ? "Ce site EST la racine : c'est par lui que le réseau sort vers Internet."
            : "Aucun chemin vers la racine dans le câblage connu — ce site est isolé du maillage."}
        </p>
      ) : (
        <>
          <SiteRole group={group} topo={topo} />

          {group.best_reason && (
            // Ne pas trancher est une réponse, à condition de dire pourquoi —
            // et c'est en soi actionnable (« instrumentez cette dorsale »).
            <p className="shrink-0 rounded-lg bg-amber-50 p-2 text-xs text-amber-800">
              Pas de meilleure route désignée : {group.best_reason}.
            </p>
          )}

          <div className="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
            {(expanded ? group.paths : group.paths.slice(0, VISIBLE)).map((route) => (
              <RouteCard
                key={route.id}
                route={route}
                selected={selectedRouteId === route.id}
                onSelect={() =>
                  onSelectRoute(selectedRouteId === route.id ? null : route.id)
                }
              />
            ))}

            {/* ⚠️ On REPLIE, on ne tronque pas : toutes les sorties du site sont
                dans la réponse et atteignables en un clic. Un chemin caché par
                le backend, lui, ne se déplie pas. */}
            {group.paths.length > VISIBLE && (
              <button
                onClick={() => setExpanded(!expanded)}
                className="w-full rounded-lg border border-dashed border-slate-300 py-1
                           text-[11px] text-blue-700 hover:bg-slate-50"
              >
                {expanded
                  ? 'Replier'
                  : `Voir les ${group.paths.length - VISIBLE} autre${
                      group.paths.length - VISIBLE > 1 ? 's' : ''
                    } route${group.paths.length - VISIBLE > 1 ? 's' : ''}`}
              </button>
            )}
          </div>

          <div className="shrink-0 space-y-1 border-t border-slate-100 pt-2">
            <p className="text-[11px] text-slate-400">
              {group.role === 'child'
                ? `1 sortie · ${group.found} chemin${group.found > 1 ? 's' : ''} au-delà`
                : `${group.exits.length} sortie${group.exits.length > 1 ? 's' : ''} · ` +
                  `${group.found} chemin${group.found > 1 ? 's' : ''}`}{' '}
              vers {topo.root ?? 'la racine'}
              {group.kept < group.found &&
                ` (${group.kept} détaillé${group.kept > 1 ? 's' : ''})`}
              .
            </p>
            {(group.truncated.by_hops || group.truncated.by_budget) && (
              <p className="text-[11px] text-amber-700">
                Énumération tronquée ({group.truncated.by_hops ? 'longueur max' : 'budget'}) —
                la liste n&apos;est pas complète.
              </p>
            )}
            <p className="text-[11px] text-slate-400">
              Chemins permis par le <strong>câblage</strong>. Le système ne lit pas
              le routage réel : ce n&apos;est pas forcément le chemin qu&apos;emprunte
              le trafic.
            </p>
          </div>
        </>
      )}
    </div>
  )
}

function RouteCard({
  route,
  selected,
  onSelect,
}: {
  route: TopologyRoute
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full rounded-lg border p-2 text-left transition-colors ${
        selected
          ? 'border-blue-500 bg-blue-50'
          : 'border-slate-200 bg-white hover:bg-slate-50'
      }`}
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        {route.is_best && (
          <span className="rounded bg-green-100 px-1.5 py-0.5 text-[10px] font-semibold text-green-800">
            {/* La couverture est dite AVEC le badge : « meilleure » sur 1 saut
                radio mesuré sur 2 n'est pas la même affirmation que sur 2 sur 2.
                Un chemin tout en fibre est 'full' — il n'a rien à mesurer. */}
            {route.coverage === 'full'
              ? 'MEILLEURE'
              : `MEILLEURE · ${route.measured_hops}/${route.radio_hop_count} mesurés`}
          </span>
        )}
        {!route.usable && (
          <span className="rounded bg-red-100 px-1.5 py-0.5 text-[10px] font-semibold text-red-700">
            COUPÉE
          </span>
        )}
        {route.usable && route.radio_hop_count === 0 && (
          <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-medium text-blue-800">
            FIBRE DIRECTE
          </span>
        )}
        {route.usable && route.radio_hop_count > 0 && route.headroom_mbps === null && (
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
            CHARGE INCONNUE
          </span>
        )}
        <span className="ml-auto text-[10px] text-slate-400">
          {route.hop_count} saut{route.hop_count > 1 ? 's' : ''}
        </span>
      </div>

      {/* ⚠️ Par où le site SORT — la décision, telle qu'un opérateur se la pose
          (« ARF1 repart par PK1 ou par TS1 ? »). Elle n'était lisible qu'au
          deuxième nom de la chaîne, alors que c'est le premier fait à saisir.
          Les chemins d'un même site se distinguent d'abord par là. */}
      {route.sites.length > 1 && (
        <p className="mb-1 text-xs font-semibold text-slate-800">
          {/* Un chemin d'un seul saut arrive DIRECTEMENT à la racine : écrire
              « sortie par HQ » n'y voudrait rien dire. */}
          {route.hop_count === 1
            ? `Sortie directe vers ${shortSite(route.sites[1])}`
            : `Sortie par ${shortSite(route.sites[1])}`}
        </p>
      )}

      <Chain route={route} />
      {/* ⚠️ « celui où il RESTE le moins », surtout pas « le plus rempli » :
          DN1↔AT1 remplit 887/1951 (46 %) et ARF1↔TS1 seulement 452/1252 (36 %),
          et c'est pourtant le second qui bride — il ne lui reste que 800 contre
          1064. Le mérite de la jauge est qu'on peut refaire la soustraction de
          tête, ce que le pourcentage seul interdisait. */}
      <p className="mt-0.5 text-[10px] text-slate-400">
        porté / plafond par saut, en Mb/s — le goulot est celui où il reste le moins
      </p>

      {/* Sur une route coupée, LE fait à lire vient tout de suite après la
          chaîne — pas en dernière ligne sous des chiffres de capacité. */}
      {!route.usable && (
        <p className="mt-2 text-[11px] font-medium text-red-600">
          {/* ⚠️ « Fibre coupée » et « hors service » ne demandent pas le même
              geste : sur une fibre, l'équipement RÉPOND toujours (le site est
              joint par sa radio de secours) et c'est le verre ou le SFP qu'il
              faut aller voir. Les confondre enverrait chercher une panne
              d'équipement là où il n'y en a pas. */}
          {route.down_hops.some((h) => h.fibre_cut) ? 'Fibre coupée : ' : 'Hors service : '}
          {route.down_hops
            .map((h) => `${shortSite(h.site_a)} ↔ ${shortSite(h.site_b)}`)
            .join(', ')}
        </p>
      )}

      {/* LE verdict, en Mb/s. « Ce chemin peut encore écouler 300 Mb/s » se
          traduit en décision ; « 45 % » ne se traduit en rien.
          ⚠️ Rien de tout ça sur une route COUPÉE : vu à l'écran, une route « HS »
          affichait une barre VERTE à moitié pleine, qui se lisait « en bonne
          santé » sur un chemin qui ne passe pas. */}
      {route.usable && (
        <div className="mt-2">
          {route.radio_hop_count === 0 ? (
            <p className="text-[11px] text-slate-600">
              Fibre jusqu&apos;au HQ — aucun saut radio ne la bride.
            </p>
          ) : route.headroom_mbps !== null ? (
            <>
              {/* La jauge du maillon qui bride : ce qu'il porte, sur ce qu'il
                  tient. Le « il reste » suit, pour n'avoir aucune soustraction
                  à faire — mais les deux nombres sont là pour la vérifier. */}
              <div className="mb-1 flex items-baseline justify-between gap-2">
                <span className="text-sm font-semibold text-slate-900">
                  {Math.round(route.bottleneck?.peak_traffic_mbps ?? 0)}
                  <span className="font-normal text-slate-400">
                    {' '}sur {Math.round(route.bottleneck?.max_rate_mbps ?? 0)} Mb/s
                  </span>
                </span>
                <span className="text-[10px] text-slate-400">
                  il reste {fmtRate(route.headroom_mbps)}
                </span>
              </div>
              {/* La barre montre la part CONSOMMÉE du plafond au pic des 24 h. */}
              <div className="h-2.5 overflow-hidden rounded bg-slate-100">
                <div
                  className="h-full rounded"
                  style={{
                    width: `${Math.min(100, Math.max(2, route.max_occupancy_pct ?? 0))}%`,
                    backgroundColor: route.hops.some((h) => h.is_bottleneck && h.saturated)
                      ? SATURATION_COLOR
                      : '#16a34a',
                  }}
                />
              </div>
              {route.bottleneck && (
                <div className="mt-1 text-[11px] text-slate-600">
                  <p>
                    Point de saturation :{' '}
                    <strong>
                      {shortSite(route.bottleneck.site_a)} ↔ {shortSite(route.bottleneck.site_b)}
                    </strong>
                  </p>

                </div>
              )}
            </>
          ) : (
            <p className="text-[11px] text-slate-400">
              Charge non mesurée sur ce chemin — marge inconnue.
            </p>
          )}
        </div>
      )}

      {/* Une dorsale debout mais à l'arrêt est anormale : signalée, jamais
          disqualifiante. ⚠️ « non mesuré » n'est PAS « à l'arrêt » — un switch
          n'expose pas toujours son débit, et le backend ne remonte ici que les
          liens vraiment mesurés à zéro. */}
      {route.same_bottleneck_as && (
        <p className="mt-1 text-[11px] text-slate-400">
          Cède au même endroit qu&apos;une autre sortie — pas une alternative
          pour ce point de saturation.
        </p>
      )}

      {route.fibre_idle_hops.length > 0 && (
        <p className="mt-1 text-[11px] text-amber-700">
          Fibre debout mais sans trafic :{' '}
          {route.fibre_idle_hops
            .map((h) => `${shortSite(h.site_a)} ↔ ${shortSite(h.site_b)}`)
            .join(', ')}
        </p>
      )}

      <p className="mt-1 text-[11px] text-slate-400">
        {route.min_capacity_mbps !== null
          ? `Capacité min ${fmtRate(route.min_capacity_mbps)}`
          : 'Capacité inconnue'}
        {route.degraded_hops.length > 0 &&
          ` · ${route.degraded_hops.length} saut${
            route.degraded_hops.length > 1 ? 's' : ''
          } dégradé${route.degraded_hops.length > 1 ? 's' : ''}`}
        {route.hops.some((h) => h.redundant) && ' · redondance sur le chemin'}
      </p>
    </button>
  )
}

/** La chaîne des sites, chaque flèche portant la MARGE du saut franchi.
 *
 * ⚠️ La marge en Mb/s, PAS le pourcentage d'occupation — et c'est une
 * correction. La chaîne affichait des `%` pendant que le verdict se décidait en
 * Mb/s, si bien que les deux se contredisaient à l'écran : sur la route TS1
 * d'ARF1, `DN1↔AT1` affichait 46 % (le plus haut) alors que le goulot désigné
 * était `ARF1↔TS1` à 36 %. Les deux étaient justes — 46 % d'un tuyau de
 * 1951 Mb/s laisse 1064, 36 % d'un tuyau de 1252 n'en laisse que 800 — mais
 * rien à l'écran ne permettait de le reconstituer.
 *
 * Avec la marge, le goulot est simplement le plus petit nombre de la chaîne.
 */
function Chain({ route }: { route: TopologyRoute }) {
  return (
    <div className="flex flex-wrap items-center gap-x-1 gap-y-1 text-[11px]">
      {route.sites.map((site, index) => (
        <span key={site} className="flex items-center gap-1">
          {index > 0 && <HopArrow hop={route.hops[index - 1]} />}
          <span className="font-medium text-slate-700">{shortSite(site)}</span>
        </span>
      ))}
    </div>
  )
}

function HopArrow({ hop }: { hop: TopologyRouteHop | undefined }) {
  if (!hop) return <span className="text-slate-300">→</span>

  const down = hop.state === 'down'
  // Le goulot est mis en relief ; le VIOLET est réservé au verdict `saturated`
  // du backend — un maillon peut être le plus chargé du chemin sans être plein.
  const color = down
    ? '#dc2626'
    : hop.is_bottleneck && hop.saturated
      ? SATURATION_COLOR
      : undefined

  return (
    <span
      className={`flex items-center gap-0.5 ${hop.is_bottleneck ? 'font-semibold' : ''}`}
      style={color ? { color } : undefined}
      title={`${hop.site_a} ↔ ${hop.site_b}`}
    >
      <span className={color ? undefined : 'text-slate-300'}>→</span>
      {down ? (
        <span className="text-red-600">{hop.fibre_cut ? 'FIBRE ✂' : 'HS'}</span>
      ) : hop.is_fibre ? (
        // ⚠️ La fibre n'affiche PAS un tiret : le tiret veut dire « pas de
        // mesure », alors qu'ici il n'y a rien à mesurer. Elle dit ce qu'elle
        // est, et seulement si elle est à l'arrêt — cas anormal — elle alerte.
        <span
          className={hop.traffic === 'idle' ? 'text-amber-600' : 'text-blue-600'}
          title={hop.traffic === 'idle' ? 'fibre up mais sans trafic' : 'liaison fibre'}
        >
          fibre{hop.traffic === 'idle' ? ' ⚠' : ''}
        </span>
      ) : hop.headroom_mbps !== null ? (
        <span
          className={color ? undefined : 'text-slate-500'}
          title={
            `porte ${Math.round(hop.peak_traffic_mbps ?? 0)} sur un plafond de ` +
            `${Math.round(hop.max_rate_mbps ?? 0)} Mb/s — il reste ` +
            `${Math.round(hop.headroom_mbps)} Mb/s (` +
            `${(hop.occupancy_pct ?? 0).toFixed(0)} % du temps d'antenne)`
          }
        >
          {Math.round(hop.peak_traffic_mbps ?? 0)}/{Math.round(hop.max_rate_mbps ?? 0)}
        </span>
      ) : hop.occupancy_pct !== null ? (
        // Occupation connue mais marge non projetable (lien quasi vide) : on ne
        // met PAS un pourcentage au milieu de marges en Mb/s, ça se lirait comme
        // une marge minuscule. Le tiret dit « pas de chiffre comparable ».
        <span className="text-slate-300" title={`${hop.occupancy_pct.toFixed(0)} % — trop peu chargé pour projeter un plafond`}>—</span>
      ) : (
        <span className="text-slate-300" title="charge non mesurée">—</span>
      )}
    </span>
  )
}

/** « A, B et C » — `join(' et ')` donnait « A et B et C ». */
function joinFr(items: string[]): string {
  if (items.length < 2) return items[0] ?? ''
  return `${items.slice(0, -1).join(', ')} et ${items[items.length - 1]}`
}

/** « A2 CT2 » → « CT2 ». Le préfixe du parc n'apporte rien dans une chaîne. */
function shortSite(site: string): string {
  return site.replace(/^A2\s+/, '')
}

function fmtRate(mbps: number): string {
  return mbps >= 1000 ? `${(mbps / 1000).toFixed(2)} Gb/s` : `${mbps.toFixed(0)} Mb/s`
}
