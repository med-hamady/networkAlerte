'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher, type ClientBlockResult } from '@/lib/api'
import type { AccessClientRow, AccessClientsResponse, AccessStats } from '@/lib/types'
import ClientAccessActionModal from '@/components/ClientAccessActionModal'
import IpLink from '@/components/IpLink'

type Filter = 'all' | 'active' | 'blocked_full' | 'blocked_whatsapp' | 'bridge'
  | 'disconnected' | 'out_of_supervision'
  | 'out_of_supervision_30d' | 'out_of_supervision_90d'

// Les trois filtres d'ancienneté « hors supervision ». N'apparaissent qu'une fois
// « Hors supervision » sélectionné (sous-rangée), pour ne pas charger la barre
// principale. Découpage aligné sur le blocage de masse sur le routeur.
const OOS_FILTERS = new Set<Filter>([
  'out_of_supervision', 'out_of_supervision_30d', 'out_of_supervision_90d',
])
const OOS_SUB: { value: Filter; label: string; count: keyof AccessStats }[] = [
  { value: 'out_of_supervision',     label: 'Tous (7 j+)', count: 'out_of_supervision' },
  { value: 'out_of_supervision_30d', label: '≥ 30 j',      count: 'out_of_supervision_30d' },
  { value: 'out_of_supervision_90d', label: '≥ 90 j',      count: 'out_of_supervision_90d' },
]

// `count` = clé de stats affichée en badge sur l'onglet principal.
const FILTERS: { value: Filter; label: string; count?: keyof AccessStats }[] = [
  { value: 'all',                label: 'Tous'             },
  { value: 'active',             label: 'Accès actif'      },
  { value: 'blocked_full',       label: 'Coupure totale'   },
  { value: 'blocked_whatsapp',   label: 'WhatsApp autorisé' },
  { value: 'bridge',             label: 'Mode bridge ⚠'    },
  { value: 'disconnected',       label: 'Hors ligne > 1 mois' },
  { value: 'out_of_supervision', label: 'Hors supervision', count: 'out_of_supervision' },
]

function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60)        return `il y a ${Math.round(seconds)} s`
  if (seconds < 3600)      return `il y a ${Math.round(seconds / 60)} min`
  if (seconds < 86400)     return `il y a ${Math.round(seconds / 3600)} h`
  return `il y a ${Math.round(seconds / 86400)} j`
}

export default function AccessPage() {
  const [filter, setFilter] = React.useState<Filter>('all')
  const [search, setSearch] = React.useState('')
  // Debounce the typed search so we don't refetch on every keystroke; the
  // filtering/sorting itself runs server-side (fn_access_clients).
  const [debouncedSearch, setDebouncedSearch] = React.useState('')
  React.useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search.trim()), 250)
    return () => clearTimeout(t)
  }, [search])

  // Stats + filtered + sorted list all computed in SQL. The frontend renders it.
  const { data, mutate, isLoading } = useSWR<AccessClientsResponse>(
    endpoints.accessClients(debouncedSearch, filter),
    fetcher,
    { refreshInterval: 30_000, keepPreviousData: true },
  )

  const stats = data?.stats ?? {
    total: 0, active: 0, blocked_full: 0, blocked_whatsapp: 0, bridge: 0, disconnected: 0,
    out_of_supervision: 0, out_of_supervision_30d: 0, out_of_supervision_90d: 0,
  }
  const sorted = data?.items ?? []
  const isEmptyFleet = stats.total === 0

  // Modal state
  const [modalLr, setModalLr] = React.useState<AccessClientRow | null>(null)
  const [modalAction, setModalAction] = React.useState<'block' | 'unblock'>('block')

  const onActionSuccess = (_result: ClientBlockResult) => {
    setModalLr(null)
    mutate() // refresh the list
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">FAI</h1>
        <p className="text-blue-400 text-sm mt-1">
          Gère l'accès internet de chaque client en coupant ou rétablissant depuis son LR.
          Deux modes au choix : <strong>coupure totale</strong> (shutdown du port LAN) ou
          <strong> WhatsApp autorisé</strong> (filtre laissant DNS + WhatsApp, FB/Insta bloqués).
        </p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="Clients (total)"
          value={stats.total}
          tone="blue"
          sub={stats.disconnected > 0 ? `${stats.disconnected} hors ligne > 1 mois` : undefined}
        />
        <StatCard
          label="Accès actif"
          value={stats.active}
          tone="green"
          sub={stats.out_of_supervision > 0
            ? `${stats.out_of_supervision} hors supervision exclu${stats.out_of_supervision > 1 ? 's' : ''}`
            : undefined}
        />
        <StatCard
          label="Bloqués"
          value={stats.blocked_full + stats.blocked_whatsapp}
          tone="red"
          sub={stats.blocked_full + stats.blocked_whatsapp > 0
            ? `${stats.blocked_full} total · ${stats.blocked_whatsapp} WhatsApp`
            : undefined}
        />
        <StatCard
          label="Mode bridge"
          value={stats.bridge}
          tone={stats.bridge > 0 ? 'amber' : 'slate'}
          sub={stats.bridge > 0 ? 'à reconfigurer' : undefined}
        />
      </div>

      {/* Filters + search */}
      <div className="flex flex-wrap gap-3 items-center justify-between">
        <div className="flex flex-wrap gap-1 rounded-lg bg-white border border-blue-100 p-1 shadow-sm">
          {FILTERS.map(({ value, label, count }) => {
            const badge = count ? stats[count] : undefined
            // L'onglet « Hors supervision » reste actif quand un de ses
            // sous-filtres d'ancienneté est sélectionné.
            const active = value === 'out_of_supervision'
              ? OOS_FILTERS.has(filter)
              : filter === value
            return (
              <button
                key={value}
                onClick={() => setFilter(value)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                  active ? 'bg-blue-600 text-white' : 'text-blue-600 hover:bg-blue-50'
                }`}
              >
                {label}
                {badge !== undefined && (
                  <span className={`ml-1.5 tabular-nums rounded px-1 ${
                    active ? 'bg-white/25' : 'bg-blue-100 text-blue-600'
                  }`}>
                    {badge}
                  </span>
                )}
              </button>
            )
          })}
        </div>
        <input
          type="search"
          placeholder="Recherche par nom ou IP…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full md:w-80 px-3 py-2 text-sm rounded-lg border border-blue-200 focus:outline-none focus:ring-2 focus:ring-blue-200"
        />
      </div>

      {/* Sous-filtres d'ancienneté — n'apparaissent que sous « Hors supervision ». */}
      {OOS_FILTERS.has(filter) && (
        <div className="flex flex-wrap items-center gap-1 -mt-2">
          <span className="text-[11px] text-amber-600 font-semibold mr-1">Depuis :</span>
          {OOS_SUB.map(({ value, label, count }) => {
            const active = filter === value
            return (
              <button
                key={value}
                onClick={() => setFilter(value)}
                className={`px-2.5 py-1 text-[11px] font-semibold rounded-md border transition-colors ${
                  active
                    ? 'bg-amber-500 text-white border-amber-500'
                    : 'bg-white text-amber-700 border-amber-200 hover:bg-amber-50'
                }`}
              >
                {label}
                <span className={`ml-1 tabular-nums rounded px-1 ${
                  active ? 'bg-white/25' : 'bg-amber-100 text-amber-700'
                }`}>
                  {stats[count]}
                </span>
              </button>
            )
          })}
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center text-blue-300 shadow-sm">
          Chargement…
        </div>
      ) : sorted.length === 0 ? (
        <div className="bg-white border border-blue-100 rounded-xl px-6 py-12 text-center shadow-sm">
          <p className="text-blue-400 text-sm">
            {isEmptyFleet
              ? 'Aucun LR enregistré.'
              : 'Aucun LR ne correspond au filtre / à la recherche.'}
          </p>
        </div>
      ) : (
        <div className="bg-white border border-blue-100 rounded-xl overflow-hidden shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-blue-50 border-b border-blue-100">
                <tr>
                  {['Client', 'Topologie', 'État', 'Coupé depuis', 'Action'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-blue-500 uppercase tracking-wider whitespace-nowrap">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-50">
                {sorted.map(lr => {
                  const isBridge = lr.effective_mode === 'bridge'
                  const isBlocked = lr.client_blocked
                  return (
                    <tr key={lr.id} className="hover:bg-blue-50/60 align-top">
                      <td className="px-4 py-3">
                        <div className="text-slate-800 font-medium">{lr.name}</div>
                        <div className="text-blue-300 font-mono text-[11px]"><IpLink ip={lr.ip_address} /></div>
                        {lr.uisp_ap_name && (
                          <div className="text-blue-300 text-[10px] mt-0.5">AP : {lr.uisp_ap_name}</div>
                        )}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <TopologyBadge mode={lr.effective_mode} />
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        {isBlocked ? (
                          <div className="flex flex-col gap-1">
                            <span className="text-red-500 font-semibold text-xs">● Bloqué</span>
                            <ModeBadge mode={lr.block_mode} />
                          </div>
                        ) : lr.out_of_supervision ? (
                          // Ni bloqué ni « actif » : aucune source ne parle de
                          // lui (pas d'IP, et UISP ne l'a pas vu). L'afficher
                          // « ● Actif » était un mensonge par défaut.
                          <span
                            className="text-amber-600 font-semibold text-xs"
                            title="Sans IP et non vu par UISP — aucune mesure possible. Récupéré dès qu'un AP le rapporte."
                          >
                            ● Hors supervision
                          </span>
                        ) : (
                          <span className="text-green-600 font-semibold text-xs">● Actif</span>
                        )}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-xs text-slate-700">
                        {isBlocked ? (
                          timeAgo(lr.client_blocked_at)
                        ) : lr.out_of_supervision ? (
                          <span className="text-amber-600" title="Ancienneté depuis la dernière vue UISP">
                            {lr.days_offline != null
                              ? `hors sup. depuis ${lr.days_offline} j`
                              : 'jamais vu par UISP'}
                          </span>
                        ) : (
                          <span className="text-blue-300">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        {isBlocked ? (
                          <button
                            onClick={() => { setModalLr(lr); setModalAction('unblock') }}
                            className="px-3 py-1.5 rounded-lg bg-green-600 text-white text-xs font-semibold hover:bg-green-700"
                          >
                            Débloquer
                          </button>
                        ) : isBridge || !lr.reachable ? (
                          <button
                            disabled
                            title={isBridge
                              ? 'LR en mode bridge — repasser en routeur via airOS'
                              : 'LR injoignable — pas de session SSH pour appliquer le blocage'}
                            className="px-3 py-1.5 rounded-lg bg-blue-50 text-blue-300 text-xs font-semibold cursor-not-allowed"
                          >
                            Bloquer
                          </button>
                        ) : (
                          <button
                            onClick={() => { setModalLr(lr); setModalAction('block') }}
                            className="px-3 py-1.5 rounded-lg bg-red-600 text-white text-xs font-semibold hover:bg-red-700"
                          >
                            Bloquer
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <p className="text-[11px] text-blue-400 leading-relaxed">
        Cette page est alimentée <strong>uniquement par l'inventaire UISP</strong> : le mode
        (routeur/bridge) et l'état en ligne/hors ligne proviennent du dernier état connu d'UISP — les
        clients restent donc visibles avec leur mode même quand leur Rocket est hors ligne. Les blocages
        sont ré-appliqués automatiquement sur le LR toutes les 120 s (survivent au reboot du LR). Les LR
        en bridge ne peuvent pas être bloqués depuis cette page (iptables et dnsmasq sont contournés par
        leur configuration) ; repasse-les en mode routeur via leur interface airOS. Les LR hors ligne ne
        peuvent pas être coupés (pas de session SSH).
      </p>

      <ClientAccessActionModal
        lr={modalLr}
        action={modalAction}
        onClose={() => setModalLr(null)}
        onSuccess={onActionSuccess}
      />
    </div>
  )
}

/* ─── Stat card ──────────────────────────────────────────────────────── */

type StatTone = 'blue' | 'green' | 'red' | 'amber' | 'slate'

function StatCard({ label, value, tone, sub }: {
  label: string
  value: number
  tone: StatTone
  sub?: string
}) {
  const toneClasses: Record<StatTone, string> = {
    blue:   'border-blue-200 bg-blue-50 text-blue-800',
    green:  'border-green-200 bg-green-50 text-green-800',
    red:    'border-red-200 bg-red-50 text-red-700',
    amber:  'border-amber-200 bg-amber-50 text-amber-800',
    slate:  'border-slate-200 bg-slate-50 text-slate-700',
  }
  return (
    <div className={`rounded-xl border px-4 py-3 ${toneClasses[tone]}`}>
      <p className="text-[11px] font-semibold uppercase tracking-wider opacity-80">{label}</p>
      <p className="text-2xl font-bold tabular-nums mt-0.5">{value}</p>
      {sub && <p className="text-[10px] opacity-70 mt-0.5">{sub}</p>}
    </div>
  )
}

/* ─── Badges ─────────────────────────────────────────────────────────── */

// Mode is sourced entirely from the UISP snapshot (uisp_mode).
function TopologyBadge({ mode }: { mode: 'router' | 'bridge' | 'unknown' }) {
  if (mode === 'bridge') {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-amber-50 border border-amber-300 text-amber-800 text-[11px] font-semibold"
        title="Mauvaise configuration (UISP) — le blocage ne peut pas fonctionner. Repasse le LR en mode routeur."
      >
        ⚠ Bridge
      </span>
    )
  }
  if (mode === 'router') {
    return (
      <span
        className="inline-flex items-center px-2 py-0.5 rounded-md bg-slate-50 border border-slate-200 text-slate-600 text-[11px] font-semibold"
        title="Mode routeur (UISP) — le blocage client fonctionne."
      >
        Routeur
      </span>
    )
  }
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-md bg-blue-50 border border-blue-200 text-blue-400 text-[11px] font-semibold"
      title="UISP ne rapporte pas de mode pour ce LR."
    >
      Inconnue
    </span>
  )
}

function ModeBadge({ mode }: { mode: 'full' | 'whatsapp_only' }) {
  if (mode === 'whatsapp_only') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-amber-100 text-amber-800 text-[11px] font-semibold">
        WhatsApp autorisé
      </span>
    )
  }
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-red-100 text-red-700 text-[11px] font-semibold">
      Coupure totale
    </span>
  )
}
