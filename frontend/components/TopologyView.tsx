'use client'

// Rendu de la topologie inter-sites — la partie PRÉSENTATION, séparée du fetch.
//
// Extrait de app/topology/page.tsx pour qu'un aperçu (app/topology-preview)
// puisse montrer exactement le même écran à partir d'un jeu de données figé.
// C'est le point : valider ce qui sera déployé, pas un sosie qui divergera.

import { useState } from 'react'
import type { NetworkTopology, TopologyEdge, TopologyPhysicalLink } from '@/lib/types'
import TopologyGraph from '@/components/TopologyGraph'

export default function TopologyView({ topo }: { topo: NetworkTopology }) {
  const [selectedSite, setSelectedSite] = useState<string | null>(null)

  const { layout, stats } = topo
  const selectedEdges = selectedSite
    ? topo.edges.filter(e => e.site_a === selectedSite || e.site_b === selectedSite)
    : topo.edges

  const unmeasured = topo.edges.filter(e => e.health.state === 'unmeasured')
  const degraded = topo.edges.filter(e => e.health.degraded)
  const down = topo.edges.filter(e => e.health.state === 'down')

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-slate-900">Topologie du réseau</h1>
        <p className="text-sm text-slate-500 mt-1">
          {stats.infra_sites} sites d&apos;infra · {stats.edges} liaisons
          {stats.physical_links > stats.edges
            ? ` (${stats.physical_links} liens physiques)` : ''} · câblage lu sur
          le contrôleur UISP, santé mesurée par notre supervision.
        </p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Tile label="Liaisons" value={stats.edges} tone="neutral" />
        <Tile label="Hors service" value={down.length} tone={down.length ? 'red' : 'neutral'} />
        <Tile label="Dégradées" value={degraded.length} tone={degraded.length ? 'amber' : 'neutral'} />
        <Tile label="Non mesurées" value={unmeasured.length}
              tone={unmeasured.length ? 'slate' : 'neutral'} />
      </div>

      <section className="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-4 text-xs text-slate-600">
            <Legend color="#16a34a" label="mesurée, au-dessus du plancher" />
            <Legend color="#f59e0b" label="dégradée" />
            <Legend color="#dc2626" label="hors service" />
            <Legend color="#9ca3af" label="non mesurée" />
            <span className="flex items-center gap-1.5">
              <svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4"
                stroke="#64748b" strokeWidth="2" strokeDasharray="7 5" /></svg>
              boucle de redondance
            </span>
          </div>
          {selectedSite && (
            <button onClick={() => setSelectedSite(null)}
                    className="text-xs text-blue-700 hover:underline">
              Tout afficher
            </button>
          )}
        </div>

        <TopologyGraph topo={topo} onSelectSite={setSelectedSite} selectedSite={selectedSite} />

        <p className="text-xs text-slate-400">
          Racine : {topo.root} ({topo.root_source}). Le lien Internet→HQ
          n&apos;existe pas dans le contrôleur — la racine est un réglage, pas une
          déduction.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-base font-semibold text-slate-900">
          {selectedSite ? `Liaisons de ${selectedSite}` : 'Toutes les liaisons'}
          <span className="text-slate-400 font-normal"> ({selectedEdges.length})</span>
        </h2>
        <div className="space-y-2">
          {selectedEdges.map(edge => <EdgeRow key={`${edge.site_a}|${edge.site_b}`} edge={edge} />)}
        </div>
      </section>

      <Anomalies layout={layout} stats={stats} unmeasured={unmeasured} />
    </div>
  )
}

function Tile({ label, value, tone }: {
  label: string; value: number; tone: 'neutral' | 'red' | 'amber' | 'slate'
}) {
  const tones = {
    neutral: 'border-slate-200 text-slate-900',
    red: 'border-red-200 bg-red-50 text-red-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    slate: 'border-slate-300 bg-slate-50 text-slate-700',
  }[tone]
  return (
    <div className={`border rounded-xl p-4 ${tones}`}>
      <div className="text-2xl font-bold">{value}</div>
      <div className="text-xs mt-0.5 opacity-80">{label}</div>
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <svg width="26" height="8"><line x1="0" y1="4" x2="26" y2="4" stroke={color} strokeWidth="2.5" /></svg>
      {label}
    </span>
  )
}

function EdgeRow({ edge }: { edge: TopologyEdge }) {
  const badge =
    edge.health.state === 'down' ? { text: 'HORS SERVICE', cls: 'bg-red-100 text-red-700' }
    : edge.health.state === 'unmeasured' ? { text: 'NON MESURÉE', cls: 'bg-slate-100 text-slate-600' }
    : edge.health.degraded ? { text: 'DÉGRADÉE', cls: 'bg-amber-100 text-amber-700' }
    : { text: 'OK', cls: 'bg-green-100 text-green-700' }

  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-semibold text-slate-900">
          {edge.site_a} <span className="text-slate-400">↔</span> {edge.site_b}
        </span>
        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded ${badge.cls}`}>
          {badge.text}
        </span>
        {edge.redundant && (
          <span className="text-[11px] font-semibold px-2 py-0.5 rounded bg-blue-100 text-blue-700">
            REDONDANTE ×{edge.links.length}
          </span>
        )}
        {!edge.is_tree_edge && (
          <span className="text-[11px] px-2 py-0.5 rounded bg-slate-100 text-slate-600">
            boucle
          </span>
        )}
        <span className="ml-auto text-sm text-slate-600">
          {edge.health.capacity_mbps != null
            ? <>
                {Math.round(edge.health.capacity_mbps)} Mb/s
                {edge.health.link_potential_pct != null &&
                  <span className="text-slate-400"> · potentiel {Math.round(edge.health.link_potential_pct)} %</span>}
              </>
            : <span className="text-slate-400">aucune mesure</span>}
        </span>
      </div>
      <div className="mt-3 space-y-1.5">
        {edge.links.map((link, i) => <PhysicalLink key={i} link={link} />)}
      </div>
    </div>
  )
}

function PhysicalLink({ link }: { link: TopologyPhysicalLink }) {
  return (
    <div className="text-xs text-slate-600 flex flex-wrap items-center gap-x-2 gap-y-1
                    border-l-2 border-slate-200 pl-3">
      <span className="uppercase text-[10px] tracking-wide text-slate-400">{link.type}</span>
      {link.state && link.state !== 'active' && (
        <span className="text-red-600">[{link.state}]</span>
      )}
      <EndLabel end={link.device_a} />
      <span className="text-slate-300">↔</span>
      <EndLabel end={link.device_b} />
    </div>
  )
}

function EndLabel({ end }: { end: TopologyPhysicalLink['device_a'] }) {
  if (!end.supervised) {
    return (
      <span className="text-slate-400">
        {end.uisp_name} <span className="italic">(non supervisé)</span>
      </span>
    )
  }
  const dot = end.status === 'up' ? 'bg-green-500'
    : end.status === 'down' ? 'bg-red-500' : 'bg-slate-300'
  return (
    <span className="flex items-center gap-1.5">
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      {end.name}
      {end.capacity_mbps != null && (
        <span className="text-slate-400">{Math.round(end.capacity_mbps)} Mb/s</span>
      )}
    </span>
  )
}

// Ce que le graphe ne peut PAS dire tout seul : un site flottant se lit comme un
// site sans panne, une liaison grise comme une liaison correcte. On les nomme.
function Anomalies({ layout, stats, unmeasured }: {
  layout: NetworkTopology['layout']
  stats: NetworkTopology['stats']
  unmeasured: TopologyEdge[]
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
  if (unmeasured.length) {
    blocks.push({
      title: `${unmeasured.length} liaison(s) sans aucune mesure`,
      body: <>Aucun des deux bouts ne rend de capacité : tracées en gris, jamais
        en vert — un lien non mesuré n&apos;est pas un lien sain.{' '}
        {unmeasured.map(e => `${e.site_a}↔${e.site_b}`).join(', ')}</>,
    })
  }
  if (stats.unsupervised_ends.length) {
    blocks.push({
      title: `${stats.unsupervised_ends.length} extrémité(s) non supervisée(s)`,
      body: <>Absentes de notre inventaire : ni statut ni mesure de ce côté.{' '}
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
