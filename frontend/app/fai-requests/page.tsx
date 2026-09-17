'use client'

/**
 * Demandes de coupure — ce que le système de paiement nous a demandé, et où en
 * est réellement chaque client aujourd'hui.
 *
 * ⚠️ Pourquoi cette page N'EST PAS /fai-journal avec un filtre de plus : les
 * deux n'ont pas la même unité. Le journal rend des ÉVÉNEMENTS (toutes actions,
 * toutes origines, y compris le renforcement automatique qui rejoue seul) ;
 * ici, une ligne est une DEMANDE d'un script appelant, à laquelle on accroche
 * l'état courant du client. C'est ce croisement — et non l'historique — qui
 * répond à « la campagne du 12 a-t-elle vraiment coupé tout le monde ? ».
 *
 * Les deux vues lisent le même endpoint et le même barème de rendu
 * (`lib/faiActions`) : elles ne peuvent pas se contredire.
 */

import React from 'react'
import useSWR from 'swr'
import { endpoints, fetcher } from '@/lib/api'
import type { FaiJournalEntry, FaiJournalResponse } from '@/lib/types'
import {
  ACTION_STYLE, ENFORCED_BY_LABEL, FALLBACK_ACTION_STYLE, NOW_STYLE,
  formatTs, nowState, readableMessage, sourceLabel, userLabel, type NowState,
} from '@/lib/faiActions'

// L'origine par défaut : c'est la question posée. « Toutes » reste à un clic —
// voir la note sur les demandes non attribuées, plus bas.
const DEFAULT_SOURCE = 'Block_all.php'

const SOURCES: { value: string; label: string }[] = [
  { value: DEFAULT_SOURCE, label: 'Block_all.php (campagne impayés)' },
  { value: 'payment',      label: 'Système de paiement (non attribué)' },
  { value: 'enforce',      label: 'Renforcement automatique' },
  { value: 'script',       label: 'Blocage de masse' },
  { value: '',             label: 'Toutes les origines' },
]

type NowFilter = '' | NowState

const NOW_FILTERS: { value: NowFilter; label: string }[] = [
  { value: '',         label: 'Tout'           },
  { value: 'cut',      label: 'Coupés'         },
  { value: 'not_cut',  label: 'Non coupés ⚠'   },
  { value: 'restored', label: 'Rétablis'       },
  { value: 'gone',     label: 'Fiche supprimée' },
]

const isoDay = (d: Date) => d.toISOString().slice(0, 10)

export default function FaiRequestsPage() {
  const today = isoDay(new Date())
  const weekAgo = isoDay(new Date(Date.now() - 7 * 86_400_000))

  const [source, setSource] = React.useState(DEFAULT_SOURCE)
  const [now, setNow] = React.useState<NowFilter>('')
  const [search, setSearch] = React.useState('')
  const [debounced, setDebounced] = React.useState('')
  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 250)
    return () => clearTimeout(t)
  }, [search])

  // Brouillon vs appliqué : une plage ne part au serveur qu'au clic, sinon
  // chaque frappe dans un champ date déclencherait une requête sur une borne
  // à moitié saisie.
  const [draftStart, setDraftStart] = React.useState(weekAgo)
  const [draftEnd, setDraftEnd] = React.useState(today)
  const [range, setRange] = React.useState<{ start: string; end: string } | null>(null)
  const rangeInvalid = !draftStart || !draftEnd || draftEnd < draftStart

  const { data, isLoading } = useSWR<FaiJournalResponse>(
    endpoints.faiJournal({
      source, search: debounced, start: range?.start, end: range?.end,
    }),
    fetcher,
    { refreshInterval: 30_000, keepPreviousData: true },
  )

  // ⚠️ Les compteurs de l'API (`stats`) décrivent TOUT le fichier d'audit, pas
  // la sélection — c'est leur contrat. Les tuiles de cette page comptent donc
  // ce qu'elles affichent, sur les lignes rendues.
  const entries = React.useMemo(() => data?.entries ?? [], [data])
  const rows = React.useMemo(
    () => (now ? entries.filter((e) => nowState(e.current) === now) : entries),
    [entries, now],
  )
  const counts = React.useMemo(() => {
    const c: Record<NowState, number> = { cut: 0, not_cut: 0, restored: 0, gone: 0 }
    for (const e of entries) c[nowState(e.current)] += 1
    return c
  }, [entries])

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-blue-900 tracking-tight">Demandes de coupure</h1>
        <p className="text-blue-400 text-sm mt-1">
          Chaque demande reçue du système de paiement, avec l'état <strong>réel</strong> du
          client aujourd'hui. Les deux ne disent pas la même chose : une demande qui a
          échoué hier peut avoir été rattrapée depuis, et une demande « appliquée » peut
          être tombée si le client a redémarré son équipement.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Demandes affichées" value={entries.length} tone="neutral" />
        <StatCard label="Clients coupés" value={counts.cut} tone="slate" />
        <StatCard label="Non coupés" value={counts.not_cut} tone="red" />
        <StatCard label="Rétablis depuis" value={counts.restored} tone="green" />
      </div>

      {counts.not_cut > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-xl px-5 py-3">
          <p className="text-sm font-bold text-red-800">
            {counts.not_cut} client{counts.not_cut > 1 ? 's' : ''} sous ordre de coupure
            {counts.not_cut > 1 ? ' sont' : ' est'} toujours en ligne
          </p>
          <p className="text-xs text-red-700 mt-0.5">
            La demande a été enregistrée mais la coupure n'est posée nulle part — ni sur
            l'équipement du client, ni sur le routeur. Le renforcement rejoue toutes les
            2 minutes ; si la situation dure, la cause est sur la page{' '}
            <a href="/fai-journal" className="underline font-semibold">Journal des blocages</a>.
          </p>
        </div>
      )}

      {/* Filtres */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="px-3 py-1.5 rounded-lg border border-blue-200 bg-white text-sm
                       text-blue-900 font-medium focus:outline-none focus:ring-2 focus:ring-blue-300"
          >
            {SOURCES.map((s) => (
              <option key={s.value || 'all'} value={s.value}>{s.label}</option>
            ))}
          </select>

          <div className={`flex items-center gap-2 rounded-lg bg-white border p-1.5 ${
            range ? 'border-blue-400' : 'border-blue-200'
          }`}>
            <span className="text-[11px] font-semibold text-blue-500 uppercase tracking-wider pl-1">Du</span>
            <input
              type="date" value={draftStart} max={draftEnd || today}
              onChange={(e) => setDraftStart(e.target.value)}
              className="text-xs text-slate-700 border border-blue-100 rounded-md px-2 py-1
                         focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
            <span className="text-[11px] font-semibold text-blue-500 uppercase tracking-wider">Au</span>
            <input
              type="date" value={draftEnd} min={draftStart} max={today}
              onChange={(e) => setDraftEnd(e.target.value)}
              className="text-xs text-slate-700 border border-blue-100 rounded-md px-2 py-1
                         focus:outline-none focus:ring-1 focus:ring-blue-400"
            />
            <button
              onClick={() => !rangeInvalid && setRange({ start: draftStart, end: draftEnd })}
              disabled={rangeInvalid}
              className="px-3 py-1 text-xs font-semibold rounded-md bg-blue-600 text-white
                         hover:bg-blue-700 transition-colors disabled:opacity-40"
            >
              Appliquer
            </button>
            {range && (
              <button
                onClick={() => setRange(null)}
                className="px-2 py-1 text-xs font-semibold rounded-md text-blue-700 hover:bg-blue-50"
              >
                Tout l'historique
              </button>
            )}
          </div>

          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Filtrer par MAC, nom du client ou agent…"
            className="ml-auto w-72 px-3 py-1.5 rounded-lg border border-blue-200 text-sm
                       focus:outline-none focus:ring-2 focus:ring-blue-300"
          />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {NOW_FILTERS.map(({ value, label }) => (
            <button
              key={value || 'all'}
              onClick={() => setNow(value)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium border transition-colors ${
                now === value
                  ? 'bg-blue-900 text-white border-blue-900'
                  : 'bg-white text-blue-700 border-blue-200 hover:bg-blue-50'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-blue-100 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-blue-50 text-blue-900">
              <tr>
                <Th>Demandé le</Th>
                <Th>Client</Th>
                <Th>MAC</Th>
                <Th>Résultat de la demande</Th>
                <Th>Maintenant</Th>
                <Th>Coupé où ?</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-blue-50">
              {rows.map((e, i) => (
                <Row key={`${e.timestamp}-${e.mac}-${i}`} entry={e} />
              ))}
            </tbody>
          </table>
        </div>

        {rows.length === 0 && (
          <div className="px-5 py-10 text-center">
            <p className="text-sm text-blue-400">
              {isLoading ? 'Chargement…' : 'Aucune demande ne correspond à ces filtres.'}
            </p>
            {/* ⚠️ Un écran vide ne doit jamais se lire « ce script n'a rien
                demandé » : une demande n'est attribuée à Block_all.php que si
                son MOTIF porte la signature attendue. Si le script change sa
                formulation, ses demandes retombent en « payment ». */}
            {!isLoading && source === DEFAULT_SOURCE && (
              <p className="text-xs text-blue-400 mt-2">
                Une demande n'est attribuée à ce script que si son motif porte sa
                signature.{' '}
                <button
                  onClick={() => setSource('')}
                  className="underline font-semibold text-blue-700"
                >
                  Voir toutes les origines
                </button>
              </p>
            )}
          </div>
        )}
      </div>

      <p className="text-xs text-blue-400">
        « Résultat de la demande » est figé à l'instant de l'appel ; « Maintenant » est
        relu en base à chaque affichage. Ce que porte réellement le routeur de cœur se
        vérifie sur <a href="/router-rules" className="underline font-semibold">Règles du routeur</a>.
      </p>
    </div>
  )
}

function Row({ entry: e }: { entry: FaiJournalEntry }) {
  const state = nowState(e.current)
  const style = NOW_STYLE[state]
  const action = ACTION_STYLE[e.action]
  const agent = userLabel(e.user)
  const msg = readableMessage(e.message)

  return (
    <tr className="hover:bg-blue-50/40">
      <Td className="whitespace-nowrap text-blue-500 tabular-nums">
        {formatTs(e.timestamp)}
        {agent && <p className="text-[11px] text-blue-400 mt-0.5 break-all max-w-[12rem]">{agent}</p>}
      </Td>
      <Td className="font-medium text-blue-900">
        {e.name}
        <p className="text-[11px] text-blue-400 mt-0.5">
          {e.current.site ?? '—'} · {sourceLabel(e.source)}
        </p>
      </Td>
      <Td className="font-mono text-xs text-blue-500">{e.mac ?? '—'}</Td>
      <Td>
        <span className={`inline-block px-2 py-0.5 rounded-md border text-xs font-semibold
                          ${action?.cls ?? FALLBACK_ACTION_STYLE}`}>
          {action?.label ?? e.action}
        </span>
        <span className={`ml-1.5 text-xs font-semibold ${e.ok ? 'text-green-700' : 'text-red-700'}`}>
          {e.ok ? 'appliquée' : 'échec'}
        </span>
        {/* Reformulé pour la lecture ; le brut reste en infobulle (piste d'audit). */}
        <p className="text-xs text-blue-400 mt-0.5 max-w-md" title={msg.raw}>{msg.text}</p>
      </Td>
      <Td>
        <span className={`inline-block px-2 py-0.5 rounded-md border text-xs font-bold ${style.cls}`}>
          {style.label}
        </span>
        {/* Le motif du blocage EN COURS : un impayé et un balayage « hors
            supervision » produisent la même coupure mais pas le même geste. */}
        {state === 'cut' && e.current.blocked_reason && (
          <p className="text-[11px] text-blue-400 mt-0.5 max-w-xs">{e.current.blocked_reason}</p>
        )}
        {state === 'not_cut' && e.current.unenforceable_reason && (
          <p className="text-[11px] text-red-600 mt-0.5 max-w-xs">
            {e.current.unenforceable_reason}
          </p>
        )}
      </Td>
      <Td className="whitespace-nowrap">
        {e.current.enforced_by ? (
          <span className={`text-xs font-semibold ${
            e.current.enforced_by === 'lr' ? 'text-blue-700' : 'text-slate-600'
          }`}>
            {ENFORCED_BY_LABEL[e.current.enforced_by]}
          </span>
        ) : (
          <span className="text-xs text-blue-300">—</span>
        )}
      </Td>
    </tr>
  )
}

function StatCard({
  label, value, tone,
}: { label: string; value: number; tone: 'neutral' | 'green' | 'red' | 'slate' }) {
  const cls = {
    neutral: 'text-blue-900',
    green:   'text-green-700',
    red:     'text-red-700',
    slate:   'text-slate-700',
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
