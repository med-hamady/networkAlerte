'use client'

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { FaiAttentionRow, FaiJournalEntry, FaiJournalResponse } from '@/lib/types'
import IpLink from '@/components/IpLink'

type StatusFilter = '' | 'ok' | 'failed' | 'abandoned'

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: '',          label: 'Tout'                },
  { value: 'ok',        label: 'Appliqué'            },
  { value: 'failed',    label: 'Non appliqué'        },
  { value: 'abandoned', label: 'Abandonné ⚠'         },
]

// Une action = ce qu'on a essayé de faire. La couleur porte le sens métier :
// rouge = coupure, vert = rétablissement, ambre = échec définitif.
const ACTION_STYLE: Record<FaiJournalEntry['action'], { label: string; cls: string }> = {
  BLOCK:    { label: 'Blocage',        cls: 'bg-red-50 text-red-700 border-red-200'       },
  UNBLOCK:  { label: 'Déblocage',      cls: 'bg-green-50 text-green-700 border-green-200' },
  RETRY_OK: { label: 'Rattrapé',       cls: 'bg-blue-50 text-blue-700 border-blue-200'    },
  ABANDON:  { label: 'Abandonné',      cls: 'bg-amber-50 text-amber-800 border-amber-300' },
}

// Qui a demandé l'action. `script` = blocage de masse (migration depuis le MikroTik).
const SOURCE_LABEL: Record<string, string> = {
  payment: 'Système de paiement',
  enforce: 'Renforcement auto',
  script:  'Blocage de masse',
}

function formatTs(ts: string): string {
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts
  return d.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

export default function FaiJournalPage() {
  const [status, setStatus] = React.useState<StatusFilter>('')
  const [search, setSearch] = React.useState('')
  const [debounced, setDebounced] = React.useState('')
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 250)
    return () => clearTimeout(t)
  }, [search])

  const { data, isLoading } = useSWR<FaiJournalResponse>(
    endpoints.faiJournal(status, debounced),
    fetcher,
    { refreshInterval: 30_000, keepPreviousData: true },
  )

  const stats = data?.stats ?? { total: 0, ok: 0, failed: 0, abandoned: 0 }
  const entries = data?.entries ?? []
  const attention = data?.attention ?? []
  const blocking = attention.filter((r) => r.kind === 'unenforceable')
  const pending = attention.filter((r) => r.kind === 'pending')

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Journal des blocages</h1>
        <p className="text-blue-400 text-sm mt-1">
          Chaque coupure et rétablissement, qu'il vienne du <strong>système de paiement</strong>{' '}
          ou du renforcement automatique. Un ordre non appliqué est rejoué toutes les 2 minutes —
          sauf si le LR <strong>refuse la connexion</strong> (mot de passe, clé d'hôte) : il passe
          alors en « à traiter » ci-dessous et attend une intervention technique.
        </p>
      </div>

      {/* Compteurs sur la fenêtre affichée */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Actions" value={stats.total} tone="neutral" />
        <StatCard label="Appliquées" value={stats.ok} tone="green" />
        <StatCard label="En attente de rattrapage" value={stats.failed} tone="blue" />
        <StatCard label="Abandonnées" value={stats.abandoned} tone="amber" />
      </div>

      {/* À traiter — l'état RÉEL en base, pas l'historique */}
      {blocking.length > 0 && (
        <section className="bg-white rounded-xl border border-amber-300 overflow-hidden">
          <header className="px-5 py-3 bg-amber-50 border-b border-amber-200">
            <h2 className="text-sm font-bold text-amber-900">
              À traiter — {blocking.length} LR refuse{blocking.length > 1 ? 'nt' : ''} la connexion
            </h2>
            <p className="text-xs text-amber-700 mt-0.5">
              Le LR répond mais rejette notre authentification SSH. Aucune nouvelle tentative
              automatique : corriger les identifiants sur la fiche, ou intervenir sur l'équipement.
            </p>
          </header>
          <AttentionTable rows={blocking} showReason />
        </section>
      )}

      {pending.length > 0 && (
        <section className="bg-white rounded-xl border border-blue-200 overflow-hidden">
          <header className="px-5 py-3 bg-blue-50 border-b border-blue-100">
            <h2 className="text-sm font-bold text-blue-900">
              En attente — {pending.length} ordre{pending.length > 1 ? 's' : ''} à rejouer
            </h2>
            <p className="text-xs text-blue-500 mt-0.5">
              Le LR est injoignable (éteint, radio coupée). L'ordre sera appliqué automatiquement
              dès son retour — rien à faire.
            </p>
          </header>
          <AttentionTable rows={pending} />
        </section>
      )}

      {/* Filtres */}
      <div className="flex flex-wrap items-center gap-2">
        {FILTERS.map(({ value, label }) => (
          <button
            key={value || 'all'}
            onClick={() => setStatus(value)}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
              status === value
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
          placeholder="Filtrer par MAC ou nom du client…"
          className="ml-auto w-72 px-3 py-1.5 rounded-lg border border-blue-200 text-sm
                     focus:outline-none focus:ring-2 focus:ring-blue-300"
        />
      </div>

      {/* Historique */}
      <div className="bg-white rounded-xl border border-blue-100 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-blue-50 text-blue-900">
              <tr>
                <Th>Date</Th>
                <Th>Action</Th>
                <Th>Client</Th>
                <Th>MAC</Th>
                <Th>Origine</Th>
                <Th>Résultat</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {entries.map((e, i) => (
                <tr key={`${e.timestamp}-${e.mac}-${i}`} className="hover:bg-blue-50/40">
                  <Td className="whitespace-nowrap text-blue-500 tabular-nums">
                    {formatTs(e.timestamp)}
                  </Td>
                  <Td>
                    <span className={`inline-block px-2 py-0.5 rounded-md border text-xs font-semibold
                                      ${ACTION_STYLE[e.action]?.cls ?? 'bg-slate-50 text-slate-600 border-slate-200'}`}>
                      {ACTION_STYLE[e.action]?.label ?? e.action}
                    </span>
                    {e.mode === 'whatsapp_only' && (
                      <span className="ml-1.5 text-[10px] text-blue-400">WhatsApp</span>
                    )}
                  </Td>
                  <Td className="font-medium text-blue-900">{e.name}</Td>
                  <Td className="font-mono text-xs text-blue-500">{e.mac ?? '—'}</Td>
                  <Td className="text-xs text-blue-500">{SOURCE_LABEL[e.source] ?? e.source}</Td>
                  <Td>
                    <span className={`font-semibold ${e.ok ? 'text-green-700' : 'text-red-700'}`}>
                      {e.ok ? 'Appliqué' : 'Non appliqué'}
                    </span>
                    <p className="text-xs text-blue-400 mt-0.5 max-w-xl">{e.message}</p>
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {entries.length === 0 && (
          <p className="px-5 py-10 text-center text-sm text-blue-400">
            {isLoading
              ? 'Chargement…'
              : debounced || status
              ? 'Aucune action ne correspond à ce filtre.'
              : 'Aucune action enregistrée pour le moment.'}
          </p>
        )}
      </div>
    </div>
  )
}

function AttentionTable({ rows, showReason }: { rows: FaiAttentionRow[]; showReason?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-white text-blue-900">
          <tr>
            <Th>Client</Th>
            <Th>MAC</Th>
            <Th>IP</Th>
            <Th>Site</Th>
            <Th>Ordre</Th>
            {showReason && <Th>Cause</Th>}
          </tr>
        </thead>
        <tbody className="divide-y divide-blue-50">
          {rows.map((r) => (
            <tr key={r.id} className="hover:bg-blue-50/40">
              <Td className="font-medium text-blue-900">{r.name}</Td>
              <Td className="font-mono text-xs text-blue-500">{r.mac ?? '—'}</Td>
              <Td>{r.ip_address ? <IpLink ip={r.ip_address} /> : '—'}</Td>
              <Td className="text-blue-500">{r.site ?? '—'}</Td>
              <Td>
                <span className={`inline-block px-2 py-0.5 rounded-md border text-xs font-semibold ${
                  r.intent === 'block'
                    ? 'bg-red-50 text-red-700 border-red-200'
                    : 'bg-green-50 text-green-700 border-green-200'
                }`}>
                  {r.intent === 'block' ? 'à couper' : 'à rétablir'}
                </span>
              </Td>
              {showReason && (
                <Td className="text-xs text-amber-800 max-w-md">{r.reason ?? '—'}</Td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function StatCard({
  label, value, tone,
}: { label: string; value: number; tone: 'neutral' | 'green' | 'blue' | 'amber' }) {
  const cls = {
    neutral: 'text-blue-900',
    green:   'text-green-700',
    blue:    'text-blue-700',
    amber:   'text-amber-700',
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
