'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher, type ClientBlockResult } from '@/lib/api'
import type { AccessClientRow, AccessClientsResponse, AccessStats } from '@/lib/types'
import ClientAccessActionModal from '@/components/ClientAccessActionModal'
import { PERM, usePermissions } from '@/lib/permissions'
import IpLink from '@/components/IpLink'

type Filter = 'all' | 'active' | 'blocked_full' | 'blocked_whatsapp' | 'bridge'
  | 'disconnected' | 'out_of_supervision'
  | 'out_of_supervision_30d' | 'out_of_supervision_90d'
  | 'blocked' | 'blocked_ssh' | 'blocked_router' | 'blocked_pending'

// Sous-filtres imbriqués : la sous-rangée n'apparaît qu'une fois l'onglet parent
// sélectionné, pour ne pas charger la barre principale.

// « Hors supervision » → tranches d'ancienneté (aligné sur le blocage routeur).
// Le seuil d'entrée est `OUT_OF_SUPERVISION_DAYS` (60 j) : la tranche « ≥ 30 j »
// a été retirée de la rangée, elle aurait toujours affiché le même compte que
// « Tous ». Le filtre reste accepté par l'API.
const OOS_FILTERS = new Set<Filter>([
  'out_of_supervision', 'out_of_supervision_30d', 'out_of_supervision_90d',
])
const OOS_SUB: { value: Filter; label: string; count: keyof AccessStats }[] = [
  { value: 'out_of_supervision',     label: 'Tous (60 j+)', count: 'out_of_supervision' },
  { value: 'out_of_supervision_90d', label: '≥ 90 j',       count: 'out_of_supervision_90d' },
]

// « Bloqués » → par MÉCANISME : coupé sur son équipement (SSH) vs sur le routeur.
const BLOCKED_FILTERS = new Set<Filter>([
  'blocked', 'blocked_ssh', 'blocked_router', 'blocked_pending',
])
const BLOCKED_SUB: { value: Filter; label: string; count: keyof AccessStats }[] = [
  { value: 'blocked',         label: 'Tous',            count: 'blocked' },
  { value: 'blocked_ssh',     label: 'Par SSH (LR)',    count: 'blocked_ssh' },
  { value: 'blocked_router',  label: 'Sur le routeur',  count: 'blocked_router' },
  { value: 'blocked_pending', label: 'En attente',      count: 'blocked_pending' },
]

// `count` = clé de stats affichée en badge sur l'onglet principal.
const FILTERS: { value: Filter; label: string; count?: keyof AccessStats }[] = [
  { value: 'all',                label: 'Tous'             },
  { value: 'active',             label: 'Accès actif'      },
  { value: 'blocked',            label: 'Bloqués', count: 'blocked' },
  { value: 'bridge',             label: 'Mode bridge ⚠'    },
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
  const { can } = usePermissions()
  const canBlock = can(PERM.faiBlock)
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

  // ⚠️ `stats` vaut null quand le profil n'a pas `fai.stats` : le backend a
  // RETIRÉ les compteurs de la réponse. Ne jamais retomber sur un objet de
  // zéros — « 0 client » est un chiffre faux, et il déclencherait en plus la
  // bannière « parc vide » sur un réseau qui compte un millier d'abonnés.
  const stats: AccessStats | null = data?.stats ?? null
  const sorted = data?.items ?? []
  const isEmptyFleet = stats !== null && stats.total === 0
  // Sur l'onglet « Accès actif », personne n'est coupé : la colonne n'y
  // porterait que des tirets.
  const showCutSince = filter !== 'active'

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
      </div>

      {/* Compteurs du parc — absents (et non mis à zéro) si le profil n'a pas
          le droit `fai.stats`. Le reste de la page reste entier : ce droit
          porte sur la TAILLE DU PARC, pas sur la capacité à traiter un abonné. */}
      {stats && (
      <div className="grid grid-cols-2 md:grid-cols-4 divide-x divide-y md:divide-y-0 divide-slate-200">
        <StatCard
          label="Clients (total)"
          value={stats.total}
          tone="blue"
          icon={<StatIcon file="client_fai.png" mask />}
        />
        <StatCard
          label="Accès actif"
          value={stats.active}
          tone="green"
          icon={<StatIcon file="client_active.png" />}
        />
        <StatCard
          label="Bloqués"
          value={stats.blocked_full + stats.blocked_whatsapp}
          tone="red"
          icon={<StatIcon file="client_blocked.png" />}
        />
        <StatCard
          label="Mode bridge"
          value={stats.bridge}
          tone={stats.bridge > 0 ? 'amber' : 'slate'}
          icon={<StatIcon file="incident.png" />}
        />
      </div>
      )}

      {/* Filtres + recherche — onglets soulignés posés sur un filet, sans
          cadre (même esprit que les compteurs au-dessus). */}
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-b border-slate-200">
        <nav className="flex flex-wrap -mb-px" aria-label="Filtres">
          {FILTERS.map(({ value, label, count }) => {
            // Pas de compteurs autorisés ⇒ pas de badge du tout. Le libellé de
            // l'onglet reste, donc le filtrage continue de fonctionner
            // normalement : c'est le CHIFFRE qui est retiré, pas la navigation.
            const badge = count && stats ? stats[count] : undefined
            // Un onglet parent reste actif quand un de ses sous-filtres l'est.
            const active =
              value === 'out_of_supervision' ? OOS_FILTERS.has(filter)
              : value === 'blocked' ? BLOCKED_FILTERS.has(filter)
              : filter === value
            const badgeTone =
              value === 'blocked' ? 'bg-red-50 text-red-600'
              : value === 'out_of_supervision' ? 'bg-amber-50 text-amber-700'
              : 'bg-slate-100 text-slate-600'
            return (
              <button
                key={value}
                onClick={() => setFilter(value)}
                aria-current={active ? 'page' : undefined}
                className={`inline-flex items-center gap-2 px-4 py-3 text-sm border-b-2 transition-colors whitespace-nowrap ${
                  active
                    ? 'border-blue-700 text-blue-900 font-semibold'
                    : 'border-transparent text-slate-500 font-medium hover:text-blue-800 hover:border-slate-300'
                }`}
              >
                {label}
                {badge !== undefined && (
                  <span className={`tabular-nums text-[11px] font-semibold rounded-full px-2 py-0.5 ${badgeTone}`}>
                    {badge}
                  </span>
                )}
              </button>
            )
          })}
        </nav>
        <div className="relative w-full md:w-80 mb-2">
          <svg
            aria-hidden
            viewBox="0 0 20 20"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none"
          >
            <circle cx="9" cy="9" r="6" />
            <path d="M14 14l4 4" strokeLinecap="round" />
          </svg>
          <input
            type="search"
            placeholder="Recherche par nom ou IP…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2 text-sm rounded-full bg-slate-100 border border-transparent placeholder:text-slate-400 focus:bg-white focus:border-blue-200 focus:outline-none focus:ring-2 focus:ring-blue-100 transition-colors"
          />
        </div>
      </div>

      {/* Sous-filtres « Bloqués » par mécanisme — sous l'onglet « Bloqués ». */}
      {BLOCKED_FILTERS.has(filter) && (
        <div className="flex flex-wrap items-center gap-1 -mt-3">
          <span className="text-[11px] text-red-600 font-semibold mr-1">Coupé :</span>
          {BLOCKED_SUB.map(({ value, label, count }) => {
            const active = filter === value
            return (
              <button
                key={value}
                onClick={() => setFilter(value)}
                className={`px-2.5 py-1 text-[11px] font-semibold rounded-md border transition-colors ${
                  active
                    ? 'bg-red-500 text-white border-red-500'
                    : 'bg-white text-red-700 border-red-200 hover:bg-red-50'
                }`}
              >
                {label}
                {stats && (
                  <span className={`ml-1 tabular-nums rounded px-1 ${
                    active ? 'bg-white/25' : 'bg-red-100 text-red-700'
                  }`}>
                    {stats[count]}
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}

      {/* Sous-filtres d'ancienneté — n'apparaissent que sous « Hors supervision ». */}
      {OOS_FILTERS.has(filter) && (
        <div className="flex flex-wrap items-center gap-1 -mt-3">
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
                {stats && (
                  <span className={`ml-1 tabular-nums rounded px-1 ${
                    active ? 'bg-white/25' : 'bg-amber-100 text-amber-700'
                  }`}>
                    {stats[count]}
                  </span>
                )}
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
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {(showCutSince
                    ? ['Client', 'État', 'Coupé depuis', 'Action']
                    : ['Client', 'État', 'Action']
                  ).map(h => (
                    <th
                      key={h}
                      className={`px-5 py-3 text-xs font-bold text-slate-700 uppercase tracking-wider whitespace-nowrap ${
                        h === 'Action' ? 'text-right' : 'text-left'
                      }`}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {sorted.map(lr => {
                  const isBridge = lr.effective_mode === 'bridge'
                  const isBlocked = lr.client_blocked
                  return (
                    <tr key={lr.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-5 py-3.5">
                        <div className="text-slate-800 font-semibold">{lr.name}</div>
                        <div className="flex flex-wrap items-center gap-x-2 mt-0.5 text-[11px] text-slate-400">
                          {lr.ip_address && (
                            <span className="font-mono"><IpLink ip={lr.ip_address} /></span>
                          )}
                          {lr.ip_address && lr.uisp_ap_name && <span aria-hidden>·</span>}
                          {lr.uisp_ap_name && <span>AP {lr.uisp_ap_name}</span>}
                        </div>
                      </td>
                      <td className="px-5 py-3.5 whitespace-nowrap">
                        {/* ⚠️ Voir la page et couper un abonné sont deux droits
                            distincts : un profil de consultation garde la liste
                            complète, sans le bouton. Le masquer ne protège rien
                            par lui-même — la route répond 403 — mais évite de
                            proposer un geste qui échouerait. */}
                        {!canBlock ? (
                          <span className="text-slate-300 text-xs">—</span>
                        ) : isBlocked ? (
                          <div className="flex flex-col items-start gap-1">
                            <StatePill tone="red">Bloqué</StatePill>
                            <div className="text-[11px] pl-1">
                              <MechanismBadge lr={lr} />
                            </div>
                          </div>
                        ) : lr.out_of_supervision ? (
                          // Ni bloqué ni « actif » : aucune source ne parle de
                          // lui (pas d'IP, et UISP ne l'a pas vu). L'afficher
                          // « ● Actif » était un mensonge par défaut.
                          <span title="Sans IP et non vu par UISP — aucune mesure possible. Récupéré dès qu'un AP le rapporte.">
                            <StatePill tone="amber">Hors supervision</StatePill>
                          </span>
                        ) : (
                          <StatePill tone="green">Actif</StatePill>
                        )}
                      </td>
                      {showCutSince && (
                      <td className="px-5 py-3.5 whitespace-nowrap text-xs text-slate-600">
                        {isBlocked ? (
                          timeAgo(lr.client_blocked_at)
                        ) : lr.out_of_supervision ? (
                          <span className="text-amber-600" title="Ancienneté depuis la dernière vue UISP">
                            {lr.days_offline != null
                              ? `hors sup. depuis ${lr.days_offline} j`
                              : 'jamais vu par UISP'}
                          </span>
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                      )}
                      <td className="px-5 py-3.5 whitespace-nowrap text-right">
                        {isBlocked ? (
                          <button
                            onClick={() => { setModalLr(lr); setModalAction('unblock') }}
                            className="px-4 py-1.5 rounded-full border border-green-600 text-green-700 text-xs font-semibold hover:bg-green-600 hover:text-white transition-colors"
                          >
                            Débloquer
                          </button>
                        ) : isBridge || !lr.reachable ? (
                          <button
                            disabled
                            title={isBridge
                              ? 'LR en mode bridge — repasser en routeur via airOS'
                              : 'LR injoignable — pas de session SSH pour appliquer le blocage'}
                            className="px-4 py-1.5 rounded-full border border-slate-200 text-slate-300 text-xs font-semibold cursor-not-allowed"
                          >
                            Bloquer
                          </button>
                        ) : (
                          <button
                            onClick={() => { setModalLr(lr); setModalAction('block') }}
                            className="px-4 py-1.5 rounded-full border border-red-500 text-red-600 text-xs font-semibold hover:bg-red-600 hover:text-white transition-colors"
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

/**
 * Un compteur du parc, posé directement sur la page (pas de carte) : même
 * présentation que les statistiques du Dashboard — icône, chiffre en premier,
 * libellé dessous, colonnes séparées par un filet. La couleur ne porte plus que
 * sur le CHIFFRE, là où l'œil va.
 */
function StatCard({ label, value, tone, sub, icon }: {
  label: string
  value: number
  tone: StatTone
  sub?: string
  icon?: React.ReactNode
}) {
  const valueColor: Record<StatTone, string> = {
    blue:  'text-blue-900',
    green: 'text-green-600',
    red:   'text-red-500',
    amber: 'text-amber-600',
    slate: 'text-slate-400',
  }
  return (
    <div className="flex items-center gap-3 px-5 py-4 min-w-0">
      {icon && <span className="flex items-center justify-center shrink-0">{icon}</span>}
      <div className="min-w-0">
        <p className={`text-[26px] font-bold leading-none tabular-nums ${valueColor[tone]}`}>{value}</p>
        <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wider mt-1 truncate">
          {label}
        </p>
        {sub && <p className="text-[10px] text-slate-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}

/**
 * Icône PNG de `public/brand/icons/`. `mask` pour un dessin NOIR au trait, qui
 * prend alors le bleu pétrole de la marque (une `<img>` resterait noire) ; sans
 * `mask`, le dessin est déjà colorié et s'affiche tel quel.
 */
function StatIcon({ file, mask = false }: { file: string; mask?: boolean }) {
  if (mask) {
    const src = `url(/brand/icons/${file})`
    return (
      <span
        aria-hidden
        className="inline-block w-6 h-6 bg-current text-blue-700"
        style={{
          maskImage: src, WebkitMaskImage: src,
          maskSize: 'contain', WebkitMaskSize: 'contain',
          maskRepeat: 'no-repeat', WebkitMaskRepeat: 'no-repeat',
          maskPosition: 'center', WebkitMaskPosition: 'center',
        }}
      />
    )
  }
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={`/brand/icons/${file}`} alt="" aria-hidden className="w-6 h-6" />
}

/* ─── Badges ─────────────────────────────────────────────────────────── */

// Le MODE de coupure n'est pas affiché (décision opérateur du 2026-09-22) :
// « Coupure totale » était la même mention sur toutes les lignes, et le mode
// « WhatsApp autorisé » est masqué de cette page depuis le 2026-09-19.

// Par quel mécanisme la coupure est appliquée : sur l'équipement (SSH), sur le
// routeur (repli, LR injoignable), ou pas encore (le job rejoue).
function MechanismBadge({ lr }: { lr: AccessClientRow }) {
  const [label, title, cls] = lr.client_block_enforced_at
    ? ['par SSH', "Coupé sur l'équipement du client (SSH)", 'text-slate-500']
    : lr.router_blocked
      ? ['sur le routeur', 'LR injoignable — coupé sur le routeur de cœur', 'text-slate-500']
      : ['en attente', 'Pas encore appliqué — le job de renforcement rejoue toutes les 2 min', 'text-blue-500']
  return (
    <span className={cls} title={title}>
      {label}
    </span>
  )
}

// État d'un abonné : pastille arrondie teintée, point de couleur devant.
function StatePill({ tone, children }: { tone: 'red' | 'green' | 'amber'; children: React.ReactNode }) {
  const cls = {
    red:   'bg-red-50 text-red-600',
    green: 'bg-green-50 text-green-700',
    amber: 'bg-amber-50 text-amber-700',
  }[tone]
  const dot = { red: 'bg-red-500', green: 'bg-green-500', amber: 'bg-amber-500' }[tone]
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ${cls}`}>
      <span aria-hidden className={`w-1.5 h-1.5 rounded-full ${dot}`} />
      {children}
    </span>
  )
}
