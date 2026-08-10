'use client'

/**
 * Règles de coupure client portées par le ROUTEUR DE CŒUR — lecture en direct.
 *
 * Le journal dit ce qui s'est *passé*, la base ce qu'on *croit* avoir posé ;
 * cette page dit ce que le routeur porte **maintenant**. C'est la seule des
 * trois qui puisse répondre à « ce client a payé, pourquoi est-il coupé ? ».
 *
 * ⚠️ Pas de `refreshInterval`, à dessein : chaque chargement ouvre une session
 * API sur le routeur. On charge à l'arrivée sur la page (le clic dans le menu
 * EST la demande) et le bouton « Actualiser » rejoue la lecture. Un onglet
 * oublié ne martèle donc jamais le routeur.
 */

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { RouterRuleRow, RouterRulesResponse } from '@/lib/types'
import IpLink from '@/components/IpLink'

// Quatre premiers = l'état vis-à-vis de la base ; `redundant` est transverse (une
// règle redondante est forcément une « coupure voulue ») d'où un filtre à part et
// non une cinquième valeur d'état.
type RuleFilter = '' | 'unexpected' | 'unknown' | 'expected' | 'redundant'

const FILTERS: { value: RuleFilter; label: string }[] = [
  { value: '',           label: 'Tout'                 },
  { value: 'unexpected', label: 'À libérer ⚠'          },
  { value: 'unknown',    label: 'MAC inconnue'         },
  { value: 'redundant',  label: 'Règles redondantes'   },
  { value: 'expected',   label: 'Coupures voulues'     },
]

// L'état d'une règle vis-à-vis de ce que la base veut. Rouge = un client est
// hors ligne alors que plus personne ne veut le couper : le seul cas qui appelle
// une action tout de suite.
const STATE_STYLE: Record<RouterRuleRow['state'], { label: string; cls: string }> = {
  unexpected: { label: 'coupé à tort',   cls: 'bg-red-50 text-red-700 border-red-200'          },
  unknown:    { label: 'MAC inconnue',   cls: 'bg-amber-50 text-amber-800 border-amber-300'    },
  expected:   { label: 'coupure voulue', cls: 'bg-slate-100 text-slate-700 border-slate-300'   },
}

/** Le balayage « hors supervision » coupe des clients PERDUS DE VUE, pas des
 *  mauvais payeurs (cf. scripts/block_out_of_supervision.py). Sur le routeur sa
 *  règle est indiscernable d'une coupure pour impayé ; seul le motif enregistré
 *  en base les sépare, et la décision derrière n'est pas la même — d'où un
 *  marqueur propre plutôt qu'une ligne de texte de plus à lire. */
function isOutOfSupervision(reason: string | null): boolean {
  return (reason ?? '').toLowerCase().includes('hors supervision')
}

function formatTs(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

/** Compteur de la règle — ce que le routeur a réellement jeté. */
function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes < 1024) return `${bytes} o`
  const units = ['Kio', 'Mio', 'Gio', 'Tio']
  let value = bytes / 1024
  let i = 0
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1 }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[i]}`
}

export default function RouterRulesPage() {
  const [state, setState] = React.useState<RuleFilter>('')
  const [search, setSearch] = React.useState('')

  const { data, error, isLoading, isValidating, mutate } = useSWR<RouterRulesResponse>(
    endpoints.routerRules,
    fetcher,
    // Lecture à la demande : ni intervalle, ni revalidation au retour d'onglet.
    { revalidateOnFocus: false, revalidateOnReconnect: false, keepPreviousData: true },
  )

  const stats = data?.stats
  const rules = data?.rules ?? []
  const missing = data?.missing ?? []

  const needle = search.trim().toLowerCase()
  const shown = rules.filter((r) => {
    if (state === 'redundant' ? !r.redundant : Boolean(state) && r.state !== state) return false
    if (!needle) return true
    return (
      r.mac.toLowerCase().includes(needle)
      || (r.name ?? '').toLowerCase().includes(needle)
      || (r.site ?? '').toLowerCase().includes(needle)
      || (r.blocked_reason ?? '').toLowerCase().includes(needle)
      || r.comment.toLowerCase().includes(needle)
    )
  })

  return (
    <div className="space-y-6">
      <div className="flex items-start gap-4">
        <div>
          <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Règles du routeur</h1>
          <p className="text-blue-400 text-sm mt-1 max-w-3xl">
            Les coupures d'abonnés réellement posées sur le routeur de cœur
            (<span className="font-mono text-xs">chain=forward · action=drop</span>), lues{' '}
            <strong>en direct</strong> à l'ouverture de cette page. Le journal dit ce qui s'est
            passé, la base ce qu'on croit avoir posé — ici c'est ce que le routeur porte
            maintenant. Une règle n'est pas forcément un impayé : le balayage{' '}
            <strong>hors supervision</strong> coupe aussi des abonnés qu'on a perdus de vue. Le
            motif de chaque coupure est affiché.
          </p>
        </div>
        <button
          onClick={() => mutate()}
          disabled={isValidating}
          className="ml-auto shrink-0 px-3 py-1.5 rounded-lg border border-blue-200 bg-white text-sm
                     font-semibold text-blue-700 hover:bg-blue-50 disabled:opacity-50
                     transition-colors"
        >
          {isValidating ? 'Lecture…' : 'Actualiser'}
        </button>
      </div>

      {/* Le routeur n'a pas répondu : surtout ne rien laisser croire. */}
      {error && (
        <div className="bg-white rounded-xl border border-red-300 px-5 py-4">
          <h2 className="text-sm font-bold text-red-800">Routeur injoignable</h2>
          <p className="text-xs text-red-700 mt-1">
            Impossible de lire les règles — cette page n'affirme donc <strong>rien</strong> sur
            l'état des coupures. Vérifier que le routeur répond sur son port API et que le compte
            dédié est valide.
          </p>
        </div>
      )}

      {data && !data.available && (
        <div className="bg-white rounded-xl border border-amber-300 px-5 py-4">
          <h2 className="text-sm font-bold text-amber-900">Repli routeur non configuré</h2>
          <p className="text-xs text-amber-700 mt-1">
            {data.error ?? 'MIKROTIK_ENABLED est à false, ou le mot de passe est vide.'} Aucune
            coupure n'est posée sur le routeur tant qu'il est désactivé — la liste vide ci-dessous
            ne veut pas dire « aucun client bloqué ».
          </p>
        </div>
      )}

      {data?.available && (
        <p className="text-xs text-blue-400">
          Routeur <span className="font-mono">{data.host}</span> · lu le {formatTs(data.fetched_at)}
        </p>
      )}

      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          <StatCard label="Règles de coupure" value={stats.total} tone="neutral" />
          <StatCard label="Coupés à tort" value={stats.unexpected} tone="red" />
          <StatCard label="MAC inconnues" value={stats.unknown} tone="amber" />
          <StatCard label="Redondantes" value={stats.redundant} tone="blue" />
          <StatCard label="Coupures manquantes" value={stats.missing} tone="red" />
          <StatCard label="Posées par nous" value={stats.supervisor} tone="blue" />
        </div>
      )}

      {/* L'écart INVERSE : la base croit le client coupé, le routeur n'a rien. */}
      {missing.length > 0 && (
        <section className="bg-white rounded-xl border border-red-300 overflow-hidden">
          <header className="px-5 py-3 bg-red-50 border-b border-red-200">
            <h2 className="text-sm font-bold text-red-900">
              Coupure absente du routeur — {missing.length} client
              {missing.length > 1 ? 's' : ''}
            </h2>
            <p className="text-xs text-red-700 mt-0.5">
              La base indique ces clients coupés par le routeur, et le routeur ne porte aucune
              règle pour eux. Sauf coupure confirmée sur leur propre équipement (colonne
              ci-dessous), ils sont <strong>en ligne</strong>.
            </p>
          </header>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-white text-blue-900">
                <tr>
                  <Th>Client</Th><Th>MAC</Th><Th>IP</Th><Th>Site</Th>
                  <Th>Motif</Th><Th>Coupé sur son LR</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-blue-50">
                {missing.map((m) => (
                  <tr key={m.lr_id} className="hover:bg-blue-50/40">
                    <Td className="font-medium text-blue-900">{m.name}</Td>
                    <Td className="font-mono text-xs text-blue-500">{m.mac ?? '—'}</Td>
                    <Td>{m.ip_address ? <IpLink ip={m.ip_address} /> : '—'}</Td>
                    <Td className="text-blue-500">{m.site ?? '—'}</Td>
                    <Td className="text-xs text-blue-400 max-w-xs break-words">
                      {m.blocked_reason ?? '—'}
                    </Td>
                    <Td>
                      {m.enforced_on_lr
                        ? <span className="text-xs font-semibold text-green-700">oui</span>
                        : <span className="text-xs font-semibold text-red-700">non — en ligne ⚠</span>}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* Filtres */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map(({ value, label }) => (
          <button
            key={value || 'all'}
            onClick={() => setState(value)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
              state === value
                ? 'bg-blue-900 text-white border-blue-900'
                : 'bg-white text-blue-700 border-blue-200 hover:bg-blue-50'
            }`}
          >
            {label}
          </button>
        ))}
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filtrer par MAC, client, site, motif ou commentaire…"
          className="ml-auto w-72 px-3 py-1.5 rounded-lg border border-blue-200 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-300"
        />
      </div>

      <div className="bg-white rounded-xl border border-blue-100 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-blue-50 text-blue-900">
              <tr>
                <Th>Client</Th>
                <Th>MAC</Th>
                <Th>Site</Th>
                <Th>État</Th>
                <Th>Origine</Th>
                <Th>Trafic jeté</Th>
                <Th>Commentaire</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {shown.map((r) => (
                <tr key={r.rule_id ?? r.mac} className="hover:bg-blue-50/40">
                  <Td className="font-medium text-blue-900">
                    {r.name ?? <span className="text-blue-300">hors inventaire</span>}
                    {r.ip_address && (
                      <p className="text-xs font-normal mt-0.5"><IpLink ip={r.ip_address} /></p>
                    )}
                  </Td>
                  <Td className="font-mono text-xs text-blue-500">{r.mac}</Td>
                  <Td className="text-blue-500">{r.site ?? '—'}</Td>
                  <Td>
                    <span className={`inline-block px-2 py-0.5 rounded-md border text-xs font-semibold
                                      ${STATE_STYLE[r.state].cls}`}>
                      {STATE_STYLE[r.state].label}
                    </span>
                    {/* Une règle désactivée ne coupe rien : sans ce marqueur, la
                        ligne se lirait comme une coupure effective. */}
                    {r.disabled && (
                      <span className="ml-1.5 text-[10px] font-semibold text-amber-700">
                        désactivée — ne coupe pas
                      </span>
                    )}
                    {isOutOfSupervision(r.blocked_reason) && (
                      <span className="ml-1.5 inline-block px-2 py-0.5 rounded-md border text-xs
                                       font-semibold bg-blue-50 text-blue-700 border-blue-200">
                        hors supervision
                      </span>
                    )}
                    {/* Le motif tel qu'il a été enregistré : c'est lui qui dit si
                        la coupure vient d'un impayé ou du balayage des clients
                        perdus de vue — deux décisions qu'on ne traite pas pareil. */}
                    {r.blocked_reason && (
                      <p className="text-[11px] text-blue-400 mt-0.5 max-w-xs break-words">
                        {r.blocked_reason}
                      </p>
                    )}
                    {r.redundant && (
                      <p className="text-[11px] text-blue-400 mt-0.5">
                        déjà coupé sur son LR — règle redondante
                      </p>
                    )}
                  </Td>
                  <Td className="text-xs text-blue-500">
                    {r.origin === 'supervisor' ? 'Superviseur' : 'Système historique'}
                  </Td>
                  <Td className="text-xs text-blue-500 tabular-nums whitespace-nowrap">
                    {formatBytes(r.bytes)}
                    {r.packets !== null && (
                      <p className="text-[11px] text-blue-400 mt-0.5">{r.packets} paquets</p>
                    )}
                  </Td>
                  <Td className="text-xs text-blue-400 max-w-md break-words">
                    {r.comment || '—'}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {shown.length === 0 && (
          <p className="px-5 py-10 text-center text-sm text-blue-400">
            {isLoading
              ? 'Lecture du routeur…'
              : error
              ? 'Lecture impossible — voir le message ci-dessus.'
              : needle || state
              ? 'Aucune règle ne correspond à ce filtre.'
              : 'Le routeur ne porte aucune règle de coupure client.'}
          </p>
        )}
      </div>

      <p className="text-xs text-blue-400 max-w-3xl">
        Cette page ne modifie rien sur le routeur. Pour libérer un client coupé à tort, passer par
        le déblocage habituel : la règle du routeur est retirée dans la foulée. Retirer la règle à
        la main ne suffirait pas — le renforcement la reposerait au cycle suivant tant que la base
        veut couper ce client.
      </p>
    </div>
  )
}

function StatCard({
  label, value, tone,
}: { label: string; value: number; tone: 'neutral' | 'blue' | 'amber' | 'red' }) {
  const cls = {
    neutral: 'text-blue-900',
    blue:    'text-blue-700',
    amber:   'text-amber-700',
    red:     'text-red-700',
  }[tone]
  return (
    <div className="bg-white rounded-xl border border-blue-100 px-4 py-3">
      <p className="text-xs text-blue-400">{label}</p>
      <p className={`text-2xl font-bold mt-0.5 tabular-nums ${cls}`}>{value}</p>
    </div>
  )
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-4 py-2.5 text-left text-xs font-bold uppercase tracking-wide">{children}</th>
  )
}

function Td({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-4 py-2.5 align-top ${className}`}>{children}</td>
}
